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
from src.web import app_members, app_staff, member_auth
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
    # 公开信息，不带凭据：Desktop 的首次引导要从它自己的窗口核对一个地址是不是 VortoOne（跨域读取）
    return JSONResponse({"name": "VortoOne", "version": version, "api": API_CONTRACT, "minApi": API_CONTRACT,
                         "url": str(request.base_url).rstrip("/"),
                         "signIn": {"email": bool(get_api_token()), "google": False, "github": False, "apple": False},
                         "signup": "invite", "cloud": False},
                        headers={"Access-Control-Allow-Origin": "*"})


@router.get("/api/app/auth-config")
async def auth_config():
    return {"socialProviders": {"google": False, "github": False}, "signupOpen": False, "firstRun": False,
            "passwordReset": False, "feedback": False}


class _SignIn(BaseModel):
    email: str = ""
    password: str = ""


@router.post("/api/app/sign-in-options")
async def sign_in_options(body: _SignIn):
    """单主人服务不按邮箱枚举账号；所有地址都走同一个令牌密码入口。"""
    return {"account": True, "password": True, "providers": [], "canSignUp": False}


@router.post("/api/auth/sign-in/email")
async def sign_in(body: _SignIn, request: Request, response: Response):
    """界面的登录框。密码一栏填的是主人令牌就按所有者登录（邮箱不参与判断，沿用 /api/auth/login）；
    不是的话按成员账号的邮箱加密码登录。两种失败回同一句话，看不出是哪一种没对上。"""
    from src.web.routers.auth_routes import LoginBody, login
    from src.workflow.members.accounts import Locked, MemberAccounts, WrongCredentials
    root = os.getcwd()
    result = await login(LoginBody(token=body.password), request, response)
    if not isinstance(result, JSONResponse):
        return {"redirect": False, "token": None, "user": views.me(root)["user"]}
    wrong = JSONResponse({"code": "INVALID_EMAIL_OR_PASSWORD", "message": "邮箱、密码或令牌不正确"}, status_code=401)
    if not views.owner_connection() or not member_auth.same_origin(request):
        return wrong
    accounts = MemberAccounts(root)
    try:
        account_id, token = await asyncio.to_thread(accounts.sign_in, body.email, body.password)
    except Locked as error:
        return JSONResponse({"code": "TOO_MANY_REQUESTS", "message": str(error)}, status_code=429)
    except (WrongCredentials, ValueError, OSError):
        return wrong
    account = accounts.get(account_id) or {}
    member_auth.set_cookie(request, response, token)
    return {"redirect": False, "token": None,
            "user": {"id": account.get("member") or account_id, "name": account.get("name"),
                     "email": account.get("email")}}


class _SignUp(BaseModel):
    name: str = ""
    email: str = ""
    password: str = ""


@router.post("/api/auth/sign-up/email")
async def sign_up(body: _SignUp, request: Request, response: Response):
    """凭邀请注册成员账号：必须带一份还有效的邀请令牌，邮箱要和邀请上写的一致。注册不等于加入——
    加入是下一步「接受邀请」。没有开放注册。"""
    from src.workflow.members.accounts import MemberAccounts
    from src.workflow.members.registry import MemberRegistry, login_name
    root = os.getcwd()
    closed = JSONResponse({"code": "SIGNUP_CLOSED", "message": "要有一份还有效的邀请才能注册"}, status_code=403)
    if not views.owner_connection() or not member_auth.same_origin(request):
        return closed
    try:
        invitation = MemberRegistry(root).invitation(request.headers.get("x-invitation-token", ""))
        if invitation is None or (invitation.get("email") and invitation["email"] != login_name(body.email)):
            return closed
        accounts = MemberAccounts(root)
        account_id = await asyncio.to_thread(accounts.create, body.email, body.name, body.password)
        token = accounts.open_session(account_id)
    except ValueError as error:
        return JSONResponse({"code": "INVALID_SIGN_UP", "message": str(error)}, status_code=422)
    except OSError:
        return JSONResponse({"code": "UNAVAILABLE", "message": "账号暂时建不了"}, status_code=409)
    member_auth.set_cookie(request, response, token)
    return {"token": None, "user": {"id": account_id, "name": body.name.strip(), "email": login_name(body.email)}}


class _Reset(BaseModel):
    token: str = ""
    newPassword: str = ""       # noqa: N815 —— 界面的字段名


