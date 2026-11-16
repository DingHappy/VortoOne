"""新版 Desktop 界面（frontend/）使用的接口：/api/app/*、/api/instance 与 /api/ws。

路径和字段按界面的客户端合同（frontend/packages/client/src/api/http-client.ts）来，数据来自
src/web/app_views.py 对现有记录的只读整理。本文件是第一批：登录、空间、角色名册、与主助理
的私聊记录，以及界面启动时会读取的其余列表。

还没有对应能力的列表返回「空」的合法结构，不编造内容；写入类接口没接的一律不注册，
界面收到 404 而不是假成功。发消息、审批和实时事件在后续批次接入现有会话与确认门。
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict

from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.gateway import app_protocol as F
from src.web import app_views as views
from src.web import app_staff
from src.web.app_bridge import NoModel, existing_bridge, main_chat_bridge

router = APIRouter(tags=["app"])

_ORG = "/api/app/organizations/{org_id}"
API_CONTRACT = 1


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


class _NotThisHome(Exception):
    pass


def _root(org_id: str) -> str:
    root = os.getcwd()
    if org_id != views.home_id(root):
        raise _NotThisHome()
    return root


def install_error_handler(app) -> None:
    @app.exception_handler(_NotThisHome)
    async def _not_this_home(_request, _error_value):  # noqa: ANN001
        return _error(404, "not_found", "没有这个空间")


# ── 登录前可读 ────────────────────────────────────────────────────────────────

@router.get("/api/instance")
async def instance(request: Request):
    from src.web.auth import get_api_token
    from importlib import metadata
    version = "dev"
    for package in ("vortoone", "vortocode"):  # 包名改名（ADR-018 批次 E）前后都认
        try:
            version = metadata.version(package)
            break
        except metadata.PackageNotFoundError:
            continue
    return {"name": "VortoOne", "version": version, "api": API_CONTRACT, "minApi": API_CONTRACT,
            "url": str(request.base_url).rstrip("/"),
            "signIn": {"email": bool(get_api_token()), "google": False, "github": False, "apple": False},
            "signup": "invite", "cloud": False}


@router.get("/api/app/auth-config")
async def auth_config():
    return {"socialProviders": {"google": False, "github": False}, "signupOpen": False, "firstRun": False,
            "passwordReset": False, "feedback": False}


class _SignIn(BaseModel):
    email: str = ""
    password: str = ""


@router.post("/api/auth/sign-in/email")
async def sign_in(body: _SignIn, request: Request, response: Response):
    """界面的登录框：密码一栏填主人令牌，校验与种 Cookie 沿用 /api/auth/login，邮箱不参与判断。"""
    from src.web.routers.auth_routes import LoginBody, login
    result = await login(LoginBody(token=body.password), request, response)
    if isinstance(result, JSONResponse):
        return JSONResponse({"code": "INVALID_EMAIL_OR_PASSWORD", "message": "令牌不正确"}, status_code=401)
    return {"redirect": False, "token": None, "user": views.me(os.getcwd())["user"]}


@router.post("/api/auth/sign-out")
async def sign_out(response: Response):
    from src.web.routers.auth_routes import logout
    await logout(response)
    return {"success": True}


# ── 主人与空间 ────────────────────────────────────────────────────────────────

@router.get("/api/app/me")
async def me():
    return views.me(os.getcwd())


@router.get("/api/app/models")
async def models():
    return {"models": [], "defaultModel": ""}


@router.get(_ORG + "/overview")
async def overview(org_id: str):
    return views.overview(_root(org_id))


@router.get(_ORG + "/settings")
async def settings(org_id: str):
    return views.settings(_root(org_id))


@router.get(_ORG + "/members")
async def members(org_id: str):
    return {"members": [views.person(_root(org_id))]}


@router.get(_ORG + "/people")
async def people(org_id: str):
    return {"people": [{**views.person(_root(org_id)), "online": True, "presence": "online", "status": None,
                        "lastSeenAt": views.now_iso()}]}


@router.get(_ORG + "/roles")
async def roles(org_id: str):
    _root(org_id)
    return {"roles": [], "areas": [], "colors": [], "icons": []}


# ── 角色 ─────────────────────────────────────────────────────────────────────

@router.get(_ORG + "/agents")
async def agents(org_id: str):
    return {"agents": views.agents(_root(org_id))}


@router.get(_ORG + "/agent-status")
async def agent_status(org_id: str):
    return {"agents": views.agent_statuses(_root(org_id))}


@router.get(_ORG + "/ai-status")
async def ai_status(org_id: str):
    return views.ai_status(_root(org_id))


# ── 私聊与消息 ────────────────────────────────────────────────────────────────

@router.get(_ORG + "/dms")
async def dms(org_id: str):
    return {"dms": [views.main_dm(_root(org_id))]}


class _OpenDm(BaseModel):
    agentId: str = ""
    userId: str = ""


@router.post(_ORG + "/dms")
async def open_dm(org_id: str, body: _OpenDm):
    root = _root(org_id)
    if body.agentId == views.main_agent_id(root):
        dm = views.main_dm(root)
    else:
        member_id = views.member_for_channel(root, views.dm_channel_id(body.agentId))
        if member_id is None:
            return _error(404, "not_found", "没有这个角色")
        dm = views.member_dm(root, member_id)
    return {"channelId": dm["channelId"], "dm": dm}


def _main_channel(org_id: str, channel_id: str) -> str | None:
    root = _root(org_id)
    return root if views.is_main_dm(root, channel_id) else None


def _member_channel(org_id: str, channel_id: str) -> tuple[str, str] | None:
    """这个对话是不是与某位专员的私聊；是则返回 (家, 专员)。"""
    root = _root(org_id)
    member_id = views.member_for_channel(root, channel_id)
    return (root, member_id) if member_id else None


_NEEDS_OWNER = "专员对话需要携带主人令牌（Authorization: Bearer）"


def _staff_read(action):
    try:
        return action()
    except (ValueError, OSError) as error:
        return _error(409, "staff_unavailable", str(error))


@router.get(_ORG + "/channels/{channel_id}/members")
async def channel_members(org_id: str, channel_id: str):
    root = _main_channel(org_id, channel_id)
    member = _member_channel(org_id, channel_id) if root is None else None
    if member:
        return {"members": [{"type": "person", "id": views.owner_id(member[0])},
                            {"type": "agent", "id": views.member_agent_id(*member)}]}
    if root is None:
        return _error(404, "not_found", "没有这个对话")
    return {"members": views.channel_members(root)}


@router.get(_ORG + "/channels/{channel_id}/messages")
async def channel_messages(org_id: str, channel_id: str, request: Request, before: str = "", limit: int = 100,
                           threadId: str = ""):  # noqa: N803 —— 界面的查询参数名
    root = _main_channel(org_id, channel_id)
    member = _member_channel(org_id, channel_id) if root is None else None
    if member:
        if not app_staff.owner_proof(request):
            return _error(403, "owner_proof_required", _NEEDS_OWNER)
        if threadId:
            return {"messages": []}
        return _staff_read(lambda: {"messages": views.member_messages(*member, before=before, limit=limit)})
    if root is None:
        return _error(404, "not_found", "没有这个对话")
    if threadId:
        return {"messages": []}
    messages = views.main_messages(root, before=before, limit=limit)
    bridge = existing_bridge(root)
    if bridge is not None and not before:
        messages += [views.approval_card(root, item) for item in reversed(bridge.pending_confirmations())]
    return {"messages": messages}


class _Post(BaseModel):
    body: str = ""
    clientId: str | None = None
    threadId: str | None = None


@router.post(_ORG + "/channels/{channel_id}/messages")
async def post_message(org_id: str, channel_id: str, body: _Post, request: Request):
    """主人在私聊里发一条：交给主要聊天开一个回合（前面还有回合就排队）。应答是这条消息本身，
    助理的回复之后经实时连接或下一次读取出现。"""
    root = _main_channel(org_id, channel_id)
    member = _member_channel(org_id, channel_id) if root is None else None
    if root is None and member is None:
        return _error(404, "not_found", "没有这个对话")
    text = body.body.strip()
    if not text:
        return _error(400, "empty_message", "消息不能为空")
    if len(text) > 20000:
        return _error(413, "message_too_long", "消息太长")
    if body.threadId:
        return _error(400, "threads_unsupported", "这里还不支持在话题里回复")
    client_id = (body.clientId or "")[:120] or None
    if member:
        if len(text) > 4000:
            return _error(413, "message_too_long", "交给专员的一件事最多 4000 字")
        if not app_staff.owner_proof(request):
            return _error(403, "owner_proof_required", _NEEDS_OWNER)
        return await asyncio.to_thread(_staff_read, lambda: app_staff.send(member[0], member[1], text, client_id))
    try:
        return await (await main_chat_bridge(root)).post(text, client_id)
    except NoModel:
        return _error(409, "no_ai", "还没有连接模型，没法回复")


@router.get(_ORG + "/channels/{channel_id}/active-runs")
async def channel_active_runs(org_id: str, channel_id: str, request: Request):
    root = _main_channel(org_id, channel_id)
    member = _member_channel(org_id, channel_id) if root is None else None
    if member:
        if not app_staff.owner_proof(request):
            return _error(403, "owner_proof_required", _NEEDS_OWNER)
        return _staff_read(lambda: {"runs": views.member_runs(*member)})
    if root is None:
        return _error(404, "not_found", "没有这个对话")
    bridge = existing_bridge(root)
    return {"runs": bridge.active_runs() if bridge else []}


@router.get(_ORG + "/active-runs")
async def active_runs(org_id: str):
    bridge = existing_bridge(_root(org_id))
    return {"runs": bridge.active_runs() if bridge else []}


@router.post(_ORG + "/runs/{run_id}/cancel", status_code=204)
async def cancel_run(org_id: str, run_id: str, request: Request):
    """「停止」：主要聊天的回合走现有 Desktop 的停止路径；专员手上的事走主人的停止决定。"""
    root = _root(org_id)
    if run_id.startswith("task-"):
        if not app_staff.owner_proof(request):
            return _error(403, "owner_proof_required", _NEEDS_OWNER)
        try:
            stopped = app_staff.stop(root, run_id)
        except (ValueError, OSError) as error:
            return _error(409, "staff_unavailable", str(error))
        return Response(status_code=204) if stopped else _error(409, "run_finished", "这件事已经不在进行")
    bridge = existing_bridge(root)
    if bridge is None or not await bridge.stop(run_id):
        return _error(409, "run_finished", "这一轮已经结束")
    return Response(status_code=204)


# ── 审批：内核发出的确认 ──────────────────────────────────────────────────────

@router.get(_ORG + "/approvals")
async def approvals(org_id: str, status: str = ""):
    root = _root(org_id)
    bridge = existing_bridge(root)
    if bridge is None or status not in {"", "pending"}:
        return {"approvals": []}
    return {"approvals": [views.approval(root, item) for item in bridge.pending_confirmations()]}


@router.get(_ORG + "/approvals/{approval_id}")
async def approval(org_id: str, approval_id: str):
    root = _root(org_id)
    bridge = existing_bridge(root)
    pending = bridge.pending(approval_id) if bridge else None
    if pending is None:
        return _error(404, "not_found", "这个确认已经不在等待")
    return views.approval(root, pending)


async def _decide(org_id: str, approval_id: str, approve: bool):
    root = _root(org_id)
    bridge = existing_bridge(root)
    decided = await bridge.decide(approval_id, approve) if bridge else None
    if decided is None:
        return _error(409, "approval_decided", "这个确认已经不在等待")
    return decided


class _Approve(BaseModel):
    reason: str | None = None
    always: bool = False
    via: str | None = None


@router.post(_ORG + "/approvals/{approval_id}/approve")
async def approve(org_id: str, approval_id: str, body: _Approve):
    if body.always:
        # 「以后都允许」在这里没有对应的授权：不把一次批准悄悄扩大成常设放行。
        return _error(400, "always_unsupported", "这里只能批准这一次")
    return await _decide(org_id, approval_id, True)


@router.post(_ORG + "/approvals/{approval_id}/refuse")
async def refuse(org_id: str, approval_id: str, body: _Approve):
    return await _decide(org_id, approval_id, False)


@router.get(_ORG + "/channels/{channel_id}/pins")
async def channel_pins(org_id: str, channel_id: str):
    if _main_channel(org_id, channel_id) is None and _member_channel(org_id, channel_id) is None:
        return _error(404, "not_found", "没有这个对话")
    return {"pins": []}


@router.get(_ORG + "/channels/{channel_id}/threads")
async def channel_threads(org_id: str, channel_id: str):
    if _main_channel(org_id, channel_id) is None and _member_channel(org_id, channel_id) is None:
        return _error(404, "not_found", "没有这个对话")
    return {"threads": [], "nextCursor": None}


# ── 启动时读取、目前没有对应记录的列表：返回空的合法结构 ─────────────────────────

_EMPTY: Dict[str, Any] = {
    "/former-agents": {"agents": []},
    "/agent-groups": {"groups": []},
    "/channels": {"channels": []},
    "/channel-categories": {"categories": []},
    "/archived-channels": {"channels": []},
    "/unread-tasks": {"tasks": []},
    "/unread-threads": {"count": 0},
    "/notifications/count": {"unread": 0, "waiting": 0},
    "/notifications": {"items": [], "nextCursor": None,
                       "counts": {"all": 0, "mentions": 0, "waiting": 0, "unread": 0, "reactions": 0}},
    "/waiting": {"items": [], "count": 0},
    "/missions": {"missions": [], "counts": {"active": 0, "waitingYou": 0, "closed": 0}},
    "/machines": {"machines": []},
    "/machines/outages": {"outages": [], "targets": []},
    "/runs": {"runs": []},
    "/usage": {"usage": []},
    "/issues": {"issues": []},
    "/task-manager": {"tasks": [], "ended": [], "activity": {}},
    "/proposals": {"proposals": []},
    "/team-memories": {"memories": []},
    "/skill-settings": {"agentsImprove": False},
    "/wiki/pages": {"pages": []},
    "/dashboards": {"home": None, "dashboards": [], "homeDashboardId": None},
    "/me/sidebar": {"items": {}},
    "/me/sidebar/order": {"order": views.SIDEBAR_SECTIONS},
    "/me/sidebar/tools": {"order": views.SIDEBAR_TOOLS},
    "/me/sidebar/view": {"view": {"agentSort": "workspace", "showAllAgents": True, "pins": None}},
}


def _register_empty(path: str, payload: Any) -> None:
    async def read(org_id: str):
        _root(org_id)
        return json.loads(json.dumps(payload))

    read.__name__ = "empty_" + path.strip("/").replace("/", "_").replace("-", "_")
    router.add_api_route(_ORG + path, read, methods=["GET"])


for _path, _payload in _EMPTY.items():
    _register_empty(_path, _payload)


# ── 记忆、例行工作、技能、制品 ────────────────────────────────────────────────

def _listing(build):
    """读取现有登记；登记损坏时如实报错，不当成空表。"""
    try:
        return build()
    except (ValueError, OSError) as error:
        return _error(409, "unavailable", str(error))


@router.get(_ORG + "/agents/{agent_id}/memories")
async def agent_memories(org_id: str, agent_id: str):
    if not _known_agent(org_id, agent_id):
        return _error(404, "not_found", "没有这个角色")
    return _listing(lambda: {"memories": views.memories(_root(org_id), agent_id)})


@router.get(_ORG + "/memories")
async def all_memories(org_id: str):
    root = _root(org_id)
    return _listing(lambda: {"memories": views.memories(root, views.main_agent_id(root))})


@router.get(_ORG + "/routines")
async def routines(org_id: str):
    root = _root(org_id)
    return _listing(lambda: {"routines": views.routines(root)})


@router.get(_ORG + "/skills")
async def skills(org_id: str):
    root = _root(org_id)
    return _listing(lambda: {"skills": [{k: v for k, v in skill.items() if k != "enabled"}
                                        for skill in views.skills(root)]})


@router.get(_ORG + "/agents/{agent_id}/skills")
async def agent_skills(org_id: str, agent_id: str):
    """个人技能只给主助理用；专员按岗位装配，不在这里。"""
    if not _known_agent(org_id, agent_id):
        return _error(404, "not_found", "没有这个角色")
    root = _root(org_id)
    return _listing(lambda: {"skills": views.skills(root) if agent_id == views.main_agent_id(root) else []})


@router.get(_ORG + "/artifacts")
async def artifacts(org_id: str, q: str = "", kind: str = "", kinds: str = ""):
    root = _root(org_id)
    wanted = {value for value in [kind, *kinds.split(",")] if value} or None
    return _listing(lambda: views.artifacts(root, query=q, kinds=wanted))


@router.get(_ORG + "/artifacts/{artifact_id}")
async def artifact(org_id: str, artifact_id: str, version: int | None = None):
    found = views.artifact(_root(org_id), artifact_id, version)
    return found if found is not None else _error(404, "not_found", "没有这个制品")


@router.get(_ORG + "/dashboards/home")
async def home_dashboard(org_id: str):
    """首页的默认版面：只有「等我处理」和「正在进行」两块，没有自定义组件。"""
    root = _root(org_id)
    created = views.person(root)["joinedAt"]
    return {"id": views.home_id(root), "kind": "home", "title": "", "about": "", "audience": "private",
            "roleIds": [], "editPolicy": "owner", "editRoleIds": [], "pinned": False, "position": 0,
            "owner": None, "ownerUserId": views.owner_id(root), "mine": True, "widgetCount": 0, "thumbs": [],
            "viewerCount": 1, "canEdit": False, "canManage": False, "proposal": None, "createdAt": created,
            "updatedAt": created, "widgets": [],
            "tiles": [{"key": "waiting", "position": 0, "w": 2, "h": 1},
                      {"key": "working", "position": 3, "w": 2, "h": 1}]}


@router.get(_ORG + "/computer-setup")
async def computer_setup(org_id: str):
    _root(org_id)
    return {"mode": "pair", "state": None, "step": None, "reason": None, "since": None, "requestedAt": None,
            "expectedReadyAt": None, "secondsLeft": None, "secondsWaited": None, "notifyByEmail": False,
            "specs": None, "machineId": None}


def _known_agent(org_id: str, agent_id: str) -> bool:
    return any(agent["id"] == agent_id for agent in views.agents(_root(org_id)))


@router.get(_ORG + "/agents/{agent_id}")
async def agent_detail(org_id: str, agent_id: str):
    agent = next((a for a in views.agents(_root(org_id)) if a["id"] == agent_id), None)
    if agent is None:
        return _error(404, "not_found", "没有这个角色")
    return views.agent_detail(agent)


@router.get(_ORG + "/agents/{agent_id}/desktop")
async def agent_desktop(org_id: str, agent_id: str):
    if not _known_agent(org_id, agent_id):
        return _error(404, "not_found", "没有这个角色")
    return {"available": False, "reason": "no_desktop", "detail": None, "running": False, "startedAt": None,
            "controllerUserId": None, "handoff": None, "viewers": 0, "canControl": False, "held": False,
            "browserOnDesktop": False}


@router.get(_ORG + "/agents/{agent_id}/browser")
async def agent_browser(org_id: str, agent_id: str):
    if not _known_agent(org_id, agent_id):
        return _error(404, "not_found", "没有这个角色")
    return {"allowedDomains": None, "available": False, "reason": "no_machine", "detail": None, "machine": None}


@router.get(_ORG + "/agents/{agent_id}/browser/handoff")
async def agent_browser_handoff(org_id: str, agent_id: str):
    if not _known_agent(org_id, agent_id):
        return _error(404, "not_found", "没有这个角色")
    return {"handoff": None}


@router.get(_ORG + "/agents/{agent_id}/scopes")
async def agent_scopes(org_id: str, agent_id: str):
    if not _known_agent(org_id, agent_id):
        return _error(404, "not_found", "没有这个角色")
    return {"scopes": []}


# ── 实时连接 ─────────────────────────────────────────────────────────────────

@router.websocket("/api/ws")
async def app_socket(websocket: WebSocket):
    """每个空间一条连接。订阅与主助理的私聊后，经桥收到这段会话的消息、运行状态和审批事件；
    实时只是尽力而为，界面在重连和订阅确认后会重新读取，内容以读取为准。"""
    from src.web.auth import ws_token_ok
    root = os.getcwd()
    if not ws_token_ok(websocket) or websocket.query_params.get("orgId") != views.home_id(root):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    await websocket.send_json(F.make_app_frame(F.READY, orgId=views.home_id(root), userId=views.owner_id(root)))
    bridge = None
    watches: Dict[str, asyncio.Task] = {}
    try:
        while True:
            try:
                frame = json.loads(await websocket.receive_text())
            except ValueError:
                continue
            kind = frame.get("type") if isinstance(frame, dict) else None
            if kind == "ping":
                await websocket.send_json(F.make_app_frame(F.PONG))
            elif kind == "subscribe":
                channel = str(frame.get("channelId") or "")
                if views.is_main_dm(root, channel):
                    bridge = await main_chat_bridge(root)
                    bridge.subscribe(websocket)
                    await websocket.send_json(F.make_app_frame(F.SUBSCRIBED, channelId=channel))
                elif views.member_for_channel(root, channel) and not app_staff.owner_proof(websocket):
                    await websocket.send_json(
                        F.make_app_frame(F.ERROR, code="owner_proof_required", channelId=channel))
                elif views.member_for_channel(root, channel):
                    if channel not in watches:
                        watches[channel] = asyncio.create_task(
                            app_staff.watch(websocket, root, views.member_for_channel(root, channel)))
                    await websocket.send_json(F.make_app_frame(F.SUBSCRIBED, channelId=channel))
                else:
                    await websocket.send_json(F.make_app_frame(F.ERROR, code="not_found", channelId=channel))
            elif kind == "unsubscribe":
                channel = str(frame.get("channelId") or "")
                if bridge is not None and views.is_main_dm(root, channel):
                    bridge.unsubscribe(websocket)
                watch = watches.pop(channel, None)
                if watch is not None:
                    watch.cancel()
                await websocket.send_json(F.make_app_frame(F.UNSUBSCRIBED, channelId=channel))
    except WebSocketDisconnect:
        return
    finally:
        for watch in watches.values():
            watch.cancel()
        if bridge is not None:
            bridge.unsubscribe(websocket)
