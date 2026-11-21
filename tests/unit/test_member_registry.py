"""成员登记处：存取正确，且每个写操作都过内核的身份组规则。"""
from datetime import datetime, timedelta, timezone

import pytest

from src.agents.identity_groups import ADMINS, EVERYONE, may_reorder, may_remove_member, Post
from src.agents.principal import MEMBER, NOBODY, OWNER, Principal
from src.workflow.members.registry import MemberRegistry, Missing, Refused

OWNER_P = Principal(OWNER, "owner")
T0 = datetime(2026, 11, 16, tzinfo=timezone.utc)


def member(member_id):
    return Principal(MEMBER, member_id)


@pytest.fixture
def reg(tmp_path):
    return MemberRegistry(str(tmp_path))


def join(reg, *, groups=(), by=OWNER_P, name="小王"):
    _, token = reg.invite(by, groups=groups, now=T0)
    return reg.accept_invitation(token, name, now=T0)


# ── 初始状态与存盘 ────────────────────────────────────────────────────────────

def test_a_new_space_has_the_two_built_in_groups_and_nobody(reg):
    state = reg.snapshot()
    assert state["order"] == [ADMINS, EVERYONE]
    assert state["members"] == {} and state["invitations"] == {}
    assert not (reg.directory / "space.json").exists()      # 只读不落盘


def test_changes_survive_a_new_instance(reg, tmp_path):
    group = reg.create_group(OWNER_P, "财务", items=["space.view_usage"])
    who = join(reg, groups=[group])
    again = MemberRegistry(str(tmp_path))
    assert again.standing(who)["groups"] == [group]
    assert "space.view_usage" in again.standing(who)["permissions"]


def test_a_damaged_file_stops_everything_instead_of_resetting(reg):
    join(reg)
    (reg.directory / "space.json").write_text("{", encoding="utf-8")
    with pytest.raises(ValueError):
        reg.snapshot()
    with pytest.raises(ValueError):
        reg.create_group(OWNER_P, "新的")
    assert (reg.directory / "space.json").read_text(encoding="utf-8") == "{"


def test_a_file_that_breaks_the_rules_is_not_trusted(reg):
    join(reg)
    path = reg.directory / "space.json"
    path.write_text(path.read_text(encoding="utf-8").replace('"chat.speak"', '"admin"'), encoding="utf-8")
    with pytest.raises(ValueError):        # 「所有人」带上了管理员权限
        reg.snapshot()


def test_a_refused_change_writes_nothing(reg):
    who = join(reg)
    before = (reg.directory / "space.json").read_text(encoding="utf-8")
    with pytest.raises(Refused):
        reg.create_group(member(who), "我想当管理员", items=["admin"])
    assert (reg.directory / "space.json").read_text(encoding="utf-8") == before


# ── 谁能改 ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("actor", [NOBODY, member("m_not_registered")])
def test_system_turns_and_unknown_members_cannot_write(reg, actor):
    with pytest.raises(Refused):
        reg.create_group(actor, "x")
    with pytest.raises(Refused):
        reg.invite(actor)
    with pytest.raises(Refused):
        reg.reorder_groups(actor, [ADMINS, EVERYONE])


def test_a_plain_member_cannot_manage_groups_members_or_invites(reg):
    who, other = join(reg), join(reg, name="小李")
    group = reg.create_group(OWNER_P, "财务")
    for attempt in (lambda: reg.create_group(member(who), "x"),
                    lambda: reg.change_group(member(who), group, name="y"),
                    lambda: reg.delete_group(member(who), group),
                    lambda: reg.set_member_groups(member(who), other, [group]),
                    lambda: reg.remove_member(member(who), other),
                    lambda: reg.invite(member(who))):
        with pytest.raises(Refused):
            attempt()


