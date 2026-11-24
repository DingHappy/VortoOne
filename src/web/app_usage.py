"""用量页（主人 2026-11-23 定）：把 workflow/usage_month 的数字排成界面要的样子。

美元是按所有者给模型填的价格折算的估计（``estimated``，界面标「≈」）；没填价格的模型只记 token。
成员只看得到自己的，且只有占上限的百分比（``money: false``）；金额、别人的、上限设置只给所有者。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from src.web import app_views as views
from src.workflow import usage_month as usage
from src.workflow.members.registry import MemberRegistry


def _agent_ids(root: str) -> Dict[str, str]:
    """台账里的 agent（专员 id / MAIN）→ 界面的 agent id。"""
    ids = {usage.MAIN: views.main_agent_id(root)}
    for agent in views.agents(root):
        if agent["id"] != ids[usage.MAIN]:
            ids[agent["handle"]] = agent["id"]
    return ids


def _person_id(root: str, person: str) -> str:
    return views.owner_id(root) if person == usage.OWNER else person


def _person_key(root: str, user_id: str) -> str:
    return usage.OWNER if user_id == views.owner_id(root) else user_id


def _iso_day(day: date) -> str:
    return day.isoformat()


def _since(value: Optional[str]) -> date:
    if value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            pass
    return usage.month_start()


def report(root: str, since: Optional[str]) -> Dict[str, Any]:
    """GET …/usage/report：从 since 起按 agent、按种类。agent 没有美元预算（额度按 token 记），预算填 0。"""
    start = _since(since)
    rows = usage.entries(root, start)
    ids, names = _agent_ids(root), {agent["id"]: agent for agent in views.agents(root)}
    kinds: Dict[str, Dict[str, Any]] = {}
    agents: Dict[str, Dict[str, Any]] = {}
    for entry in rows:
        kind = kinds.setdefault(entry.kind, {"kind": entry.kind, "calls": 0, "partialCalls": 0, "inputTokens": 0,
                                             "outputTokens": 0, "costUsd": 0.0})
        kind["calls"] += entry.calls
        kind["partialCalls"] += entry.unpriced_calls          # 缺价格、没折成美元的调用
        kind["inputTokens"] += entry.prompt
        kind["outputTokens"] += entry.completion
        kind["costUsd"] += entry.cost
        agent_id = ids.get(entry.agent)
        if agent_id is None:
            continue                                  # 已不在名册里的 agent：不混进别人名下
        agent = names.get(agent_id) or {"handle": entry.agent, "name": entry.agent}
        row = agents.setdefault(agent_id, {"agentId": agent_id, "handle": agent["handle"], "name": agent["name"],
                                           "calls": 0, "inputTokens": 0, "outputTokens": 0, "costUsd": 0.0,
                                           "monthlyBudgetUsd": 0, "budgetIsDefault": True, "budgetUsedPercent": 0})
        row["calls"] += entry.calls
        row["inputTokens"] += entry.prompt
        row["outputTokens"] += entry.completion
        row["costUsd"] += entry.cost
    for row in [*kinds.values(), *agents.values()]:
        row["costUsd"] = round(row["costUsd"], 6)
    return {"since": _iso_day(start), "defaultAgentBudgetUsd": 0,
            "totalUsd": round(sum(entry.cost for entry in rows), 6),
            "byKind": list(kinds.values()), "agents": sorted(agents.values(), key=lambda row: -row["costUsd"])}


def plans(root: str) -> Dict[str, Any]:
    """GET …/usage/plans：这里没有订阅套餐账号（accounts 空），只有 API 用量。"""
    start = usage.month_start()
    rows = usage.entries(root, start)
    ids = _agent_ids(root)
    names = {agent["id"]: agent["name"] for agent in views.agents(root)}
    api: Dict[str, Dict[str, Any]] = {}
    for entry in rows:
        agent_id = ids.get(entry.agent)
        if agent_id is None:
            continue
        row = api.setdefault(agent_id, {"agentId": agent_id, "name": names.get(agent_id, entry.agent), "tokens": 0,
                                        "budgetPercent": 0, "costUsd": 0.0, "budgetUsd": 0, "budgetIsDefault": True})
        row["tokens"] += entry.prompt + entry.completion
        row["costUsd"] += entry.cost
    for row in api.values():
        row["costUsd"] = round(row["costUsd"], 6)
    return {"month": start.strftime("%Y-%m"), "money": True, "accounts": [], "api": list(api.values())}


def _people_rows(root: str) -> List[Dict[str, Any]]:
    from src.web.app_members import members
    return members(root)


def people(root: str, month: Optional[str]) -> Dict[str, Any]:
    """GET …/usage/people：每个人这个月花了多少、占上限多少（只给所有者）。"""
    start = _month(month)
    until = min(usage.next_month(start) - timedelta(days=1), datetime.now(timezone.utc).date())
    rows = usage.entries(root, start, until)
    ids = _agent_ids(root)
    state = MemberRegistry(root).snapshot()
    current = usage.limits(MemberRegistry(root))
    shown = []
    for person in _people_rows(root):
        key = _person_key(root, person["userId"])
        mine = [entry for entry in rows if entry.person == key]
        spent = sum(entry.cost for entry in mine)
        by_agent: Dict[str, float] = {}
        for entry in mine:
            agent_id = ids.get(entry.agent)
            if agent_id:
                by_agent[agent_id] = by_agent.get(agent_id, 0.0) + entry.prompt + entry.completion
        total_tokens = sum(by_agent.values()) or 1
        limit = usage.view(state, key, spent, money=True)
        shown.append({**{k: limit[k] for k in limit}, "limitRoleId": limit.get("limitRoleId"),
                      "userId": person["userId"], "name": person["name"], "image": person.get("image"),
                      "owner": key == usage.OWNER, "roleIds": list(person.get("roleIds") or []),
                      "runs": len({entry.task for entry in mine if entry.task}),
                      "costUsd": round(spent, 6), "estimated": bool(spent),
                      "agents": [{"agentId": agent_id, "share": round(tokens / total_tokens * 100)}
                                 for agent_id, tokens in sorted(by_agent.items(), key=lambda row: -row[1])],
                      "caresFor": [], "created": 0,
                      "lastActiveAt": _iso_day(max(entry.day for entry in mine)) if mine else None, "now": None})
    return {"month": start.strftime("%Y-%m"), "since": _iso_day(start), "until": _iso_day(until),
            "totalUsd": round(sum(entry.cost for entry in rows), 6), "plansOnly": False,
            "limitsOn": any(row["limitFrom"] not in ("none", "owner") for row in shown) or current.get("defaultUsd") is not None,
            "people": shown, "live": []}


def _month(value: Optional[str]) -> date:
    if value:
        try:
            return date.fromisoformat(value[:7] + "-01")
        except ValueError:
            pass
    return usage.month_start()


def person(root: str, user_id: str, *, money: bool, month: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """GET …/usage/people/{id}：一个人的这个月。money 为假（成员看自己）时金额换成占上限的百分比。"""
    found = next((row for row in _people_rows(root) if row["userId"] == user_id), None)
    if found is None:
        return None
    key = _person_key(root, user_id)
    start = _month(month)
    until = min(usage.next_month(start) - timedelta(days=1), datetime.now(timezone.utc).date())
    mine = [entry for entry in usage.entries(root, start, until) if entry.person == key]
    state = MemberRegistry(root).snapshot()
    spent = sum(entry.cost for entry in mine)
    limit = usage.view(state, key, spent, money=money)
    limit_usd = usage.limit_of(state, key)[0]

    def amount(value: float) -> float:
        if money:
            return round(value, 6)
        return round(value / limit_usd * 100, 2) if limit_usd else 0

    ids = _agent_ids(root)
    days: Dict[str, Dict[str, float]] = {}
    agents: Dict[str, Dict[str, Any]] = {}
    for entry in mine:
        agent_id = ids.get(entry.agent)
        if agent_id is None:
            continue
        day = days.setdefault(_iso_day(entry.day), {})
        day[agent_id] = day.get(agent_id, 0.0) + entry.cost
        row = agents.setdefault(agent_id, {"agentId": agent_id, "runs": set(), "amount": 0.0})
        row["amount"] += entry.cost
        if entry.task:
            row["runs"].add(entry.task)
    return {"userId": user_id, "name": found["name"], "image": found.get("image"),
            "roleIds": list(found.get("roleIds") or []), "owner": key == usage.OWNER, "money": money,
            "estimated": bool(spent), "renewsAt": usage.next_month(start).isoformat() + "T00:00:00Z",
            "since": _iso_day(start), "until": _iso_day(until), "limit": limit, "total": amount(spent),
            "days": [{"date": day, "agents": {agent: amount(value) for agent, value in values.items()}}
                     for day, values in sorted(days.items())],
            "agents": [{"agentId": row["agentId"], "runs": len(row["runs"]), "amount": amount(row["amount"])}
                       for row in agents.values()],
            "recent": [], "created": []}


def agent_usage(root: str, agent_id: str) -> Optional[Dict[str, Any]]:
    """GET …/agents/{id}/usage：这个 agent 这个月的 token（只有 API，一个来源）。"""
    ids = {value: key for key, value in _agent_ids(root).items()}
    if agent_id not in ids:
        return None
    start = usage.month_start()
    mine = [entry for entry in usage.entries(root, start) if entry.agent == ids[agent_id]]
    tokens = sum(entry.prompt + entry.completion for entry in mine)
    days: Dict[str, int] = {}
    for entry in mine:
        days[_iso_day(entry.day)] = days.get(_iso_day(entry.day), 0) + entry.prompt + entry.completion
    cost = round(sum(entry.cost for entry in mine), 6)
    sources = [{"key": "api", "type": "api", "kind": None, "account": None, "removed": False, "tokens": tokens,
                "share": 100 if tokens else 0, "lastDay": max(days) if days else None, "now": True,
                "costUsd": cost, "budgetPercent": 0}] if tokens else []
    return {"month": start.strftime("%Y-%m"), "money": True, "tokens": tokens, "equivalentUsd": cost,
            "sources": sources, "days": [{"date": day, "tokens": {"api": value}} for day, value in sorted(days.items())],
            "current": None}


def usage_queue(root: str, agent_id: str) -> Optional[Dict[str, Any]]:
    """GET …/agents/{id}/usage-queue：在等额度的事（专员额度用完时排着的）。"""
    ids = {value: key for key, value in _agent_ids(root).items()}
    if agent_id not in ids:
        return None
    staff = ids[agent_id]
    rows = []
    for task in views._staff_tasks(root):
        dispatch = (task.collaboration or {}).get("dispatch") or {}
        quota = (task.collaboration or {}).get("quota") or {}
        if dispatch.get("member") != staff or task.status != "blocked" or quota.get("status") != "waiting":
            continue
        where = dispatch.get("channel") or {}
        initiator = dispatch.get("initiator") or {}
        rows.append({"runId": task.id, "kind": "routine" if dispatch.get("routine") else "conversation",
                     "channelId": where.get("id") or views.dm_channel_id(agent_id), "threadId": where.get("thread"),
                     "channelKind": "channel" if where else "dm", "channelName": None,
                     "messageId": where.get("message") or task.id,
                     "askers": [initiator["id"]] if initiator.get("kind") == "member" else [views.owner_id(root)],
                     "since": views._iso(task.updated or task.created), "after": {"count": 0, "names": []},
                     "state": "queued", "title": None, "mayStop": True})
    return {"agentId": agent_id, "reason": "budget" if rows else None, "plan": None, "resetsAt": None,
            "checkedAt": None, "sharedWith": [], "rows": rows, "hidden": 0, "mayStopAll": True, "closed": []}