@router.post("/api/auth/reset-password")
async def reset_password(body: _Reset, request: Request):
    """凭重置链接里的令牌改密码。改完这个账号在各处的登录全部退出，要用新密码重新登录。"""
    from src.workflow.members.accounts import MemberAccounts
    if not views.owner_connection() or not member_auth.same_origin(request):
        return JSONResponse({"code": "INVALID_TOKEN", "message": "重置链接不对或已过期"}, status_code=400)
    try:
        accounts = MemberAccounts(os.getcwd())
        account_id = await asyncio.to_thread(accounts.reset_password, body.token, body.newPassword)
        member_id = str((await asyncio.to_thread(accounts.get, account_id) or {}).get("member") or "")
        if member_id:                                   # 换了密码：他连上来的 AI 应用和个人令牌一并作废
            from src.workflow.ai_apps.store import AiApps
            await asyncio.to_thread(AiApps(os.getcwd()).disconnect_person, member_id)
    except LookupError:
        return JSONResponse({"code": "INVALID_TOKEN", "message": "重置链接不对或已过期"}, status_code=400)
    except ValueError as error:
        return JSONResponse({"code": "INVALID_PASSWORD", "message": str(error)}, status_code=422)
    except OSError:
        return JSONResponse({"code": "UNAVAILABLE", "message": "暂时改不了密码"}, status_code=409)
    return {"status": True}


# ── 外壳交接登录 ──────────────────────────────────────────────────────────────
# Desktop 外壳自己保管主人令牌，页面不该拿到它。外壳先用令牌换一个只能用一次、很快过期的交接码，
# 再让窗口打开带交接码的地址；服务端核对后种下登录 Cookie 并跳回首页。

_HANDOVER_SECONDS = 60
_HANDOVERS: Dict[str, float] = {}        # 交接码的摘要 → 过期时刻（单进程内存；重启即全部作废）


def _handover_key(code: str) -> str:
    import hashlib
    return hashlib.sha256(code.encode("utf-8", "surrogatepass")).hexdigest()


@router.post("/api/auth/handover")
async def handover_start(request: Request):
    """只认显式的 Bearer 主人令牌：已登录的页面不能替别的窗口再造登录。"""
    import secrets
    import time
    from src.web.auth import ct_eq, get_api_token
    token = get_api_token()
    authorization = request.headers.get("authorization", "")
    if not token or not authorization.startswith("Bearer ") or not ct_eq(authorization[7:].strip(), token):
        return _error(403, "forbidden", "交接登录需要主人令牌")
    now = time.monotonic()
    for key in [key for key, expires in _HANDOVERS.items() if expires <= now]:
        _HANDOVERS.pop(key, None)
    if len(_HANDOVERS) >= 32:
        return _error(429, "too_many", "等待中的交接登录太多，请稍后再试")
    code = secrets.token_urlsafe(32)
    _HANDOVERS[_handover_key(code)] = now + _HANDOVER_SECONDS
    return {"code": code, "expiresIn": _HANDOVER_SECONDS}


@router.get("/api/auth/handover/{code}")
async def handover_finish(code: str, request: Request):
    """用掉交接码：对了就登录并跳回首页；错的、过期的、用过的一律回登录页，不说明是哪一种。"""
    import time
    from fastapi.responses import RedirectResponse
    from src.web.auth import SESSION_COOKIE, get_api_token
    expires = _HANDOVERS.pop(_handover_key(code), None)
    token = get_api_token()
    if expires is None or expires <= time.monotonic() or not token:
        return RedirectResponse("/login", status_code=303)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                        secure=(request.url.scheme == "https"), path="/", max_age=30 * 24 * 3600)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.post("/api/auth/sign-out")
async def sign_out(request: Request, response: Response):
    """所有者的登录和成员的会话都退出。成员的会话在服务端也作废，不只是清 Cookie。"""
    from src.web.routers.auth_routes import logout
    from src.workflow.members.accounts import MemberAccounts
    await logout(response)
    token = request.cookies.get(member_auth.MEMBER_COOKIE, "")
    if token and member_auth.same_origin(request):
        try:
            MemberAccounts(os.getcwd()).sign_out(token)
        except (ValueError, OSError):
            pass                       # 账号文件读写不了：Cookie 照清，会话到期自己失效
    member_auth.clear_cookie(response)
    return {"success": True}


