"""Confirmed dispatches for unsent channel drafts and their private review handoff."""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import logging
import sqlite3
import time
import uuid
from hashlib import sha256

from src.agents.principal import Principal
from src.web import app_board, app_channel_agents, app_channel_recommendations, app_channels, app_views
from src.web.auth import get_api_token
from src.workflow.board.store import Board, MAX_TITLE
from src.workflow.members.registry import Damaged, Missing, Refused

_TTL = 15 * 60
_CONTEXT = b"vortoone:channel-recommendation:v1\0"
_log = logging.getLogger(__name__)


class DispatchError(ValueError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


def _signature(payload: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), _CONTEXT + payload, sha256).hexdigest()


def preview_token(org_id: str, channel_id: str, actor: Principal, draft: str, owner_id: str | None, result: dict) -> str | None:
    secret = get_api_token()
    if not secret or result["kind"] not in {"person", "agent", "collaboration"}:
        return None
    plan = {"id": "task-" + uuid.uuid4().hex[:24], "org": org_id, "channel": channel_id,
            "actor": {"kind": actor.kind, "id": actor.id}, "owner": owner_id, "draft": draft,
            "kind": result["kind"], "primary": result["primary"], "assistant": result.get("assistant"),
            "expires": int(time.time()) + _TTL}
    payload = base64.urlsafe_b64encode(json.dumps(plan, ensure_ascii=False, separators=(",", ":")).encode())
    return payload.decode("ascii") + "." + _signature(payload, secret)


def open_confirmation(token: str, org_id: str, channel_id: str, actor: Principal) -> dict:
    secret = get_api_token()
    try:
        value, signature = token.split(".", 1)
        payload = value.encode("ascii")
        if not secret or not hmac.compare_digest(signature.encode("ascii"), _signature(payload, secret).encode()):
            raise ValueError
        plan = json.loads(base64.b64decode(payload, altchars=b"-_", validate=True))
    except ValueError:
        raise DispatchError(400, "invalid", "接手建议凭据无效，请重新生成建议") from None
    if plan["org"] != org_id or plan["channel"] != channel_id or plan["actor"] != {"kind": actor.kind, "id": actor.id}:
        raise DispatchError(404, "not_found", "没有这条接手建议")
    return plan


def _authorize(root: str, service, actor: Principal, plan: dict) -> None:
    candidates, owner_id = app_channel_recommendations.candidates_in_channel(
        root, service, actor, plan["channel"], plan["draft"])
    allowed = {(row["type"], row["id"]) for row in candidates}
    targets = (plan["primary"], plan["assistant"])
    # Authorize the same displayed people and work against the current roster and issue owner.
    if owner_id != plan["owner"] or any(row and (row["type"], row["id"]) not in allowed for row in targets):
        raise DispatchError(409, "recommendation.changed", "频道人选或负责人已变化，请重新生成建议")
    if plan["kind"] != "agent" and not app_board.can_edit(root, actor):
        raise Refused("你没有派发看板任务的权限")


def _reserve(root: str, service, actor: Principal, plan: dict) -> dict | None:
    board = Board(root)
    existing = board.channel_dispatch(plan["id"])
    if existing is not None and existing[1] is not None:
        return existing[1]
    if existing is None and time.time() >= plan["expires"]:
        raise DispatchError(410, "recommendation.expired", "接手建议已过期，请重新生成")
    _authorize(root, service, actor, plan)
    board.reserve_channel_dispatch(plan["id"], plan)
    return None


def _person_issue(plan: dict, draft_result: str | None = None) -> tuple[dict, dict, dict, str]:
    author = {"type": "person", "id": plan["actor"]["id"]}
    owner = plan["primary"]
    assignee = {"type": "person", "id": owner["id"]}
    audience = {"mode": "people", "userIds": list(dict.fromkeys([author["id"], assignee["id"]])), "channelId": None}
    description = plan["draft"]
    if draft_result is not None:
        description += "\n\n## 待审核的智能体草稿\n\n" + draft_result
    body = {"title": owner["work"][:MAX_TITLE], "description": description, "assignee": assignee}
    return author, body, audience, "in_review" if draft_result is not None else "todo"


def complete(root: str, task) -> dict | None:
    """Resolve a private worker result, or create its human review card once."""
    dispatch = (task.collaboration or {}).get("dispatch", {})
    where = dispatch.get("channel") or {}
    if where.get("privateDraft") is not True or task.status not in app_channel_agents.TERMINAL:
        return None
    board = Board(root)
    record = board.channel_dispatch(task.id)
    if record is None:
        return None
    plan, receipt = record
    if receipt is not None:
        return receipt
    expected = plan["assistant"] or plan["primary"]
    actor = Principal(plan["actor"]["kind"], plan["actor"]["id"], root)
    initiator = (dispatch.get("initiator") or {}).get("id")
    if (plan["channel"] != where["id"] or where.get("actorId") != actor.id or expected["type"] != "agent"
            or expected["id"] != app_views.member_agent_id(root, dispatch["member"])
            or initiator != (actor.id if actor.is_member else None)):
        return None
    draft_result = str(task.result or "").replace("\0", "").strip()
    if task.status != "done" or not draft_result:
        return board.finish_channel_dispatch(task.id, {"status": "agent_failed", "taskId": task.id})
    try:
        if actor.is_owner:
            from src.workflow.home_identity import ensure_home_identity

            if actor.id != app_views.owner_id(root) or where.get("ownerIdentity") != ensure_home_identity(root):
                raise Refused("工作区所有者已变化")
        _authorize(root, app_channels.channels(root), actor, plan)
    except (DispatchError, Missing, Refused):
        return board.finish_channel_dispatch(task.id, {"status": "agent_failed", "taskId": task.id})
    if plan["kind"] == "agent":
        return board.finish_channel_dispatch(task.id, {"status": "agent_completed", "taskId": task.id, "result": draft_result})
    return board.finish_channel_dispatch(task.id, {"status": "ready_for_review", "taskId": task.id},
                                         issue=_person_issue(plan, draft_result))


def deliver(root: str, task) -> None:
    """Worker callback. A later receipt refresh or service sweep can repair a temporary write failure."""
    try:
        complete(root, task)
    except (sqlite3.Error, Damaged, OSError, ValueError):
        _log.warning("Unable to save a confirmed recommendation delivery", exc_info=True)


async def confirm(root: str, service, actor: Principal, plan: dict) -> dict:
    receipt = await asyncio.to_thread(_reserve, root, service, actor, plan)
    if receipt is not None:
        return receipt
    if plan["kind"] == "person":
        return await asyncio.to_thread(Board(root).finish_channel_dispatch, plan["id"], {"status": "assigned"},
                                       issue=_person_issue(plan))
    task = await asyncio.to_thread(app_channel_agents.recommendation_task, root, plan["channel"], plan["id"])
    if task is None:
        target = plan["assistant"] or plan["primary"]
        task = app_channel_agents.start_recommendation(
            root, service, actor, plan["channel"], agent_id=target["id"], draft=plan["draft"],
            work=target["work"], task_id=plan["id"])
    if task.status in app_channel_agents.TERMINAL:
        receipt = await asyncio.to_thread(complete, root, task)
        if receipt is not None:
            return receipt
    return {"status": "awaiting_draft" if plan["kind"] == "collaboration" else "agent_started", "taskId": task.id}
