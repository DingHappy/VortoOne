"""在干活、等你批、等你答，新界面的「任务管理器」「等我处理」「正在运行」都要看得见。

这三个接口以前登记了路由却回固定的空（或只算主助理）：agent 明明停在审批上，界面显示没有任何事。
"""
import asyncio
import time
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest

from src.workflow.staff.service import StaffService
from src.workflow.tasks.tasks import TaskRunner
from tests.unit.test_member_agents import _join, home  # noqa: F401 - pytest fixture


@pytest.fixture
def work(home, monkeypatch):
    from src.agents.staff import build_question_tool, build_staff_member, set_full_toolset
    from src.agents.tool import PreparedToolCall, Tool
    gate = SimpleNamespace(release=None)

    async def prepare(args):
        frozen = deepcopy(args)

        async def apply():
            return "写好了"
        return PreparedToolCall(apply, "写入文件 周报.md：" + frozen["text"], "write")

    def builder(root, ask, agent_id):
        return [Tool.prepared("host_write", "写文件", {"text": "正文"}, prepare, operation_kind="write",
                              required_args=("text",))], (lambda: "ask"), "external", {}
    set_full_toolset(builder)

    async def execute(task, work, role, member, check):
        async def confirm(call):
            return await service.confirm_action(task, role, member, call)
        if "失败" in task.prompt:
            raise RuntimeError("做不了")
        if "慢" in task.prompt:
            gate.release = asyncio.Event()
            await gate.release.wait()
        if "问" in task.prompt and task.collaboration["round"] == 1:
            await build_question_tool(work, task).handler({"question": "按哪个月？", "choices": ["十月", "十一月"]})
            return "按哪个月？"
        if "写" in task.prompt:
            agent = build_staff_member(home["root"], role, member, check=check, llm=object(), confirm_action=confirm)
            await agent._run_tool("host_write", {"text": "本周进展"}, "build", lambda _text: None)
        return "做完了"
    service = StaffService(home["root"], TaskRunner(home["root"], None, max_concurrent=3), execute=execute)
    monkeypatch.setattr("src.scheduling.staff_runner.get_staff_service", lambda root: service)
    created = home["owner"].post(f"{home['base']}/agents", json={"name": "全能", "handle": "helper"})
    assert created.status_code == 200, created.text
    agent = created.json()["id"]
    channel = home["owner"].post(f"{home['base']}/dms", json={"agentId": agent}).json()["channelId"]
    yield SimpleNamespace(**{**home, "agent": agent, "channel": channel, "service": service}, gate=gate)
    set_full_toolset(None)


def _client(work, headers=None):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=work.app), base_url="http://testserver",
                             headers=dict(work.owner.headers) if headers is None else headers)


async def _get(client, work, path, **params):
    answer = await client.get(f"{work.base}/{path}", params=params)
    assert answer.status_code == 200, answer.text
    return answer.json()


async def _until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = await predicate()
        if value:
            return value
        await asyncio.sleep(0.05)
    raise AssertionError("等不到")


async def _send(client, work, body):
    sent = await client.post(f"{work.base}/channels/{work.channel}/messages", json={"body": body})
    assert sent.status_code == 200, sent.text


async def _task(client, work, status):
    async def found():
        tasks = (await _get(client, work, "task-manager"))["tasks"]
        return next((task for task in tasks if task["status"] == status), None)
    return await _until(found)


async def _ended(client, work, status):
    async def found():
        ended = (await _get(client, work, "task-manager"))["ended"]
        return next((task for task in ended if task["status"] == status), None)
    return await _until(found)


async def test_a_running_agent_is_visible_everywhere(work):
    async with _client(work) as owner:
        await _send(owner, work, "慢慢整理")
        task = await _task(owner, work, "running")
        assert (task["agentId"], task["channelId"], task["channelKind"]) == (work.agent, work.channel, "dm")
        assert task["work"]["state"] == "working" and task["canStop"] is True
        assert task["label"] is None and task["threadId"] is None and task["requester"]["userId"]
        runs = (await _get(owner, work, "active-runs"))["runs"]
        assert [(run["runId"], run["agentId"], run["status"]) for run in runs] == [(task["runId"], work.agent, "running")]
        assert await _get(owner, work, "waiting") == {"items": [], "count": 0}
        await _until(lambda: asyncio.sleep(0, result=work.gate.release))
        work.gate.release.set()
        ended = await _ended(owner, work, "done")
        assert ended["runId"] == task["runId"] and ended["finishedAt"]
        assert (await _get(owner, work, "task-manager"))["tasks"] == []
        assert (await _get(owner, work, "active-runs"))["runs"] == []


