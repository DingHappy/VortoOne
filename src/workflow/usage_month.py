"""这个月谁、哪个 agent 用了多少（主人 2026-11-23 定：用量统计要做，按价格折成美元、可给成员设上限）。

- 数字来自每天的用量台账（src/llm/usage_ledger），按来源、按模型；美元按所有者给模型填的价格折算
  （src/llm/prices）。没填价格的模型只记 token、不算钱：缺价格不等于免费。
- 归到谁名下：agent 的事（台账来源 ``staff:…:<agent>:<任务>``）看任务的发起人——成员交给 agent 的记在
  那个成员名下，其余记在所有者名下；主要聊天、定时、巡视、摘要都记在所有者和主助理名下。
- 成员上限（usage_limits）：所有者可给默认、身份组、个人设每月美元上限；到了上限时 block 拒绝这个成员
  再让 agent 干活，notify 只提醒。成员可以申请加额度，所有者调高或拒绝。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

OWNER = "owner"
MAIN = "main"
SUMMARY_SOURCES = ("对话摘要",)


@dataclass
class Entry:
    day: date
    agent: str          # 专员 id，或 MAIN（主助理）
    person: str         # 成员 id，或 OWNER
    kind: str           # agent / summary
    task: Optional[str]
    calls: int
    prompt: int
    completion: int
    cost: float         # 有价格的部分折成的美元
    unpriced: int       # 没有价格的 token
    unpriced_calls: int = 0


def month_start(today: Optional[date] = None) -> date:
    today = today or datetime.now(timezone.utc).date()
    return today.replace(day=1)


def next_month(start: date) -> date:
    return (start.replace(day=28) + timedelta(days=4)).replace(day=1)


def _days(since: date, until: date) -> Iterable[date]:
    day = since
    while day <= until:
        yield day
        day += timedelta(days=1)


def entries(root: str, since: date, until: Optional[date] = None) -> List[Entry]:
    """since 到 until（含）每天、每个来源一条。"""
    from src.llm.prices import cost, price_table
    from src.llm.usage_ledger import day_usage
    from src.workflow.tasks.tasks import TaskLedger
    until = until or datetime.now(timezone.utc).date()
    prices = price_table(root)
    ledger = TaskLedger(root)
    who: Dict[str, str] = {}
    rows: List[Entry] = []
    for day in _days(since, until):
        for source, bucket in day_usage(day).items():
            parts = str(source).split(":")
            agent, person, task = MAIN, OWNER, None
            if parts[0] == "staff" and len(parts) == 5:
                agent, task = parts[3], parts[4]
                if task not in who:
                    found = ledger.load(task)
                    initiator = ((found.collaboration or {}).get("dispatch") or {}).get("initiator") if found else None
                    who[task] = initiator["id"] if isinstance(initiator, dict) and initiator.get("kind") == "member" \
                        and isinstance(initiator.get("id"), str) else OWNER
                person = who[task]
            models = bucket.get("models") if isinstance(bucket.get("models"), dict) else None
            spent, unpriced, unpriced_calls = 0.0, 0, 0
            for model, row in (models or {"": bucket}).items():
                prompt, completion = int(row.get("prompt", 0) or 0), int(row.get("completion", 0) or 0)
                value = cost(prices, model, prompt, completion, int(row.get("cached", 0) or 0)) if model else None
                if value is None:
                    unpriced += prompt + completion
                    unpriced_calls += int(row.get("calls", 0) or 0)
                else:
                    spent += value
            rows.append(Entry(day, agent, person, "summary" if source in SUMMARY_SOURCES else "agent", task,
                              int(bucket.get("calls", 0) or 0), int(bucket.get("prompt", 0) or 0),
                              int(bucket.get("completion", 0) or 0), spent, unpriced, unpriced_calls))
    return rows


def person_month_cost(root: str, person: str, today: Optional[date] = None) -> float:
    start = month_start(today)
    return sum(entry.cost for entry in entries(root, start, today) if entry.person == person)


# ── 成员上限：记在成员登记里（usage_limits / usage_asks 两个新键，旧版本原样保留）──

def _limits(state) -> Dict[str, Any]:
    value = state.get("usage_limits")
    if not isinstance(value, dict):
        value = {"defaultUsd": None, "roles": {}, "people": {}, "raised": [], "onLimit": "block", "warn": True}
    return value


def limits(registry) -> Dict[str, Any]:
    with registry._locked_state() as state:
        return dict(_limits(state))


def _usd(value: Any, what: str) -> Optional[float]:
    if value is None:
        return None
    if type(value) not in (int, float) or not 0 <= value <= 100000:
        raise ValueError(f"{what}要是 0～100000 美元，或者不限")
    return round(float(value), 2)


def set_limits(registry, actor, body: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(body, dict) or set(body) - {"defaultUsd", "roles", "people", "onLimit", "warn"}:
        raise ValueError("上限只接受 defaultUsd、roles、people、onLimit、warn")
    with registry._change() as state:
        owner, _ = registry._actor(state, actor)
        if not owner:
            from src.workflow.members.registry import Refused
            raise Refused("用量上限只有所有者能改")
        current = _limits(state)
        if "defaultUsd" in body:
            current["defaultUsd"] = _usd(body["defaultUsd"], "默认上限")
        for key, known in (("roles", state["groups"]), ("people", state["members"])):
            if key in body:
                value = body[key]
                if not isinstance(value, dict) or any(name not in known for name in value):
                    raise ValueError("有不认识的身份组或成员" if key == "roles" else "有不认识的成员")
                current[key] = {name: _usd(usd, "上限") for name, usd in value.items()}
                if key == "people":
                    current["raised"] = [name for name in current.get("raised", []) if name in value]
        if "onLimit" in body:
            if body["onLimit"] not in ("block", "notify"):
                raise ValueError("到了上限只能 block（拒绝）或 notify（提醒）")
            current["onLimit"] = body["onLimit"]
        if "warn" in body:
            if type(body["warn"]) is not bool:
                raise ValueError("warn 要是真或假")
            current["warn"] = body["warn"]
        state["usage_limits"] = current
        return dict(current)


def limit_of(state, person: str) -> tuple:
    """(上限美元或 None, 来源, 来源身份组)。所有者不设上限。"""
    if person == OWNER:
        return None, "owner", None
    current = _limits(state)
    if person in current.get("people", {}) and current["people"][person] is not None:
        return current["people"][person], ("raised" if person in current.get("raised", []) else "person"), None
    member = state["members"].get(person) or {}
    best = None
    for group in member.get("groups") or []:
        usd = current.get("roles", {}).get(group)
        if usd is not None and (best is None or usd > best[0]):
            best = (usd, group)
    if best is not None:
        return best[0], "role", best[1]
    if current.get("defaultUsd") is not None:
        return current["defaultUsd"], "default", None
    return None, "none", None


def view(state, person: str, spent: float, *, money: bool) -> Dict[str, Any]:
    """界面的 PersonLimitView。"""
    limit, source, role = limit_of(state, person)
    asks = state.get("usage_asks") if isinstance(state.get("usage_asks"), dict) else {}
    ask = asks.get(person) or {}
    percent = None if not limit else min(999, round(spent / limit * 100))
    level = "none" if limit is None else ("at" if spent >= limit else "near" if limit and spent >= 0.8 * limit else "ok")
    row: Dict[str, Any] = {"percent": percent, "state": level, "requested": ask.get("status") == "pending",
                           "reachedAt": ask.get("reached") if level == "at" else None}
    if money:
        row.update(monthUsd=round(spent, 4), limitUsd=limit, limitFrom=source, limitRoleId=role,
                   raiseToUsd=None if limit is None else round(limit * 2, 2))
    return row


def check_member(root: str, member_id: str) -> None:
    """成员让 agent 干活之前：开着 block 且这个月到了上限，就拒绝（ValueError，原样给到界面）。"""
    from src.workflow.members.registry import MemberRegistry
    registry = MemberRegistry(root)
    state = registry.snapshot()
    limit, _, _ = limit_of(state, member_id)
    if limit is None or _limits(state).get("onLimit") != "block":
        return
    if person_month_cost(root, member_id) >= limit:
        raise ValueError("你这个月的用量到上限了：可以在用量页申请加额度，等所有者调高")


def ask_more(registry, actor) -> str:
    """成员申请加额度。再申请一次不报错。返回申请时间。"""
    if not actor.is_member:
        raise ValueError("所有者不用申请")
    with registry._change() as state:
        registry._actor(state, actor)
        asks = state.setdefault("usage_asks", {})
        ask = asks.get(actor.id)
        if not ask or ask.get("status") != "pending":
            ask = {"status": "pending", "at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}
            asks[actor.id] = ask
        return ask["at"]


def answer(registry, actor, member_id: str, *, raise_to: Optional[float]) -> None:
    """所有者答复：raise_to 不是 None 就把这个成员的上限调到它（标为「调高过」），否则拒绝。"""
    from src.workflow.members.registry import Missing, Refused
    with registry._change() as state:
        owner, _ = registry._actor(state, actor)
        if not owner:
            raise Refused("只有所有者能调用量上限")
        if member_id not in state["members"]:
            raise Missing("没有这个成员")
        asks = state.setdefault("usage_asks", {})
        if raise_to is not None:
            current = _limits(state)
            current.setdefault("people", {})[member_id] = _usd(raise_to, "上限")
            current["raised"] = sorted(set(current.get("raised", [])) | {member_id})
            state["usage_limits"] = current
        asks[member_id] = {**(asks.get(member_id) or {}), "status": "raised" if raise_to is not None else "refused"}