def test_a_manager_works_only_below_their_own_group(reg):
    lead = reg.create_group(OWNER_P, "主管", items=["space.manage_posts", "space.manage_members", "space.invite",
                                                  "space.view_usage"], act="write")
    boss = join(reg, groups=[lead])
    low = reg.create_group(member(boss), "组员", items=["space.view_usage"])
    assert reg.snapshot()["order"] == [ADMINS, lead, low, EVERYONE]
    with pytest.raises(Refused):                      # 给不出自己没有的权限项
        reg.create_group(member(boss), "越权", items=["space.view_audit"])
    with pytest.raises(Refused):                      # 不能建在自己之前
        reg.create_group(member(boss), "插队", before=lead)
    with pytest.raises(Refused):                      # 办事级别不能高于自己的
        reg.create_group(member(boss), "更高", act="execute")
    with pytest.raises(Refused):                      # 动不了自己所在的身份组
        reg.change_group(member(boss), lead, name="大主管")
    with pytest.raises(Refused):                      # 授予不了管理员
        reg.set_member_groups(member(boss), join(reg, name="小李"), [ADMINS])
    who = join(reg, name="小张")
    assert reg.set_member_groups(member(boss), who, [low]) == [low]
    peer = join(reg, groups=[lead], name="另一位主管")
    with pytest.raises(Refused):                      # 同级的人移不走
        reg.remove_member(member(boss), peer)
    reg.remove_member(member(boss), who)
    with pytest.raises(Missing):
        reg.standing(who)


def test_admins_group_is_fixed_and_built_ins_stay(reg):
    with pytest.raises(ValueError):
        reg.change_group(OWNER_P, ADMINS, items=[])
    reg.change_group(OWNER_P, ADMINS, name="管理团队")
    for group in (ADMINS, EVERYONE):
        with pytest.raises(ValueError):
            reg.delete_group(OWNER_P, group)
    with pytest.raises(ValueError):                   # 「所有人」不能拿到管理员
        reg.change_group(OWNER_P, EVERYONE, items=["admin"])


def test_deleting_a_group_takes_it_from_its_holders(reg):
    group = reg.create_group(OWNER_P, "财务", items=["space.view_usage"])
    who = join(reg, groups=[group])
    reg.delete_group(OWNER_P, group)
    assert reg.standing(who)["groups"] == []
    assert "space.view_usage" not in reg.standing(who)["permissions"]
    assert group not in reg.snapshot()["order"]


def test_reordering(reg):
    a = reg.create_group(OWNER_P, "甲", items=["space.manage_posts"])
    b = reg.create_group(OWNER_P, "乙")
    c = reg.create_group(OWNER_P, "丙")
    who = join(reg, groups=[a])
    reg.reorder_groups(member(who), [ADMINS, a, c, b, EVERYONE])
    assert reg.snapshot()["order"] == [ADMINS, a, c, b, EVERYONE]
    for bad in ([ADMINS, c, a, b, EVERYONE],          # 把别的挪到自己前面
                [a, ADMINS, c, b, EVERYONE],          # 把自己挪到管理员前面
                [ADMINS, a, c, EVERYONE, b],          # 「所有人」不在最后
                [ADMINS, a, c, EVERYONE]):            # 少了一个
        with pytest.raises(Refused):
            reg.reorder_groups(member(who), bad)
    reg.reorder_groups(OWNER_P, [b, ADMINS, c, a, EVERYONE])


def test_reorder_and_removal_rules_directly():
    posts = {ADMINS: Post(ADMINS, frozenset({"admin"})), "a": Post("a", frozenset({"space.manage_members"})),
             "b": Post("b", frozenset()), EVERYONE: Post(EVERYONE, frozenset())}
    order = [ADMINS, "a", "b", EVERYONE]
    assert not may_reorder(["a"], order, [ADMINS, "a", "b", EVERYONE], posts)     # 没有「管理身份组」
    assert may_reorder([], order, ["b", "a", ADMINS, EVERYONE], posts, owner=True)
    assert not may_reorder([], order, ["b", "a", EVERYONE, ADMINS], posts, owner=True)
    assert may_remove_member(["a"], ["b"], posts, order)
    assert may_remove_member(["a"], [], posts, order)
    assert not may_remove_member(["a"], ["a"], posts, order)
    assert not may_remove_member(["b"], [], posts, order)                          # 没有「管理成员」
    assert not may_remove_member(["a"], [ADMINS], posts, order)


