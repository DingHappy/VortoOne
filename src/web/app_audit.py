"""新版 Desktop 的「审计」页：把共享审计台账投影成界面要的行。

台账（`src/gateway/audit.py`）只存元数据：工具名、打过码的参数、确认门的放行与拒绝。这里不补造台账里没有的东西：
- 工具调用归到干活的那个：会话是 ``staff:<agent>`` 的归那个 agent，其余归主助理；台账不记它成功与否，所以 ``outcome`` 是 null，不写成「完成」
- 确认门的决定：用户按的算主人，超时、预授权、系统的算系统；台账里没有对应的任务、频道与审批卡，这几栏留空
- 事件和 Hook 行是内部记录，不列
- 「等待中」只看实时确认（审批页），台账里没有正在等的调用，所以筛「等待中」时是空的

只给所有者看：参数里有主人对话里的路径和命令。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.gateway.audit import audit_snapshot

TOOL_CALL = "tool.call"
OUTCOMES = frozenset({"done", "failed", "refused", "pending"})
ACTOR_TYPES = frozenset({"person", "agent", "system"})
KINDS = frozenset({"tool", "change"})
MAX_LIMIT = 100
DEFAULT_LIMIT = 50
_READ = 500                       # 台账一次最多读这么多行（`audit_snapshot` 的上限）
_PERSON_SOURCES = frozenset({"user", "unknown"})


class AuditQueryError(ValueError):
    pass


def _base(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": entry["id"], "at": entry["ts"], "runId": None, "channelId": None, "channel": None,
            "onBehalfOf": None, "approval": None, "secret": None}


def _tool_row(entry: Dict[str, Any], agent: Dict[str, Any]) -> Dict[str, Any]:
    details: Dict[str, Any] = {}
    if entry.get("args"):
        details["input"] = entry["args"]
    return {**_base(entry), "actor": {"type": "agent", **agent}, "action": TOOL_CALL,
            "target": {"type": "tool", "id": entry.get("tool") or None}, "details": details, "outcome": None}


def _decision_row(entry: Dict[str, Any], owner: Dict[str, Any]) -> Dict[str, Any]:
    if entry.get("approval_outcome") == "expired" or entry.get("decision_source") == "timeout":
        action = "approval.expired"
    else:
        action = "approval.approved" if entry.get("decision") == "allowed" else "approval.refused"
    # 旧记录没有 decision_source：那时确认门只问人
    source = entry.get("decision_source", "user")
    actor = ({"type": "person", **owner} if source in _PERSON_SOURCES and action != "approval.expired"
             else {"type": "system", "id": None, "name": None})
    details: Dict[str, Any] = {"operation": entry.get("operation") or ""}
    if entry.get("tainted"):
        details["tainted"] = True
    for key in ("operation_kind", "risk", "approval_scope"):
        if entry.get(key) and entry[key] != "unknown":
            details[key] = entry[key]
    return {**_base(entry), "actor": actor, "action": action, "target": {"type": "approval", "id": None},
            "details": details, "outcome": None}


def _rows(root: str, owner: Dict[str, Any], agent: Dict[str, Any],
          agents: Dict[str, Dict[str, Any]]) -> tuple[List[Dict[str, Any]], str]:
    snapshot = audit_snapshot(root, _READ)
    rows = []
    for entry in snapshot["entries"]:             # 新的在前
        if entry["category"] == "tool":
            session = str(entry.get("session") or "")
            who = agent
            if session.startswith("staff:"):
                handle = session[len("staff:"):]
                who = agents.get(handle) or {"id": None, "name": handle}     # 删掉的 agent 也不算到主助理头上
            rows.append(_tool_row(entry, who))
        elif entry["category"] == "decision":
            rows.append(_decision_row(entry, owner))
    return rows, snapshot["status"]


def check_query(*, actor_type: str = "", outcome: str = "", kind: str = "", limit: Optional[int] = None) -> None:
    if actor_type and actor_type not in ACTOR_TYPES:
        raise AuditQueryError("actorType 只能是 person、agent 或 system")
    if kind and kind not in KINDS:
        raise AuditQueryError("kind 只能是 tool 或 change")
    if outcome and not set(outcome.split(",")) <= OUTCOMES:
        raise AuditQueryError("outcome 只能是 done、failed、refused、pending")
    if limit is not None and not 1 <= limit <= MAX_LIMIT:
        raise AuditQueryError(f"limit 要在 1 到 {MAX_LIMIT} 之间")


def page(root: str, *, owner: Dict[str, Any], agent: Dict[str, Any], actor_type: str = "", actor_id: str = "",
         via: str = "", outcome: str = "", waiting: bool = False, kind: str = "", before: str = "",
         limit: Optional[int] = None, agents: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """一页审计行，新的在前；``next`` 是下一页的 ``before``。``status`` 为 partial 时台账有读不全的地方。"""
    check_query(actor_type=actor_type, outcome=outcome, kind=kind, limit=limit)
    rows, status = _rows(root, owner, agent, agents or {})
    if waiting:
        rows = []
    if actor_type:
        rows = [row for row in rows if row["actor"]["type"] == actor_type]
    if actor_id:
        rows = [row for row in rows if row["actor"]["id"] == actor_id]
    if via:
        rows = [row for row in rows if row["actor"]["type"] == "agent" and row["actor"]["id"] == via]
    if outcome:
        wanted = set(outcome.split(","))
        rows = [row for row in rows if row["outcome"] in wanted]
    if kind:
        rows = [row for row in rows if (row["action"] == TOOL_CALL) == (kind == "tool")]
    if before:
        ids = [row["id"] for row in rows]
        rows = rows[ids.index(before) + 1:] if before in ids else []
    size = limit or DEFAULT_LIMIT
    return {"rows": rows[:size], "next": rows[size - 1]["id"] if len(rows) > size else None, "status": status}
