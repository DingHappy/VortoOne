"""成员发起的 agent 任务：记下发起人，执行时按他的级别收窄岗位，不碰主人的私人资料。"""
import asyncio

import pytest

from src.agents.principal import MEMBER, OWNER, Grants, Principal, member_initiator_grants
from src.agents.taint import reset_taint
from src.workflow.members.registry import MemberRegistry
from src.workflow.staff import initiators
from src.workflow.staff.registry import StaffRegistry
from src.workflow.staff.service import StaffService
from src.workflow.tasks.tasks import TaskRunner

OWNER_P = Principal(OWNER, "owner")


@pytest.fixture(autouse=True)
def clean_taint():
    reset_taint()
    yield
    reset_taint()


def _tools(monkeypatch, invoked=None):
    """三个离线工具：读、写、执行各一个。"""
    import src.workflow.staff.registry as module
    from src.agents.tool import Tool
    catalog = module.tool_catalog

    async def run(_args):
        if invoked is not None:
            invoked.append(True)
        return "ok"
    extra = {name: Tool(name, "离线夹具", {}, run, operation_kind=kind)
             for name, kind in (("fixture_read", "read"), ("fixture_write", "write"), ("fixture_execute", "execute"))}
    monkeypatch.setattr(module, "tool_catalog", lambda root: {**catalog(root), **extra})


def _role(registry, role_id, grants):
    return registry.register_role({"id": role_id, "title": role_id, "brief": "只做所派任务", "grants": grants,
                                   "engine": ["vortocode"], "max_steps": 12, "timeout_seconds": 600})


def _worker(registry):
    return _role(registry, "worker", {"operations": ["read", "write", "execute"],
                                      "tools": ["fixture_read", "fixture_write", "fixture_execute"],
                                      "sees": [], "web": False, "project": False})


def _join(root, **level):
    members = MemberRegistry(str(root))
    _, token = members.invite(OWNER_P)
    who = members.accept_invitation(token, "小王")
    for agent, value in level.items():
        members.set_act_level_on(OWNER_P, agent, who, value)
    return who


# ── 发起人自己带来的权限 ──────────────────────────────────────────────────────

def test_a_member_brings_no_private_sources_and_no_project():
    role = Grants.of({"operations": ["read", "own_data"], "tools": ["recall_memory"], "sees": ["memory", "goals"],
                      "web": False, "project": True})
    mine = member_initiator_grants(role, ["read", "execute"])
    assert mine.operations == {"read"}                 # 角色没登记的操作，级别再高也没有
    assert mine.sees == frozenset() and mine.project is False


# ── 岗位按级别收窄 ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("level, operations, tools", [
    ("read", ["read"], ["fixture_read"]),
    ("write", ["read", "write"], ["fixture_read", "fixture_write"]),
    ("execute", ["read", "write", "execute"], ["fixture_read", "fixture_write", "fixture_execute"]),
])
def test_the_level_decides_which_tools_remain(tmp_path, monkeypatch, level, operations, tools):
    _tools(monkeypatch)
    registry = StaffRegistry(str(tmp_path))
    role = _worker(registry)
    narrowed = initiators.role_for_member(registry, role, level)
    assert narrowed["grants"]["operations"] == operations and narrowed["grants"]["tools"] == tools
    assert role["grants"]["tools"] == ["fixture_read", "fixture_write", "fixture_execute"]      # 登记本身不变


@pytest.mark.parametrize("level", ["none", "everything", ""])
def test_no_level_means_no_work(tmp_path, monkeypatch, level):
    _tools(monkeypatch)
    registry = StaffRegistry(str(tmp_path))
    with pytest.raises(ValueError):
        initiators.role_for_member(registry, _worker(registry), level)


def test_tools_over_the_owners_private_sources_are_dropped(tmp_path):
    registry = StaffRegistry(str(tmp_path))
    role = _role(registry, "personal", {"operations": ["read", "own_data"],
                                        "tools": ["recall_memory", "search_conversations", "personal_goals"],
                                        "sees": ["memory", "conversation", "goals"], "web": False, "project": False})
    narrowed = initiators.role_for_member(registry, role, "execute")
    assert narrowed["grants"]["tools"] == [] and narrowed["grants"]["sees"] == []
    assert narrowed["grants"]["project"] is False
    delegated = initiators.role_for_member(registry, role, "read", acts_for_members=True)
    assert delegated["grants"]["tools"] == [] and delegated["grants"]["sees"] == []