# ── 邀请 ──────────────────────────────────────────────────────────────────────

def test_an_invitation_works_once_and_keeps_only_a_digest(reg):
    group = reg.create_group(OWNER_P, "财务")
    invitation, token = reg.invite(OWNER_P, groups=[group], now=T0)
    stored = (reg.directory / "space.json").read_text(encoding="utf-8")
    assert token not in stored and invitation in stored
    who = reg.accept_invitation(token, "  小王 ", now=T0)
    state = reg.snapshot()
    assert state["members"][who]["name"] == "小王" and state["members"][who]["groups"] == [group]
    assert state["invitations"] == {}
    with pytest.raises(Missing):
        reg.accept_invitation(token, "再来一次", now=T0)


def test_expired_wrong_and_revoked_invitations_do_not_admit(reg):
    _, token = reg.invite(OWNER_P, days=1, now=T0)
    with pytest.raises(Missing):
        reg.accept_invitation("not-the-token", "小王", now=T0)
    with pytest.raises(Missing):
        reg.accept_invitation(token, "小王", now=T0 + timedelta(days=1))
    invitation, token = reg.invite(OWNER_P, now=T0)
    reg.revoke_invitation(OWNER_P, invitation)
    with pytest.raises(Missing):
        reg.accept_invitation(token, "小王", now=T0)
    assert reg.snapshot()["members"] == {}


def test_a_bad_name_does_not_use_up_the_invitation(reg):
    _, token = reg.invite(OWNER_P, now=T0)
    with pytest.raises(ValueError):
        reg.accept_invitation(token, " ", now=T0)
    assert reg.accept_invitation(token, "小王", now=T0)


def test_invitation_groups_must_be_grantable_and_deleted_ones_are_skipped(reg):
    inviter_group = reg.create_group(OWNER_P, "招募", items=["space.invite"])
    low = reg.create_group(OWNER_P, "组员")
    inviter = join(reg, groups=[inviter_group])
    with pytest.raises(Refused):
        reg.invite(member(inviter), groups=[ADMINS], now=T0)
    _, token = reg.invite(member(inviter), groups=[low], now=T0)
    reg.delete_group(OWNER_P, low)
    who = reg.accept_invitation(token, "小李", now=T0)
    assert reg.snapshot()["members"][who]["groups"] == []
    assert reg.snapshot()["members"][who]["invited_by"] == inviter


@pytest.mark.parametrize("legacy", [False, True])
def test_invitation_rechecks_the_issuers_computer_level_at_acceptance(reg, legacy):
    manager = reg.create_group(OWNER_P, "招募", items=["space.invite"], computer="all_computers")
    viewing = reg.create_group(OWNER_P, "屏幕权限", computer="all_computers")
    issuer = join(reg, groups=[manager])
    invitation, token = reg.invite(member(issuer), groups=[viewing], now=T0)
    if legacy:
        with reg._change() as state:
            state["invitations"][invitation].pop("by_kind")
    assert reg.invitation(token, now=T0)["groups"] == [viewing]

    reg.change_group(OWNER_P, manager, computer="all_browsers")
    assert reg.invitation(token, now=T0) is None
    with pytest.raises(Missing, match="邀请权限已变化"):
        reg.accept_invitation(token, "新成员", now=T0)
    assert set(reg.snapshot()["members"]) == {issuer}


def test_invitation_from_a_removed_member_expires_immediately(reg):
    recruiter = reg.create_group(OWNER_P, "招募", items=["space.invite"])
    issuer = join(reg, groups=[recruiter])
    _, token = reg.invite(member(issuer), now=T0)
    reg.remove_member(OWNER_P, issuer)
    assert reg.invitation(token, now=T0) is None
    with pytest.raises(Missing):
        reg.accept_invitation(token, "新成员", now=T0)


