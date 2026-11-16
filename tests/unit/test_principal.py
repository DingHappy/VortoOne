"""发起人与按发起人封顶的权限判定（ADR-019）。本模块还没接到任何入口，这里只钉判定本身。"""

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
        assert P.pre_authorized_for(ANN, kind, owner_pre_authorized=True) is False
    assert P.pre_authorized_for(OWNER, "write", owner_pre_authorized=False, member_allowed=["write"]) is False


def test_member_allowance_covers_write_and_execute_only_and_dies_with_taint():
    allowed = ["write", "execute", "deliver", "irreversible", "interact", "read"]
    got = {kind: P.pre_authorized_for(ANN, kind, owner_pre_authorized=False, member_allowed=allowed)
           for kind in allowed}
    assert got == {"write": True, "execute": True, "deliver": False, "irreversible": False, "interact": False,
                   "read": False}
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
