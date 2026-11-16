"""身份组与权限项的判定（HOME_AND_MEMBERS_DESIGN 7.2、7.3）。纯函数，还没有登记处和入口使用它。"""

import pytest

from src.agents import identity_groups as R

POSTS = {
    R.ADMINS: R.Post(R.ADMINS, frozenset({R.ADMIN}), "all_computers"),
    "leads": R.Post("leads", frozenset({"space.manage_members", "space.manage_posts", "space.invite",
                                        "roles.manage_routines"}), "all_browsers", "execute"),
    "ops": R.Post("ops", frozenset({"roles.manage_routines"}), "own_computer", "write"),
    "finance": R.Post("finance", frozenset({"space.manage_billing"})),
    R.EVERYONE: R.Post(R.EVERYONE, R.EVERYONE_DEFAULT, R.COMPUTER_DEFAULT),
}
ORDER = [R.ADMINS, "leads", "ops", "finance", R.EVERYONE]


def test_a_plain_member_gets_the_everyone_defaults():
    mine = R.permissions_of([], POSTS)
    assert mine == R.EVERYONE_DEFAULT
    assert {"chat.speak", "roles.talk", "roles.dispatch"} <= mine
    assert not mine & {"roles.approve", "space.invite", "roles.act_for_members", "space.manage_members", R.ADMIN}
    assert R.computer_level([], POSTS) == "own_browser"


def test_posts_add_up_and_admin_means_everything():
    mine = R.permissions_of(["ops", "finance"], POSTS)
    assert mine == R.EVERYONE_DEFAULT | {"roles.manage_routines", "space.manage_billing"}
    assert R.computer_level(["ops", "finance"], POSTS) == "own_computer"
    assert R.permissions_of([R.ADMINS], POSTS) == R.PERMISSIONS
    assert R.permissions_of([], POSTS, owner=True) == R.PERMISSIONS
    assert R.computer_level([], POSTS, owner=True) == "all_computers"


def test_a_deleted_post_simply_stops_counting():
    assert R.permissions_of(["gone", "ops"], POSTS) == R.permissions_of(["ops"], POSTS)


def test_some_things_only_the_owner_can_do_whatever_the_post():
    for action in R.OWNER_ONLY:
        assert R.has(action, [], POSTS, owner=True)
        assert not R.has(action, [R.ADMINS], POSTS)
    assert R.has("space.manage_billing", [R.ADMINS], POSTS) and not R.has("space.manage_billing", ["ops"], POSTS)
    assert not R.has("made.up", [R.ADMINS], POSTS) and not R.has("made.up", [], POSTS, owner=True)


def test_post_records_are_checked():
    R.check_post(POSTS["leads"])
    for bad in (R.Post(R.EVERYONE, frozenset({R.ADMIN})), R.Post("x", frozenset({"made.up"})),
                R.Post("x", frozenset(), "root"), R.Post(R.ADMINS, frozenset({"chat.speak"})),
                R.Post("x", frozenset(), "none", "sudo")):
        with pytest.raises(ValueError):
            R.check_post(bad)
    R.check_order(ORDER)
    for bad in ([R.EVERYONE, "ops"], ["ops"], ["ops", "ops", R.EVERYONE]):
        with pytest.raises(ValueError):
            R.check_order(bad)


def test_only_posts_below_your_own_can_be_changed():
    change = dict(before=frozenset(), after=frozenset({"roles.manage_routines"}))
    assert R.may_change_post(["leads"], "ops", POSTS, ORDER, **change)
    assert not R.may_change_post(["leads"], "leads", POSTS, ORDER, **change)        # 自己这一级
    assert not R.may_change_post(["leads"], R.ADMINS, POSTS, ORDER, **change)       # 在自己之上
    assert not R.may_change_post(["ops"], "finance", POSTS, ORDER, **change)        # 没有「管理身份组」
    assert not R.may_change_post(["leads"], "unlisted", POSTS, ORDER, **change)     # 顺序表里没有的够不着
    assert R.may_change_post([], R.ADMINS, POSTS, ORDER, owner=True, **change)


def test_nobody_hands_out_what_they_do_not_hold():
    grow = dict(before=frozenset(), after=frozenset({"space.manage_billing"}))
    assert not R.may_change_post(["leads"], "ops", POSTS, ORDER, **grow)            # 自己没有计费
    shrink = dict(before=frozenset({"space.manage_billing"}), after=frozenset())
    assert not R.may_change_post(["leads"], "finance", POSTS, ORDER, **shrink)      # 拿下来也要自己持有
    assert R.may_change_post([R.ADMINS], "finance", POSTS, ORDER, **grow)
    admin = dict(before=frozenset(), after=frozenset({R.ADMIN}))
    assert not R.may_change_post([R.ADMINS], R.EVERYONE, POSTS, ORDER, **admin)
    assert not R.may_change_post([], R.EVERYONE, POSTS, ORDER, owner=True, **admin)  # 所有者也不行


