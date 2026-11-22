"""新界面里交给 agent 一件事，要用真的执行池走通：执行池只能在事件循环线程里登记任务。

之前的测试都用不需要事件循环的替身执行池，没发现这几个入口挪到了工作线程：
所有者私聊派活回 500（RuntimeError: no running event loop），事情以「未能进入现有执行池」失败。
"""
import time

import pytest

from src.workflow.staff.service import StaffService
from src.workflow.tasks.tasks import TaskRunner
from tests.unit.test_member_agents import _join, home  # noqa: F401 - pytest fixture


@pytest.fixture
def real_pool(home, monkeypatch):
    async def execute(task, work, role, member, check):
        check()
        return "按要求做完了"
    service = StaffService(home["root"], TaskRunner(home["root"], None, max_concurrent=2), execute=execute)
    monkeypatch.setattr("src.scheduling.staff_runner.get_staff_service", lambda root: service)
    home["service"] = service
    return home


def _reply(client, home, channel, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        messages = client.get(f"{home['base']}/channels/{channel}/messages").json()["messages"]
        replies = [m["body"] for m in messages if m["author"]["type"] == "agent"]
        if replies:
            return replies[-1]
        time.sleep(0.05)
    raise AssertionError("agent 没有交回结果")


def test_the_owner_hands_an_agent_a_task_and_it_runs(real_pool):
    home = real_pool
    sent = home["owner"].post(f"{home['base']}/channels/{home['channel']}/messages", json={"body": "整理一下资料"})
    assert sent.status_code == 200, sent.text
    assert _reply(home["owner"], home, home["channel"]) == "按要求做完了"


def test_a_member_hands_an_agent_a_task_and_it_runs(real_pool):
    home = real_pool
    member = _join(home)
    sent = member.post(f"{home['base']}/channels/{home['channel']}/messages", json={"body": "帮我查一下"})
    assert sent.status_code == 200, sent.text
    assert _reply(member, home, home["channel"]) == "按要求做完了"


def test_asking_an_agent_on_a_card_runs(real_pool):
    home = real_pool
    owner, base = home["owner"], home["base"]
    card = owner.post(f"{base}/issues", json={"title": "整理发票"}).json()
    asked = owner.post(f"{base}/issues/{card['id']}/ask-agent", json={"agentId": home["agent"]["id"]})
    assert asked.status_code == 200, asked.text
    assert _reply(owner, home, home["channel"]) == "按要求做完了"


def test_dispatch_claims_the_main_chat_when_it_was_never_opened(real_pool, monkeypatch):
    """新界面里直接去 agent 私聊派活（没打开过主要聊天）：先登记主要聊天，而不是回「请先打开主要聊天」。"""
    home = real_pool
    claimed = []

    async def bridge(root):
        claimed.append(root)
    monkeypatch.setattr("src.web.routers.app_api.views.main_chat_key", lambda root: "" if not claimed else "sid-owner")
    monkeypatch.setattr("src.web.routers.app_api.main_chat_bridge", bridge)
    sent = home["owner"].post(f"{home['base']}/channels/{home['channel']}/messages", json={"body": "第一件事"})
    assert sent.status_code == 200, sent.text
    assert claimed == [home["root"]]


def test_answering_a_question_continues_on_the_real_pool(real_pool):
    from src.agents.staff import build_question_tool
    home = real_pool

    async def execute(task, work, role, member, check):
        if task.collaboration["round"] == 1:
            await build_question_tool(work, task).handler({"question": "按哪个月？", "choices": ["十月", "十一月"]})
            return "按哪个月？"
        return "按回答做完了"
    home["service"].execute = execute
    owner, base, channel = home["owner"], home["base"], home["channel"]
    assert owner.post(f"{base}/channels/{channel}/messages", json={"body": "统计报销"}).status_code == 200
    assert _reply(owner, home, channel) == "按哪个月？"
    messages = owner.get(f"{base}/channels/{channel}/messages").json()["messages"]
    question = next(m["meta"]["question"] for m in messages if (m.get("meta") or {}).get("question"))
    answered = owner.post(f"{base}/questions/{question['id']}/answer", json={"choice": "十一月"})
    assert answered.status_code == 200, answered.text
    deadline = time.monotonic() + 10
    while _reply(owner, home, channel) != "按回答做完了":
        assert time.monotonic() < deadline
        time.sleep(0.05)