@router.post("/api/app/device-accounts/sign-out")
async def device_sign_out(request: Request, response: Response):
    """新版客户端退出当前账号（界面的「退出」走这里）。所有者和成员都用它；一个浏览器只登一个账号。"""
    await sign_out(request, response)
    return {"signedIn": False}


# ── 主人与空间 ────────────────────────────────────────────────────────────────

@router.get("/api/app/me")
async def me(request: Request):
    root = os.getcwd()
    account = getattr(request.state, "member_account", None)
    if account is None:
        return views.me(root)
    return await _members_call(lambda: app_members.me(root, member_auth.principal(request, root), account))


@router.get("/api/app/models")
async def models():
    return {"models": [], "defaultModel": ""}


def _who(request: Request, root: str):
    """调用人。所有者照旧；成员要在册，账号还没加入或已被移除的返回 None。"""
    who = member_auth.principal(request, root)
    if who.is_owner:
        return who
    return who if member_auth.member_id(getattr(request.state, "member_account", None)) else None


_NOT_MEMBER = "还不是这个空间的成员"


@router.get(_ORG + "/overview")
async def overview(org_id: str, request: Request):
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    if who.is_owner:
        return views.overview(root)
    return await _members_call(lambda: app_members.member_overview(root, who))


@router.get(_ORG + "/settings")
async def settings(org_id: str):
    return views.settings(_root(org_id))


@router.get(_ORG + "/members")
async def members(org_id: str, request: Request):
    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner and not member_auth.member_id(request.state.member_account):
        return _error(403, "forbidden", "还不是这个空间的成员")
    return await _members_call(lambda: {"members": app_members.members(root)})


@router.get(_ORG + "/people")
async def people(org_id: str, request: Request):
    root = _root(org_id)
    if _who(request, root) is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    return await _members_call(lambda: {"people": app_members.people(root)})


# ── 身份组与成员（界面里叫 roles / members）────────────────────────────────────
# 规矩在登记处和内核；这里把登记处的三种拒绝对成状态码。写入只在设了主人令牌的服务上开放。

async def _members_call(action, *, write: bool = False):
    from src.workflow.members.registry import Damaged, Missing, Refused
    if write and not views.owner_connection():
        return _error(403, "forbidden", "改成员和身份组需要已认证的主人连接")
    try:
        return await asyncio.to_thread(action)
    except Refused as error:
        return _error(403, "forbidden", str(error))
    except (Missing, LookupError) as error:
        return _error(404, "not_found", str(error))
    except (Damaged, OSError):
        # 登记读不出来：照实说，不拿空名单顶替（那会让界面以为没有成员、没有身份组）
        return _error(409, "unavailable", "成员登记读不出来，需要先修复")
    except ValueError as error:
        return _error(400, "invalid", str(error))


@router.get(_ORG + "/roles")
async def roles(org_id: str, request: Request):
    root = _root(org_id)
    who = member_auth.principal(request, root)
    if not who.is_owner and not member_auth.member_id(request.state.member_account):
        return _error(403, "forbidden", "还不是这个空间的成员")
    return await _members_call(lambda: app_members.roles(root, who))


@router.post(_ORG + "/roles")
async def create_role(org_id: str, body: Dict[str, Any], request: Request):
    root = _root(org_id)
    who = member_auth.principal(request, root)
    return await _members_call(lambda: {"id": app_members.create_role(root, who, body)}, write=True)


# 先于 /roles/{role_id} 登记：否则 "order" 会被当成一个身份组的 id
@router.put(_ORG + "/roles/order", status_code=204)
async def reorder_roles(org_id: str, body: Dict[str, Any], request: Request):
    root = _root(org_id)
    who = member_auth.principal(request, root)
    result = await _members_call(lambda: app_members.reorder_roles(root, who, body.get("ids")), write=True)
    return result if isinstance(result, JSONResponse) else Response(status_code=204)


@router.patch(_ORG + "/roles/{role_id}", status_code=204)
async def change_role(org_id: str, role_id: str, body: Dict[str, Any], request: Request):
    root = _root(org_id)
    who = member_auth.principal(request, root)
    result = await _members_call(lambda: app_members.change_role(root, who, role_id, body), write=True)
    return result if isinstance(result, JSONResponse) else Response(status_code=204)


