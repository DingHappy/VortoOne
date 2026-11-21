"""发起人的权限判定，以及回合与确认门对发起人的处理（ADR-019）。"""

import asyncio

import pytest

from src.agents import principal as P
from src.agents.gate import ALLOW, ASK, DENY, decide

OWNER = P.Principal(P.OWNER, "owner-1", "home")
ANN = P.Principal(P.MEMBER, "ann", "home")
ROLE = P.Grants.of({"operations": ["read", "write", "execute", "deliver"], "tools": ["grep", "edit_file"],
                    "sees": ["plugin:mail"], "web": True, "project": True})
READER = P.Grants.of({"operations": ["read"], "tools": ["grep"], "sees": [], "web": True, "project": False})


def test_a_turn_nobody_declared_is_a_system_turn():
    assert P.current_principal() == P.NOBODY and P.NOBODY.kind == P.SYSTEM
    assert not P.NOBODY.is_owner and not P.NOBODY.is_member


def test_people_must_be_named_and_kinds_are_closed():
    for bad in (lambda: P.Principal(P.OWNER), lambda: P.Principal(P.MEMBER, ""), lambda: P.Principal("admin", "x")):
        with pytest.raises(ValueError):
            bad()
    with pytest.raises(TypeError):
        P.set_principal("owner")


async def test_the_principal_stays_with_its_own_turn():
    seen = {}

    async def turn(name, who):
        token = P.set_principal(who)
        await asyncio.sleep(0)
        seen[name] = P.current_principal()
        P.reset_principal(token)

    await asyncio.gather(turn("a", OWNER), turn("b", ANN))
    assert seen == {"a": OWNER, "b": ANN} and P.current_principal() == P.NOBODY


def test_the_owner_gets_the_roles_grants_unchanged():
    assert P.effective_grants(OWNER, ROLE) is ROLE
    assert P.effective_grants(OWNER, ROLE, READER) is ROLE
    assert P.effective_grants(P.NOBODY, ROLE) is ROLE


def test_a_member_is_capped_by_what_they_hold_themselves():
    got = P.effective_grants(ANN, ROLE, READER)
    assert got == P.Grants(frozenset({"read"}), frozenset({"grep"}), frozenset(), True, False)
    assert P.effective_grants(ANN, ROLE) == P.Grants()            # 没给出他的权限：什么都没有
    assert P.effective_grants(ANN, READER, ROLE) == got           # 角色没有的，人有也没用


def test_acting_for_members_uses_the_roles_grants_only():
    assert P.effective_grants(ANN, ROLE, READER, acts_for_members=True) is ROLE
    assert P.effective_grants(ANN, READER, ROLE, acts_for_members=True) is READER   # 不会超出角色


def test_grants_read_from_a_partial_record_only_get_stricter():
    assert P.Grants.of(None) == P.Grants() and P.Grants.of({"web": "yes", "project": 1}) == P.Grants()


def test_the_personal_space_is_closed_to_members():
    assert P.may_address_main_assistant(OWNER) and P.may_address_main_assistant(P.NOBODY)
    assert not P.may_address_main_assistant(ANN)
    assert P.may_write_owner_memory(OWNER) and not P.may_write_owner_memory(ANN)


def test_the_owners_own_pre_authorization_is_not_lent_to_members():
    for kind in ("read", "write", "execute", "deliver", "irreversible", "interact"):
        assert P.pre_authorized_for(OWNER, kind, owner_pre_authorized=True) is True
        assert P.pre_authorized_for(OWNER, kind, owner_pre_authorized=False) is False
        assert P.pre_authorized_for(ANN, kind, owner_pre_authorized=True) is (kind == "read")
    assert P.pre_authorized_for(OWNER, "write", owner_pre_authorized=False, member_allowed=["write"]) is False


def test_member_allowance_covers_write_and_execute_only_and_dies_with_taint():
    allowed = ["write", "execute", "deliver", "irreversible", "interact", "read"]
    got = {kind: P.pre_authorized_for(ANN, kind, owner_pre_authorized=False, member_allowed=allowed)
           for kind in allowed}
    assert got == {"write": True, "execute": True, "deliver": False, "irreversible": False, "interact": False,
                   "read": True}
    assert P.pre_authorized_for(ANN, "read", owner_pre_authorized=False, tainted=True) is False
    assert P.pre_authorized_for(ANN, "own_data", owner_pre_authorized=True, member_allowed=["own_data"]) is False
    assert P.pre_authorized_for(ANN, "internal", owner_pre_authorized=True) is True      # 沿用现有判定
    assert P.pre_authorized_for(ANN, "internal", owner_pre_authorized=False) is False
    assert P.pre_authorized_for(ANN, "execute", owner_pre_authorized=False, member_allowed=["write"]) is False
    assert P.pre_authorized_for(ANN, "write", owner_pre_authorized=False, member_allowed=allowed,
                                tainted=True) is False