def test_web_tools_stay_for_a_web_role(tmp_path):
    registry = StaffRegistry(str(tmp_path))
    role = _role(registry, "reader", {"operations": ["read"], "tools": ["web_search", "web_fetch"], "sees": [],
                                      "web": True, "project": False})
    assert initiators.role_for_member(registry, role, "read")["grants"]["tools"] == ["web_search", "web_fetch"]


@pytest.mark.parametrize("change", [{"id": "routine-offline"}, {"project_binding": {"project": "x"}}])
def test_routine_and_project_roles_are_not_for_members(tmp_path, monkeypatch, change):
    _tools(monkeypatch)
    registry = StaffRegistry(str(tmp_path))
    with pytest.raises(ValueError):
        initiators.role_for_member(registry, {**_worker(registry), **change}, "execute")


# ── 发起人记录 ────────────────────────────────────────────────────────────────

class _Task:
    def __init__(self, dispatch):
        self.collaboration = {"dispatch": dispatch}


def test_reading_the_initiator_back():
    assert initiators.initiator_of(_Task({})) is None
    who = initiators.initiator_of(_Task({"initiator": initiators.record("m_1")}))
    assert who.is_member and who.id == "m_1"


@pytest.mark.parametrize("bad", [{}, {"kind": "owner", "id": "x"}, {"kind": MEMBER, "id": ""}, {"kind": MEMBER},
                                 "m_1", {"kind": MEMBER, "id": 7}])
def test_a_broken_initiator_record_is_never_read_as_the_owner(bad):
    with pytest.raises(ValueError):
        initiators.initiator_of(_Task({"initiator": bad}))


def test_level_comes_from_the_member_registry(tmp_path):
    who = _join(tmp_path, **{"staff-a": "write"})
    assert initiators.level_on(str(tmp_path), "staff-a", Principal(MEMBER, who)) == "write"
    assert initiators.level_on(str(tmp_path), "staff-b", Principal(MEMBER, who)) == "read"       # 身份组默认
    assert initiators.level_on(str(tmp_path), "staff-a", Principal(MEMBER, "m_gone")) == "none"
    (MemberRegistry(str(tmp_path)).directory / "space.json").write_text("{", encoding="utf-8")
    assert initiators.level_on(str(tmp_path), "staff-a", Principal(MEMBER, who)) == "none"       # 读不出来


# ── 走一遍执行 ────────────────────────────────────────────────────────────────

def _service(tmp_path, monkeypatch, seen, *, on_turn=None):
    """真实的登记与派活，执行走真实的 execute_staff；只把「装配出来的 agent」换成记录用的替身。"""
    import src.agents.staff as staff_module
    _tools(monkeypatch)
    registry = StaffRegistry(str(tmp_path))
    role = _worker(registry)
    member = registry.register_member({"id": "staff-a", "name": "办事的", "role": role["id"], "engine": "vortocode"})

    class _Agent:
        execution_report = None

        def __init__(self, check):
            self.check = check

        async def run_turn(self, prompt, mode="build", execution_ref=None):
            seen["mode"] = mode
            if on_turn is not None:
                await on_turn(self.check)
            return "办完了"

    def build(root, turn_role, person, **options):
        seen.update(tools=list(turn_role["grants"]["tools"]), principal=options.get("principal"),
                    preapproved=options.get("member_allowed", ()))
        return _Agent(options['check'])
    monkeypatch.setattr(staff_module, "build_staff_member", build)
    pool = TaskRunner(str(tmp_path), None, max_concurrent=1)

    async def execute(task, work, role, person, check):
        return await staff_module.execute_staff(str(tmp_path), task, work, role, person, check)
    return StaffService(str(tmp_path), pool, execute=execute), member


async def _finished(service, task, timeout=3.0):
    work = service.collaboration(task.owner_session)
    for _ in range(int(timeout / 0.02)):
        current = work.get(task.id)
        if current.status not in {"queued", "running"}:
            return current
        await asyncio.sleep(0.02)
    raise AssertionError("任务没有结束")