def test_only_the_sender_or_a_member_manager_revokes(reg):
    inviter_group = reg.create_group(OWNER_P, "招募", items=["space.invite"])
    inviter, other = join(reg, groups=[inviter_group]), join(reg, groups=[inviter_group], name="小李")
    invitation, _ = reg.invite(member(inviter), now=T0)
    with pytest.raises(Refused):
        reg.revoke_invitation(member(other), invitation)
    reg.revoke_invitation(member(inviter), invitation)
    with pytest.raises(Missing):
        reg.revoke_invitation(OWNER_P, invitation)


# ── 办事级别 ──────────────────────────────────────────────────────────────────

def test_act_level_comes_from_groups_unless_set_on_the_agent(reg):
    writers = reg.create_group(OWNER_P, "可写", act="write")
    who, plain = join(reg, groups=[writers]), join(reg, name="小李")
    assert reg.act_level_on("agent-a", member(who)) == "write"
    assert reg.act_level_on("agent-a", member(plain)) == "read"
    reg.set_act_level_on(OWNER_P, "agent-a", plain, "execute")
    reg.set_act_level_on(OWNER_P, "agent-a", who, "none")
    assert reg.act_level_on("agent-a", member(plain)) == "execute"
    assert reg.act_level_on("agent-a", member(who)) == "none"
    assert reg.act_level_on("agent-b", member(plain)) == "read"        # 只对那一个 agent
    reg.set_act_level_on(OWNER_P, "agent-a", who, None)
    assert reg.act_level_on("agent-a", member(who)) == "write"
    assert "agent-a" in reg.snapshot()["agent_levels"]


def test_act_level_for_owner_system_and_strangers(reg):
    assert reg.act_level_on("agent-a", OWNER_P) == "execute"
    assert reg.act_level_on("agent-a", NOBODY) == "none"
    assert reg.act_level_on("agent-a", member("m_not_registered")) == "none"


def test_no_talk_permission_means_no_conversation_unless_named(reg):
    reg.change_group(OWNER_P, EVERYONE, items=["chat.speak"])
    who = join(reg)
    assert reg.act_level_on("agent-a", member(who)) == "none"
    reg.set_act_level_on(OWNER_P, "agent-a", who, "read")
    assert reg.act_level_on("agent-a", member(who)) == "read"


def test_a_responsible_member_sets_levels_up_to_their_own(reg):
    writers = reg.create_group(OWNER_P, "可写", act="write")
    lead, who = join(reg, groups=[writers]), join(reg, name="小李")
    with pytest.raises(Refused):                      # 不是负责人
        reg.set_act_level_on(member(lead), "agent-a", who, "write")
    reg.set_act_level_on(member(lead), "agent-a", who, "write", responsible=True)
    with pytest.raises(Refused):                      # 高于自己的
        reg.set_act_level_on(member(lead), "agent-a", who, "execute", responsible=True)
    reg.set_act_level_on(OWNER_P, "agent-a", who, "execute")
    with pytest.raises(Refused):                      # 说不清的级别
        reg.set_act_level_on(OWNER_P, "agent-a", who, "everything")
    with pytest.raises(Missing):
        reg.set_act_level_on(OWNER_P, "agent-a", "m_gone", "read")
    with pytest.raises(ValueError):
        reg.set_act_level_on(OWNER_P, "../x", who, "read")


def test_clearing_a_level_cannot_raise_someone_above_the_actor(reg):
    readers = reg.create_group(OWNER_P, "负责人")                       # 只读
    executors = reg.create_group(OWNER_P, "可执行", act="execute")
    lead, who = join(reg, groups=[readers]), join(reg, groups=[executors], name="小李")
    reg.set_act_level_on(OWNER_P, "agent-a", who, "none")
    with pytest.raises(Refused):                      # 撤掉之后对方会回到「可执行」，高于操作人的只读
        reg.set_act_level_on(member(lead), "agent-a", who, None, responsible=True)
    assert reg.act_level_on("agent-a", member(who)) == "none"


def test_removing_a_member_clears_their_agent_levels(reg):
    who = join(reg)
    reg.set_act_level_on(OWNER_P, "agent-a", who, "write")
    reg.remove_member(member(who), who)               # 自己退出
    assert reg.snapshot()["agent_levels"] == {}