def test_with_the_gate_a_member_write_asks_unless_allowed_and_is_denied_when_nobody_can_be_asked():
    def outcome(kind, allowed=(), tainted=False, reachable=True):
        ok = P.pre_authorized_for(ANN, kind, owner_pre_authorized=True, member_allowed=allowed, tainted=tainted)
        return decide(tainted=tainted, pre_authorized=ok, can_ask_human=reachable)

    assert outcome("write") == ASK
    assert outcome("write", reachable=False) == DENY            # 该问的人不在：拒，不退化成问发起人
    assert outcome("write", allowed=["write"]) == ALLOW
    assert outcome("write", allowed=["write"], tainted=True) == ASK
    assert outcome("write", allowed=["write"], tainted=True, reachable=False) == DENY
    assert outcome("deliver", allowed=["deliver"]) == ASK


def test_a_member_cannot_approve_without_holding_approval():
    bo = P.Principal(P.MEMBER, "bo", "home")
    assert P.may_approve(OWNER, ANN) and P.may_approve(OWNER, OWNER)
    assert not P.may_approve(ANN, ANN) and not P.may_approve(bo, ANN)
    assert P.may_approve(bo, ANN, holds_approval=True) and P.may_approve(ANN, ANN, holds_approval=True)
    assert not P.may_approve(P.NOBODY, ANN, holds_approval=True)


# ── 接入：确认门、记忆写入、回合开始 ─────────────────────────────────────────

async def _gate_outcome(principal, kind, *, allowed=(), auto_approve=True, answer=None):
    from src.agents.gate import make_confirm_gate
    asked = []

    async def ask(message, *args, **kwargs):
        asked.append(message)
        return bool(answer)

    gate = make_confirm_gate(ask if answer is not None else None, auto_approve=auto_approve,
                             can_ask_human=answer is not None)
    token = P.set_principal(principal, member_allowed=allowed)
    try:
        return await gate("改一个文件", kind=kind), len(asked)
    finally:
        P.reset_principal(token)


async def test_the_gate_treats_owner_and_undeclared_turns_as_before():
    for who in (OWNER, P.NOBODY):
        assert await _gate_outcome(who, "write") == (True, 0)                  # --yes 照旧免问
        assert await _gate_outcome(who, "deliver", answer=True) == (True, 1)   # 对外发送照旧要问
        assert await _gate_outcome(who, "write", auto_approve=False) == (False, 0)


async def test_the_gate_does_not_lend_the_owners_standing_approval_to_a_member():
    assert await _gate_outcome(ANN, "write") == (False, 0)                     # 问不到人：拒
    assert await _gate_outcome(ANN, "write", answer=True) == (True, 1)         # 问得到：问
    assert await _gate_outcome(ANN, "write", answer=False) == (False, 1)
    assert await _gate_outcome(ANN, "write", allowed=["write"]) == (True, 0)   # 所有者对该角色放行了写
    assert await _gate_outcome(ANN, "execute", allowed=["write"], answer=True) == (True, 1)
    assert await _gate_outcome(ANN, "deliver", allowed=["deliver"], answer=True) == (True, 1)
    assert await _gate_outcome(ANN, "read") == (True, 0)


async def test_member_allowance_is_void_in_a_tainted_turn():
    from src.agents.taint import mark_tainted, reset_taint
    reset_taint()
    mark_tainted()
    try:
        assert await _gate_outcome(ANN, "write", allowed=["write"]) == (False, 0)
        assert await _gate_outcome(ANN, "write", allowed=["write"], answer=True) == (True, 1)
    finally:
        reset_taint()


