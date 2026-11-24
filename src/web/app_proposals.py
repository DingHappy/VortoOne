"""界面「提议」：agent 提的团队背景修改（见 workflow/members/context_proposals.py）。

所有者看全部、决定；成员只看得到自己让 agent 提的，可以「请所有者看看」。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.agents.principal import Principal
from src.web import app_views as views
from src.workflow.members.context_proposals import ContextProposals


def _people(root: str) -> Dict[str, str]:
    from src.workflow.members.registry import MemberRegistry
    names = {member_id: str(member.get("name") or member_id)
             for member_id, member in MemberRegistry(root).snapshot()["members"].items()}
    owner = views.person(root)
    names[owner["userId"]] = owner["name"]
    return names


def _row(root: str, item: Dict[str, Any], current: str, names: Dict[str, str], actor: Principal) -> Dict[str, Any]:
    owner_id = views.owner_id(root)
    asker = item["initiator"] or owner_id
    agent = views.member_agent_id(root, item["by"]) if item["by"] else ""
    evidence = [{"where": "agent", "what": item["why"] or item["title"], "type": "agent"}]
    if item["tainted"]:
        evidence.append({"where": "external", "type": "agent",
                         "what": "提这条时，这一轮读过外部内容（频道里的消息、网页或别处发来的东西）：请看清每一句再决定。"})
    decided = item["decidedBy"]
    return {"id": item["id"], "agentId": "", "proposedByAgentId": agent, "target": {"type": "org_context"},
            "access": None, "settings": None,
            "requestedBy": {"userId": asker, "name": names.get(asker, asker)},
            "askedAt": item["askedAt"], "undoActionId": None, "title": item["title"], "why": item["why"],
            "whyCode": "asked", "evidence": evidence, "before": item["before"], "after": item["after"],
            "status": item["status"], "stale": ContextProposals.stale(item, current),
            "canDecide": actor.is_owner and item["status"] == "pending", "decider": {"type": "account_owner"},
            "createdAt": item["createdAt"],
            "decidedBy": {"userId": decided, "name": names.get(decided, decided)} if decided else None,
            "decidedAt": item["decidedAt"]}


def listing(root: str, actor: Principal, ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    from src.memory.assistant_profile import load_home_context
    items = ContextProposals(root).list()
    if not actor.is_owner:
        items = [item for item in items if item["initiator"] == actor.id]
    if ids is not None:
        items = [item for item in items if item["id"] in ids]
    pending = sorted((i for i in items if i["status"] == "pending"), key=lambda i: i["createdAt"], reverse=True)
    others = sorted((i for i in items if i["status"] != "pending"), key=lambda i: i["createdAt"], reverse=True)
    current, names = load_home_context(root), _people(root)
    return [_row(root, item, current, names, actor) for item in [*pending, *others][:200]]


def one(root: str, actor: Principal, item: Dict[str, Any]) -> Dict[str, Any]:
    from src.memory.assistant_profile import load_home_context
    return _row(root, item, load_home_context(root), _people(root), actor)
