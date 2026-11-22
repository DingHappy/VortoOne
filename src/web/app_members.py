"""把界面的「roles / members」写法对到成员登记处（身份组与成员）。

这里只做翻译：界面的权限键 ↔ 内核的权限项，界面的四个电脑键 ↔ 电脑权限阶梯的一级。能不能改
由登记处交内核判定，这里不判。

操作人由路由从请求里认出来（``web/member_auth.principal``）传进来：过了主人令牌的是所有者，
凭成员会话进来的是那个成员。成员会话只在 ``member_auth.MEMBER_ROUTES`` 列出的接口上算数。

界面可以管理身份组的办事级别，并通过智能体设置管理「代成员办事」与「成员放行」。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional

from src.agents import identity_groups as rules
from src.agents.identity_groups import ADMIN, ADMINS, EVERYONE
from src.agents.principal import Principal
from src.web import app_views as views
from src.workflow.members.registry import MemberRegistry, Missing, Refused

# 界面的权限键 → 内核的权限项
_KEYS: Dict[str, str] = {
    "post": "chat.speak", "create_channels": "chat.create_channel", "mention_everyone": "chat.mention_everyone",
    "manage_channels": "chat.manage_channels",
    "talk_to_agents": "roles.talk", "view_all_agents": "roles.view_all", "assign_work": "roles.dispatch",
    "manage_routines": "roles.manage_routines", "approve_actions": "roles.approve",
    "create_agents": "roles.create_own", "manage_agents": "roles.manage_all",
    "act_for_members": "roles.act_for_members",
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
              "create_agents", "manage_agents", "act_for_members"]},
    {"id": "tools", "use": ["pair_computers"],
     "keys": ["pair_computers", *_COMPUTER_KEYS, "manage_connectors", "manage_credentials"]},
    {"id": "knowledge", "use": ["edit_wiki", "upload_files"], "keys": ["edit_wiki", "upload_files", "review_memory"]},
    {"id": "org", "use": ["invite_people"],
     "keys": ["invite_people", "view_usage", "view_audit", "configure", "manage_members", "manage_roles",
              "manage_billing"]},
]
COLORS = ["indigo", "violet", "rose", "amber", "green", "teal", "graphite"]
ICONS = ["shield", "code", "wallet", "mail", "bolt", "flag", "book", "chart", "bulb", "globe", "person"]


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


def _act_level(value: Any, field: str = "actLevel") -> str:
    if not isinstance(value, str) or value not in rules.ACT_LEVELS:
        raise ValueError(f"{field} 必须是 none、read、write 或 execute")
    return value


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


def _standing(state: Mapping[str, Any], actor: Principal) -> tuple:
    """（是不是所有者，持有的身份组，身份组表）。不在册的成员什么身份组都没有。"""
    posts = MemberRegistry._posts(state)
    member = state["members"].get(actor.id) if actor.is_member else None
    return actor.is_owner, list((member or {}).get("groups") or []), posts


def permission_keys(root: str, actor: Principal) -> List[str]:
    """这个人在界面上有哪些权限键（界面据此决定显示哪些入口；真正的放行仍在登记处和内核）。"""
    if actor.is_owner:
        return list(views.OWNER_PERMISSIONS)
    state = MemberRegistry(root).snapshot()
    if actor.id not in state["members"]:
        return []
    _owner, held, posts = _standing(state, actor)
    items = rules.permissions_of(held, posts)
    step = rules.COMPUTER_LEVELS.index(rules.computer_level(held, posts))
    return ["read"] + sorted(_ITEMS[item] for item in items if item in _ITEMS) + list(_COMPUTER_KEYS[:step])


def roles(root: str, actor: Principal) -> Dict[str, Any]:
    state = MemberRegistry(root).snapshot()
    owner, mine, posts = _standing(state, actor)
    manages = rules.has("space.manage_posts", mine, posts, owner=owner)
    held = [set(member.get("groups") or []) for member in state["members"].values()]
    rows = []
    edit_act_levels = owner and views.owner_connection()
    for position, group_id in enumerate(state["order"]):
        record, look = state["groups"][group_id], state["groups"][group_id].get("look") or {}
        rows.append({
            "id": group_id, "kind": "everyone" if group_id == EVERYONE else "custom", "name": record["name"],
            "icon": look.get("icon") or ("shield" if group_id == ADMINS else "person"),
            "color": look.get("color") or "graphite", "position": position, "permissions": _keys(record),
            "actLevel": posts[group_id].act, "actLevelEditable": edit_act_levels and group_id != ADMINS,
            "mentionable": bool(look.get("mentionable")), "hoist": bool(look.get("hoist")),
            "people": len(held) + 1 if group_id == EVERYONE else sum(group_id in groups for groups in held),
            "editable": owner or (manages and rules.outranks(mine, group_id, state["order"])),
            "givable": rules.may_grant(mine, group_id, posts, state["order"], owner=owner),
            "channelIds": [], "hiddenChannels": 0,
            "agentIds": [],
        })
    return {"roles": rows, "areas": AREAS, "colors": COLORS, "icons": ICONS}


def create_role(root: str, actor: Principal, body: Mapping[str, Any]) -> str:
    registry = MemberRegistry(root)
    source: Mapping[str, Any] = {}
    act = rules.ACT_DEFAULT
    if body.get("copyFrom"):
        source_id = str(body["copyFrom"])
        state = registry.snapshot()
        source = state["groups"].get(source_id) or {}
        if not source:
            raise LookupError("没有要复制的那个身份组")
        act = registry._posts(state)[source_id].act
    if "permissions" in body:
        items, computer = _split(body["permissions"])
    else:
        items, computer = frozenset(source.get("items") or []), str(source.get("computer") or "none")
    look = {**(source.get("look") or {}), **(_look(body) or {})}
    if "actLevel" in body:
        act = _act_level(body["actLevel"])
    return registry.create_group(actor, body.get("name"), items=items, computer=computer, act=act, look=look)


def change_role(root: str, actor: Principal, role_id: str, body: Mapping[str, Any]) -> None:
    change: Dict[str, Any] = {}
    if "name" in body:
        change["name"] = body["name"]
    if "permissions" in body:
        change["items"], change["computer"] = _split(body["permissions"])
    if "actLevel" in body:
        change["act"] = _act_level(body["actLevel"])
    look = _look(body)
    if look is not None:
        change["look"] = look
    if not change:
        raise ValueError("没有可改的内容")
    MemberRegistry(root).change_group(actor, role_id, **change)


def delete_role(root: str, actor: Principal, role_id: str) -> None:
    MemberRegistry(root).delete_group(actor, role_id)


def reorder_roles(root: str, actor: Principal, ids: Any) -> None:
    if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
        raise ValueError("ids 必须是字符串列表")
    MemberRegistry(root).reorder_groups(actor, ids)


def members(root: str) -> List[Dict[str, Any]]:
    """所有者，加上在册的成员。成员没有邮箱和头像：登记处不存这些。"""
    state = MemberRegistry(root).snapshot()
    timezone = views.person(root)["timezone"]
    rows = [views.person(root)]
    for member_id, member in sorted(state["members"].items(), key=lambda pair: str(pair[1].get("joined") or "")):
        rows.append({"userId": member_id, "name": str(member.get("name") or member_id),
                     "email": str(member.get("email") or ""), "image": None,
                     "role": "member", "roleIds": [group for group in member.get("groups") or []
                                                   if group in state["groups"]],
                     "joinedAt": str(member.get("joined") or ""), "handle": member_id, "timezone": timezone})
    return rows


def set_member_roles(root: str, actor: Principal, member_id: str, role_ids: Any) -> List[str]:
    if not isinstance(role_ids, list) or any(not isinstance(item, str) for item in role_ids):
        raise ValueError("roleIds 必须是字符串列表")
    return MemberRegistry(root).set_member_groups(actor, member_id, role_ids)


def remove_member(root: str, actor: Principal, member_id: str) -> None:
    """移出空间，账号和登录一并作废。先删登记：登记里没有他，他的会话就已经不算成员了。"""
    from src.workflow.ai_apps.store import AiApps
    from src.workflow.members.accounts import MemberAccounts
    MemberRegistry(root).remove_member(actor, member_id)
    MemberAccounts(root).remove_member(member_id)
    AiApps(root).disconnect_person(member_id)        # 他连上来的 AI 应用和个人令牌一并作废


# ── 成员自己看到的「我」──────────────────────────────────────────────────────

def member_auth_key(account: Mapping[str, Any]) -> str:
    """成员账号在界面偏好里的键：跟着账号走，和成员 id、所有者 id 分开。"""
    return "account:" + str(account["id"])


def me(root: str, actor: Principal, account: Mapping[str, Any]) -> Dict[str, Any]:
    """登录的成员看到的自己。账号还没加入空间（或已被移除）时没有任何空间。"""
    state = MemberRegistry(root).snapshot()
    joined = actor.id in state["members"]
    return {
        "user": {"id": actor.id if joined else str(account["id"]), "name": str(account["name"]),
                 "email": str(account["email"]), "image": None,
                 **views.ui_account_preferences(root, member_auth_key(account))},
        "organizations": [{"id": views.home_id(root), "name": views.overview(root)["name"], "role": "member",
                           "showActivity": True, "permissions": permission_keys(root, actor),
                           "memberRoutineProposals": True, "teamChannels": True,
                           "channelCategories": True}] if joined else [],
    }


# ── 邀请 ──────────────────────────────────────────────────────────────────────

def _invitation(invitation_id: str, item: Mapping[str, Any], names: Mapping[str, str], url: Optional[str] = None,
                ) -> Dict[str, Any]:
    return {"id": invitation_id, "email": str(item.get("email") or ""), "role": "member",
            "roleIds": list(item.get("groups") or []), "channelIds": [], "agentIds": [], "status": "pending",
            "expiresAt": str(item.get("expires") or ""), "createdAt": str(item.get("created") or ""),
            "invitedBy": names.get(str(item.get("by") or "")), "url": url}


def _names(root: str, state: Mapping[str, Any]) -> Dict[str, str]:
    names = {member_id: str(member.get("name") or "") for member_id, member in state["members"].items()}
    names[views.owner_id(root)] = views.OWNER_NAME
    return names


def _may_see_invitations(state: Mapping[str, Any], actor: Principal) -> bool:
    owner, held, posts = _standing(state, actor)
    return owner or (actor.id in state["members"] and (rules.has("space.invite", held, posts)
                                                       or rules.has("space.manage_members", held, posts)))


def invitations(root: str, actor: Principal) -> List[Dict[str, Any]]:
    """还没用掉的邀请。能发邀请或管理成员的人才看得到；链接只在发出的那一刻给，这里没有。"""
    from src.workflow.members.registry import Refused
    state = MemberRegistry(root).snapshot()
    if not _may_see_invitations(state, actor):
        raise Refused("没有查看邀请的权限")
    from datetime import datetime, timezone
    names, stamp = _names(root, state), datetime.now(timezone.utc).isoformat(timespec="seconds")   # 与登记处同一写法
    return [_invitation(invitation_id, item, names) for invitation_id, item in state["invitations"].items()
            if str(item.get("expires") or "") > stamp]


def invite(root: str, actor: Principal, body: Mapping[str, Any], origin: str) -> Dict[str, Any]:
    """发一份写明邮箱的邀请，返回邀请和它的链接。链接由发的人自己交给对方——本服务不发信。"""
    role_ids = body.get("roleIds") or []
    if not isinstance(role_ids, list) or any(not isinstance(item, str) for item in role_ids):
        raise ValueError("roleIds 必须是字符串列表")
    if body.get("role") not in (None, "member"):
        raise ValueError("只能邀请成为成员")
    if not isinstance(body.get("email"), str) or not body["email"].strip():
        raise ValueError("要写明对方的邮箱")
    registry = MemberRegistry(root)
    invitation_id, token = registry.invite(actor, groups=role_ids, email=body["email"])
    state = registry.snapshot()
    invitation = _invitation(invitation_id, state["invitations"][invitation_id], _names(root, state),
                             f"{origin}/invite/{token}")
    return {"invitation": invitation, "url": invitation["url"]}


def all_invitations(root: str, actor: Principal) -> List[Dict[str, Any]]:
    """没用掉的邀请，过期的也列出来（status=expired），直到登记处下次清理。"""
    from datetime import datetime, timezone
    state = MemberRegistry(root).snapshot()
    if not _may_see_invitations(state, actor):
        raise Refused("没有查看邀请的权限")
    names, stamp = _names(root, state), datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for invitation_id, item in state["invitations"].items():
        row = _invitation(invitation_id, item, names)
        row.pop("url", None)
        row["status"] = "pending" if str(item.get("expires") or "") > stamp else "expired"
        rows.append(row)
    return sorted(rows, key=lambda row: row["createdAt"], reverse=True)


_EMAIL = re.compile(r"\A[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{1,63}\Z")
MAX_BATCH = 50


def invite_many(root: str, actor: Principal, body: Mapping[str, Any], origin: str) -> List[Dict[str, Any]]:
    """一次邀请几个人：每一份照单发邀请的规矩来。加进频道、预先给 agent 这里没有对应能力，拒绝。"""
    if set(body) - {"emails", "invites", "roleIds", "channelIds", "agentIds", "note"}:
        raise ValueError("不认识的字段")
    if body.get("channelIds") or body.get("agentIds"):
        raise ValueError("邀请时加进频道、预先给 agent 这里还不支持；对方加入后再加")
    shared = body.get("roleIds") or []
    wanted: List[tuple] = [(email, shared) for email in body.get("emails") or []]
    for item in body.get("invites") or []:
        if not isinstance(item, Mapping) or set(item) - {"email", "roleIds"}:
            raise ValueError("invites 的每一项只能有 email 和 roleIds")
        wanted.append((item.get("email"), item.get("roleIds") or shared))
    if not wanted:
        raise ValueError("要写至少一个邮箱")
    if len(wanted) > MAX_BATCH:
        raise ValueError(f"一次最多邀请 {MAX_BATCH} 人")
    members = {str(member.get("email") or "") for member in MemberRegistry(root).snapshot()["members"].values()}
    results, seen = [], set()
    for email, role_ids in wanted:
        address = email.strip().lower() if isinstance(email, str) else ""
        if not _EMAIL.match(address):
            results.append({"email": str(email or ""), "status": "invalid_email"})
            continue
        if address in seen:                                # 同一批里写了两次
            results.append({"email": address, "status": "already_invited"})
            continue
        seen.add(address)
        if address in members:
            results.append({"email": address, "status": "already_member"})
            continue
        try:
            made = invite(root, actor, {"email": address, "roleIds": role_ids}, origin)
        except Refused:
            raise
        except ValueError as error:
            if "已经是成员" in str(error):
                results.append({"email": address, "status": "already_member"})
                continue
            raise
        results.append({"email": address, "status": "invited", "invitationId": made["invitation"]["id"],
                        "url": made["url"]})
    return results


def update_invitation(root: str, actor: Principal, invitation_id: str, body: Mapping[str, Any]) -> Dict[str, Any]:
    if set(body) - {"roleIds", "channelIds", "agentIds"}:
        raise ValueError("不认识的字段")
    if body.get("channelIds") or body.get("agentIds"):
        raise ValueError("邀请时加进频道、预先给 agent 这里还不支持；对方加入后再加")
    registry = MemberRegistry(root)
    if "roleIds" in body:
        role_ids = body["roleIds"]
        if not isinstance(role_ids, list) or any(not isinstance(item, str) for item in role_ids):
            raise ValueError("roleIds 必须是字符串列表")
        groups = registry.update_invitation(actor, invitation_id, role_ids)
    else:
        found = registry.snapshot()["invitations"].get(invitation_id)
        if found is None:
            raise LookupError("没有这份邀请")
        groups = list(found.get("groups") or [])
    return {"roleIds": groups, "channelIds": [], "agentIds": []}


def revoke_invitation(root: str, actor: Principal, invitation_id: str) -> None:
    MemberRegistry(root).revoke_invitation(actor, invitation_id)


def invitation_preview(root: str, token: str) -> Optional[Dict[str, Any]]:
    """收到邀请的人凭令牌看到的：哪个空间、谁邀请的、给哪些身份组、这个邮箱有没有账号。"""
    from src.workflow.members.accounts import MemberAccounts
    registry = MemberRegistry(root)
    item = registry.invitation(token)
    if item is None:
        return None
    state = registry.snapshot()
    email = str(item.get("email") or "")
    exists = bool(email) and MemberAccounts(root).exists(email)
    listed = roles(root, Principal())["roles"]
    return {"orgName": views.overview(root)["name"], "email": email, "role": "member", "kind": "email",
            "invitedBy": _names(root, state).get(str(item.get("by") or "")), "mode": "personal",
            "roles": [{"name": role["name"], "color": role["color"], "icon": role["icon"]} for role in listed
                      if role["id"] in (item.get("groups") or [])],
            "agents": [], "yours": 0, "expiresAt": str(item.get("expires") or ""),
            "account": {"exists": exists, "password": exists, "providers": []}}


def expired_invitation_state(root: str, token: str) -> Optional[Dict[str, Any]]:
    """只显示同一令牌原已可见的工作区和邀请人，帮助对方联系原邀请人。"""
    registry = MemberRegistry(root)
    item = registry.invitation(token, expired=True)
    if item is None:
        return None
    return {"state": "expired", "orgName": views.overview(root)["name"],
            "invitedBy": _names(root, registry.snapshot()).get(str(item.get("by") or "")),
            "at": item["expires"]}


def password_link(root: str, actor: Principal, member_id: str, origin: str) -> Dict[str, str]:
    """给一个成员生成一次性的重置密码链接。只有管得了他的人能生成。"""
    from src.workflow.members.accounts import MemberAccounts
    from src.workflow.members.registry import Refused
    if not MemberRegistry(root).manages(actor, member_id):
        raise Refused("不能重置这个成员的密码")
    accounts = MemberAccounts(root)
    account_id = accounts.account_of(member_id)
    if account_id is None:
        raise LookupError("这个成员没有可重置的账号")
    token, expires = accounts.reset_link(account_id)
    return {"url": f"{origin}/reset-password?token={token}", "expiresAt": expires}


# ── 成员和 agent ──────────────────────────────────────────────────────────────
# 成员看得到的 agent：他对其办事级别不是「不能对话」、且岗位允许成员使用的那些。主助理永远不在里面
# （ADR-019：主助理连着所有者的个人记忆和电脑）。同一个 agent 的私聊，每个成员看到的只有自己发起的事。

def _staff_roles(root: str) -> Dict[str, Any]:
    """agent（专员 id）→ 它登记的岗位。读不出来的跳过：看不清的 agent 不给成员。"""
    from src.workflow.staff.registry import StaffRegistry
    registry = StaffRegistry(root)
    found: Dict[str, Any] = {}
    for person in views._staff(root).get("members") or []:
        staff_id = str(person.get("id") or "")
        if not staff_id or person.get("status") == "retired":
            continue
        try:
            found[staff_id] = registry.role(person.get("role"))
        except (ValueError, OSError):
            continue
    return found


def usable_level(root: str, staff_id: str, actor: Principal, role: Optional[Mapping[str, Any]] = None) -> str:
    """这个成员对这个 agent 实际能用的级别：登记的级别，岗位不许成员用时按「不能对话」。"""
    from src.workflow.staff import initiators
    from src.workflow.staff.registry import StaffRegistry
    registry = StaffRegistry(root)
    try:
        staff = registry.member(staff_id)
        current = dict(role) if role is not None else registry.role(staff["role"])
        level, delegated, _ = initiators.member_authority(root, current, staff, actor)
        initiators.role_for_member(registry, current, level, acts_for_members=delegated)
    except (ValueError, OSError, KeyError, TypeError):
        return "none"
    return level


def member_agents(root: str, actor: Principal) -> List[Dict[str, Any]]:
    roles_by_staff = _staff_roles(root)
    try:
        permissions = permission_keys(root, actor)
    except (ValueError, OSError):
        return []                                     # 成员登记读不出来：任何 agent 都不可见
    shown = []
    for agent in views.agents(root):
        staff_id = agent["handle"]
        if agent["id"] == views.main_agent_id(root) or staff_id not in roles_by_staff:
            continue
        role = roles_by_staff[staff_id]
        level = usable_level(root, staff_id, actor, role)
        if level == "none":
            continue
        grants = role["grants"]
        # 受众里还有谁是所有者的事：成员只看到自己在受众里
        shown.append({**{key: value for key, value in agent.items() if key != "audience"},
                      "seesDetails": False, "audienceMode": "people",
                      "canManageMemberActionLevels": "manage_agents" in permissions,
                      "canManageMemberPolicy": agent.get("supportsMemberPolicy", False) and rules.may_set_act_for_members(
                          level, grants["operations"], holds_permission="act_for_members" in permissions,
                          private=bool(grants["sees"] or grants["project"]), owner=False)})
    return shown


def member_staff(root: str, actor: Principal, agent_id: str) -> Optional[str]:
    """成员要找的这个 agent 是哪个专员；他看不到的返回 None（与「没有这个 agent」不作区分）。"""
    return next((agent["handle"] for agent in member_agents(root, actor) if agent["id"] == agent_id), None)


def require_agent_screen_level(root: str, actor: Principal, agent_id: str, surface: str) -> None:
    """Check role visibility and clearance before looking up a live screen binding."""
    agents = views.agents(root) if actor.is_owner else member_agents(root, actor)
    agent = next((row for row in agents if row["id"] == agent_id), None)
    if agent is None:
        raise Missing("没有这个角色")
    level = rules.COMPUTER_LEVELS[-1] if actor.is_owner else MemberRegistry(root).standing(actor.id)["computer"]
    if not rules.screen_level_allows(level, surface, own=agent.get("ownerUserId") == actor.id):
        raise Refused("当前身份组没有这个角色的屏幕权限")


def agent_member_action_levels(root: str, actor: Principal, staff_id: str) -> Dict[str, Any]:
    from src.workflow.staff.agent_lifecycle import member_settings_agent
    member_settings_agent(root, staff_id)
    return MemberRegistry(root).agent_member_action_levels(actor, staff_id)


def set_agent_member_action_level(root: str, actor: Principal, staff_id: str, member_id: str,
                                  body: Mapping[str, Any]) -> None:
    from src.workflow.staff.agent_lifecycle import set_member_action_level

    if "level" not in body:
        raise ValueError("缺少 level")
    level = None if body["level"] is None else _act_level(body["level"], "level")
    set_member_action_level(root, actor, staff_id, member_id, level)


def agent_member_policy(root: str, actor: Principal, staff_id: str,
                        change: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    from src.workflow.staff.agent_lifecycle import member_policy

    return member_policy(root, actor, staff_id, dict(change) if change is not None else None)


def member_agent_detail(root: str, actor: Principal, agent_id: str) -> Optional[Dict[str, Any]]:
    """成员看到的 agent 资料：没有用量、运行次数这些所有者的数字。"""
    agent = next((row for row in member_agents(root, actor) if row["id"] == agent_id), None)
    if agent is None:
        return None
    return {**{key: agent[key] for key in ("id", "name", "handle", "role", "instructions", "model",
                                           "ownerUserId", "createdAt", "audienceMode", "canTalk", "seesDetails")},
            "credentialId": None, "budgetUsedPercent": 0, "tokensThisMonth": 0, "runsThisMonth": 0,
            "failuresThisMonth": 0, "runsByDay": [], "pendingApprovals": 0, "channels": []}


def member_dms(root: str, actor: Principal) -> List[Dict[str, Any]]:
    return [{"channelId": views.dm_channel_id(agent["id"]),
             "with": {"type": "agent", "id": agent["id"], "name": agent["name"], "handle": agent["handle"]},
             "openedAt": agent["createdAt"], "lastMessage": None, "unread": 0}
            for agent in member_agents(root, actor)]


def member_overview(root: str, actor: Principal) -> Dict[str, Any]:
    return {"name": views.overview(root)["name"], "role": "member", "people": len(members(root)),
            "agents": len(member_agents(root, actor)), "budgetUsedPercent": 0, "approvalsWaitingForMe": 0,
            "proposalsWaitingForMe": 0}


def _person_status(row: Dict[str, Any], prefs: Optional[Dict[str, Any]], *, own: bool,
                   connected: bool) -> Dict[str, Any]:
    from src.web import app_prefs

    availability = (prefs or {}).get("availability")
    if availability not in app_prefs.AVAILABILITIES:
        availability = "auto"
    if not own and (prefs is None or availability == "hidden"):
        presence, status = "offline", None
    else:
        status = app_prefs.status_line(prefs or {})
        presence = ("online" if availability == "auto" else availability) if own or connected else "offline"
    view = {**row, "online": presence in {"online", "away", "busy"}, "presence": presence,
            "status": status, "lastSeenAt": None}
    if own:
        view["availability"] = availability
    return view


def people(root: str, viewer: Principal) -> List[Dict[str, Any]]:
    """给在册成员看状态。隐身者的可用状态和文字仅本人可见。"""
    from src.web import app_prefs, app_presence

    connected = app_presence.active_people(root)
    try:
        saved = app_prefs.status_preferences(root)
    except ValueError:
        # 文件损坏时不能确认谁设了隐身。登录照常，公开状态从严。
        saved = None
    return [_person_status(row, saved.get(row["userId"], {}) if saved is not None else None,
                           own=row["userId"] == viewer.id, connected=row["userId"] in connected)
            for row in members(root)]


def set_person_status(root: str, actor: Principal, body: Dict[str, Any]) -> Dict[str, Any]:
    from src.web import app_prefs

    row = views.person(root) if actor.is_owner else next(
        (person for person in members(root) if person["userId"] == actor.id), None)
    if row is None:
        raise Refused("还不是这个空间的成员")
    saved = app_prefs.set_status(root, actor.id, body)
    return _person_status(row, saved, own=True, connected=True)


def team_directory(root: str, actor: Principal, *, activity: bool, main_running: bool) -> Dict[str, Any]:
    """团队页沿用人员和可见 agent 名册，只投影团队列表需要的字段。"""
    agents = views.agents(root) if actor.is_owner else member_agents(root, actor)
    running = views.running_staff(root, None if actor.is_owner else actor.id) if activity else set()
    main = views.main_agent_id(root)
    return {
        "agents": [
            {**{key: agent[key] for key in ("id", "name", "handle", "role", "ownerUserId", "canTalk", "seesDetails")},
             "appearance": agent.get("appearance"), "capabilities": [],
             "busy": main_running if agent["id"] == main else agent["handle"] in running}
            for agent in agents
        ],
        "people": [{"id": row["userId"], "name": row["name"], "image": row["image"]} for row in members(root)],
        # 当前没有在这个家内登记发给其他 agent 的咨询，也没有频道或汇报关系。
        "consultations": [],
    }


def member_send(root: str, actor: Principal, staff_id: str, text: str, client_id: Optional[str]) -> Dict[str, Any]:
    """成员在私聊里交给 agent 一件事。记下发起人；执行时按他的级别收窄，读以外的每一步都要所有者批准。"""
    task = member_send_task(root, actor, staff_id, text)
    return views.member_owner_message(root, staff_id, task, client_id, viewer=actor.id)


def member_send_task(root: str, actor: Principal, staff_id: str, text: str,
                     via: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """member_send 的本体，返回任务。via：经哪个 AI 应用发来（/mcp 认出成员的连接时）。"""
    from src.gateway.audit import append_audit
    from src.gateway.main_conversation import main_chat_key
    from src.scheduling.staff_runner import get_staff_service
    from src.workflow.members.registry import Refused
    from src.workflow.staff.conversations import StaffConversations
    import uuid
    if usable_level(root, staff_id, actor) == "none":
        raise Refused("你不能和这个 agent 对话")
    if len(text) > 4000:
        raise ValueError("交给 agent 的一件事最多 4000 字")
    owner = main_chat_key(root)
    if not owner:
        raise ValueError("所有者还没有打开主要聊天，agent 现在不能接活")
    service = get_staff_service(root)
    task_id = StaffConversations.request_task_id(staff_id, f"{actor.id}:{uuid.uuid4().hex}")
    if not append_audit(root, {"category": "decision", "decision": "allowed", "decision_source": "member",
                               "operation": "成员交给 agent 处理任务", "operation_kind": "execute",
                               "staff_task": task_id, "initiator": actor.id, "agent": staff_id,
                               **({"via": via.get("name"), "grant": via.get("grant")} if via else {})}, durable=True):
        raise OSError("审计未能保存，没有派出")
    task = service.assign(owner, {"member": staff_id, "prompt": text, "acceptance": [], "delivery": "notify"},
                          task_id=task_id, initiator=actor.id, via=via)
    return task.to_dict()
