"""成员的身份组、权限项与办事级别：纯数据的判定（HOME_AND_MEMBERS_DESIGN 7.2、7.3，ADR-019）。

叫法（主人 2026-11-16 定）：给人分权限的这一套叫「身份组」，设计文档里写作「岗位」的就是它；
agent 登记的职责仍叫岗位，是另一回事。

所有者是身份，不是身份组；其余的人能做什么，由他持有的身份组解析成权限项集合，任何地方只问
「这个人有没有这个权限项」，不比较身份组名字。本模块不存任何东西、不导入上层：身份组表和谁持有哪些
身份组由调用方读好传入。

固定规则都在这里一处：

- 「所有人」身份组每个成员自动持有，永远排在最后，不能持有「管理员」权限项。
- 身份组有先后。除所有者外，只能修改、删除、授予排在自己最高身份组之后的身份组。
- 谁都不能给出自己没有的：改身份组只能加减自己持有的权限项，授予的身份组其权限项必须全被自己持有。
- 只有所有者能做、任何身份组都给不了的事，不是权限项，见 ``OWNER_ONLY``。
- 办事级别决定一个人能让 agent 做到哪一步：不能对话、只读、可写、可执行，高的包含低的。来自他的
  身份组（取最高），也可以对某个 agent 单独给某个人定一级——单独定的优先。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, List, Mapping, Sequence

ADMIN = "admin"
EVERYONE = "everyone"            # 内置身份组的 id
ADMINS = "admins"                # 内置身份组的 id

# 权限项（范围.名字）。「所有人」一列是新建空间时的默认值。
_ITEMS: Dict[str, bool] = {
    "chat.speak": True, "chat.create_channel": True, "chat.mention_everyone": False, "chat.manage_channels": False,
    "roles.talk": True, "roles.dispatch": True, "roles.create_own": True, "roles.manage_routines": False,
    "roles.approve": False, "roles.manage_all": False, "roles.set_auto_approval": False,
    "roles.act_for_members": False, "roles.view_all": False,
    "computers.connect_own": True, "computers.manage_connections": False, "computers.manage_credentials": False,
    "material.edit": True, "material.upload": True, "material.review_memory": False,
    "space.invite": False, "space.view_usage": False, "space.view_audit": False, "space.edit_settings": False,
    "space.manage_members": False, "space.manage_posts": False, "space.manage_billing": False,
}
PERMISSIONS: FrozenSet[str] = frozenset(_ITEMS) | {ADMIN}
EVERYONE_DEFAULT: FrozenSet[str] = frozenset(item for item, default in _ITEMS.items() if default)
# 只有所有者能做，任何身份组都给不了：不在 PERMISSIONS 里，判定时只看是不是所有者
OWNER_ONLY: FrozenSet[str] = frozenset({"owners.manage", "space.delete", "space.transfer", "space.move"})

# 人对角色电脑的权限是一条阶梯，每个身份组选一级，高一级包含低一级；多个身份组取最高
COMPUTER_LEVELS = ("none", "own_browser", "own_computer", "all_browsers", "all_computers")
COMPUTER_DEFAULT = "own_browser"

# 办事级别：一个人能让 agent 做到哪一步。对外发送、不可逆、替人操作界面不单列一级——
# 可写及以上可以让 agent 去做，但那几类始终要批准（见 principal.pre_authorized_for）。
ACT_LEVELS = ("none", "read", "write", "execute")
ACT_DEFAULT = "read"
_LEVEL_DROPS = {"none": None, "read": None, "write": frozenset({"execute"}), "execute": frozenset()}


@dataclass(frozen=True)
class Post:
    id: str
    items: FrozenSet[str]
    computer: str = "none"
    act: str = "read"


def check_post(post: Post) -> None:
    """一条身份组记录本身合不合规矩。不合就抛 ValueError——登记处拿它挡住坏数据。"""
    unknown = post.items - PERMISSIONS
    if unknown:
        raise ValueError(f"未知的权限项：{sorted(unknown)}")
    if post.computer not in COMPUTER_LEVELS:
        raise ValueError(f"未知的电脑权限级别：{post.computer!r}")
    if post.act not in ACT_LEVELS:
        raise ValueError(f"未知的办事级别：{post.act!r}")
    if post.id == EVERYONE and ADMIN in post.items:
        raise ValueError("「所有人」不能持有管理员权限")
    if post.id == ADMINS and ADMIN not in post.items:
        raise ValueError("「管理员」身份组必须持有管理员权限")


def check_order(order: Sequence[str]) -> None:
    if len(set(order)) != len(order):
        raise ValueError("身份组顺序里有重复")
    if EVERYONE not in order or order[-1] != EVERYONE:
        raise ValueError("「所有人」必须存在且排在最后")


def _held(held: Iterable[str], posts: Mapping[str, Post]) -> List[Post]:
    """这个人实际持有的身份组：他名下的（已不存在的跳过），加上自动持有的「所有人」。"""
    ids = {str(post_id) for post_id in held} | {EVERYONE}
    return [posts[post_id] for post_id in ids if post_id in posts]


def permissions_of(held: Iterable[str], posts: Mapping[str, Post], *, owner: bool = False) -> FrozenSet[str]:
    """把一个人的身份组解析成权限项集合。所有者和持有「管理员」的人得到全部权限项。"""
    if owner:
        return PERMISSIONS
    items: set = set()
    for post in _held(held, posts):
        items |= post.items & PERMISSIONS
    return PERMISSIONS if ADMIN in items else frozenset(items)


def has(permission: str, held: Iterable[str], posts: Mapping[str, Post], *, owner: bool = False) -> bool:
    """唯一的检查：这个人有没有这个权限项。只有所有者能做的事，只有所有者有。"""
    if permission in OWNER_ONLY:
        return owner
    return permission in PERMISSIONS and permission in permissions_of(held, posts, owner=owner)


def computer_level(held: Iterable[str], posts: Mapping[str, Post], *, owner: bool = False) -> str:
    if owner:
        return COMPUTER_LEVELS[-1]
    return max((post.computer for post in _held(held, posts) if post.computer in COMPUTER_LEVELS),
               key=COMPUTER_LEVELS.index, default="none")


def _rank(post_id: str, order: Sequence[str]) -> int:
    """排得越前数字越小。不在顺序表里的身份组按最前算：没人（除所有者）够得着它。"""
    return order.index(post_id) if post_id in order else -1


def _top(held: Iterable[str], order: Sequence[str]) -> int:
    """这个人最高的身份组排第几。只持有「所有人」的人排在最后，够不着任何身份组。"""
    ranks = [order.index(post_id) for post_id in set(held) | {EVERYONE} if post_id in order]
    return min(ranks, default=len(order))


def outranks(actor_held: Iterable[str], post_id: str, order: Sequence[str], *, owner: bool = False) -> bool:
    """这个身份组是不是排在操作人最高身份组之后。「所有人」排在最后，但只有比它高的人才动得了它。"""
    return owner or _top(actor_held, order) < _rank(post_id, order)


def may_change_post(actor_held: Iterable[str], post_id: str, posts: Mapping[str, Post], order: Sequence[str], *,
                    before: FrozenSet[str], after: FrozenSet[str], owner: bool = False) -> bool:
    """能不能把一个身份组的权限项从 before 改成 after（新建时 before 为空，删除时 after 为空）。

    要有「管理身份组」，身份组要排在自己之后，而且加上去和拿下来的每一项都是自己持有的。
    新建身份组时，order 里要已经含有它打算放的位置——放不到自己之后的位置就建不了。"""
    if post_id == EVERYONE and ADMIN in after:
        return False
    if owner:
        return True
    mine = permissions_of(actor_held, posts)
    return ("space.manage_posts" in mine and outranks(actor_held, post_id, order)
            and (before ^ after) <= mine)


def may_grant(actor_held: Iterable[str], post_id: str, posts: Mapping[str, Post], order: Sequence[str], *,
              target_held: Iterable[str] = (), owner: bool = False) -> bool:
    """能不能把这个身份组授予（或收回）某个人。

    要有「管理成员」，身份组排在自己之后且它的权限项全被自己持有，对方现有的最高身份组也要排在自己之后。
    「所有人」是自动持有的，不能授予也不能收回。"""
    if post_id == EVERYONE or post_id not in posts:
        return False
    if owner:
        return True
    mine = permissions_of(actor_held, posts)
    return ("space.manage_members" in mine and outranks(actor_held, post_id, order)
            and posts[post_id].items <= mine and _top(actor_held, order) < _top(target_held, order))


def may_reorder(actor_held: Iterable[str], before: Sequence[str], after: Sequence[str],
                posts: Mapping[str, Post], *, owner: bool = False) -> bool:
    """能不能把身份组的先后从 before 改成 after。

    只能是同一批身份组换位置，「所有人」留在最后。除所有者外要有「管理身份组」，而且自己最高身份组
    及其之前的那一段必须原样不动——只能调整排在自己之后的身份组，不能把谁挪到自己前面。"""
    if sorted(before) != sorted(after) or len(set(after)) != len(after):
        return False
    if EVERYONE not in after or after[-1] != EVERYONE:
        return False
    if owner:
        return True
    if "space.manage_posts" not in permissions_of(actor_held, posts):
        return False
    fixed = _top(actor_held, before) + 1
    return list(before[:fixed]) == list(after[:fixed])


def may_remove_member(actor_held: Iterable[str], target_held: Iterable[str], posts: Mapping[str, Post],
                      order: Sequence[str], *, owner: bool = False) -> bool:
    """能不能把一个成员移出空间：要有「管理成员」，对方最高的身份组要排在自己之后。所有者不在此列。"""
    if owner:
        return True
    return ("space.manage_members" in permissions_of(actor_held, posts)
            and _top(actor_held, order) < _top(target_held, order))


def invite_posts(actor_held: Iterable[str], wanted: Iterable[str], posts: Mapping[str, Post],
                 order: Sequence[str], *, owner: bool = False) -> List[str]:
    """发邀请时核对：邀请带的身份组必须都是邀请人授予得了的。返回可带上的身份组；有一个不行就抛错。"""
    if not has("space.invite", actor_held, posts, owner=owner):
        raise PermissionError("没有邀请成员的权限")
    chosen = list(dict.fromkeys(str(post_id) for post_id in wanted if post_id != EVERYONE))
    for post_id in chosen:
        if post_id not in posts or not (owner or (outranks(actor_held, post_id, order)
                                                  and posts[post_id].items <= permissions_of(actor_held, posts))):
            raise PermissionError(f"不能在邀请里带上身份组 {post_id}")
    return chosen


def accepted_posts(invited: Iterable[str], posts: Mapping[str, Post]) -> List[str]:
    """接受邀请时：发出之后被删除的身份组自动跳过。"""
    return [post_id for post_id in dict.fromkeys(invited) if post_id in posts and post_id != EVERYONE]


# ── 办事级别 ──────────────────────────────────────────────────────────────────

def act_level(held: Iterable[str], posts: Mapping[str, Post], *, owner: bool = False) -> str:
    """这个人按身份组得到的办事级别：多个身份组取最高；所有者最高。"""
    if owner:
        return ACT_LEVELS[-1]
    return max((post.act for post in _held(held, posts) if post.act in ACT_LEVELS),
               key=ACT_LEVELS.index, default="none")


AUDIENCE_MODES = ("everyone", "people", "owner")
MAX_AUDIENCE = 500


def check_audience(value: object, members: Iterable[str], groups: Iterable[str]) -> Dict[str, object]:
    """agent 的受众（谁能和它对话）：所有人、指定的人和身份组、只有所有者。返回整理好的记录。

    指定的人只能是在册成员；指定的身份组不能是「所有人」（那就是所有人这一档）。"""
    if not isinstance(value, Mapping) or value.get("mode") not in AUDIENCE_MODES:
        raise ValueError("受众只能是所有人、指定的人或只有所有者")
    mode = str(value["mode"])
    if mode != "people":
        return {"mode": mode, "userIds": [], "roleIds": []}
    known_people, known_groups = set(members), set(groups) - {EVERYONE}
    people, posts = value.get("userIds") or [], value.get("roleIds") or []
    if not isinstance(people, list) or not isinstance(posts, list) or len(people) + len(posts) > MAX_AUDIENCE:
        raise ValueError("指定的人和身份组太多或格式不对")
    if any(not isinstance(x, str) or x not in known_people for x in people):
        raise ValueError("指定的人里有不在册的成员")
    if any(not isinstance(x, str) or x not in known_groups for x in posts):
        raise ValueError("指定的身份组不存在，或者是「所有人」")
    return {"mode": mode, "userIds": sorted(set(people)), "roleIds": sorted(set(posts))}


def in_audience(audience: object, person: str, held: Iterable[str]) -> bool:
    """这个成员在不在 agent 的受众里（所有者不经过这里）。没设受众按所有人；读不懂的按不在。"""
    if not audience:
        return True
    if not isinstance(audience, Mapping):
        return False
    mode = audience.get("mode")
    if mode == "everyone":
        return True
    if mode == "people":
        return person in (audience.get("userIds") or []) or bool(set(held) & set(audience.get("roleIds") or []))
    return False


def act_level_on(agent_levels: Mapping[str, str], person: str, group_level: str, *, owner: bool = False,
                 talks: bool = True, audience: object = None, held: Iterable[str] = ()) -> str:
    """这个人对这一个 agent 的办事级别。

    先看受众：不在 agent 受众里的成员不能对话，单独定的级别也不例外（受众是「谁能用」，级别是「能做到哪一步」）。
    agent_levels 是该 agent 上单独给人定的级别（人 → 级别）：定了就用它，可以比身份组的高也可以低，
    包括「不能对话」。没单独定的用身份组的级别；连「和 agent 对话」这项权限都没有的（talks=False）
    一律不能对话，除非在这个 agent 上被单独点了名。读到不认识的级别按不能对话。"""
    if owner:
        return ACT_LEVELS[-1]
    if not in_audience(audience, person, held):
        return "none"
    if person in agent_levels:
        level = agent_levels[person]
        return level if level in ACT_LEVELS else "none"
    if not talks:
        return "none"
    return group_level if group_level in ACT_LEVELS else "none"


def may_set_act_level(level: str, *, actor_level: str, responsible: bool = False, manages_all: bool = False,
                      owner: bool = False) -> bool:
    """能不能在一个 agent 上给某个人单独定级别。

    所有者可以。其余的人要是这个 agent 的负责人、或持有「管理所有 agent」，而且定出去的级别
    不能高于自己对这个 agent 的级别——谁都不能给出自己没有的。"""
    if level not in ACT_LEVELS:
        return False
    if owner:
        return True
    if not (responsible or manages_all) or actor_level not in ACT_LEVELS:
        return False
    return ACT_LEVELS.index(level) <= ACT_LEVELS.index(actor_level)


def operations_at(level: str, role_operations: Iterable[str]) -> FrozenSet[str]:
    """这一级下，agent 登记的操作类别里还剩哪些可用。

    不能对话：什么都没有。只读：只剩读。可写：去掉执行。可执行：全部。
    结果永远是 agent 自己那份的子集——级别只收窄，不会给出 agent 没登记的操作。"""
    mine = frozenset(str(kind) for kind in role_operations)
    if level == "read":
        return mine & {"read"}
    drops = _LEVEL_DROPS.get(level)
    return frozenset() if drops is None else mine - drops
