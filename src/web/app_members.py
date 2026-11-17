"""把界面的「roles / members」写法对到成员登记处（身份组与成员）。

这里只做翻译：界面的权限键 ↔ 内核的权限项，界面的四个电脑键 ↔ 电脑权限阶梯的一级。能不能改
由登记处交内核判定，这里不判。

**操作人目前恒为所有者**：现在唯一的登录方式是主人令牌，能调到这里的就是所有者（``actor``）。
成员能登录之后，只改 ``actor`` 这一处，让它返回登录的那个人。

界面上没有的两样东西从这里设不了：办事级别（不能对话/只读/可写/可执行），以及「调整免批范围」
「代成员办事」两个权限项。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from src.agents import identity_groups as rules
from src.agents.identity_groups import ADMIN, ADMINS, EVERYONE
from src.agents.principal import OWNER, Principal
from src.web import app_views as views
from src.workflow.members.registry import MemberRegistry

# 界面的权限键 → 内核的权限项
_KEYS: Dict[str, str] = {
    "post": "chat.speak", "create_channels": "chat.create_channel", "mention_everyone": "chat.mention_everyone",
    "manage_channels": "chat.manage_channels",
    "talk_to_agents": "roles.talk", "view_all_agents": "roles.view_all", "assign_work": "roles.dispatch",
    "manage_routines": "roles.manage_routines", "approve_actions": "roles.approve",
    "create_agents": "roles.create_own", "manage_agents": "roles.manage_all",
    "pair_computers": "computers.connect_own", "manage_connectors": "computers.manage_connections",
    "manage_credentials": "computers.manage_credentials",
    "edit_wiki": "material.edit", "upload_files": "material.upload", "review_memory": "material.review_memory",
    "invite_people": "space.invite", "view_usage": "space.view_usage", "view_audit": "space.view_audit",
    "configure": "space.edit_settings", "manage_members": "space.manage_members",
    "manage_roles": "space.manage_posts", "manage_billing": "space.manage_billing",
    "administrator": ADMIN,
}
_ITEMS = {item: key for key, item in _KEYS.items()}
# 界面的四个电脑键，从低到高，对应电脑权限阶梯的第 1～4 级
_COMPUTER_KEYS = ("own_browsers", "own_computers", "control_browsers", "control_computers")

AREAS: List[Dict[str, Any]] = [
    {"id": "chat", "use": ["post", "create_channels"],
     "keys": ["post", "create_channels", "mention_everyone", "manage_channels"]},
    {"id": "agents", "use": ["talk_to_agents", "view_all_agents", "assign_work", "manage_routines"],
     "keys": ["talk_to_agents", "view_all_agents", "assign_work", "manage_routines", "approve_actions",
              "create_agents", "manage_agents"]},
    {"id": "tools", "use": ["pair_computers"],
     "keys": ["pair_computers", *_COMPUTER_KEYS, "manage_connectors", "manage_credentials"]},
    {"id": "knowledge", "use": ["edit_wiki", "upload_files"], "keys": ["edit_wiki", "upload_files", "review_memory"]},
    {"id": "org", "use": ["invite_people"],
     "keys": ["invite_people", "view_usage", "view_audit", "configure", "manage_members", "manage_roles",
              "manage_billing"]},
]
COLORS = ["indigo", "violet", "rose", "amber", "green", "teal", "graphite"]
ICONS = ["shield", "code", "wallet", "mail", "bolt", "flag", "book", "chart", "bulb", "globe", "person"]


def actor(root: str) -> Principal:
    """这次请求是谁发起的。单人前提：能通过鉴权的就是所有者。成员登录接上后改这里。"""
    return Principal(OWNER, views.owner_id(root), root)


def _split(keys: Any) -> tuple:
    """界面给的一组权限键 → （权限项集合，电脑权限级别）。不认识的键是错的请求，不悄悄丢掉。"""
    if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys):
        raise ValueError("permissions 必须是字符串列表")
    unknown = sorted(set(keys) - set(_KEYS) - set(_COMPUTER_KEYS))
    if unknown:
        raise ValueError(f"不认识的权限：{unknown}")
    step = max((_COMPUTER_KEYS.index(key) + 1 for key in keys if key in _COMPUTER_KEYS), default=0)
    return frozenset(_KEYS[key] for key in keys if key in _KEYS), rules.COMPUTER_LEVELS[step]


def _keys(record: Mapping[str, Any]) -> List[str]:
    keys = [_ITEMS[item] for item in record.get("items") or [] if item in _ITEMS]
    step = rules.COMPUTER_LEVELS.index(record["computer"]) if record.get("computer") in rules.COMPUTER_LEVELS else 0
    return sorted(keys) + ([_COMPUTER_KEYS[step - 1]] if step else [])


def _look(body: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    look: Dict[str, Any] = {}
    for key, allowed in (("icon", ICONS), ("color", COLORS)):
        if key in body:
            if body[key] not in allowed:
                raise ValueError(f"{key} 不在可选范围内")
            look[key] = body[key]
    for key in ("mentionable", "hoist"):
        if key in body:
            if type(body[key]) is not bool:
                raise ValueError(f"{key} 必须是开或关")
            look[key] = body[key]
    return look or None


def roles(root: str) -> Dict[str, Any]:
    state = MemberRegistry(root).snapshot()
    held = [set(member.get("groups") or []) for member in state["members"].values()]
    rows = []
    for position, group_id in enumerate(state["order"]):
        record, look = state["groups"][group_id], state["groups"][group_id].get("look") or {}
        rows.append({
            "id": group_id, "kind": "everyone" if group_id == EVERYONE else "custom", "name": record["name"],
            "icon": look.get("icon") or ("shield" if group_id == ADMINS else "person"),
            "color": look.get("color") or "graphite", "position": position, "permissions": _keys(record),
            "mentionable": bool(look.get("mentionable")), "hoist": bool(look.get("hoist")),
            "people": len(held) + 1 if group_id == EVERYONE else sum(group_id in groups for groups in held),
            "editable": True, "givable": group_id != EVERYONE, "channelIds": [], "hiddenChannels": 0,
            "agentIds": [],
        })
    return {"roles": rows, "areas": AREAS, "colors": COLORS, "icons": ICONS}


def create_role(root: str, body: Mapping[str, Any]) -> str:
    registry = MemberRegistry(root)
    source: Mapping[str, Any] = {}
    if body.get("copyFrom"):
        source = registry.snapshot()["groups"].get(str(body["copyFrom"])) or {}
        if not source:
            raise LookupError("没有要复制的那个身份组")
    if "permissions" in body:
        items, computer = _split(body["permissions"])
    else:
        items, computer = frozenset(source.get("items") or []), str(source.get("computer") or "none")
    look = {**(source.get("look") or {}), **(_look(body) or {})}
    return registry.create_group(actor(root), body.get("name"), items=items,
                                 computer=computer, act=str(source.get("act") or rules.ACT_DEFAULT), look=look)


def change_role(root: str, role_id: str, body: Mapping[str, Any]) -> None:
    change: Dict[str, Any] = {}
    if "name" in body:
        change["name"] = body["name"]
    if "permissions" in body:
        change["items"], change["computer"] = _split(body["permissions"])
    look = _look(body)
    if look is not None:
        change["look"] = look
    if not change:
        raise ValueError("没有可改的内容")
    MemberRegistry(root).change_group(actor(root), role_id, **change)


def delete_role(root: str, role_id: str) -> None:
    MemberRegistry(root).delete_group(actor(root), role_id)


def reorder_roles(root: str, ids: Any) -> None:
    if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
        raise ValueError("ids 必须是字符串列表")
    MemberRegistry(root).reorder_groups(actor(root), ids)


def members(root: str) -> List[Dict[str, Any]]:
    """所有者，加上在册的成员。成员没有邮箱和头像：登记处不存这些。"""
    state = MemberRegistry(root).snapshot()
    timezone = views.person(root)["timezone"]
    rows = [views.person(root)]
    for member_id, member in sorted(state["members"].items(), key=lambda pair: str(pair[1].get("joined") or "")):
        rows.append({"userId": member_id, "name": str(member.get("name") or member_id), "email": "", "image": None,
                     "role": "member", "roleIds": [group for group in member.get("groups") or []
                                                   if group in state["groups"]],
                     "joinedAt": str(member.get("joined") or ""), "handle": member_id, "timezone": timezone})
    return rows


def set_member_roles(root: str, member_id: str, role_ids: Any) -> List[str]:
    if not isinstance(role_ids, list) or any(not isinstance(item, str) for item in role_ids):
        raise ValueError("roleIds 必须是字符串列表")
    return MemberRegistry(root).set_member_groups(actor(root), member_id, role_ids)


def remove_member(root: str, member_id: str) -> None:
    MemberRegistry(root).remove_member(actor(root), member_id)