@router.delete(_ORG + "/roles/{role_id}", status_code=204)
async def delete_role(org_id: str, role_id: str, request: Request):
    root = _root(org_id)
    who = member_auth.principal(request, root)
    result = await _members_call(lambda: app_members.delete_role(root, who, role_id), write=True)
    return result if isinstance(result, JSONResponse) else Response(status_code=204)


@router.put(_ORG + "/members/{user_id}/roles")
async def set_member_roles(org_id: str, user_id: str, body: Dict[str, Any], request: Request):
    root = _root(org_id)
    who = member_auth.principal(request, root)
    return await _members_call(
        lambda: {"roleIds": app_members.set_member_roles(root, who, user_id, body.get("roleIds"))}, write=True)


@router.delete(_ORG + "/members/{user_id}", status_code=204)
async def remove_member(org_id: str, user_id: str, request: Request):
    root = _root(org_id)
    who = member_auth.principal(request, root)
    result = await _members_call(lambda: app_members.remove_member(root, who, user_id), write=True)
    return result if isinstance(result, JSONResponse) else Response(status_code=204)


def _origin(request: Request) -> str:
    """本服务对外的地址，用来拼邀请和重置链接。取这次请求自己用的地址——发链接的人正是从这里访问的。"""
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme
    return f"{scheme if scheme in ('http', 'https') else 'http'}://{host}"


@router.get(_ORG + "/invitations")
async def invitations(org_id: str, request: Request):
    root = _root(org_id)
    who = member_auth.principal(request, root)
    return await _members_call(lambda: {"invitations": app_members.invitations(root, who)})


@router.post(_ORG + "/invitations")
async def invite(org_id: str, body: Dict[str, Any], request: Request):
    root = _root(org_id)
    who, origin = member_auth.principal(request, root), _origin(request)
    return await _members_call(lambda: app_members.invite(root, who, body, origin), write=True)


@router.delete(_ORG + "/invitations/{invitation_id}", status_code=204)
async def revoke_invitation(org_id: str, invitation_id: str, request: Request):
    root = _root(org_id)
    who = member_auth.principal(request, root)
    result = await _members_call(lambda: app_members.revoke_invitation(root, who, invitation_id), write=True)
    return result if isinstance(result, JSONResponse) else Response(status_code=204)


@router.post(_ORG + "/members/{user_id}/password-link")
async def password_link(org_id: str, user_id: str, request: Request):
    root = _root(org_id)
    who, origin = member_auth.principal(request, root), _origin(request)
    return await _members_call(lambda: app_members.password_link(root, who, user_id, origin), write=True)


# 下面两条不过鉴权中间件（收到邀请的人可能还没有账号）：凭邀请令牌本身，接受时再核对成员会话。

@router.get("/api/app/invitations/{token}")
async def invitation_preview(token: str):
    root = os.getcwd()
    result = await _members_call(lambda: app_members.invitation_preview(root, token))
    return result if result is not None else _error(404, "not_found", "邀请不存在或已失效")


@router.post("/api/app/invitations/{token}/accept")
async def accept_invitation(token: str, request: Request):
    """已登录的成员账号接受邀请、成为成员。所有者不需要、也不能接受邀请。"""
    from src.workflow.members.accounts import MemberAccounts
    from src.workflow.members.registry import MemberRegistry
    root = os.getcwd()
    account = member_auth.account(request)
    if account is None or not member_auth.same_origin(request):
        return _error(401, "unauthorized", "先登录或注册，再接受邀请")

    def join():
        registry, accounts = MemberRegistry(root), MemberAccounts(root)
        if member_auth.member_id(account):
            return {"orgId": views.home_id(root)}               # 已经是成员：再点一次不出错
        # 上一次接受到一半（成员记下了、账号还没指过去）：补上指向，不再消耗一份邀请
        mine = next((member_id for member_id, member in registry.snapshot()["members"].items()
                     if member.get("account") == account["id"]), None)
        if mine is None:
            mine = registry.accept_invitation(token, account["name"], email=account["email"], account=account["id"])
        accounts.bind_member(account["id"], mine)
        return {"orgId": views.home_id(root)}
    return await _members_call(join)


# ── 角色 ─────────────────────────────────────────────────────────────────────

