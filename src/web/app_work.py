"""新版界面里「谁在干活、什么在等你」：任务管理器、等我处理、正在运行。纯投影，不存任何状态。

来源都是现有记录：主要聊天的回合（桥）、专员任务台账、正在等的审批卡、专员问的问题。

- 只列所有者自己交代的事。成员交给 agent 的事是那个人和 agent 之间的对话：只报件数（``hidden``），不报内容。
  成员的事停在审批上时照样进「等我处理」——批准本来就归所有者，审批页里也是这些。
- 标题按合同不拿交代的原话当标题（``label`` 为 null），界面改说在哪个对话里。
- 台账不按一件事记工具调用次数和每分钟活动：``steps`` 是 0、``activity`` 是空的，不编数字。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Collection, Dict, List, Optional

from src.gateway import protocol
from src.gateway.turn_digest import turn_parts
from src.web import app_views as views

MAX_ENDED, MAX_WAITING = 50, 100
_DONE = frozenset({"done", "submitted", "review"})
_BRAIN = {"vortocode": "model", "claude-code": "claude_code", "codex": "codex"}
_TOOL_NAME = re.compile(r"[A-Za-z0-9_.:-]{1,120}")
_TOOL_ENDS = {"succeeded", "failed", "blocked", "cancelled", "canceled", "skipped", "denied"}


def since_time(value: str) -> datetime:
    """``since`` 参数；没给就是最近一天。格式不对抛 ValueError。"""
    if not value:
        return datetime.now(timezone.utc) - timedelta(days=1)
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _step_time(value: Any) -> Optional[str]:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.isoformat() if moment.tzinfo is not None else None


def task_steps(root: str, run_id: str, *, running: bool) -> Optional[Dict[str, Any]]:
    """Project the owner's main-chat tool records without input, output or message text."""
    view = views._main_view(root, include_activities=True)
    request, reply = turn_parts(view["transcript"], run_id)
    created = _step_time(request.get("at")) if request is not None else None
    if created is None:
        return None
    finished = _step_time(reply.get("at")) if reply is not None and not running else None
    terminal_phase = None
    steps = []
    started, ended = set(), set()
    for event in view.get("activities", []):
        if event.get("rid") != run_id:
            continue
        event_type, status = event.get("type"), event.get("status")
        timestamp = _step_time(event.get("recorded_at"))
        if event_type == protocol.AGENT_PHASE and status in ("failed", "cancelled") and timestamp:
            terminal_phase = timestamp
        if event_type != protocol.AGENT_TOOL or timestamp is None:
            continue
        call_id, name = event.get("id"), event.get("name")
        if not isinstance(call_id, str) or not 1 <= len(call_id) <= 160:
            continue
        if status == "running" and call_id not in started:
            if not isinstance(name, str) or _TOOL_NAME.fullmatch(name) is None:
                continue
            started.add(call_id)
            kind, fields = "tool_call", {"name": name}
        elif isinstance(status, str) and status in _TOOL_ENDS and call_id in started and call_id not in ended:
            ended.add(call_id)
            kind, fields = "tool_result", {"isError": status != "succeeded"}
        else:
            continue
        steps.append({"seq": len(steps) + 1, "kind": kind, "createdAt": timestamp,
                      "data": {"toolUseId": call_id, **fields}})
    if not running:
        finished = terminal_phase or finished
    return {"run": {"createdAt": created, "finishedAt": finished}, "steps": steps}


def _open_question(task: Any) -> Optional[Dict[str, Any]]:
    from src.workflow.staff.questions import questions_of, state_of
    return next((item for item in questions_of(task)
                 if state_of(task.status, task.collaboration, item) == "open"), None)


def _split(root: str) -> tuple[List[Any], List[Any]]:
    """（所有者自己交代的, 别人交代的）。认不出是谁交代的按别人的算。"""
    from src.workflow.staff.initiators import initiator_of
    mine, others = [], []
    for task in views._staff_tasks(root):
        try:
            own = initiator_of(task) is None
        except ValueError:
            own = False
        (mine if own else others).append(task)
    return mine, others


def _on_budget(task: Any) -> bool:
    """停下来等额度：不是在干活，也不是等人。"""
    return task.status == "blocked" and (task.collaboration.get("quota") or {}).get("status") == "waiting"


def _status(task: Any, approving: Collection[str], question: Optional[Dict[str, Any]]) -> Optional[str]:
    """还没完的事现在是什么状态；完了是 None。"""
    if task.status == "queued":
        return "queued"
    if task.status in views.ACTIVE_TASK:
        return "waiting_approval" if task.id in approving else "waiting_input" if _on_budget(task) else "running"
    return "waiting_input" if question is not None else None


def _place(root: str, task: Any) -> Dict[str, Any]:
    agent = views.member_agent_id(root, str(task.collaboration["dispatch"]["member"]))
    where = task.collaboration["dispatch"].get("channel")
    if where:                                       # 团队频道里被 @ 的事：在那个话题里
        return {"runId": task.id, "agentId": agent, "channelId": where["id"], "channelKind": "channel",
                "channelName": None, "threadId": where["thread"], "label": None, "background": False}
    # 私聊是平的：threadId 为 null
    return {"runId": task.id, "agentId": agent, "channelId": views.dm_channel_id(agent), "channelKind": "dm",
            "channelName": None, "threadId": None, "label": None,
            "background": bool(task.collaboration["dispatch"].get("routine"))}


