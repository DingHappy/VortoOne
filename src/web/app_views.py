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
from typing import Any, Dict, List, Optional

_NAMESPACE = uuid.UUID("6f1d2a57-6f0e-4f0b-9c7e-0a7e5c0f1a11")

# 一位主人在自己家里拥有的全部界面权限（界面按这些键决定显示哪些入口；真正的放行仍在内核）。
OWNER_PERMISSIONS = [
    "read", "manage_owners", "administrator", "approve_actions", "manage_agents", "control_computers",
    "control_browsers", "manage_connectors", "manage_credentials", "manage_members", "manage_roles",
    "view_usage", "configure", "view_audit", "manage_billing", "create_agents", "manage_channels",
    "mention_everyone", "manage_routines", "review_memory", "invite_people", "pair_computers",
    "own_computers", "own_browsers", "talk_to_agents", "view_all_agents", "assign_work", "post",
    "create_channels", "edit_wiki", "upload_files",
]

SIDEBAR_TOOLS = ["waiting", "approvals", "proposals", "threads", "issues", "wiki", "drive", "routines",
                 "calendar", "team", "dashboards", "taskManager"]
SIDEBAR_SECTIONS = ["tools", "channels", "agents", "people"]

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
    """主助理加未退役的专员。专员的对话还没接进这套界面，所以 canTalk 为假。"""
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
    for member in staff.get("members") or []:
        if member.get("status") == "retired":
            continue
        member_id = str(member.get("id") or "")
        if not member_id:
            continue
        rows.append({
            "id": member_agent_id(root, member_id), "name": str(member.get("name") or member_id),
            "handle": member_id, "role": titles.get(str(member.get("role")), str(member.get("role") or "")),
            "instructions": "", "model": str(member.get("model") or ""), "ownerUserId": owner,
            "hasCredential": model_connected(), "createdAt": created, "audienceMode": "everyone",
            "canTalk": False, "seesDetails": True,
        })
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


def me(root: str) -> Dict[str, Any]:
    return {
        "user": {"id": owner_id(root), "name": OWNER_NAME, "email": "", "image": None,
                 "locale": "zh-Hans", "localeAuto": False},
        "organizations": [{"id": home_id(root), "name": _profile(root)["name"], "role": "owner",
                           "showActivity": True, "permissions": list(OWNER_PERMISSIONS)}],
    }


def person(root: str) -> Dict[str, Any]:
    return {"userId": owner_id(root), "name": OWNER_NAME, "email": "", "image": None, "role": "owner",
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
    rows = [_message(root, mid, from_owner=item.get("role") == "user", body=str(item.get("text") or ""),
                     created=_iso(item.get("at")), run_id=str(item.get("rid") or "") or None)
            for item, mid in zip(items, _message_ids(items))]
    known = {row["id"] for row in rows}
    for queued in view.get("queued") or []:
        rid = str(queued.get("rid") or queued.get("id") or "")
        if rid and message_id(rid, "user") not in known:
            rows.append(_message(root, message_id(rid, "user"), from_owner=True,
                                 body=str(queued.get("text") or ""), created=_iso(queued.get("created_at"))))
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
    title = next((line.strip() for line in text.splitlines() if line.strip()), "需要你确认")[:120]
    return {"id": str(pending["id"]), "runId": pending.get("rid"), "agentId": main_agent_id(root),
            "channelId": dm_channel_id(main_agent_id(root)), "threadId": None,
            "messageId": approval_card_id(str(pending["id"])),
            "tool": str(pending.get("operation_kind") or ""), "scope": str(pending.get("operation_kind") or ""),
            "title": title, "detail": detail, "params": {}, "status": status,
            "expiresAt": _iso(expires.timestamp()), "createdAt": created,
            "decidedByUserId": owner_id(root) if decided else None,
            "decidedAt": now_iso() if status != "pending" else None, "reason": None,
            "canDecide": status == "pending"}


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


def agent_detail(agent: Dict[str, Any]) -> Dict[str, Any]:
    """角色资料页。用量与运行次数这一批还没接到现有账本，界面合同要求数字，这里是占位的 0，
    不代表真实用量；接入 StaffBudgets 与执行记录后替换。"""
    return {**{key: agent[key] for key in ("id", "name", "handle", "role", "instructions", "model",
                                           "ownerUserId", "createdAt", "audienceMode", "canTalk",
                                           "seesDetails")},
            "credentialId": None, "budgetUsedPercent": 0, "tokensThisMonth": 0, "runsThisMonth": 0,
            "failuresThisMonth": 0, "runsByDay": [], "pendingApprovals": 0, "channels": []}
