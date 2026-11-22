"""新版 Desktop 界面读取的数据：把一个家的现有记录整理成界面要的「空间 / 角色 / 私聊 / 消息」。

这里只读、只整理，不做任何判定：确认、权限、污点和预算仍由各自的内核与领域服务决定。
一个家目前只对应一个空间、一位主人；多人和多空间的归属见 docs/HOME_AND_MEMBERS_DESIGN.md，尚未实现。

标识都由现有记录确定地推出（uuid5），不另存一份对照表：同一个家、同一个专员每次得到相同的 id。
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlsplit

_NAMESPACE = uuid.UUID("6f1d2a57-6f0e-4f0b-9c7e-0a7e5c0f1a11")

# 一位主人在自己家里拥有的全部界面权限（界面按这些键决定显示哪些入口；真正的放行仍在内核）。
OWNER_PERMISSIONS = [
    "read", "manage_owners", "administrator", "approve_actions", "manage_agents", "control_computers",
    "control_browsers", "manage_connectors", "manage_credentials", "manage_members", "manage_roles",
    "view_usage", "configure", "view_audit", "manage_billing", "create_agents", "manage_channels",
    "mention_everyone", "manage_routines", "review_memory", "invite_people", "pair_computers",
    "own_computers", "own_browsers", "talk_to_agents", "view_all_agents", "assign_work", "post",
    "create_channels", "edit_wiki", "upload_files", "act_for_members",
]


MAIN_AGENT_HANDLE = "assistant"
OWNER_NAME = "我"


def _id(*parts: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, ":".join(parts)))


def _iso(value: Any = None) -> str:
    """界面要带毫秒的 UTC 时间；读不出来的记录用纪元起点，不拿「现在」冒充。"""
    moment: Optional[datetime] = None
    if isinstance(value, (int, float)):
        moment = datetime.fromtimestamp(float(value), timezone.utc)
    elif isinstance(value, str) and value:
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            moment = None
    if moment is None:
        moment = datetime.fromtimestamp(0, timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def now_iso() -> str:
    return _iso(datetime.now(timezone.utc).timestamp())


def home_id(root: str) -> str:
    return _id("home", os.path.realpath(root))


def owner_id(root: str) -> str:
    return _id("owner", os.path.realpath(root))


def main_agent_id(root: str) -> str:
    return _id("agent", "main", os.path.realpath(root))


def member_agent_id(root: str, member_id: str) -> str:
    return _id("agent", "member", os.path.realpath(root), member_id)


def dm_channel_id(agent_id: str) -> str:
    return _id("dm", agent_id)


def _profile(root: str) -> Dict[str, str]:
    from src.memory import assistant_profile
    return assistant_profile.load_profile(root)


def _home_created(root: str) -> str:
    try:
        return _iso(os.stat(root).st_birthtime)  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        try:
            return _iso(os.stat(root).st_ctime)
        except OSError:
            return _iso()


def model_connected() -> bool:
    """与主会话入口同一口径（routers/realtime.handle_agent_message）：没有模型凭据就没法回复。"""
    return bool(os.getenv("OPENAI_API_KEY"))


def _staff(root: str) -> Dict[str, Any]:
    from src.workflow.staff.service import staff_directory
    try:
        return staff_directory(root)
    except (OSError, ValueError):
        # 名册读不出来时只显示主助理；不把读取失败说成「没有专员」之外的任何状态。
        return {"roles": [], "members": []}


def agents(root: str) -> List[Dict[str, Any]]:
    """主助理加未退役的专员。暂停中的专员不能对话。"""
    from src.workflow.staff.initiators import member_policy_supported

    profile = _profile(root)
    owner = owner_id(root)
    created = _home_created(root)
    rows: List[Dict[str, Any]] = [{
        "id": main_agent_id(root), "name": profile["name"], "handle": MAIN_AGENT_HANDLE, "role": "主助理",
        "instructions": profile.get("soul", ""), "model": "", "ownerUserId": owner,
        "hasCredential": model_connected(), "createdAt": created, "audienceMode": "everyone",
        "canTalk": True, "seesDetails": True,
    }]
    staff = _staff(root)
    titles = {str(role.get("id")): str(role.get("title") or role.get("id")) for role in staff.get("roles") or []}
    briefs = {str(role.get("id")): str(role.get("brief") or "") for role in staff.get("roles") or []}
    roles_by_id = {role["id"]: role for role in staff.get("roles") or []}
    profiles, audiences = _agent_extras(root)
    can_manage_levels = owner_connection()
    for member in staff.get("members") or []:
        if member.get("status") == "retired":
            continue
        member_id = str(member.get("id") or "")
        if not member_id:
            continue
        extra = profiles.get(member_id) or {}
        if extra.get("archived") or extra.get("deleted"):
            continue                                    # 归档的、删掉的不在团队里（former-agents 里还有它）
        audience = audiences.get(member_id) or {"mode": "everyone", "userIds": [], "roleIds": []}
        role = roles_by_id.get(member.get("role"))
        supports_member_policy = role is not None and member_policy_supported(role, member)
        row = {
            "id": member_agent_id(root, member_id), "name": str(member.get("name") or member_id),
            "handle": member_id, "role": titles.get(str(member.get("role")), str(member.get("role") or "")),
            "instructions": briefs.get(str(member.get("role")), ""),    # 岗位登记的职责说明
            "model": str(member.get("model") or ""), "ownerUserId": owner,
            "hasCredential": model_connected(), "createdAt": str(extra.get("created") or created),
            "audience": {**audience, "consultable": False}, "audienceMode": audience["mode"],
            "canManageMemberActionLevels": can_manage_levels,
            "supportsMemberPolicy": supports_member_policy, "canManageMemberPolicy": can_manage_levels and supports_member_policy,
            "canTalk": member.get("status", "active") == "active", "seesDetails": True,
        }
        if extra.get("appearance"):
            row["appearance"] = dict(extra["appearance"])
        setup = extra.get("setup")
        if isinstance(setup, dict):
            row["setup"] = {"role": setup.get("role"), "done": list(setup.get("confirmed") or []),
                            **{key: setup[key] for key in ("hello", "starter", "apps") if key in setup}}
        rows.append(row)
    return rows


def _agent_extras(root: str):
    """agent 的样子（AgentProfiles）和谁能和它对话（成员登记处）。读不出来就当没有：样子缺了只是显示默认，
    受众缺了这里显示「所有人」——真正放不放行由内核按登记处判定，读不出登记处时成员一律进不来。"""
    from src.workflow.members.registry import MemberRegistry
    from src.workflow.staff.agent_profiles import AgentProfiles
    try:
        profiles = AgentProfiles(root).all()
    except (OSError, ValueError):
        profiles = {}
    try:
        audiences = MemberRegistry(root).snapshot().get("agent_audiences") or {}
    except (OSError, ValueError):
        audiences = {}
    return profiles, audiences


def former_agents(root: str) -> List[Dict[str, Any]]:
    """不在团队里的 agent：归档的（还能恢复）和删掉的。只给名字、角色和样子，留给它们留下的消息显示用。"""
    staff = _staff(root)
    titles = {str(role.get("id")): str(role.get("title") or role.get("id")) for role in staff.get("roles") or []}
    profiles, _ = _agent_extras(root)
    rows = []
    for member in staff.get("members") or []:
        member_id = str(member.get("id") or "")
        extra = profiles.get(member_id) or {}
        deleted = member.get("status") == "retired" or bool(extra.get("deleted"))
        if not member_id or not (deleted or extra.get("archived")):
            continue
        rows.append({"id": member_agent_id(root, member_id), "name": str(member.get("name") or member_id),
                     "role": titles.get(str(member.get("role")), str(member.get("role") or "")),
                     "appearance": dict(extra["appearance"]) if extra.get("appearance") else None,
                     "deleted": deleted})
    return rows


def agent_statuses(root: str) -> List[Dict[str, Any]]:
    staff = {member_agent_id(root, str(m.get("id"))): m for m in _staff(root).get("members") or []}
    now = now_iso()
    rows = []
    for agent in agents(root):
        member = staff.get(agent["id"])
        online = member is None or member.get("status", "active") == "active"
        rows.append({"agentId": agent["id"], "presence": "online" if online else "offline",
                     "status": None, "activeAt": now if online else None})
    return rows


def ai_status(root: str) -> Dict[str, Any]:
    ready = model_connected()
    state = {"state": "ready"} if ready else {"state": "no_ai", "reason": "no_key"}
    return {"any": ready, "apiKey": ready, "agents": [{"agentId": a["id"], **state} for a in agents(root)]}


def _owner_card(root: str) -> Dict[str, str]:
    """所有者显示的名字和邮箱：认领时填的；只用令牌的老家照旧叫「我」、没有邮箱。"""
    from src.workflow.members.owner_account import OwnerAccount
    found = OwnerAccount(root).profile()
    return {"name": (found or {}).get("name") or OWNER_NAME, "email": (found or {}).get("email") or ""}


def me(root: str) -> Dict[str, Any]:
    card = _owner_card(root)
    return {
        "user": {"id": owner_id(root), "name": card["name"], "email": card["email"], "image": None,
                 **ui_locale(root, owner_id(root))},
        "organizations": [{"id": home_id(root), "name": _profile(root)["name"], "role": "owner",
                           "showActivity": True, "permissions": list(OWNER_PERMISSIONS),
                           "routineProposalReview": owner_connection(), "teamChannels": owner_connection()}],
    }


def ui_locale(root: str, account_key: str) -> Dict[str, Any]:
    from src.web import app_prefs
    try:
        return app_prefs.locale(root, account_key)
    except ValueError:
        # 偏好文件坏了不挡登录：语言回到默认，改的时候再如实报错
        return {"locale": app_prefs.DEFAULT_LOCALE, "localeAuto": False}


def person(root: str) -> Dict[str, Any]:
    card = _owner_card(root)
    return {"userId": owner_id(root), "name": card["name"], "email": card["email"], "image": None, "role": "owner",
            "joinedAt": _home_created(root), "handle": "owner", "timezone": _timezone(root)}


def _timezone(root: str) -> str:
    return str(_staff(root).get("routine_timezone") or "")


def overview(root: str) -> Dict[str, Any]:
    return {"name": _profile(root)["name"], "role": "owner", "people": 1, "agents": len(agents(root)),
            "budgetUsedPercent": 0, "approvalsWaitingForMe": 0, "proposalsWaitingForMe": 0}


def settings(root: str) -> Dict[str, Any]:
    return {"name": _profile(root)["name"], "context": "", "defaultCredentialId": None, "routerModel": None,
            "summaryModel": None, "timezone": _timezone(root), "actionCheck": True, "autoModels": {},
            "teamWatch": False, "welcomePeople": False, "gifs": False, "gifRating": "g",
            "agentStickers": False, "agentMedia": "never", "voiceMessages": False, "voiceAvailable": False,
            "gifsAvailable": False, "agentMediaAvailable": False, "mode": "personal",
            "defaultAgentBudgetUsd": None}


# ── 私聊与消息 ────────────────────────────────────────────────────────────────

def main_chat_key(root: str) -> Optional[str]:
    from src.gateway.main_conversation import main_chat_key as registered
    return registered(root)


def _main_view(root: str) -> Dict[str, Any]:
    """主要聊天的记录与排队中的输入。会话在内存里就读内存（回合进行中存盘会落后），否则读存盘。"""
    key = main_chat_key(root)
    if not key:
        return {"transcript": [], "queued": []}
    from src.web.routers.realtime import live_session_view
    live = live_session_view(key)
    if live is not None:
        return live
    from src.gateway.session_store import load_session
    data = load_session(root, key) or {}
    return {"transcript": [item for item in data.get("transcript") or [] if isinstance(item, dict)],
            "queued": [item for item in data.get("prompt_queue") or [] if isinstance(item, dict)]}


def message_id(rid: str, role: str, ordinal: int = 0) -> str:
    """带回合号的消息：id 只由回合、角色和回合内序号决定，发消息时就能给出，之后读到的还是它。"""
    return _id("message", "turn", rid, role, str(ordinal))


def _message_ids(items: List[Dict[str, Any]]) -> List[str]:
    """对话记录本身没有消息标识，这里推出一个。有回合号的按回合推；
    更早没有回合号的记录按角色、时间、内容和同组序号推。记录从前面裁剪时留下的消息 id 不变
    （除非被裁的正好是同一回合的前一条）。服务端存储给每条消息真正的标识后应改用那个。"""
    seen: Dict[tuple, int] = {}
    ids = []
    for item in items:
        rid, role = str(item.get("rid") or ""), str(item.get("role") or "")
        if rid:
            group: tuple = (rid, role)
            ordinal = seen.get(group, 0)
            ids.append(message_id(rid, role, ordinal))
        else:
            text = hashlib.sha256(str(item.get("text") or "").encode("utf-8", "surrogatepass")).hexdigest()[:16]
            group = (role, str(item.get("at") or ""), text)
            ordinal = seen.get(group, 0)
            ids.append(_id("message", *group, str(ordinal)))
        seen[group] = ordinal + 1
    return ids


def _message(root: str, message_id_value: str, *, from_owner: bool, body: str, created: str,
             run_id: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    agent = main_agent_id(root)
    return {"id": message_id_value, "channelId": dm_channel_id(agent), "threadId": None,
            "author": {"type": "person" if from_owner else "agent", "id": owner_id(root) if from_owner else agent},
            "body": body, "mentionedAgentIds": [], "agentRunId": None if from_owner else run_id,
            "createdAt": created, **extra}


def owner_message(root: str, rid: str, body: str, *, client_id: Optional[str] = None) -> Dict[str, Any]:
    """主人刚发出的一条：回合还没开始记录时先用它应答，id 与之后记录里读到的一致。"""
    extra = {"clientId": client_id} if client_id else {}
    return _message(root, message_id(rid, "user"), from_owner=True, body=body, created=now_iso(), **extra)


def main_messages(root: str, *, before: str = "", limit: int = 100) -> List[Dict[str, Any]]:
    """主要聊天的记录，按时间从早到晚；排在队列里还没开始的输入接在最后。before 是界面翻页用的时间。"""
    view = _main_view(root)
    items = view["transcript"]
    from src.gateway.transcript_order import transcript_sequences
    sequences = transcript_sequences(items)
    rows = [_message(root, mid, from_owner=item.get("role") == "user", body=str(item.get("text") or ""),
                     created=_iso(item.get("at")), run_id=str(item.get("rid") or "") or None, sequence=sequence)
            for item, mid, sequence in zip(items, _message_ids(items), sequences)]
    known = {row["id"] for row in rows}
    for queued in view.get("queued") or []:
        rid = str(queued.get("rid") or queued.get("id") or "")
        if rid and message_id(rid, "user") not in known:
            rows.append(_message(root, message_id(rid, "user"), from_owner=True,
                                 body=str(queued.get("text") or ""), created=_iso(queued.get("created_at")),
                                 sequence=(rows[-1]["sequence"] + 1 if rows else 1)))
    if before:
        cutoff = _iso(before)
        rows = [row for row in rows if row["createdAt"] < cutoff]
    return rows[-max(1, min(int(limit), 400)):]


# ── 确认 → 审批 ───────────────────────────────────────────────────────────────

def approval_card_id(confirm_id: str) -> str:
    return _id("approval-card", confirm_id)


def approval_card(root: str, pending: Dict[str, Any]) -> Dict[str, Any]:
    """对话里承载这次审批的那条消息：正文为空，界面按 meta.approvalId 画卡片。只在等待期间存在。"""
    return _message(root, approval_card_id(str(pending["id"])), from_owner=False, body="",
                    created=_iso(pending.get("created")), run_id=pending.get("rid"),
                    meta={"approvalId": str(pending["id"])})


CONFIRM_SECONDS = 300  # 与 routers/realtime._make_ws_confirm 的等待上限一致


def _website_login(pending: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Project the original tool's site identity for an approval tied to a run."""
    website = pending.get("website")
    if (not pending.get("rid") or pending.get("operation_kind") != "interact"
            or not isinstance(website, dict)
            or set(website) != {"version", "action", "origin", "account_label", "device_mode"}
            or type(website["version"]) is not int or website["version"] != 1 or website["action"] != "login"):
        return None
    origin, label, mode = website["origin"], website["account_label"], website["device_mode"]
    if (mode not in ("desktop", "mobile") or not isinstance(origin, str) or not 1 <= len(origin) <= 512
            or "\\" in origin or any(ord(char) <= 32 or ord(char) == 127 for char in origin)
            or not isinstance(label, str) or not label.strip() or len(label) > 80
            or any(ord(char) < 32 or ord(char) == 127 for char in label)):
        return None
    try:
        url = urlsplit(origin)
        if (url.scheme not in ("http", "https") or origin != f"{url.scheme}://{url.netloc}"
                or not url.hostname or url.username is not None or url.password is not None or url.port == 0):
            return None
    except ValueError:
        return None
    return {"origin": origin, "accountLabel": label, "deviceMode": mode,
            **({"pairedBrowser": True} if pending.get("paired_browser") is True else {}),
            **({"canPreview": True, "livePreview": True} if pending.get("website_preview") is True else {})}