async def _visible_ids(request: Request, root: str):
    """调用人看得到的 agent id；所有者是 None（全部）。成员不在册时抛 PermissionError。"""
    who = _who(request, root)
    if who is None:
        raise PermissionError(_NOT_MEMBER)
    if who.is_owner:
        return None
    return {agent["id"] for agent in await asyncio.to_thread(app_members.member_agents, root, who)}


@router.get(_ORG + "/agents")
async def agents(org_id: str, request: Request):
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    if who.is_owner:
        return {"agents": views.agents(root)}
    return await _members_call(lambda: {"agents": app_members.member_agents(root, who)})


@router.get(_ORG + "/agent-status")
async def agent_status(org_id: str, request: Request):
    root = _root(org_id)
    try:
        shown = await _visible_ids(request, root)
    except PermissionError as error:
        return _error(403, "forbidden", str(error))
    return {"agents": [row for row in views.agent_statuses(root) if shown is None or row["agentId"] in shown]}


@router.get(_ORG + "/ai-status")
async def ai_status(org_id: str, request: Request):
    root = _root(org_id)
    try:
        shown = await _visible_ids(request, root)
    except PermissionError as error:
        return _error(403, "forbidden", str(error))
    status = views.ai_status(root)
    if shown is not None:
        status["agents"] = [row for row in status["agents"] if row["agentId"] in shown]
    return status


# ── 私聊与消息 ────────────────────────────────────────────────────────────────

@router.get(_ORG + "/dms")
async def dms(org_id: str, request: Request):
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    if who.is_owner:
        return {"dms": [views.main_dm(root)]}
    return await _members_call(lambda: {"dms": app_members.member_dms(root, who)})


class _OpenDm(BaseModel):
    agentId: str = ""
    userId: str = ""


@router.post(_ORG + "/dms")
async def open_dm(org_id: str, body: _OpenDm, request: Request):
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    if not who.is_owner:
        dm = next((row for row in await asyncio.to_thread(app_members.member_dms, root, who)
                   if row["with"]["id"] == body.agentId), None)
        if dm is None:
            return _error(404, "not_found", "没有这个角色")
        return {"channelId": dm["channelId"], "dm": dm}
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


_NEEDS_OWNER = "专员对话需要主人证明：先登录，并从本服务自己的页面发起"


def _member_dm(request: Request, org_id: str, channel_id: str):
    """凭成员会话打开的私聊：返回 (家, 专员, 成员)；不是成员会话返回 None。
    成员看不到的 agent、以及主助理的私聊，一律当作没有这个对话（抛 LookupError）。"""
    if getattr(request.state, "member_account", None) is None:
        return None
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        raise PermissionError(_NOT_MEMBER)
    staff_id = views.member_for_channel(root, channel_id)
    if staff_id is None or app_members.member_staff(root, who, views.member_agent_id(root, staff_id)) is None:
        raise LookupError("没有这个对话")
    return root, staff_id, who


async def _as_member(request: Request, org_id: str, channel_id: str, answer):
    """成员会话走这里：answer(家, 专员, 成员) 给出回答。不是成员会话返回 None，交回原有逻辑。"""
    try:
        found = await asyncio.to_thread(_member_dm, request, org_id, channel_id)
    except PermissionError as error:
        return _error(403, "forbidden", str(error))
    except LookupError as error:
        return _error(404, "not_found", str(error))
    if found is None:
        return None
    from src.workflow.members.registry import Refused
    try:
        return await asyncio.to_thread(answer, *found)
    except Refused as error:
        return _error(403, "forbidden", str(error))
    except (ValueError, OSError) as error:
        return _error(409, "staff_unavailable", str(error))


def _staff_read(action):
    try:
        return action()
    except (ValueError, OSError) as error:
        return _error(409, "staff_unavailable", str(error))


@router.get(_ORG + "/channels/{channel_id}/members")
async def channel_members(org_id: str, channel_id: str, request: Request):
    answer = await _as_member(request, org_id, channel_id, lambda root, staff, who: {"members": [
        {"type": "person", "id": who.id}, {"type": "agent", "id": views.member_agent_id(root, staff)}]})
    if answer is not None:
        return answer
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
    answer = await _as_member(request, org_id, channel_id, lambda root, staff, who: {
        "messages": [] if threadId else views.member_messages(root, staff, before=before, limit=limit,
                                                              viewer=who.id)})
    if answer is not None:
        return answer
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
    answer = await _as_member(request, org_id, channel_id,
                              lambda root, staff, who: app_members.member_send(root, who, staff, text, client_id))
    if answer is not None:
        return answer
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
    answer = await _as_member(request, org_id, channel_id,
                              lambda root, staff, who: {"runs": views.member_runs(root, staff, viewer=who.id)})
    if answer is not None:
        return answer
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
async def channel_pins(org_id: str, channel_id: str, request: Request):
    answer = await _as_member(request, org_id, channel_id, lambda *_found: {"pins": []})
    if answer is not None:
        return answer
    if _main_channel(org_id, channel_id) is None and _member_channel(org_id, channel_id) is None:
        return _error(404, "not_found", "没有这个对话")
    return {"pins": []}


