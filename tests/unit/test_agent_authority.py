"""完整能力的 agent 只拿「同一套工具和规则」，不拿主人在主要聊天那一回合的授权（#1027 的回归）。

- 主人的电脑 / 浏览器授权存在回合上下文里；后台任务会复制上下文，但不能继承它：
  任务活得比那一回合久，而且它的审批卡要的主人证明比主要聊天里的文件确认弱。
- ADR-021 写明不给的派活、排期，一样都不能经别的名字漏进来。
- 主助理有的约束 agent 也要有：受信任的项目 Hook、工具与确认的审计、Desktop 监管。
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.workflow.staff.registry import StaffRegistry
from src.workflow.staff.roles import FULL_GRANTS
from src.workflow.tasks.tasks import TaskRunner


@pytest.fixture
def full(tmp_path, monkeypatch):
    from src.agents.staff import set_full_toolset
    from src.web.agent_toolset import main_assistant_toolset
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("src.gateway.workspace_scope.current_workspace_scope", lambda: "general")
    monkeypatch.setenv("VORTOONE_API_TOKEN", "test-token")
    monkeypatch.setenv("VORTOONE_CRON", "1")
    monkeypatch.delenv("VORTOONE_DESKTOP_SIDECAR", raising=False)
    registry = StaffRegistry(str(tmp_path))
    role = registry.register_role({"id": "agent-full", "title": "助手", "brief": "你是助手。",
                                   "grants": deepcopy(FULL_GRANTS), "engine": ["vortocode"],
                                   "max_steps": 8, "timeout_seconds": 60})
    member = registry.register_member({"id": "helper", "name": "助手", "role": role["id"], "engine": "vortocode"})
    set_full_toolset(main_assistant_toolset)

    def build():
        from src.agents.staff import build_staff_member

        async def confirm(call):
            return False
        return build_staff_member(str(tmp_path), role, member, check=lambda: None, llm=object(),
                                  confirm_action=confirm)
    yield SimpleNamespace(root=str(tmp_path), member=member, build=build)
    set_full_toolset(None)


async def test_a_task_does_not_inherit_the_owners_turn_authority(full, monkeypatch):
    from src.gateway.computer_file_authority import OwnerTurn, current_file_confirmation, current_owner_turn
    from src.scheduling.staff_runner import set_staff_service
    seen = []

    async def execute(*args, **kwargs):
        seen.append((current_owner_turn.get(), current_file_confirmation.get()))
        return "ok"
    monkeypatch.setattr("src.agents.staff.execute_staff", execute)
    runner = TaskRunner(full.root, None, max_concurrent=1)
    service = set_staff_service(full.root, runner)
    owner = OwnerTurn(full.root, "sid-owner", "rid", ("home", "owner", "epoch"))
    turn, confirmation = current_owner_turn.set(owner), current_file_confirmation.set(owner)
    try:
        task = service.assign("sid-owner", {"member": full.member["id"], "prompt": "整理文件"})
    finally:
        current_owner_turn.reset(turn)
        current_file_confirmation.reset(confirmation)
    try:
        await runner.join(task.id)
        assert seen == [(None, None)]
    finally:
        set_staff_service(full.root)
        await runner.shutdown()


def test_tools_bound_to_the_owners_turn_are_not_handed_to_agents(full):
    assert not [name for name in full.build().tools if name.startswith("connected_computer_")]


def test_nothing_that_dispatches_or_schedules_leaks_into_an_agent():
    from src.agents.staff import set_full_toolset, _full_tools
    from src.agents.tool import Tool

    async def nothing(_args):
        return ""
    refused = ["staff_assign", "team_propose", "task_followup", "task_review", "codex_supervision", "codex_handover",
               "cron_add", "cron_toggle", "cron_set_web", "cron_set_prompt", "cron_run"]
    kept = ["web_search", "cron_list", "task_status", "codex_status"]
    set_full_toolset(lambda root, ask, agent: ([Tool(name, name, {}, nothing, operation_kind="write")
                                                 for name in refused + kept], "ask", "external", {}))
    try:
        assert {tool.name for tool in _full_tools("/unused", None, "helper")[0]} == set(kept)
    finally:
        set_full_toolset(None)


def test_agents_run_under_the_projects_trusted_hooks(full, monkeypatch):
    hooks = SimpleNamespace()
    monkeypatch.setattr("src.gateway.agent_session.load_project_hook_system", lambda root, **_kw: hooks)
    assert full.build()._hook_system is hooks


async def test_agents_tools_and_decisions_are_audited_under_the_agent(full, monkeypatch):
    from src.gateway.audit import audit_snapshot
    from src.web import app_audit
    monkeypatch.setattr("src.gateway.trust_setting.get_trust_level", lambda root: "reads")     # 自动放行也要留痕
    agent = full.build()
    agent._on_tool("web_search", {"query": "x"}, "结果")
    await agent.confirm_gate("读一下", "read", force_prompt=False)
    categories = {entry["category"] for entry in audit_snapshot(full.root, 50)["entries"]}
    assert categories >= {"tool", "decision"}
    page = app_audit.page(full.root, owner={"id": "o", "name": "主人"}, agent={"id": "main", "name": "主助理"},
                          agents={"helper": {"id": "helper-id", "name": "助手"}})
    row = next(row for row in page["rows"] if row["action"] == "tool.call")
    assert row["actor"] == {"type": "agent", "id": "helper-id", "name": "助手"}


def test_agents_on_desktop_are_supervised_like_the_main_assistant(full, monkeypatch):
    monkeypatch.setenv("VORTOONE_DESKTOP_SIDECAR", "1")
    agent = full.build()
    assert agent._supervised is True and agent.confirm_gate.supervised is True
