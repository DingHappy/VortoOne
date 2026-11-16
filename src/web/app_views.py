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

def _main_transcript(root: str) -> List[Dict[str, Any]]:
    from src.gateway.main_conversation import main_chat_key
    from src.gateway.session_store import load_session
    key = main_chat_key(root)
    if not key:
        return []
    data = load_session(root, key) or {}
    return [item for item in data.get("transcript") or [] if isinstance(item, dict)]


def _message_ids(items: List[Dict[str, Any]]) -> List[str]:
    """对话记录本身没有消息标识：用回合、角色、时间和同组内的序号推出一个。

    记录从前面裁剪时，留下的消息 id 不变（除非被裁的正好是同一回合同一秒的前一条）。
    这是读取侧的推导；服务端存储给每条消息一个真正的标识之后应改用那个。"""
    seen: Dict[tuple, int] = {}
    ids = []
    for item in items:
        group = (str(item.get("rid") or ""), str(item.get("role") or ""), str(item.get("at") or ""))
        ordinal = seen.get(group, 0)
        seen[group] = ordinal + 1
        text = hashlib.sha256(str(item.get("text") or "").encode("utf-8", "surrogatepass")).hexdigest()[:16]
        ids.append(_id("message", *group, str(ordinal), text))
    return ids


def main_messages(root: str, *, before: str = "", limit: int = 100) -> List[Dict[str, Any]]:
    """主要聊天的记录，按时间从早到晚；before 是界面翻页用的时间。"""
    channel = dm_channel_id(main_agent_id(root))
    owner, agent = owner_id(root), main_agent_id(root)
    items = _main_transcript(root)
    rows = []
    for item, message_id in zip(items, _message_ids(items)):
        is_owner = item.get("role") == "user"
        rows.append({
            "id": message_id, "channelId": channel, "threadId": None,
            "author": {"type": "person" if is_owner else "agent", "id": owner if is_owner else agent},
            "body": str(item.get("text") or ""), "mentionedAgentIds": [], "agentRunId": None,
            "createdAt": _iso(item.get("at")),
        })
    if before:
        cutoff = _iso(before)
        rows = [row for row in rows if row["createdAt"] < cutoff]
    return rows[-max(1, min(int(limit), 400)):]


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