@pytest.mark.parametrize("scope_kind", ["task_workspace", "protective"])
async def test_member_cannot_use_host_tool_exemptions(scope_kind, tmp_path):
    from src.agents.capabilities import SessionCapabilities
    from src.agents.gate import make_confirm_gate
    from src.agents.tool import ApprovedTaskScope, PreparedToolCall, Tool
    from src.agents.tool_runtime import ToolExecutionContext, ToolRuntime

    asked, ran = [], []

    async def human(_message, kind):
        asked.append(kind)
        return False

    async def action(_args=None):
        ran.append(P.current_principal())
        return "done"

    scope = ApprovedTaskScope("approved-task", str(tmp_path.resolve()))
    if scope_kind == "task_workspace":
        tool = Tool("write_file", "", {}, action, operation_kind="write", task_scope=scope,
                    execution_root=str(tmp_path.resolve()))
        args = {"path": "result.txt"}
    else:
        async def prepare(_args):
            return PreparedToolCall(action, "停用作业", "write", protective=True)

        tool = Tool.prepared("cron_toggle", "", {}, prepare, operation_kind="write", protective=True)
        args = {"name": "fixture", "enabled": False}

    runtime = ToolRuntime(lambda: {tool.name: tool}, ToolExecutionContext(
        confirm=make_confirm_gate(human, can_ask_human=True),
        capabilities=SessionCapabilities.for_profile("local", str(tmp_path)),
        task_scopes=(scope,),
    ))
    for actor, expected in ((OWNER, "succeeded"), (ANN, "blocked")):
        token = P.set_principal(actor)
        try:
            assert (await runtime.execute(tool.name, args, "build")).status == expected
        finally:
            P.reset_principal(token)

    assert ran == [OWNER]
    assert asked == ["write"]


async def test_member_cannot_consume_or_acquire_a_turn_approval():
    from src.agents.gate import make_confirm_gate
    from src.agents.scoped_approval import current_offer, turn_approval_scope

    offers = []

    async def human(_message):
        offers.append(current_offer())
        return False

    gate = make_confirm_gate(human, can_ask_human=True, capability_profile="local", enable_turn_scope=True)
    owner = P.set_principal(OWNER)
    try:
        with turn_approval_scope() as scope:
            assert scope.grant("draft")
            remaining = scope.grants["draft"]
            member = P.set_principal(ANN)
            try:
                assert not await gate("保存草稿", "draft")
                assert scope.grants["draft"] == remaining
            finally:
                P.reset_principal(member)
            assert await gate("保存草稿", "draft")
            assert scope.grants["draft"] == remaining - 1
    finally:
        P.reset_principal(owner)
    assert offers == [None]


def test_a_members_words_do_not_enter_the_owners_memory():
    from src.memory.write_policy import MemoryWritePolicy, MemoryWriteRequest
    request = MemoryWriteRequest(content="主人周三不开会", source="chat", tainted=False, write_method="tool")
    assert MemoryWritePolicy().evaluate(request).outcome == "durable"
    token = P.set_principal(ANN)
    try:
        decision = MemoryWritePolicy().evaluate(request)
    finally:
        P.reset_principal(token)
    assert (decision.outcome, decision.reasons) == ("reject", ("member_initiated",))
    token = P.set_principal(OWNER)
    try:
        assert MemoryWritePolicy().evaluate(request).outcome == "durable"
    finally:
        P.reset_principal(token)


def test_desktop_and_cli_sessions_declare_the_owner_and_im_does_not(tmp_path, monkeypatch):
    from src.gateway.agent_session import build_session
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    for kind, expected in (("web", P.OWNER), ("cli", P.OWNER), ("im", None)):
        agent = build_session(str(tmp_path), kind=kind)
        assert (agent.principal.kind if agent.principal else None) == expected, kind


async def test_each_turn_runs_as_the_agents_declared_principal_and_does_not_inherit_the_last_one():
    from src.agents.main_agent import MainAgent
    seen = []

    class LLM:
        async def chat(self, messages, **kwargs):
            seen.append((P.current_principal(), P.current_member_allowance()))
            return {"content": "done"}

    agent = MainAgent([], llm=LLM())
    token = P.set_principal(OWNER)
    try:
        await agent.run_turn("hi", mode="plan")
        assert P.current_principal() == OWNER
        agent.principal, agent.member_allowed = ANN, ("write",)
        await agent.run_turn("hi", mode="plan")
        assert (P.current_principal(), P.current_member_allowance()) == (OWNER, frozenset())
    finally:
        P.reset_principal(token)
    assert seen == [(P.NOBODY, frozenset()), (ANN, frozenset({"write"}))]


@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_turn_restores_caller_authority_when_interrupted(monkeypatch, failure):
    from src.agents.main_agent import MainAgent

    async def fail(*_args, **_kwargs):
        assert (P.current_principal(), P.current_member_allowance()) == (OWNER, frozenset())
        raise failure("interrupted")

    agent = MainAgent([], llm=object())
    agent.principal = OWNER
    monkeypatch.setattr(agent, "_run_turn_body", fail)
    parent = P.set_principal(ANN, member_allowed=("write",))
    try:
        with pytest.raises(failure, match="interrupted"):
            await agent.run_turn("hi", mode="plan")
        assert (P.current_principal(), P.current_member_allowance()) == (ANN, frozenset({"write"}))
    finally:
        P.reset_principal(parent)