def test_granting_needs_rank_over_both_the_post_and_the_person():
    assert R.may_grant(["leads"], "ops", POSTS, ORDER)
    assert not R.may_grant(["leads"], "finance", POSTS, ORDER)                     # 身份组的权限项自己没有
    assert not R.may_grant(["leads"], "leads", POSTS, ORDER)
    assert not R.may_grant(["leads"], "ops", POSTS, ORDER, target_held=["leads"])   # 对方不在自己之后
    assert not R.may_grant(["leads"], "ops", POSTS, ORDER, target_held=[R.ADMINS])
    assert not R.may_grant(["ops"], "finance", POSTS, ORDER)                       # 没有「管理成员」
    assert not R.may_grant([R.ADMINS], R.EVERYONE, POSTS, ORDER)                   # 自动持有，不授予
    assert not R.may_grant([], "gone", POSTS, ORDER, owner=True)
    assert R.may_grant([R.ADMINS], "finance", POSTS, ORDER, target_held=["leads"])
    assert R.may_grant([], R.ADMINS, POSTS, ORDER, owner=True, target_held=[R.ADMINS])


def test_invites_carry_only_posts_the_inviter_could_grant():
    assert R.invite_posts(["leads"], ["ops", "ops", R.EVERYONE], POSTS, ORDER) == ["ops"]
    assert R.invite_posts(["leads"], [], POSTS, ORDER) == []
    for held, wanted in ((["leads"], ["finance"]), (["leads"], [R.ADMINS]), (["leads"], ["gone"]), (["ops"], [])):
        with pytest.raises(PermissionError):
            R.invite_posts(held, wanted, POSTS, ORDER)
    assert R.invite_posts([], [R.ADMINS], POSTS, ORDER, owner=True) == [R.ADMINS]


def test_posts_deleted_after_an_invite_was_sent_are_skipped():
    assert R.accepted_posts(["ops", "gone", R.EVERYONE, "ops"], POSTS) == ["ops"]


# ── 办事级别 ──────────────────────────────────────────────────────────────────

def test_the_act_level_comes_from_the_highest_group():
    assert R.act_level([], POSTS) == "read"                       # 只有「所有人」
    assert R.act_level(["ops"], POSTS) == "write"
    assert R.act_level(["ops", "leads", "finance"], POSTS) == "execute"
    assert R.act_level([], POSTS, owner=True) == "execute"
    assert R.act_level([], {}) == "none"                          # 连「所有人」都读不到：什么都不给


def test_a_level_set_on_one_agent_for_one_person_wins_both_ways():
    on_agent = {"wang": "write", "li": "none", "zhao": "sudo"}
    assert R.act_level_on(on_agent, "wang", "read") == "write"            # 单独抬高
    assert R.act_level_on(on_agent, "li", "execute") == "none"            # 单独关掉
    assert R.act_level_on(on_agent, "zhao", "execute") == "none"          # 读到不认识的：按不能对话
    assert R.act_level_on(on_agent, "chen", "write") == "write"           # 没单独定：用身份组的
    assert R.act_level_on(on_agent, "chen", "write", talks=False) == "none"
    assert R.act_level_on(on_agent, "wang", "read", talks=False) == "write"   # 被点了名的不受这条限制
    assert R.act_level_on(on_agent, "li", "none", owner=True) == "execute"


def test_setting_a_level_on_an_agent_cannot_exceed_your_own():
    assert R.may_set_act_level("execute", actor_level="none", owner=True)
    assert R.may_set_act_level("write", actor_level="write", responsible=True)
    assert R.may_set_act_level("none", actor_level="write", responsible=True)
    assert not R.may_set_act_level("execute", actor_level="write", responsible=True)
    assert not R.may_set_act_level("execute", actor_level="write", manages_all=True)
    assert R.may_set_act_level("read", actor_level="execute", manages_all=True)
    assert not R.may_set_act_level("read", actor_level="execute")          # 既不是负责人也不管理全部
    assert not R.may_set_act_level("sudo", actor_level="execute", owner=True)


def test_a_level_only_narrows_what_the_agent_registered():
    role = ["read", "write", "execute", "deliver"]
    assert R.operations_at("none", role) == frozenset()
    assert R.operations_at("read", role) == {"read"}
    assert R.operations_at("write", role) == {"read", "write", "deliver"}
    assert R.operations_at("execute", role) == set(role)
    assert R.operations_at("execute", ["read"]) == {"read"}               # agent 没登记的，级别给不出来
    assert R.operations_at("sudo", role) == frozenset()


def test_the_level_feeds_the_kernels_cap_on_a_members_turn():
    from src.agents import principal as P
    role = P.Grants.of({"operations": ["read", "write", "execute"], "tools": ["grep", "edit_file"],
                        "sees": [], "web": True, "project": True})
    ann = P.Principal(P.MEMBER, "ann", "home")
    mine = P.Grants(R.operations_at("write", role.operations), role.tools, role.sees, role.web, role.project)
    got = P.effective_grants(ann, role, mine)
    assert got.operations == {"read", "write"} and got.tools == role.tools and got.web is True