def approval(root: str, pending: Dict[str, Any], *, status: str = "pending",
             decided: bool = False) -> Dict[str, Any]:
    """内核发出的一次确认，整理成界面的审批。外部内容的警示放进正文，不只藏在标记里。"""
    from datetime import timedelta
    text = str(pending.get("text") or "")
    created = _iso(pending.get("created"))
    expires = datetime.fromisoformat(created.replace("Z", "+00:00")) + timedelta(seconds=CONFIRM_SECONDS)
    detail = text
    if pending.get("tainted"):
        detail = "⚠️ 本回合读取过外部内容（网页、搜索或外部工具），请确认这是你自己要做的事。\n\n" + text
    website = _website_login(pending)
    website_title = "配对电脑网页核对" if website and website.get("pairedBrowser") else "专用浏览器登录核对"
    title = website_title if website else next(
        (line.strip() for line in text.splitlines() if line.strip()), "需要你确认")[:120]
    return {"id": str(pending["id"]), "runId": pending.get("rid"), "agentId": main_agent_id(root),
            "channelId": dm_channel_id(main_agent_id(root)), "threadId": None,
            "messageId": approval_card_id(str(pending["id"])),
            "tool": str(pending.get("operation_kind") or ""), "scope": str(pending.get("operation_kind") or ""),
            "title": title, "detail": detail, "params": {}, "status": status,
            "expiresAt": _iso(expires.timestamp()), "createdAt": created,
            "decidedByUserId": owner_id(root) if decided else None,
            "decidedAt": now_iso() if status != "pending" else None, "reason": None,
            "canDecide": status == "pending", **({"websiteLogin": website} if website else {})}