def _main_run(run: Dict[str, Any]) -> Dict[str, Any]:
    return {"channelKind": "dm", "channelName": None, "label": None, "background": False, **run}


def active_runs(root: str, main: List[Dict[str, Any]], approving: Collection[str], *,
                staff: bool) -> List[Dict[str, Any]]:
    """此刻在跑的（含停下来等批准的）。staff：带着所有者证明，才列 agent 私聊里的事。"""
    rows = [_main_run(run) for run in main]
    if not staff:
        return rows
    for task in _split(root)[0]:
        if task.status in views.ACTIVE_TASK:
            status = _status(task, approving, None)
            created = views._iso(task.created)
            rows.append({**_place(root, task), "status": status, "createdAt": created,
                         "startedAt": None if status == "queued" else created})
    return rows


def task_manager(root: str, main: List[Dict[str, Any]], approving: Collection[str], since: datetime, *,
                 staff: bool, done_today: bool = False) -> Dict[str, Any]:
    me = views.me(root)["user"]
    person = {"userId": me["id"], "name": me["name"]}

    def managed(row: Dict[str, Any], brain: Optional[str], changed: str, *, stop: bool,
                card: Optional[str] = None) -> Dict[str, Any]:
        status = row["status"]
        state = "queued" if status == "queued" else "working" if status == "running" else "you"
        return {**row, "routine": None, "plan": None, "requester": None if row["background"] else person,
                "brain": brain, "machine": None, "now": None, "steps": 0, "pendingMessageIds": [], "canStop": stop,
                "work": {"state": state, "since": changed, "card": card}}

    tasks = [managed({**_main_run(run), "threadId": None}, "model", run.get("createdAt"), stop=True) for run in main]
    view: Dict[str, Any] = {"tasks": tasks, "ended": [], "activity": {},
                            "hidden": {"total": 0, "working": 0, "byAgent": {}}}
    ended, finished_ok = [], 0
    if staff:
        engines = {str(m.get("id")): _BRAIN.get(str(m.get("engine"))) for m in views._staff(root).get("members") or []}
        mine, others = _split(root)
        for task in mine:
            question = _open_question(task)
            status = _status(task, approving, question)
            created, changed = views._iso(task.created), views._iso(task.updated or task.created)
            place = _place(root, task)
            if status is not None:
                held = status == "waiting_input" and _on_budget(task)
                card = views.message_id(task.id, "assistant") if status == "waiting_input" and not held else None
                row = {**place, "status": status, "createdAt": created,
                       "startedAt": None if status == "queued" else created}
                shown = managed(row, engines.get(str(task.collaboration["dispatch"]["member"])),
                                created if status in {"queued", "running"} else changed,
                                stop=task.status in views.ACTIVE_TASK, card=card)
                # 等额度按界面契约：held 说明在等什么，work 留空（不算等你）
                tasks.append({**shown, "held": "budget", "work": None} if held else shown)
            elif datetime.fromisoformat(changed.replace("Z", "+00:00")) >= since:
                how = "done" if task.status in _DONE else "canceled" if task.status == "cancelled" else "failed"
                finished_ok += how == "done"
                ended.append({**place, "status": how, "routine": None, "startedAt": created, "finishedAt": changed,
                              "stoppedBy": None})
        hidden = view["hidden"]
        for task in others:
            if task.status not in views.ACTIVE_TASK:
                continue
            agent = views.member_agent_id(root, str(task.collaboration["dispatch"]["member"]))
            hidden["total"] += 1
            hidden["working"] += task.status == "running"
            hidden["byAgent"][agent] = hidden["byAgent"].get(agent, 0) + 1
    view["ended"] = sorted(ended, key=lambda row: row["finishedAt"], reverse=True)[:MAX_ENDED]
    if done_today:
        view["doneToday"] = finished_ok
    return view


def waiting(root: str, approvals: List[Dict[str, Any]], *, staff: bool, channel_id: str = "") -> Dict[str, Any]:
    """等所有者处理的，等得最久的在前：要批的（与审批页同一份）和问他的问题。"""
    items = []
    for row in approvals:
        source = {"approvalId": row["id"], "channelId": row["channelId"]}
        if row.get("messageId"):
            source["messageId"] = row["messageId"]
        items.append({"id": row["id"], "kind": "approval", "asker": {"type": "agent", "id": row["agentId"]},
                      "source": source, "question": row["title"], "since": row["createdAt"]})
    if staff:
        for task in _split(root)[0]:
            question = _open_question(task)
            if question is None:
                continue
            place = _place(root, task)
            items.append({"id": question["id"], "kind": "agent_question",
                          "asker": {"type": "agent", "id": place["agentId"]},
                          "source": {"questionId": question["id"], "channelId": place["channelId"],
                                     "messageId": views.message_id(task.id, "assistant")},
                          "question": " ".join(str(question["text"]).split())[:300],
                          "since": views._iso(task.updated or task.created)})
    if channel_id:
        items = [item for item in items if item["source"]["channelId"] == channel_id]
    items.sort(key=lambda item: item["since"])
    return {"items": items[:MAX_WAITING], "count": len(items)}
