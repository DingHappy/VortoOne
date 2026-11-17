"""把界面的「roles / members」写法对到成员登记处（身份组与成员）。

这里只做翻译：界面的权限键 ↔ 内核的权限项，界面的四个电脑键 ↔ 电脑权限阶梯的一级。能不能改
由登记处交内核判定，这里不判。

操作人由路由从请求里认出来（``web/member_auth.principal``）传进来：过了主人令牌的是所有者，
凭成员会话进来的是那个成员。成员会话只在 ``member_auth.MEMBER_ROUTES`` 列出的接口上算数。

界面上没有的两样东西从这里设不了：办事级别（不能对话/只读/可写/可执行），以及「调整免批范围」
「代成员办事」两个权限项。
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from src.agents import identity_groups as rules
from src.agents.identity_groups import ADMIN, ADMINS, EVERYONE
from src.agents.principal import Principal
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
    for position, group_id in enumerate(state["order"]):
        record, look = state["groups"][group_id], state["groups"][group_id].get("look") or {}
        rows.append({
            "id": group_id, "kind": "everyone" if group_id == EVERYONE else "custom", "name": record["name"],
            "icon": look.get("icon") or ("shield" if group_id == ADMINS else "person"),
            "color": look.get("color") or "graphite", "position": position, "permissions": _keys(record),
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
    if body.get("copyFrom"):
        source = registry.snapshot()["groups"].get(str(body["copyFrom"])) or {}
        if not source:
            raise LookupError("没有要复制的那个身份组")
    if "permissions" in body:
        items, computer = _split(body["permissions"])
    else:
        items, computer = frozenset(source.get("items") or []), str(source.get("computer") or "none")
    look = {**(source.get("look") or {}), **(_look(body) or {})}
    return registry.create_group(actor, body.get("name"), items=items,
                                 computer=computer, act=str(source.get("act") or rules.ACT_DEFAULT), look=look)


def change_role(root: str, actor: Principal, role_id: str, body: Mapping[str, Any]) -> None:
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

def me(root: str, actor: Principal, account: Mapping[str, Any]) -> Dict[str, Any]:
    """登录的成员看到的自己。账号还没加入空间（或已被移除）时没有任何空间。"""
    state = MemberRegistry(root).snapshot()
    joined = actor.id in state["members"]
    return {
        "user": {"id": actor.id if joined else str(account["id"]), "name": str(account["name"]),
                 "email": str(account["email"]), "image": None, "locale": "zh-Hans", "localeAuto": False},
        "organizations": [{"id": views.home_id(root), "name": views.overview(root)["name"], "role": "member",
                           "showActivity": True, "permissions": permission_keys(root, actor)}] if joined else [],
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
    level = initiators.level_on(root, staff_id, actor)
    if level == "none":
        return level
    try:
        if not initiators.plan_allows(root, StaffRegistry(root).member(staff_id)):
            return "none"            # 用所有者个人订阅的 agent，所有者没打开就只回应他
        current = role if role is not None else StaffRegistry(root).role(
            StaffRegistry(root).member(staff_id)["role"])
        initiators.role_for_member(StaffRegistry(root), dict(current), level)
    except (ValueError, OSError, KeyError, TypeError):
        return "none"
    return level


def member_agents(root: str, actor: Principal) -> List[Dict[str, Any]]:
    roles_by_staff = _staff_roles(root)
    shown = []
    for agent in views.agents(root):
        staff_id = agent["handle"]
        if agent["id"] == views.main_agent_id(root) or staff_id not in roles_by_staff:
            continue
        if usable_level(root, staff_id, actor, roles_by_staff[staff_id]) == "none":
            continue
        # 受众里还有谁是所有者的事：成员只看到自己在受众里
        shown.append({**{key: value for key, value in agent.items() if key != "audience"},
                      "seesDetails": False, "audienceMode": "people"})
    return shown


def member_staff(root: str, actor: Principal, agent_id: str) -> Optional[str]:
    """成员要找的这个 agent 是哪个专员；他看不到的返回 None（与「没有这个 agent」不作区分）。"""
    return next((agent["handle"] for agent in member_agents(root, actor) if agent["id"] == agent_id), None)


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


def people(root: str) -> List[Dict[str, Any]]:
    """空间里的人。在线状态没有记录，所有者按在线（服务在跑就是他的），成员按离线。"""
    now = views.now_iso()
    return [{**row, "online": row["role"] == "owner", "presence": "online" if row["role"] == "owner" else "offline",
             "status": None, "lastSeenAt": now if row["role"] == "owner" else None} for row in members(root)]


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