def main_dm(root: str) -> Dict[str, Any]:
    agent = agents(root)[0]
    messages = main_messages(root, limit=1)
    last = messages[-1] if messages else None
    return {
        "channelId": dm_channel_id(agent["id"]),
        "with": {"type": "agent", "id": agent["id"], "name": agent["name"], "handle": agent["handle"]},
        "openedAt": agent["createdAt"],
        "lastMessage": None if last is None else {
            "id": last["id"], "body": last["body"][:400], "createdAt": last["createdAt"],
            "authorType": last["author"]["type"]},
        "unread": 0,
    }


def is_main_dm(root: str, channel_id: str) -> bool:
    return channel_id == dm_channel_id(main_agent_id(root))


def channel_members(root: str) -> List[Dict[str, str]]:
    return [{"type": "person", "id": owner_id(root)}, {"type": "agent", "id": main_agent_id(root)}]


def agent_detail(root: str, agent: Dict[str, Any]) -> Dict[str, Any]:
    """角色资料页。本月的 token 与件数来自账本和台账；预算比例没有对应的月度预算，固定为 0。"""
    return {**{key: agent[key] for key in ("id", "name", "handle", "role", "instructions", "model",
                                           "ownerUserId", "createdAt", "audienceMode", "canTalk",
                                           "seesDetails")},
            "credentialId": None, "budgetUsedPercent": 0, **agent_activity(root, agent["id"]),
            "pendingApprovals": 0, "channels": []}


