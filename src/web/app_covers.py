"""外出时把批准交给别人（所有者 2026-11-22 定）：所有者指定一个成员，替自己批主要聊天里助理等着的确认。

规矩：
- 只有所有者能设、能改；被托付的成员自己可以随时不再代批
- 「一直」或「我不在时」（所有者的状态是「外出」时才生效），可以设到期时间，到期自动收回
- 被托付的人只能「批这一次」，不能放宽成常设权限
- 默认只代批低风险的（读、起草、改主人自己的资料）；所有者勾了「高风险的也交给他」才包括写入、执行等
- 这些永远不代批，留给所有者：读过外部内容的回合发起的、专用浏览器或配对电脑的网页登录、要所有者本人证明的文件传输、
  要设备钥匙的、专员提议（会新建 agent、改岗位权限）
- 每一次代批都记进审计，写明是谁替所有者批的
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.web import app_prefs
from src.web import app_views as views

KEY = "approvalCover"
MODES = ("always", "while_out")
LOW_RISK = frozenset({"read", "draft", "own_data"})


class CoverError(ValueError):
    pass


def _instant(value: str) -> Optional[datetime]:
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return moment if moment.tzinfo is not None else None


def check(body: Any, members: Dict[str, Any]) -> Dict[str, Any]:
    """members：现在在空间里的成员（id → 记录）。"""
    if not isinstance(body, dict) or set(body) != {"userId", "mode", "risky", "endsAt"}:
        raise CoverError("要给出代批的人、生效方式、是否包括高风险和到期时间")
    if body["userId"] not in members:
        raise CoverError("只能托付给空间里现在的成员")
    if body["mode"] not in MODES:
        raise CoverError("生效方式只能是一直或我不在时")
    if type(body["risky"]) is not bool:
        raise CoverError("是否包括高风险要是真或假")
    ends = body["endsAt"]
    if ends is not None:
        moment = _instant(ends) if isinstance(ends, str) and len(ends) <= 40 else None
        if moment is None:
            raise CoverError("到期时间要是带时区的 ISO 时间")
        if moment <= datetime.now(timezone.utc):
            raise CoverError("到期时间要在以后")
    return {"userId": body["userId"], "mode": body["mode"], "risky": body["risky"], "endsAt": ends}


def setting(root: str) -> Optional[Dict[str, Any]]:
    """所有者的代批设置；到期了当没有。"""
    value = app_prefs.person_value(root, views.owner_id(root), KEY)
    if not isinstance(value, dict) or not isinstance(value.get("userId"), str):
        return None
    if value.get("endsAt"):
        moment = _instant(value["endsAt"])
        if moment is None or moment <= datetime.now(timezone.utc):
            return None
    return value


def save(root: str, value: Optional[Dict[str, Any]]) -> None:
    app_prefs.set_person_value(root, views.owner_id(root), KEY, value)


def active(root: str, value: Optional[Dict[str, Any]]) -> bool:
    if value is None:
        return False
    if value["mode"] == "always":
        return True
    return app_prefs.person_value(root, views.owner_id(root), "availability") == "out"


def view(root: str, value: Dict[str, Any]) -> Dict[str, Any]:
    return {"userId": value["userId"], "mode": value["mode"], "risky": bool(value["risky"]),
            "endsAt": value.get("endsAt"), "active": active(root, value)}


def covers_for(root: str, who) -> Dict[str, Any]:
    """界面的 ApprovalCovers：所有者看谁在替自己批；成员看自己在替谁批（只有所有者一个人可能托付）。"""
    value = setting(root)
    if who.is_owner:
        return {"cover": view(root, value) if value else None, "covering": []}
    mine = value is not None and value["userId"] == who.id
    return {"cover": None, "covering": [{**view(root, value), "userId": views.owner_id(root)}] if mine else []}


def covering(root: str, who) -> Optional[Dict[str, Any]]:
    """这个成员此刻在替所有者批的设置；不在代批就 None。"""
    if who.is_owner:
        return None
    value = setting(root)
    return value if value is not None and value["userId"] == who.id and active(root, value) else None


def coverable(pending: Dict[str, Any], value: Dict[str, Any], *, needs_owner_proof: bool) -> bool:
    if pending.get("tainted") or pending.get("website") or pending.get("paired_browser") or needs_owner_proof:
        return False
    if pending.get("required_key") == "device":
        return False
    if value["risky"]:
        return True
    return pending.get("operation_kind") in LOW_RISK and pending.get("risk") != "high"


def mark(approval: Dict[str, Any], root: str, member_id: str) -> Dict[str, Any]:
    """代批的审批：谁批的、替谁批的。"""
    return {**approval, "decidedByUserId": member_id, "decidedForUserId": views.owner_id(root)} \
        if approval.get("status") in {"approved", "refused"} else {**approval, "decidedForUserId": None}


def visible(root: str, rows: List[Dict[str, Any]], value: Dict[str, Any], requires_proof) -> List[Dict[str, Any]]:
    """rows：主要聊天里等着的确认（会话侧的记录）。只留下能代批的。"""
    return [row for row in rows if coverable(row, value, needs_owner_proof=requires_proof(row["id"]))]