async def test_a_member_task_runs_narrowed_and_declares_who_started_it(tmp_path, monkeypatch):
    seen = {}
    service, _member = _service(tmp_path, monkeypatch, seen)
    who = _join(tmp_path, **{"staff-a": "write"})
    task = service.assign("owner-session", {"member": "staff-a", "prompt": "整理一下"}, initiator=who)
    assert task.collaboration["dispatch"]["initiator"] == {"kind": MEMBER, "id": who}
    done = await _finished(service, task)
    assert done.status == "done", done.error
    assert seen["tools"] == ["fixture_read", "fixture_write"]
    assert seen["principal"] == Principal(MEMBER, who) and seen["mode"] == "build"


async def test_the_owners_own_task_is_untouched(tmp_path, monkeypatch):
    seen = {}
    service, _member = _service(tmp_path, monkeypatch, seen)
    task = service.assign("owner-session", {"member": "staff-a", "prompt": "整理一下"})
    assert "initiator" not in task.collaboration["dispatch"]
    assert (await _finished(service, task)).status == "done"
    assert seen["tools"] == ["fixture_read", "fixture_write", "fixture_execute"] and seen["principal"] is None


async def test_a_read_only_member_gets_a_read_only_turn(tmp_path, monkeypatch):
    seen = {}
    service, _member = _service(tmp_path, monkeypatch, seen)
    who = _join(tmp_path)                               # 身份组默认：只读
    task = service.assign("owner-session", {"member": "staff-a", "prompt": "看一下"}, initiator=who)
    assert (await _finished(service, task)).status == "done"
    assert seen["tools"] == ["fixture_read"] and seen["mode"] == "plan"


@pytest.mark.parametrize("update", ["disable-delegation", "revoke-approval"])
async def test_policy_changes_stop_an_active_member_turn(tmp_path, monkeypatch, update):
    from src.web.app_members import agent_member_policy

    seen = {}
    root = str(tmp_path)
    registry = StaffRegistry(root)

    async def change_policy(check):
        check()
        updates = {"actForMembers": False} if update == "disable-delegation" else {"autoApprove": []}
        agent_member_policy(root, OWNER_P, "staff-a", {"roleVersion": role_version, **updates})
        check()

    service, _ = _service(tmp_path, monkeypatch, seen, on_turn=change_policy)
    role_version = initiators.role_version(registry.role("worker"))
    who = _join(tmp_path, **{"staff-a": "read" if update == "disable-delegation" else "write"})
    policy = agent_member_policy(root, OWNER_P, "staff-a", {
        "roleVersion": role_version, "actForMembers": True, "autoApprove": ["write", "execute"],
    })
    assert policy["availableAutoApprove"] == ["execute", "write"]
    task = service.assign("owner-session", {"member": "staff-a", "prompt": "整理一下"}, initiator=who)
    done = await _finished(service, task)
    assert done.status == "failed" and "办事权限已变化" in done.error
    assert seen["tools"] == ["fixture_read", "fixture_write", "fixture_execute"]
    assert seen["preapproved"] == (() if update == "disable-delegation" else ("write",))


def test_member_cannot_enable_delegation_above_own_level_and_policy_is_bound_to_role(tmp_path, monkeypatch):
    from src.web.app_members import agent_member_policy
    from src.workflow.members.registry import Refused

    root = str(tmp_path)
    service, member = _service(tmp_path, monkeypatch, {})
    role = service.registry.role("worker")
    who = _join(tmp_path, **{"staff-a": "write"})
    actor = Principal(MEMBER, who)
    registry = MemberRegistry(root)
    post = registry.create_group(OWNER_P, "代办", items=["roles.act_for_members"])
    registry.set_member_groups(OWNER_P, who, [post])
    payload = {"roleVersion": initiators.role_version(role), "actForMembers": True}
    with pytest.raises(Refused):
        agent_member_policy(root, actor, "staff-a", payload)
    registry.set_act_level_on(OWNER_P, "staff-a", who, "execute")
    assert agent_member_policy(root, actor, "staff-a", payload)["actForMembers"]
    assert initiators.member_authority(root, role, member, actor)[1] is True
    assert initiators.member_authority(root, {**role, "revision": role["revision"] + 1}, member, actor)[1] is False
    registry.set_act_level_on(OWNER_P, "staff-a", who, "none")
    assert initiators.member_authority(root, role, member, actor)[0] == "none"
    with pytest.raises(ValueError):
        initiators.role_for_member(service.registry, role, "none", acts_for_members=True)