# ── 与专员的私聊：现有派活记录的另一种看法 ────────────────────────────────────
# 主人在这里发的每一条，都是交给这位专员的一件事（StaffConversations.submit）；
# 没有第二份对话记录，读到的消息全部由任务台账整理出来。

ACTIVE_TASK = {"queued", "running", "blocked"}


def member_for_channel(root: str, channel_id: str) -> Optional[str]:
    for member in _staff(root).get("members") or []:
        member_id = str(member.get("id") or "")
        if member_id and member.get("status") != "retired" \
                and dm_channel_id(member_agent_id(root, member_id)) == channel_id:
            return member_id
    return None


def member_conversation(root: str, member_id: str, viewer: Optional[str] = None) -> Dict[str, Any]:
    """viewer：看的人。None 是所有者，只看所有者自己交代的事；成员 id 只看那个成员发起的事。
    同一个 agent 的私聊，每个人看到的是自己和它之间的那一份。"""
    from src.workflow.staff.conversations import StaffConversations
    return StaffConversations(root).read(member_id, limit=50, initiator=viewer)


def _member_message(root: str, member_id: str, mid: str, *, from_owner: bool, body: str, created: str,
                    task_id: str, viewer: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    agent = member_agent_id(root, member_id)
    person = viewer or owner_id(root)
    return {"id": mid, "channelId": dm_channel_id(agent), "threadId": None,
            "author": {"type": "person" if from_owner else "agent", "id": person if from_owner else agent},
            "body": body, "mentionedAgentIds": [], "agentRunId": None if from_owner else task_id,
            "createdAt": created, **extra}


def member_owner_message(root: str, member_id: str, task: Dict[str, Any],
                         client_id: Optional[str] = None, viewer: Optional[str] = None) -> Dict[str, Any]:
    extra = {"clientId": client_id} if client_id else {}
    return _member_message(root, member_id, message_id(task["id"], "user"), from_owner=True,
                           body=str(task.get("prompt") or ""), created=_iso(task.get("created")),
                           task_id=task["id"], viewer=viewer, **extra)


def member_messages(root: str, member_id: str, conversation: Optional[Dict[str, Any]] = None, *,
                    before: str = "", limit: int = 100, viewer: Optional[str] = None) -> List[Dict[str, Any]]:
    """每件事两条：交代的话；做完（或没做成）后 agent 交回的结果。进行中的只有前一条。"""
    conversation = conversation or member_conversation(root, member_id, viewer)
    rows: List[Dict[str, Any]] = []
    for task in reversed(conversation["tasks"]):          # 台账给的是新的在前
        rows.append(member_owner_message(root, member_id, task, viewer=viewer))
        status = str(task.get("status") or "")
        if status in ACTIVE_TASK:
            continue
        if status == "done":
            body = str(task.get("result") or "").strip() or "已完成，没有交回文字说明。"
        else:
            reason = str(task.get("error") or "").strip()
            body = "这件事没有完成" + (f"：{reason}" if reason else f"（状态：{status or '未知'}）。")
        rows.append(_member_message(root, member_id, message_id(task["id"], "assistant"), from_owner=False,
                                    body=body, created=_iso(task.get("updated") or task.get("created")),
                                    task_id=task["id"], viewer=viewer))
    if before:
        cutoff = _iso(before)
        rows = [row for row in rows if row["createdAt"] < cutoff]
    return rows[-max(1, min(int(limit), 400)):]


def member_runs(root: str, member_id: str, conversation: Optional[Dict[str, Any]] = None,
                viewer: Optional[str] = None) -> List[Dict[str, Any]]:
    conversation = conversation or member_conversation(root, member_id, viewer)
    agent = member_agent_id(root, member_id)
    return [{"runId": task["id"], "agentId": agent, "channelId": dm_channel_id(agent),
             "threadId": message_id(task["id"], "user"), "triggerMessageId": message_id(task["id"], "user"),
             "status": "running" if task.get("status") == "running" else "queued",
             "startedAt": _iso(task.get("created")), "createdAt": _iso(task.get("created"))}
            for task in conversation["tasks"] if task.get("status") in ACTIVE_TASK]


def member_dm(root: str, member_id: str) -> Dict[str, Any]:
    agent = next(a for a in agents(root) if a["id"] == member_agent_id(root, member_id))
    return {"channelId": dm_channel_id(agent["id"]),
            "with": {"type": "agent", "id": agent["id"], "name": agent["name"], "handle": agent["handle"]},
            "openedAt": agent["createdAt"], "lastMessage": None, "unread": 0}


# ── 记忆、例行工作、技能、制品：现有记录的只读整理 ─────────────────────────────

def _memory(agent_id: str, row: Dict[str, Any], archived: bool) -> Dict[str, Any]:
    created = _iso(row.get("created_at"))
    return {"id": str(row["id"]), "agentId": agent_id, "kind": "fact", "text": str(row.get("content") or ""),
            "source": "", "uses": 0, "lastUsedAt": None, "pinned": False, "archived": archived,
            "lowTrust": False, "verified": True, "supersedes": None, "editedByUserId": None,
            "updatedAt": created, "removable": False}


def memories(root: str, agent_id: str) -> List[Dict[str, Any]]:
    """主助理记住的关于主人的事，连同主人归档的（归档的助理读不到）。记忆库不分类型也不记使用次数：
    类型统一报「事实」，次数为 0。专员没有自己的长期记忆。"""
    if agent_id != main_agent_id(root):
        return []
    from src.memory import assistant_profile
    return ([_memory(agent_id, row, False) for row in assistant_profile.list_memories(root)]
            + [_memory(agent_id, row, True) for row in assistant_profile.list_archived_memories(root)])


def set_memory_archived(root: str, memory_id: str, archived: bool) -> Optional[Dict[str, Any]]:
    """归档或恢复；没有这条（或它已经在目标一侧之外）返回 None。"""
    from src.memory import assistant_profile
    agent = main_agent_id(root)
    current = next((m for m in memories(root, agent) if m["id"] == memory_id), None)
    if current is None:
        return None
    if current["archived"] != archived and not assistant_profile.archive_memory(root, memory_id, archived):
        return None
    return {**current, "archived": archived}


def _trigger(expression: str, timezone_name: str) -> Dict[str, Any]:
    """后端的排期写法：`once <时间>`、`at HH:MM`、`every …` 或 cron。说明文字就用原写法，不另编一句。"""
    if expression.startswith("once "):
        return {"type": "once", "at": _iso(expression[5:].strip()), "timezone": timezone_name,
                "description": expression}
    cron = expression
    if expression.startswith("at ") and ":" in expression:
        hour, _, minute = expression[3:].strip().partition(":")
        cron = f"{int(minute)} {int(hour)} * * *" if hour.isdigit() and minute.isdigit() else ""
    elif expression.startswith("every "):
        cron = ""
    return {"type": "schedule", "phrase": expression, "rule": None, "cron": cron, "timezone": timezone_name,
            "description": expression}


def routines(root: str) -> List[Dict[str, Any]]:
    """已登记、未删除的例行工作，带上任务台账里它最近的运行。"""
    from datetime import timezone as tz
    from src.scheduling.routine_schedule import RoutineSchedule
    from src.workflow.staff.routines import Routines
    registry = Routines(root)
    rows = []
    for item in registry.list():
        if item["retired"]:
            continue
        creator_issue = registry.creator_issue(item)
        paused_reason = creator_issue.reason if creator_issue else None
        active = item["enabled"] and paused_reason is None
        try:
            upcoming = RoutineSchedule(item["schedule"], item["timezone"]).next(
                datetime.now(tz.utc), item["state"]["cursor"]) if active else None
        except (ValueError, KeyError, TypeError):
            upcoming = None
        agent = member_agent_id(root, str(item["member"]))
        rows.append({
            "id": item["id"], "name": item["name"], "agentId": agent, "prompt": item["prompt"],
            "target": {"type": "conversation", "channelId": dm_channel_id(agent), "threadId": None},
            "trigger": _trigger(str(item["schedule"]), str(item["timezone"])),
            "active": active, "nextRunAt": _iso(upcoming.timestamp()) if upcoming else None,
            "createdAt": _iso(item.get("created_at")), "access": "read",
            "report": "always" if item.get("delivery") == "notify" else "when_new",
            "pausedReason": paused_reason, "failuresInRow": 0, "state": "active" if active else "paused",
            "waitingFor": None, "brain": {"kind": "model", "machineName": None},
            "health": {"ok": 0, "silent": 0, "failed": 0, "skipped": 0, "missed": 0, "total": 0},
            "canSeeDetails": True, "canManage": owner_connection(), "lastRun": None, "recentRuns": [],
            "usage": {"runs": 0, "tokens": 0, "costNanoUsd": 0, "billedTo": "api"}, "ownActions": [],
            **({"createdByUserId": None if paused_reason == "creator_left" else item["initiator"],
                "canTakeOver": bool(creator_issue and creator_issue.verified and owner_connection())}
               if "initiator" in item else
               {"createdByUserId": owner_id(root), "awaitingTakeoverResume": registry.awaiting_takeover_resume(item)}
               if item["state"].get("takeover") else {}),
        })
    history = routine_history(root)
    for row in rows:
        past = history.get(row["id"])
        if past:
            row.update(past)
            row["usage"]["runs"] = past["health"]["total"]
    return rows


def owner_connection() -> bool:
    """例行工作的启停与删除只在设了主人令牌的服务上开放（与 /api/staff/routines 的写入同一条规矩）。"""
    from src.web.auth import get_api_token
    return bool(get_api_token())


def control_routine(root: str, routine_id: str, *, enabled: Optional[bool] = None,
                    retire: bool = False) -> Optional[Dict[str, Any]]:
    """启用、停用或删除一条例行工作。界面合同不带版本号：以服务端此刻的版本提交，同时有人改过则由
    登记处拒绝（ValueError）。启用时登记处照常核对成员与岗位绑定。返回改后的那一条；删除后返回 None。"""
    from src.workflow.staff.routines import Routines
    registry = Routines(root)
    item = next((x for x in registry.list() if x["id"] == routine_id and not x["retired"]), None)
    if item is None:
        raise LookupError(routine_id)
    registry.control(routine_id, item["revision"], enabled=enabled, retire=retire)
    return None if retire else next((row for row in routines(root) if row["id"] == routine_id), None)


_SHOWN_SKILLS: Dict[tuple, str] = {}   # (家, 技能) → 最近一次给界面看过的版本；启用只认这个版本


def _skill(root: str, item: Dict[str, Any], created: str) -> Dict[str, Any]:
    revision = item.get("revision")
    return {"id": str(item["id"]), "name": str(item.get("name") or item["id"]),
            "description": str(item.get("description") or ""),
            "version": revision if type(revision) is int else 1, "source": "manual", "sourceUrl": None,
            "defaultForNewAgents": False, "metadata": {}, "origin": "person",
            "ownerUserId": owner_id(root), "createdAt": created, "updatedAt": created,
            "enabled": bool(item.get("enabled"))}


def _personal_skills(root: str):
    from src.gateway.workspace_scope import GENERAL, current_workspace_scope
    from src.workflow.workbench.personal_skills import PersonalSkills
    return PersonalSkills(root) if current_workspace_scope() == GENERAL else None


def skills(root: str) -> List[Dict[str, Any]]:
    """主人的个人技能（只加载指令，不增加工具权限）。enabled 是主人核对过当前版本后才为真。"""
    store = _personal_skills(root)
    if store is None:
        return []
    created = _home_created(root)
    return [_skill(root, item, created) for item in store.list()["items"]]


def skill_detail(root: str, skill_id: str) -> Optional[Dict[str, Any]]:
    """一条技能的正文。看过正文才算核对：这里记下给界面看的是哪个版本，启用时只认它。"""
    store = _personal_skills(root)
    if store is None:
        return None
    try:
        item = store.get(skill_id, preview=True)
    except (OSError, ValueError):
        return None
    _SHOWN_SKILLS[(root, skill_id)] = str(item["revision"])
    row = _skill(root, item, _home_created(root))
    return {**{k: v for k, v in row.items() if k != "enabled"}, "body": item["instructions"], "files": [],
            "pending": None, "changes": []}


def set_main_skills(root: str, wanted: List[str]) -> List[Dict[str, Any]]:
    """把主助理启用的个人技能改成 wanted。停用随时可以；启用只认主人在界面上看过正文的那个版本——
    没看过、或看过之后内容变了，都拒绝（SkillConflict），要主人重新打开核对。"""
    from src.workflow.workbench.personal_skills import SkillConflict
    store = _personal_skills(root)
    if store is None:
        raise ValueError("个人技能仅在私人助理范围管理")
    items = {str(item["id"]): item for item in store.list()["items"]}
    unknown = [skill_id for skill_id in wanted if skill_id not in items]
    if unknown:
        raise LookupError(unknown[0])
    for skill_id, item in items.items():
        enable = skill_id in wanted
        if enable == bool(item.get("enabled")):
            continue
        revision = str(item["revision"])
        if enable and _SHOWN_SKILLS.get((root, skill_id)) != revision:
            raise SkillConflict("启用前请先打开这条技能核对正文")
        store.set_enabled(skill_id, enabled=enable, revision=revision)
    return skills(root)


def _artifact(root: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    agent = {"type": "agent", "id": main_agent_id(root)}
    return {"id": str(meta["id"]), "kind": "html", "title": str(meta.get("title") or meta["id"]),
            "version": int(meta.get("pinned") or meta.get("version") or 1),
            "channelId": dm_channel_id(main_agent_id(root)), "threadId": None, "createdBy": agent,
            "updatedBy": agent, "createdAt": _iso(meta.get("created_at")), "updatedAt": _iso(meta.get("updated_at")),
            "canEdit": False, "canDelete": False, "canShare": False,
            "untrusted": True,       # 制品是助理写的页面：界面按不可信内容隔离展示
            "audience": {"kind": "workspace"}, "sharing": {"mode": "conversation", "userIds": [], "roleIds": []}}


def artifacts(root: str, *, query: str = "", kinds: Optional[Set[str]] = None) -> Dict[str, Any]:
    from src.agents.tools.artifacts import ArtifactStore
    if kinds is not None and "html" not in kinds:
        return {"artifacts": [], "nextCursor": None, "counts": {}}
    needle = query.strip().lower()
    rows = [_artifact(root, meta) for meta in ArtifactStore(root).list() if isinstance(meta, dict) and meta.get("id")]
    if needle:
        rows = [row for row in rows if needle in row["title"].lower()]
    return {"artifacts": rows, "nextCursor": None, "counts": {"html": len(rows)} if rows else {}}


def artifact(root: str, artifact_id: str, version: Optional[int] = None) -> Optional[Dict[str, Any]]:
    from src.agents.tools.artifacts import ArtifactStore
    store = ArtifactStore(root)
    meta = store.meta(artifact_id)
    html = store.html(artifact_id, version) if meta else None
    if not meta or html is None:
        return None
    shown = _artifact(root, meta)
    wanted = version if version in {int(v.get("v", 0)) for v in store.versions(artifact_id)} else shown["version"]
    return {**shown, "shownVersion": wanted, "content": {"kind": "html", "html": html},
            "versioned": len(store.versions(artifact_id)) > 1}


# ── 角色资料页 ────────────────────────────────────────────────────────────────

def _member_of(root: str, agent_id: str) -> Optional[Dict[str, Any]]:
    return next((m for m in _staff(root).get("members") or []
                 if member_agent_id(root, str(m.get("id"))) == agent_id), None)


def agent_brain(root: str, agent: Dict[str, Any]) -> Dict[str, Any]:
    """这个角色用什么思考。这里只有「模型」一种，换模型与账号不在这个界面做，所以全部只读。"""
    ready = model_connected()
    return {"agentId": agent["id"], "brain": "model", "model": agent["model"] or None,
            "effectiveModel": agent["model"] or None, "fallback": False, "extraUsage": False, "computer": None,
            "options": [{"kind": "model", "available": ready, "models": [], "defaultModel": None}],
            "machines": [], "canEdit": False, "account": None, "effort": None, "speed": "standard",
            "accounts": [], "canManageAccounts": False, "accountsSupported": False, "onlyOwner": False,
            "canUsePlans": False, "ownSignIn": False, "ownUsage": {}}


def agent_access(root: str, agent_id: str) -> Dict[str, Any]:
    """专员能用的工具，来自它岗位的授权登记（只读）。主助理的工具由会话装配与能力档案决定，
    不在登记表里，这里不列。"""
    member = _member_of(root, agent_id)
    if member is None:
        return {"all": False, "builtins": [], "connectors": []}
    role = next((r for r in _staff(root).get("roles") or [] if r.get("id") == member.get("role")), None) or {}
    grants = role.get("grants") or {}
    read_only = set(grants.get("operations") or []) <= {"read"}
    scope = f"role:{role.get('id') or ''}"
    tools = [{"name": str(name), "label": str(name), "description": "", "readOnly": read_only,
              "connectionId": None, "scope": scope, "sensitive": not read_only, "state": "inherited"}
             for name in grants.get("tools") or []]
    if not tools:
        return {"all": False, "builtins": [], "connectors": []}
    return {"all": False, "connectors": [],
            "builtins": [{"scope": scope, "label": f"岗位「{role.get('title') or role.get('id')}」的授权",
                          "state": "granted", "tools": tools}]}


# ── 通知 ─────────────────────────────────────────────────────────────────────

def notifications(root: str, *, filter_name: str = "", limit: int = 50) -> Dict[str, Any]:
    """后台通知台账（定时作业结果、巡视发现等）整理成界面的通知，新的在前。

    台账不记「读没读」，所以这些通知一律按已读列出：只进「全部」，不计未读数、不进「等我处理」，
    免得界面上挂一个永远消不掉的红点。真正要主人处理的事在审批里。"""
    from src.scheduling.notices import load_notices
    agent = main_agent_id(root)
    items = []
    for row in load_notices(root, max(1, min(int(limit), 100))):
        text, stamp = str(row.get("text") or ""), _iso(row.get("ts"))
        if not text:
            continue
        key = hashlib.sha256(f"{row.get('ts')}\0{row.get('source')}\0{text}".encode("utf-8", "surrogatepass"))
        items.append({"id": _id("notice", key.hexdigest()), "kind": "agent_reply", "waiting": False,
                      "actors": [{"type": "agent", "id": agent}], "count": 1,
                      "source": {"channelId": dm_channel_id(agent)}, "latest": {"text": text},
                      "readAt": stamp, "doneAt": None, "snoozedUntil": None, "createdAt": stamp,
                      "updatedAt": stamp})
    counts = {"all": len(items), "mentions": 0, "waiting": 0, "unread": 0, "reactions": 0}
    shown = items if filter_name in {"", "all"} else []
    return {"items": shown, "counts": counts, "nextCursor": None}


# ── 运行记录与用量：来自任务台账和模型用量账本 ────────────────────────────────

_RUN_STATUS = {"queued": "queued", "running": "running", "blocked": "running", "done": "done"}


def _staff_tasks(root: str) -> List[Any]:
    """专员名下的任务，新的在前。台账读不全时照样返回读到的部分——这里只做展示，不据此下结论。"""
    from src.workflow.tasks.tasks import TaskLedger
    tasks = [task for task in TaskLedger(root).list(limit=500)
             if (task.collaboration or {}).get("dispatch", {}).get("source") == "staff"
             and (task.collaboration or {}).get("dispatch", {}).get("member")]
    return sorted(tasks, key=lambda task: (task.created, task.id), reverse=True)


def _task_run(root: str, task: Any) -> Dict[str, Any]:
    dispatch = task.collaboration["dispatch"]
    agent = member_agent_id(root, str(dispatch["member"]))
    active = task.status in ACTIVE_TASK
    return {"id": task.id, "agentId": agent, "channelId": dm_channel_id(agent),
            "threadId": message_id(task.id, "user"), "trigger": "routine" if dispatch.get("routine") else "mention",
            "status": _RUN_STATUS.get(task.status, "failed"), "error": str(task.error or "") or None,
            "what": str(task.prompt or "")[:300], "turns": int(task.collaboration.get("round") or 1),
            "inputTokens": 0, "outputTokens": 0,     # 账本按天按专员记，不到单件任务
            "createdAt": _iso(task.created), "finishedAt": None if active else _iso(task.updated or task.created)}


def runs(root: str, *, agent_id: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    """专员做过的事。主助理的回合不在任务台账里，这里不列。"""
    rows = [_task_run(root, task) for task in _staff_tasks(root)]
    if agent_id:
        rows = [row for row in rows if row["agentId"] == agent_id]
    return rows[:max(1, min(int(limit), 200))]


def _month_days() -> List[Any]:
    from datetime import date, timedelta
    today = date.today()          # 与用量账本记账用的同一个「今天」（本机日期）
    return [date(today.year, today.month, 1) + timedelta(days=offset) for offset in range(today.day)]


def month_usage(root: str) -> Dict[str, Dict[str, int]]:
    """本月每个角色的模型调用：专员按账本里的来源归到本人；其余（主要聊天、定时与巡视）归主助理。"""
    from src.llm.usage_ledger import day_usage
    members = {str(m.get("id")): member_agent_id(root, str(m.get("id"))) for m in _staff(root).get("members") or []}
    main = main_agent_id(root)
    totals: Dict[str, Dict[str, int]] = {}
    for day in _month_days():
        for source, bucket in day_usage(day).items():
            parts = str(source).split(":")
            agent = members.get(parts[3]) if parts[0] == "staff" and len(parts) == 5 else None
            if parts[0] == "staff" and agent is None:
                continue                      # 已不在名册里的专员：不混进别人名下
            row = totals.setdefault(agent or main, {"calls": 0, "input": 0, "output": 0})
            row["calls"] += int(bucket.get("calls", 0) or 0)
            row["input"] += int(bucket.get("prompt", 0) or 0)
            row["output"] += int(bucket.get("completion", 0) or 0)
    return totals


def usage(root: str) -> List[Dict[str, Any]]:
    """用量页。账本只记 token，没有价格，费用一栏是占位的 0，不代表没花钱。"""
    totals = month_usage(root)
    return [{"agentId": agent["id"], "handle": agent["handle"], "calls": totals[agent["id"]]["calls"],
             "inputTokens": totals[agent["id"]]["input"], "outputTokens": totals[agent["id"]]["output"],
             "costUsd": 0} for agent in agents(root) if agent["id"] in totals]


def agent_activity(root: str, agent_id: str) -> Dict[str, Any]:
    """角色资料页的本月数字：token 来自用量账本，件数来自任务台账（只有专员有）。"""
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    mine = [run for run in runs(root, agent_id=agent_id, limit=200) if run["createdAt"].startswith(month)]
    by_day: Dict[int, Dict[str, int]] = {}
    for run in mine:
        row = by_day.setdefault(int(run["createdAt"][8:10]), {"runs": 0, "failed": 0})
        row["runs"] += 1
        row["failed"] += run["status"] == "failed"
    used = month_usage(root).get(agent_id, {"input": 0, "output": 0})
    return {"tokensThisMonth": used["input"] + used["output"], "runsThisMonth": len(mine),
            "failuresThisMonth": sum(run["status"] == "failed" for run in mine),
            "runsByDay": [{"day": day, **row} for day, row in sorted(by_day.items())]}


_ROUTINE_RUN = {"queued": "queued", "running": "running", "blocked": "waiting", "done": "reported"}


def routine_history(root: str) -> Dict[str, Dict[str, Any]]:
    """每项例行工作最近的运行：任务台账里带着它标记的那些任务。"""
    history: Dict[str, List[Dict[str, Any]]] = {}
    for task in _staff_tasks(root):
        routine = task.collaboration["dispatch"].get("routine") or {}
        if not routine.get("id"):
            continue
        active = task.status in ACTIVE_TASK
        agent = member_agent_id(root, str(task.collaboration["dispatch"]["member"]))
        history.setdefault(str(routine["id"]), []).append({
            "id": task.id, "at": _iso(task.created), "status": _ROUTINE_RUN.get(task.status, "failed"),
            "code": None, "summary": str(task.result or task.error or "")[:200], "brain": "model",
            "link": {"channelId": dm_channel_id(agent), "threadId": None,
                     "messageId": message_id(task.id, "user" if active else "assistant")},
            "finishedAt": None if active else _iso(task.updated or task.created)})
    out = {}
    for routine_id, rows in history.items():
        finished = [row for row in rows if row["finishedAt"]]
        streak = 0
        for row in finished:
            if row["status"] != "failed":
                break
            streak += 1
        out[routine_id] = {
            "lastRun": rows[0], "recentRuns": rows[:10], "failuresInRow": streak,
            "health": {"ok": sum(row["status"] == "reported" for row in finished), "silent": 0,
                       "failed": sum(row["status"] == "failed" for row in finished), "skipped": 0, "missed": 0,
                       "total": len(finished)}}
    return out
