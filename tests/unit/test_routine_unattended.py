"""完整能力的 agent 担任例行工作时降为无人值守档（主人 2026-11-22 定）：只读、不联网、不问人。

走真实的登记、批准、派发和装配，只有模型换成假的；完整工具装配一旦被用到就算失败。
"""
import asyncio
import time
from datetime import datetime
from functools import partial
from types import SimpleNamespace

import pytest

from src.agents.staff import execute_staff, set_full_toolset
from src.workflow.staff.roles import routine_roles
from src.workflow.staff.routines import Routines
from src.workflow.staff.service import StaffService
from src.workflow.tasks.tasks import TaskLedger, TaskRunner
from tests.unit.test_member_agents import _join, home  # noqa: F401 - pytest fixture

OFFLINE = next(role for role in routine_roles() if role["id"] == "routine-offline")["grants"]


@pytest.fixture
def unattended(home, monkeypatch):
    seen = SimpleNamespace(agents=[], modes=[], full=0)

    def builder(root, ask, agent_id):
        seen.full += 1
        raise AssertionError("例行工作用到了完整能力的装配")
    set_full_toolset(builder)

    from src.agents.agent_loop import MainAgent

    async def run_turn(self, prompt, mode="build", **kwargs):
        seen.agents.append(self)
        seen.modes.append(mode)
        return "做完了"
    monkeypatch.setattr(MainAgent, "run_turn", run_turn)
    monkeypatch.setattr("src.llm.background.background_client", lambda: SimpleNamespace(close=_closed))
    service = StaffService(home["root"], TaskRunner(home["root"], None, max_concurrent=2),
                           execute=lambda *args: execute_staff(home["root"], *args))
    service.execute = partial(execute_staff, home["root"], approval_service=service)
    monkeypatch.setattr("src.scheduling.staff_runner.get_staff_service", lambda root: service)
    made = home["owner"].post(f"{home['base']}/agents", json={"name": "全能", "handle": "helper"})
    assert made.status_code == 200, made.text
    yield SimpleNamespace(**{**home, "service": service, "full": made.json()["id"]}, seen=seen)
    set_full_toolset(None)


async def _closed():
    return None


def _propose(case):
    made = case.owner.post(f"{case.base}/routines", json={
        "name": "晨报", "agentId": case.full, "prompt": "整理昨天的动态",
        "trigger": {"type": "schedule", "phrase": "每天早上9点", "timezone": "Asia/Shanghai"}})
    assert made.status_code == 200, made.text
    [proposal] = case.owner.get(f"{case.base}/routines/proposals/review").json()["proposals"]
    return made.json()["id"], proposal


def _approved(case):
    routine, proposal = _propose(case)
    decided = case.owner.post(f"{case.base}/routines/proposals/{proposal['id']}/decision",
                              json={"approve": True, **proposal["proof"]})
    assert decided.status_code == 200, decided.text
    return routine


async def _run(case, routine):
    [task_id] = Routines(case.root).dispatch(case.service, datetime.now().astimezone(), only=routine, manual=True)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        task = TaskLedger(case.root).load(task_id)
        if task.status not in {"queued", "running"}:
            return task
        await asyncio.sleep(0.02)
    raise AssertionError("等不到")


def test_the_owner_approves_what_will_really_run(unattended):
    _, proposal = _propose(unattended)
    assert proposal["operations"] == ["read"] and proposal["tools"] == OFFLINE["tools"]


async def test_a_routine_runs_read_only_offline_and_with_nobody_to_ask(unattended):
    task = await _run(unattended, _approved(unattended))
    assert task.status == "done", task.error
    [agent] = unattended.seen.agents
    assert unattended.seen.full == 0 and unattended.seen.modes == ["plan"]
    assert set(agent.tools) - {"update_plan", "request_build"} == set(OFFLINE["tools"])
    assert agent._capabilities.profile == "unattended"
    for name in OFFLINE["tools"]:
        tool = agent.tools[name]
        assert tool.operation_kind == "read" and not set(tool.possible_operations) - {"read"}, name
        assert await tool.confirm("要写点东西", "write") is False, name          # 没有人可问：一律拒


async def test_forged_tool_calls_do_not_reach_anything(unattended):
    await _run(unattended, _approved(unattended))
    [agent] = unattended.seen.agents
    for name in ("web_fetch", "web_search", "write_file", "shell", "read_file", "mail_search", "routine_propose", "ask_person"):
        assert name not in agent.tools


async def test_an_approval_card_cannot_be_raised_from_a_routine_of_a_full_agent(unattended):
    task = await _run(unattended, _approved(unattended))
    member, role = unattended.service.execution_identity(task)
    call = {"tool": "write_file", "kind": "write", "message": "写文件", "args": {}}
    with pytest.raises(ValueError, match="无人值守"):
        await unattended.service.actions.request(task, role, member, call, None)


async def test_the_same_agent_keeps_full_abilities_in_a_private_chat(unattended):
    task = unattended.service.assign("sid-owner", {"member": "helper", "prompt": "整理一下", "delivery": "notify"})
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and TaskLedger(unattended.root).load(task.id).status in {"queued", "running"}:
        await asyncio.sleep(0.02)
    assert unattended.seen.full == 1


def test_a_member_started_routine_gets_none_of_the_owners_sources(unattended):
    from src.workflow.staff.initiators import role_for_member
    from src.workflow.staff.registry import StaffRegistry
    from src.workflow.staff.roles import unattended_role
    registry = StaffRegistry(unattended.root)
    role = registry.role(registry.member("helper")["role"])
    turn = role_for_member(registry, unattended_role(role), "execute")
    assert turn["grants"] == {"operations": ["read"], "tools": [], "sees": [], "web": False, "project": False}


async def test_a_members_routine_really_runs_without_any_of_the_owners_sources(unattended):
    from tests.unit.test_member_agents import _member_routine
    case = vars(unattended)
    member = _join(case)
    _, _, body = _member_routine(case, member)
    body["agentId"] = unattended.full
    sent = member.post(f"{unattended.base}/routines/proposals", json=body)
    assert sent.status_code == 201, sent.text
    [proposal] = unattended.owner.get(f"{unattended.base}/routines/proposals/review").json()["proposals"]
    decided = unattended.owner.post(f"{unattended.base}/routines/proposals/{proposal['id']}/decision",
                                    json={"approve": True, **proposal["proof"]})
    assert decided.status_code == 200, decided.text
    task = await _run(unattended, body["definition"]["id"])
    assert task.status == "done", task.error
    [agent] = unattended.seen.agents
    assert not set(agent.tools) - {"update_plan", "request_build"}      # 主人的状态、资料、联网、登记表里的工具，一样都没有
    assert unattended.seen.full == 0 and agent._capabilities.profile == "unattended"