async def test_waiting_for_approval_waits_on_the_owner(work):
    async with _client(work) as owner:
        await _send(owner, work, "写周报")
        task = await _task(owner, work, "waiting_approval")
        assert task["work"]["state"] == "you"
        waiting = await _get(owner, work, "waiting")
        card = (await _get(owner, work, "approvals"))["approvals"][0]
        assert waiting["count"] == 1
        item = waiting["items"][0]
        assert (item["id"], item["kind"], item["question"]) == (card["id"], "approval", card["title"])
        assert item["asker"] == {"type": "agent", "id": work.agent}
        assert item["source"]["approvalId"] == card["id"] and item["source"]["channelId"] == work.channel
        assert (await _get(owner, work, "active-runs"))["runs"][0]["status"] == "waiting_approval"
        decided = await owner.post(f"{work.base}/approvals/{card['id']}/approve", json={})
        assert decided.status_code == 200
        await _ended(owner, work, "done")
        assert await _get(owner, work, "waiting") == {"items": [], "count": 0}


async def test_an_open_question_waits_on_the_owner_and_is_not_finished(work):
    async with _client(work) as owner:
        await _send(owner, work, "先问我再统计")
        task = await _task(owner, work, "waiting_input")
        messages = (await _get(owner, work, f"channels/{work.channel}/messages"))["messages"]
        card = next(message for message in messages if (message.get("meta") or {}).get("question"))
        question = card["meta"]["question"]
        assert task["work"] == {"state": "you", "since": task["work"]["since"], "card": card["id"]}
        assert task["canStop"] is False and (await _get(owner, work, "task-manager"))["ended"] == []
        item = (await _get(owner, work, "waiting"))["items"][0]
        assert (item["kind"], item["question"]) == ("agent_question", "按哪个月？")
        assert item["source"]["questionId"] == question["id"] and item["source"]["messageId"] == card["id"]
        answered = await owner.post(f"{work.base}/questions/{question['id']}/answer", json={"choice": "十一月"})
        assert answered.status_code == 200
        await _ended(owner, work, "done")
        assert await _get(owner, work, "waiting") == {"items": [], "count": 0}


async def test_failures_are_not_shown_as_done_and_the_window_is_honoured(work):
    async with _client(work) as owner:
        await _send(owner, work, "会失败的事")
        await _ended(owner, work, "failed")
        view = await _get(owner, work, "task-manager", since="2099-01-01T00:00:00.000Z", doneToday="true")
        assert view["ended"] == [] and view["doneToday"] == 0
        bad = await owner.get(f"{work.base}/task-manager", params={"since": "yesterday"})
        assert bad.status_code == 400


async def test_waiting_can_be_narrowed_to_one_conversation(work):
    async with _client(work) as owner:
        await _send(owner, work, "写周报")
        await _task(owner, work, "waiting_approval")
        assert (await _get(owner, work, "waiting", channelId=work.channel))["count"] == 1
        assert (await _get(owner, work, "waiting", channelId="another"))["count"] == 0
        card = (await _get(owner, work, "approvals"))["approvals"][0]
        await owner.post(f"{work.base}/approvals/{card['id']}/refuse", json={})
        await _ended(owner, work, "done")


async def test_a_members_work_is_counted_but_not_shown(work):
    changed = work.owner.patch(f"{work.base}/agents/{work.agent}",
                               json={"audience": {"mode": "everyone", "userIds": [], "roleIds": []}})
    assert changed.status_code == 200, changed.text
    member = _join(vars(work))
    opened = member.post(f"{work.base}/dms", json={"agentId": work.agent})
    assert opened.status_code == 200, opened.text
    headers = {**dict(member.headers), "cookie": "; ".join(f"{k}={v}" for k, v in member.cookies.items())}
    async with _client(work) as owner, _client(work, headers) as person:
        sent = await person.post(f"{work.base}/channels/{opened.json()['channelId']}/messages",
                                 json={"body": "慢慢查我的私事"})
        assert sent.status_code == 200, sent.text

        async def hidden():
            view = await _get(owner, work, "task-manager")
            return view if view["hidden"]["working"] else None
        view = await _until(hidden)
        assert view["tasks"] == [] and view["hidden"] == {"total": 1, "working": 1, "byAgent": {work.agent: 1}}
        assert (await _get(owner, work, "active-runs"))["runs"] == []
        assert (await person.get(f"{work.base}/task-manager")).status_code in {401, 403, 404}
        await _until(lambda: asyncio.sleep(0, result=work.gate.release))
        work.gate.release.set()
        await _until(lambda: asyncio.sleep(0, result=not work.service.runner._running))


async def test_without_the_owner_proof_the_agents_conversations_stay_closed(work, monkeypatch):
    async with _client(work) as owner:
        await _send(owner, work, "写周报")
        await _task(owner, work, "waiting_approval")
        card = (await _get(owner, work, "approvals"))["approvals"][0]
        with monkeypatch.context() as patch:
            patch.setattr("src.web.app_staff.owner_proof", lambda request: False)
            assert (await _get(owner, work, "task-manager"))["tasks"] == []
            assert (await _get(owner, work, "active-runs"))["runs"] == []
            assert (await _get(owner, work, "waiting")) == {"items": [], "count": 0}
        await owner.post(f"{work.base}/approvals/{card['id']}/refuse", json={})
        await _ended(owner, work, "done")
