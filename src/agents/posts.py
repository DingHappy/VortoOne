"""成员的岗位与权限项：纯数据的判定（HOME_AND_MEMBERS_DESIGN 7.2、7.3，ADR-019）。

所有者是身份，不是岗位；其余的人能做什么，由他持有的岗位解析成权限项集合，任何地方只问
「这个人有没有这个权限项」，不比较岗位名字。本模块不存任何东西、不导入上层：岗位表和谁持有哪些
岗位由调用方读好传入。

固定规则都在这里一处：

- 「所有人」岗位每个成员自动持有，永远排在最后，不能持有「管理员」权限项。
- 岗位有先后。除所有者外，只能修改、删除、授予排在自己最高岗位之后的岗位。
- 谁都不能给出自己没有的：改岗位只能加减自己持有的权限项，授予的岗位其权限项必须全被自己持有。
- 只有所有者能做、任何岗位都给不了的事，不是权限项，见 ``OWNER_ONLY``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, List, Mapping, Sequence

ADMIN = "admin"
EVERYONE = "everyone"            # 内置岗位的 id
ADMINS = "admins"                # 内置岗位的 id

# 权限项（范围.名字）。「所有人」一列是新建空间时的默认值。
_ITEMS: Dict[str, bool] = {
    "chat.speak": True, "chat.create_channel": True, "chat.mention_everyone": False, "chat.manage_channels": False,
    "roles.talk": True, "roles.dispatch": True, "roles.create_own": True, "roles.manage_routines": False,
    "roles.approve": False, "roles.manage_all": False, "roles.set_auto_approval": False,
    "roles.act_for_members": False,
    "computers.connect_own": True, "computers.manage_connections": False, "computers.manage_credentials": False,
    "material.edit": True, "material.upload": True, "material.review_memory": False,
    "space.invite": False, "space.view_usage": False, "space.view_audit": False, "space.edit_settings": False,
    "space.manage_members": False, "space.manage_posts": False, "space.manage_billing": False,
}
PERMISSIONS: FrozenSet[str] = frozenset(_ITEMS) | {ADMIN}
EVERYONE_DEFAULT: FrozenSet[str] = frozenset(item for item, default in _ITEMS.items() if default)
# 只有所有者能做，任何岗位都给不了：不在 PERMISSIONS 里，判定时只看是不是所有者
OWNER_ONLY: FrozenSet[str] = frozenset({"owners.manage", "space.delete", "space.transfer", "space.move"})

# 人对角色电脑的权限是一条阶梯，每个岗位选一级，高一级包含低一级；多个岗位取最高
COMPUTER_LEVELS = ("none", "own_browser", "own_computer", "all_browsers", "all_computers")
COMPUTER_DEFAULT = "own_browser"


@dataclass(frozen=True)
class Post:
    id: str
    items: FrozenSet[str]
    computer: str = "none"


def check_post(post: Post) -> None:
    """一条岗位记录本身合不合规矩。不合就抛 ValueError——登记处拿它挡住坏数据。"""
    unknown = post.items - PERMISSIONS
    if unknown:
        raise ValueError(f"未知的权限项：{sorted(unknown)}")
    if post.computer not in COMPUTER_LEVELS:
        raise ValueError(f"未知的电脑权限级别：{post.computer!r}")
    if post.id == EVERYONE and ADMIN in post.items:
        raise ValueError("「所有人」不能持有管理员权限")
    if post.id == ADMINS and ADMIN not in post.items:
        raise ValueError("「管理员」岗位必须持有管理员权限")


def check_order(order: Sequence[str]) -> None:
    if len(set(order)) != len(order):
        raise ValueError("岗位顺序里有重复")
    if EVERYONE not in order or order[-1] != EVERYONE:
        raise ValueError("「所有人」必须存在且排在最后")


def _held(held: Iterable[str], posts: Mapping[str, Post]) -> List[Post]:
    """这个人实际持有的岗位：他名下的（已不存在的跳过），加上自动持有的「所有人」。"""
    ids = {str(post_id) for post_id in held} | {EVERYONE}
    return [posts[post_id] for post_id in ids if post_id in posts]


def permissions_of(held: Iterable[str], posts: Mapping[str, Post], *, owner: bool = False) -> FrozenSet[str]:
    """把一个人的岗位解析成权限项集合。所有者和持有「管理员」的人得到全部权限项。"""
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
    """排得越前数字越小。不在顺序表里的岗位按最前算：没人（除所有者）够得着它。"""
    return order.index(post_id) if post_id in order else -1


def _top(held: Iterable[str], order: Sequence[str]) -> int:
    """这个人最高的岗位排第几。只持有「所有人」的人排在最后，够不着任何岗位。"""
    ranks = [order.index(post_id) for post_id in set(held) | {EVERYONE} if post_id in order]
    return min(ranks, default=len(order))


def outranks(actor_held: Iterable[str], post_id: str, order: Sequence[str], *, owner: bool = False) -> bool:
    """这个岗位是不是排在操作人最高岗位之后。「所有人」排在最后，但只有比它高的人才动得了它。"""
    return owner or _top(actor_held, order) < _rank(post_id, order)


def may_change_post(actor_held: Iterable[str], post_id: str, posts: Mapping[str, Post], order: Sequence[str], *,
                    before: FrozenSet[str], after: FrozenSet[str], owner: bool = False) -> bool:
    """能不能把一个岗位的权限项从 before 改成 after（新建时 before 为空，删除时 after 为空）。

    要有「管理岗位」，岗位要排在自己之后，而且加上去和拿下来的每一项都是自己持有的。
    新建岗位时，order 里要已经含有它打算放的位置——放不到自己之后的位置就建不了。"""
    if post_id == EVERYONE and ADMIN in after:
        return False
    if owner:
        return True
    mine = permissions_of(actor_held, posts)
    return ("space.manage_posts" in mine and outranks(actor_held, post_id, order)
            and (before ^ after) <= mine)


def may_grant(actor_held: Iterable[str], post_id: str, posts: Mapping[str, Post], order: Sequence[str], *,
              target_held: Iterable[str] = (), owner: bool = False) -> bool:
    """能不能把这个岗位授予（或收回）某个人。

    要有「管理成员」，岗位排在自己之后且它的权限项全被自己持有，对方现有的最高岗位也要排在自己之后。
    「所有人」是自动持有的，不能授予也不能收回。"""
    if post_id == EVERYONE or post_id not in posts:
        return False
    if owner:
        return True
    mine = permissions_of(actor_held, posts)
    return ("space.manage_members" in mine and outranks(actor_held, post_id, order)
            and posts[post_id].items <= mine and _top(actor_held, order) < _top(target_held, order))


def invite_posts(actor_held: Iterable[str], wanted: Iterable[str], posts: Mapping[str, Post],
                 order: Sequence[str], *, owner: bool = False) -> List[str]:
    """发邀请时核对：邀请带的岗位必须都是邀请人授予得了的。返回可带上的岗位；有一个不行就抛错。"""
    if not has("space.invite", actor_held, posts, owner=owner):
        raise PermissionError("没有邀请成员的权限")
    chosen = list(dict.fromkeys(str(post_id) for post_id in wanted if post_id != EVERYONE))
    for post_id in chosen:
        if post_id not in posts or not (owner or (outranks(actor_held, post_id, order)
                                                  and posts[post_id].items <= permissions_of(actor_held, posts))):
            raise PermissionError(f"不能在邀请里带上岗位 {post_id}")
    return chosen


def accepted_posts(invited: Iterable[str], posts: Mapping[str, Post]) -> List[str]:
    """接受邀请时：发出之后被删除的岗位自动跳过。"""
    return [post_id for post_id in dict.fromkeys(invited) if post_id in posts and post_id != EVERYONE]