@router.get(_ORG + "/channels/{channel_id}/threads")
async def channel_threads(org_id: str, channel_id: str, request: Request):
    answer = await _as_member(request, org_id, channel_id, lambda *_found: {"threads": [], "nextCursor": None})
    if answer is not None:
        return answer
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
    "/waiting": {"items": [], "count": 0},
    "/missions": {"missions": [], "counts": {"active": 0, "waitingYou": 0, "closed": 0}},
    "/machines": {"machines": []},
    "/machines/outages": {"outages": [], "targets": []},
    "/issues": {"issues": []},
    "/task-manager": {"tasks": [], "ended": [], "activity": {}},
    "/proposals": {"proposals": []},
    "/connectors": {"connections": []},
    "/connectors/custom": {"servers": []},
    "/credentials": {"credentials": []},
    "/mail/accounts": {"accounts": []},
    "/calendar": {"events": [], "routines": [], "reminders": [], "deadlines": [], "proposals": []},
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


@router.get(_ORG + "/notifications")
async def notifications(org_id: str, filter: str = "", limit: int = 50):  # noqa: A002 —— 界面的查询参数名
    root = _root(org_id)
    return _listing(lambda: views.notifications(root, filter_name=filter, limit=limit))


@router.get(_ORG + "/runs")
async def runs(org_id: str, agentId: str = "", limit: int = 50):  # noqa: N803 —— 界面的查询参数名
    root = _root(org_id)
    return _listing(lambda: {"runs": views.runs(root, agent_id=agentId, limit=limit)})


@router.get(_ORG + "/usage")
async def usage(org_id: str):
    root = _root(org_id)
    return _listing(lambda: {"usage": views.usage(root)})


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


# ── 写入：记忆归档、例行工作启停与删除、技能启停 ─────────────────────────────
# 每一项都交给现有的登记处去改，规矩（版本核对、绑定核对、范围）在那里；这里只把界面的写法对上。

@router.patch(_ORG + "/memories/{memory_id}")
async def patch_memory(org_id: str, memory_id: str, body: Dict[str, Any]):
    """只接归档与恢复。改写内容、置顶、确认没有对应能力：拒绝，而不是假装改了。"""
    root = _root(org_id)
    if set(body) != {"archived"} or type(body["archived"]) is not bool:
        return _error(400, "unsupported", "这里只能归档或恢复记忆")
    try:
        memory = await asyncio.to_thread(views.set_memory_archived, root, memory_id, body["archived"])
    except (ValueError, OSError) as error:
        return _error(409, "unavailable", str(error))
    return memory if memory is not None else _error(404, "not_found", "没有这条记忆")


def _routine_write(root: str, routine_id: str, **change):
    if not views.owner_connection():
        return _error(403, "forbidden", "例行工作的启停与删除需要已认证的主人连接")
    try:
        return views.control_routine(root, routine_id, **change)
    except LookupError:
        return _error(404, "not_found", "没有这条例行工作")
    except (ValueError, OSError) as error:
        return _error(409, "conflict", str(error))


@router.patch(_ORG + "/routines/{routine_id}")
async def patch_routine(org_id: str, routine_id: str, body: Dict[str, Any]):
    """只接启用与停用。改名、改排期、改交代的话要经主助理提议、主人批准，不能从这里直接改。"""
    root = _root(org_id)
    if set(body) != {"active"} or type(body["active"]) is not bool:
        return _error(400, "unsupported", "这里只能启用或停用；其它修改请交给助理提议")
    return await asyncio.to_thread(_routine_write, root, routine_id, enabled=body["active"])


