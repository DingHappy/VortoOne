"""agent 在后台干活时停下来等批准的那一步，要出现在新界面的审批里，并能在那里批准或拒绝。

完整能力的 agent（主人 2026-11-22 定）会写、会执行、会对外发送，这些步骤都要主人批。以前 agent 只读，
审批卡只在旧版 Desktop 的专员页里看得到；新界面的审批列表里没有，agent 就一直停在那一步。

等批准的那一步要一直挂在同一个事件循环上，所以这里用异步客户端直连应用（不带 with 的测试客户端
每个请求换一个循环，挂着的任务会被取消；带 with 又会跑整套服务启动）。
"""
import asyncio
import time
from copy import deepcopy

import httpx
import pytest

from src.workflow.staff.service import StaffService
from src.workflow.tasks.tasks import TaskRunner
from tests.unit.test_member_agents import home  # noqa: F401 - pytest fixture


@pytest.fixture
def full_agent(home, monkeypatch):
    from src.agents.staff import build_staff_member, set_full_toolset
    from src.agents.tool import PreparedToolCall, Tool
    invoked, results = [], []

    async def prepare(args):
        frozen = deepcopy(args)

        async def apply():
            invoked.append(frozen)
            return '周报写好了'
        return PreparedToolCall(apply, '写入文件 周报.md：' + frozen['text'], 'write')

    def builder(root, ask):
        return [Tool.prepared('host_write', '写文件', {'text': '正文'}, prepare, operation_kind='write',
                              required_args=('text',))], (lambda: 'ask'), 'external'
    set_full_toolset(builder)

    async def execute(task, work, role, member, check):
        async def confirm(call):
            return await service.confirm_action(task, role, member, call)
        agent = build_staff_member(home["root"], role, member, check=check, llm=object(), confirm_action=confirm)
        results.append(await agent._run_tool('host_write', {'text': '本周进展'}, 'build', lambda _: None))
        return '做完了：' + results[-1]
    service = StaffService(home["root"], TaskRunner(home["root"], None, max_concurrent=2), execute=execute)
    monkeypatch.setattr("src.scheduling.staff_runner.get_staff_service", lambda root: service)
    owner, base = home["owner"], home["base"]
    created = owner.post(f"{base}/agents", json={"name": "全能", "handle": "helper", "role": "助手"})
    assert created.status_code == 200, created.text
    channel = owner.post(f"{base}/dms", json={"agentId": created.json()["id"]}).json()["channelId"]
    yield {**home, "channel": channel, "invoked": invoked, "results": results, "service": service}
    set_full_toolset(None)


def _client(home, *, owner=True):
    headers = dict(home["owner"].headers) if owner else {}
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=home["app"]), base_url="http://testserver",
                             headers=headers)


async def _wait(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = await predicate()
        if value:
            return value
        await asyncio.sleep(0.05)
    raise AssertionError("等不到")


async def _start(home, client):
    sent = await client.post(f"{home['base']}/channels/{home['channel']}/messages", json={"body": "写周报"})
    assert sent.status_code == 200, sent.text

    async def card():
        rows = (await client.get(f"{home['base']}/approvals")).json()["approvals"]
        return next((row for row in rows if row["id"].startswith("staff-action-")), None)
    return await _wait(card)


async def _reply(home, client):
    async def reply():
        messages = (await client.get(f"{home['base']}/channels/{home['channel']}/messages")).json()["messages"]
        runs = (await client.get(f"{home['base']}/channels/{home['channel']}/active-runs")).json()["runs"]
        replies = [m["body"] for m in messages if m["author"]["type"] == "agent"]
        return replies[-1] if replies and not runs else None
    return await _wait(reply)


async def test_the_owner_approves_an_agents_step_in_the_approvals_list(full_agent):
    home, base = full_agent, full_agent["base"]
    async with _client(home) as owner:
        card = await _start(home, owner)
        assert card["tool"] == "host_write" and card["scope"] == "write" and card["canDecide"] is True
        assert "全能" in card["title"] and "本周进展" in card["detail"] and "写周报" in card["detail"]
        assert card["channelId"] == home["channel"] and card["runId"].startswith("task-")
        runs = (await owner.get(f"{base}/channels/{home['channel']}/active-runs")).json()["runs"]
        assert [run["status"] for run in runs] == ["running"]          # 在等批准，不是还没开始
        assert (await owner.get(f"{base}/approvals/{card['id']}")).json()["id"] == card["id"]
        assert not home["invoked"]
        decided = await owner.post(f"{base}/approvals/{card['id']}/approve", json={})
        assert decided.status_code == 200, decided.text
        assert decided.json()["status"] == "approved"
        assert await _reply(home, owner) == "做完了：周报写好了"
        assert home["invoked"] == [{"text": "本周进展"}]
        rows = (await owner.get(f"{base}/approvals")).json()["approvals"]
        assert not [row for row in rows if row["id"].startswith("staff-action-")]


async def test_refusing_tells_the_agent_why_and_nothing_is_written(full_agent):
    home, base = full_agent, full_agent["base"]
    async with _client(home) as owner:
        card = await _start(home, owner)
        refused = await owner.post(f"{base}/approvals/{card['id']}/refuse", json={"reason": "先给我看草稿"})
        assert refused.status_code == 200 and refused.json()["status"] == "denied"
        await _reply(home, owner)
    assert not home["invoked"] and "先给我看草稿" in home["results"][0]


async def test_agent_steps_are_left_out_of_batch_decisions_and_stale_ids_do_nothing(full_agent):
    home, base = full_agent, full_agent["base"]
    async with _client(home) as owner:
        card = await _start(home, owner)
        batch = (await owner.post(f"{base}/approvals/decide", json={"ids": [card["id"]], "decision": "approve"})).json()
        assert batch == {"approvals": [], "skipped": [card["id"]]}
        stale = card["id"].split("~")[0] + "~" + "0" * 16
        assert (await owner.post(f"{base}/approvals/{stale}/approve", json={})).status_code == 409
        assert (await owner.get(f"{base}/approvals/{stale}")).status_code == 404
        assert not home["invoked"]
        await owner.post(f"{base}/approvals/{card['id']}/refuse", json={})
        await _reply(home, owner)


async def test_deciding_an_agents_step_needs_the_owner(full_agent):
    home, base = full_agent, full_agent["base"]
    async with _client(home) as owner, _client(home, owner=False) as anonymous:
        card = await _start(home, owner)
        assert (await anonymous.post(f"{base}/approvals/{card['id']}/approve", json={})).status_code in {401, 403}
        assert not home["invoked"]
        await owner.post(f"{base}/approvals/{card['id']}/refuse", json={})
        await _reply(home, owner)