async def test_a_scheduled_member_turn_uses_the_current_creator_level(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    from src.workflow.staff.approvals import StaffApprovals
    from src.workflow.staff.routines import Routines
    from tests.unit.staff_owner_helpers import owner_decide

    seen = {}
    service, _ = _service(tmp_path, monkeypatch, seen)
    who = _join(tmp_path, **{"staff-a": "write"})
    root = str(tmp_path)
    registry = MemberRegistry(root)
    group = registry.create_group(OWNER_P, "排期", items=["roles.manage_routines"])
    registry.set_member_groups(OWNER_P, who, [group])
    approvals = StaffApprovals(root)
    data = {"id": "routine-report", "name": "例行核对", "member": "staff-a", "prompt": "核对公开资料",
            "schedule": "0 9 * * *", "timezone": "UTC", "delivery": "notify"}
    proposal = approvals.propose("owner-session", "routine", data, "例行核对", initiator=who)
    owner_decide(approvals, proposal["id"], True, proposal["digest"])
    from src.web.app_members import agent_member_policy
    role_version = initiators.role_version(service.registry.role("worker"))
    agent_member_policy(root, OWNER_P, "staff-a", {
        "roleVersion": role_version, "actForMembers": True, "autoApprove": ["write"],
    })
    registry.set_act_level_on(OWNER_P, "staff-a", who, "read")
    routines = Routines(root)
    due = datetime.fromisoformat(routines.get(data["id"])["state"]["cursor"]) + timedelta(days=1)
    try:
        task_id, = routines.dispatch(service, due)
        task = service.collaboration("owner-session").get(task_id)
        assert (await _finished(service, task)).status == "done"
        assert seen["principal"] == Principal(MEMBER, who)
        assert seen["tools"] == ["fixture_read"] and seen["mode"] == "plan"
        assert seen["preapproved"] == ()
    finally:
        await service.runner.shutdown()


async def test_losing_access_while_queued_stops_the_task(tmp_path, monkeypatch):
    seen = {}
    service, _member = _service(tmp_path, monkeypatch, seen)
    who = _join(tmp_path, **{"staff-a": "write"})
    MemberRegistry(str(tmp_path)).remove_member(OWNER_P, who)
    task = service.assign("owner-session", {"member": "staff-a", "prompt": "整理一下"}, initiator=who)
    done = await _finished(service, task)
    assert done.status != "done" and seen == {}        # 没有装配出任何 agent


@pytest.mark.parametrize('change', ['remove', 'downgrade', 'upgrade'])
async def test_running_member_turn_rechecks_access_without_adding_tools(tmp_path, monkeypatch, change):
    running, resume = asyncio.Event(), asyncio.Event()
    checked = []

    async def on_turn(check):
        running.set()
        await resume.wait()
        check()
        checked.append(True)

    seen = {}
    service, _ = _service(tmp_path, monkeypatch, seen, on_turn=on_turn)
    who = _join(tmp_path, **{'staff-a': 'write'})
    members = MemberRegistry(str(tmp_path))
    try:
        task = service.assign('owner-session', {'member': 'staff-a', 'prompt': '执行当前任务'}, initiator=who)
        await asyncio.wait_for(running.wait(), 2)
        if change == 'remove':
            members.remove_member(OWNER_P, who)
        else:
            members.set_act_level_on(OWNER_P, 'staff-a', who, 'read' if change == 'downgrade' else 'execute')
        resume.set()
        done = await _finished(service, task)
        assert seen['tools'] == ['fixture_read', 'fixture_write']
        if change == 'upgrade':
            assert done.status == 'done' and checked == [True]
        else:
            assert done.status != 'done' and not checked
            assert '发起人的办事权限已变化' in done.error
    finally:
        await service.runner.shutdown()


def test_an_owner_schedule_cannot_be_dispatched_as_an_unbound_member(tmp_path, monkeypatch):
    from src.workflow.staff.routines import Routines
    service, _member = _service(tmp_path, monkeypatch, {})
    item = Routines(str(tmp_path)).register({
        "id": "routine-report", "name": "例行核对", "member": "staff-a", "prompt": "每天做",
        "schedule": "0 9 * * *", "timezone": "UTC",
    })
    with pytest.raises(ValueError, match="发起人记录不一致"):
        service.assign("owner-session", {"member": "staff-a", "prompt": "每天做"}, initiator="m_1",
                       routine={"id": item["id"], "revision": item["revision"]})


# ── 真实装配：成员回合里写操作一定要问 ────────────────────────────────────────

async def test_a_member_turn_declares_the_member_with_no_standing_allowance(tmp_path, monkeypatch):
    from src.agents.principal import current_principal
    from src.agents.staff import build_staff_member
    _tools(monkeypatch)
    registry = StaffRegistry(str(tmp_path))
    role = initiators.role_for_member(registry, _worker(registry), "write")
    member = registry.register_member({"id": "staff-a", "name": "办事的", "role": "worker", "engine": "vortocode"})
    asked = []

    async def confirm(call):
        asked.append((call["kind"], current_principal()))
        return True
    who = Principal(MEMBER, "m_1")
    agent = build_staff_member(str(tmp_path), role, member, check=lambda: None, llm=object(),
                               confirm_action=confirm, principal=who)
    assert agent.principal == who and agent.member_allowed == ()
    owner_agent = build_staff_member(str(tmp_path), role, member, check=lambda: None, llm=object(),
                                     confirm_action=confirm)
    assert owner_agent.principal is None                 # 主人自己的任务：不申报，行为与原来一致


@pytest.mark.parametrize("level,external,approved", [
    ("write", False, True), ("write", True, False), ("read", False, False),
])
async def test_saved_member_preapproval_reaches_the_staff_gate(tmp_path, monkeypatch, level, external, approved):
    from src.agents.staff import build_staff_member
    from src.web.app_members import agent_member_policy

    asked, invoked = [], []
    _tools(monkeypatch, invoked)
    root = str(tmp_path)
    registry = StaffRegistry(root)
    role = _worker(registry)
    staff = registry.register_member({"id": "staff-a", "name": "办事的", "role": role["id"], "engine": "vortocode"})
    who = Principal(MEMBER, _join(tmp_path, **{"staff-a": level}))
    agent_member_policy(root, OWNER_P, "staff-a", {
        "roleVersion": initiators.role_version(role), "actForMembers": True, "autoApprove": ["write"],
    })
    current_level, delegated, allowed = initiators.member_authority(root, role, staff, who)
    turn_role = initiators.role_for_member(registry, role, current_level, acts_for_members=delegated)

    async def confirm(call):
        asked.append(call["kind"])
        return False

    class LLM:
        async def chat(self, _messages, **_kwargs):
            await agent._run_tool("fixture_write", {}, "build", lambda _: None)
            return {"content": "done"}

    agent = build_staff_member(root, turn_role, staff, check=lambda: None, llm=LLM(),
                               confirm_action=confirm, principal=who, member_allowed=allowed)
    await agent.run_turn("<vortocode_untrusted_memory>外部输入" if external else "开始", mode="build")
    assert asked == ([] if approved else ["write"])
    assert invoked == ([True] if approved else [])


def test_a_member_write_is_never_pre_authorized():
    """成员回合里没有任何放行：读直接过，写、执行、对外发送都要所有者批准；主人自己的放行不借给他。"""
    from src.agents.principal import pre_authorized_for
    who = Principal(MEMBER, "m_1")
    assert pre_authorized_for(who, "read", owner_pre_authorized=False)
    for kind in ("write", "execute", "deliver", "irreversible", "interact"):
        assert not pre_authorized_for(who, kind, owner_pre_authorized=True, member_allowed=())


# ── 提醒 ──────────────────────────────────────────────────────────────────────

def test_the_owner_is_only_pinged_when_a_member_task_needs_approval():
    from src.scheduling import task_notices

    class _Done:
        id, kind, status, prompt, result, error = "task-1", "delegation", "done", "整理一下", "办完了", ""
        collaboration = {"dispatch": {"source": "staff", "initiator": {"kind": MEMBER, "id": "m_1"}}}

    class _Waiting(_Done):
        status = "blocked"
        collaboration = {**_Done.collaboration, "approval": {"status": "pending", "payload": {"id": "act-1"}}}
    class _OwnersDone(_Done):
        collaboration = {"dispatch": {"source": "staff"}}
    assert task_notices._key(_OwnersDone) is not None    # 对照：主人自己的事做完会提醒
    assert task_notices._key(_Done) is None              # 成员的事做完不提醒
    assert task_notices._key(_Waiting) is not None
    assert "批准" in task_notices.message(_Waiting)