@router.delete(_ORG + "/routines/{routine_id}")
async def delete_routine(org_id: str, routine_id: str):
    root = _root(org_id)
    result = await asyncio.to_thread(_routine_write, root, routine_id, retire=True)
    return result if isinstance(result, JSONResponse) else Response(status_code=204)


@router.get(_ORG + "/skills/{skill_id}")
async def skill(org_id: str, skill_id: str):
    root = _root(org_id)
    detail = await asyncio.to_thread(views.skill_detail, root, skill_id)
    return detail if detail is not None else _error(404, "not_found", "没有这条技能")


@router.put(_ORG + "/agents/{agent_id}/skills")
async def set_agent_skills(org_id: str, agent_id: str, body: Dict[str, Any]):
    """主助理启用哪些个人技能。专员按岗位装配，不在这里改。"""
    from src.workflow.workbench.personal_skills import SkillConflict
    root = _root(org_id)
    if agent_id != views.main_agent_id(root):
        return _error(404 if not _known_agent(org_id, agent_id) else 400, "unsupported", "专员的技能按岗位装配")
    wanted = body.get("skills")
    if set(body) != {"skills"} or not isinstance(wanted, list) or not all(isinstance(x, str) for x in wanted):
        return _error(400, "invalid", "需要技能 id 列表")
    try:
        return {"skills": await asyncio.to_thread(views.set_main_skills, root, wanted)}
    except LookupError:
        return _error(404, "not_found", "没有这条技能")
    except SkillConflict as error:
        return _error(409, "conflict", str(error))
    except (ValueError, OSError):
        return _error(409, "unavailable", "个人技能无法处理，请检查格式、版本和存储权限")


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
async def agent_detail(org_id: str, agent_id: str, request: Request):
    if getattr(request.state, "member_account", None) is not None:
        root = _root(org_id)
        who = _who(request, root)
        if who is None:
            return _error(403, "forbidden", _NOT_MEMBER)
        detail = await asyncio.to_thread(app_members.member_agent_detail, root, who, agent_id)
        return detail if detail is not None else _error(404, "not_found", "没有这个角色")
    agent = next((a for a in views.agents(_root(org_id)) if a["id"] == agent_id), None)
    if agent is None:
        return _error(404, "not_found", "没有这个角色")
    return views.agent_detail(_root(org_id), agent)


@router.get("/api/app/connectors/catalog")
async def connector_catalog():
    """可接入的外部服务目录。连接在别处管理（MCP 配置与助理插件），这里还没有可列的目录。"""
    return {"connectors": [], "builtinScopes": []}


def _agent(org_id: str, agent_id: str):
    return next((a for a in views.agents(_root(org_id)) if a["id"] == agent_id), None)


@router.get(_ORG + "/agents/{agent_id}/brain")
async def agent_brain(org_id: str, agent_id: str):
    agent = _agent(org_id, agent_id)
    if agent is None:
        return _error(404, "not_found", "没有这个角色")
    return views.agent_brain(_root(org_id), agent)


@router.get(_ORG + "/agents/{agent_id}/access")
async def agent_access(org_id: str, agent_id: str):
    if _agent(org_id, agent_id) is None:
        return _error(404, "not_found", "没有这个角色")
    return views.agent_access(_root(org_id), agent_id)


@router.get(_ORG + "/agents/{agent_id}/computer")
async def agent_computer(org_id: str, agent_id: str):
    if _agent(org_id, agent_id) is None:
        return _error(404, "not_found", "没有这个角色")
    return {"target": {"kind": "none"}}


@router.get(_ORG + "/agents/{agent_id}/repositories")
async def agent_repositories(org_id: str, agent_id: str):
    if _agent(org_id, agent_id) is None:
        return _error(404, "not_found", "没有这个角色")
    return {"repositories": []}


@router.get(_ORG + "/agents/{agent_id}/holds")
async def agent_holds(org_id: str, agent_id: str):
    if _agent(org_id, agent_id) is None:
        return _error(404, "not_found", "没有这个角色")
    return {"holds": [], "canStop": False}


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
    from src.web import app_member_realtime
    root = os.getcwd()
    owner = ws_token_ok(websocket)
    member = None if owner else app_member_realtime.identity(websocket, root)
    if (not owner and member is None) or websocket.query_params.get("orgId") != views.home_id(root):
        await websocket.close(code=1008)
        return
    if member is not None:
        await app_member_realtime.serve(websocket, root, member)
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
