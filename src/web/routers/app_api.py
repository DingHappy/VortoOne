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
import re
import logging
import os
import sqlite3
from contextlib import suppress
from ipaddress import IPv6Address, ip_address
from typing import Annotated, Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, StrictBool

from src.agents.tools.artifact_trash import ArtifactTrash, TrashConflict
from src.gateway import app_protocol as F
from src.gateway.mobile_devices import PushCategory
from src.utils.home_maintenance import HomeMaintenanceError, home_maintenance
from src.web import app_artifact_upload
from src.web import app_artifacts
from src.web import app_channel_agents
from src.web import app_issue_chat
from src.web import app_views as views
from src.web import (
    app_board, app_calendar, app_channels, app_covers, app_feedback, app_gifs, app_members, app_notice_inbox,
    app_org_logo,
    app_personal_vault, app_prefs, app_profile_avatar, app_schedule, app_staff, app_voice, member_auth,
)
from src.web.app_bridge import NoModel, existing_bridge, main_chat_bridge
from src.workflow.members.channel_history import REACTION_MAX_LENGTH
from src.workflow.members.channel_threads import ThreadArchived, ThreadInUse, ThreadRangeUnavailable
from src.workflow.members.channels import (
    ChannelAgentAskConflict, ChannelCategoryNameConflict, TeamChannels, is_team_channel,
)
from src.workflow.members.direct_messages import is_person_dm
from src.workflow.members.registry import Damaged, MAX_GROUPS, MAX_MEMBERS, MemberRegistry, Refused
from src.workflow.private_calendar import CalendarError, PrivateCalendar, date_range
from src.workflow.wiki import MAX_BODY, Wiki, WikiError, WikiReader

router = APIRouter(tags=["app"])

_ORG = "/api/app/organizations/{org_id}"
_THREAD = _ORG + "/threads/{thread_id}"
_APPROVAL_BROWSER_PREVIEW = _ORG + "/approvals/{approval_id}/browser-preview"
# 界面接口的版本是区间，不要求两边相等（HOME_AND_MEMBERS_DESIGN 第 4 节）：这个服务能给 MIN_API 到 API_CONTRACT
# 的界面用，App 自己也声明它能用的最老的服务（packages/client/src/lib/server.ts 的 APP_API_MIN）。两段有重叠就能连，
# 没有重叠时界面说清是哪一边要升级。加了界面要用的新东西就把 API_CONTRACT 加一；去掉老界面还在用的东西时才动 MIN_API。
API_CONTRACT = 17
MIN_API = 1


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
    from src.web.routers.ai_apps import server_url      # 设了 VORTOONE_PUBLIC_URL（专用网络里的地址）就报它
    from importlib import metadata
    # Native Desktop supplies its package version to the bundled backend.
    # A server installed separately still reports its Python distribution version.
    bundled = os.getenv("VORTOONE_BUNDLED_DESKTOP_VERSION", "")
    if os.getenv("VORTOONE_BUNDLED_DESKTOP") == "1" and re.fullmatch(
        r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?", bundled
    ):
        version = bundled
    else:
        version = "dev"
        for package in ("vortoone", "vortocode"):  # 包名改名（ADR-018 批次 E）前后都认
            try:
                version = metadata.version(package)
                break
            except metadata.PackageNotFoundError:
                continue
    # 公开信息，不带凭据：Desktop 的首次引导要从它自己的窗口核对一个地址是不是 VortoOne（跨域读取）
    return JSONResponse({"name": "VortoOne", "version": version, "api": API_CONTRACT, "minApi": MIN_API,
                         "url": server_url(request),
                         "signIn": {"email": bool(get_api_token()), "google": False, "github": False, "apple": False},
                         "signup": "invite", "cloud": False, "ownerClaim": _claim_waiting()},
                        headers={"Access-Control-Allow-Origin": "*"})


def _claim_waiting() -> bool:
    """装好后还没人认领：只收认领链接，别的登录和注册都不收（src/workflow/members/owner_account.py）。"""
    from src.workflow.members.owner_account import OwnerAccount
    return OwnerAccount(os.getcwd()).waiting()


@router.get("/api/app/auth-config")
async def auth_config():
    local_feedback = views.owner_connection()
    return {"socialProviders": {"google": False, "github": False}, "signupOpen": False, "firstRun": False,
            "ownerClaim": _claim_waiting(), "passwordReset": _join_codes_on(),
            "feedback": local_feedback, "feedbackLocal": local_feedback}


_FEEDBACK = "/api/app/feedback"
_FEEDBACK_HEADERS = {"Cache-Control": "no-store"}


@router.get(_FEEDBACK)
async def feedback_inbox(request: Request, orgId: str, cursor: int | None = Query(None, ge=1, le=2**63 - 1),
                         limit: int = Query(50, ge=1, le=100)):
    root = _root(orgId)
    if not member_auth.principal(request, root).is_owner or not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", "只有已认证的所有者能查看本地反馈")
    try:
        listing = await asyncio.to_thread(app_feedback.listing, root, cursor, limit)
    except (sqlite3.Error, OSError):
        return _error(503, "feedback.unavailable", "本地反馈暂时不可读")
    return JSONResponse(listing, headers=_FEEDBACK_HEADERS)


@router.post(_FEEDBACK, status_code=204)
async def submit_feedback(request: Request):
    from src.workflow.members.registry import Damaged, Refused

    root = os.getcwd()
    who = _who(request, root)
    if who is None or (who.is_owner and not app_staff.owner_proof(request)):
        return _error(403, "forbidden", "反馈需要已认证的所有者或在册成员")
    if request.headers.get("content-type", "").partition(";")[0].strip().lower() != "application/json":
        return _error(415, "feedback.invalid", "本地反馈只接受 JSON 请求")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > app_feedback.MAX_BODY:
            return _error(413, "feedback.too_large", "反馈请求过大")
        raw.extend(chunk)
    try:
        identifier, org_id, feedback = app_feedback.parse_submission(json.loads(raw))
    except (ValueError, RecursionError):
        return _error(400, "feedback.invalid", "反馈只接受有效的工作区编号、请求编号及 1 至 4000 字符的文字")
    _root(org_id)

    def save():
        if who.is_owner:
            app_feedback.submit(root, identifier, feedback, who)
            return
        with MemberRegistry(root).while_member_active(who):
            app_feedback.submit(root, identifier, feedback, who)

    try:
        await asyncio.to_thread(save)
    except Refused:
        return _error(403, "forbidden", "只有在册成员能提交反馈")
    except app_feedback.FeedbackConflict as error:
        return _error(409, "feedback.conflict", str(error))
    except (Damaged, sqlite3.Error, OSError):
        return _error(503, "feedback.unavailable", "本地反馈暂时无法保存")
    return Response(status_code=204, headers=_FEEDBACK_HEADERS)


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
    是所有者账号的邮箱就按所有者的密码核对；都不是的话按成员账号的邮箱加密码登录。
    失败都回同一句话，看不出是哪一种没对上。还没人认领时成员登录不收。"""
    from src.web.auth import get_api_token
    from src.web.routers.auth_routes import LoginBody, login
    from src.workflow.members.accounts import Locked, MemberAccounts, WrongCredentials
    from src.workflow.members.owner_account import OwnerAccount
    root = os.getcwd()
    result = await login(LoginBody(token=body.password), request, response)
    if not isinstance(result, JSONResponse):
        return {"redirect": False, "token": None, "user": views.me(root)["user"]}
    wrong = JSONResponse({"code": "INVALID_EMAIL_OR_PASSWORD", "message": "邮箱、密码或令牌不正确"}, status_code=401)
    if not views.owner_connection() or not member_auth.same_origin(request):
        return wrong
    try:
        owner = await asyncio.to_thread(OwnerAccount(root).sign_in, body.email, body.password)
    except Locked as error:
        return JSONResponse({"code": "TOO_MANY_REQUESTS", "message": str(error)}, status_code=429)
    except WrongCredentials:
        return wrong
    if owner:                                           # 所有者的邮箱和密码：给的就是所有者的登录
        await login(LoginBody(token=get_api_token()), request, response)
        return {"redirect": False, "token": None, "user": views.me(root)["user"]}
    if _claim_waiting():
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
    from src.workflow.members.owner_account import OwnerAccount
    closed = JSONResponse({"code": "SIGNUP_CLOSED", "message": "要有一份还有效的邀请才能注册"}, status_code=403)
    if not views.owner_connection() or not member_auth.same_origin(request) or _claim_waiting():
        return closed
    if await asyncio.to_thread(OwnerAccount(root).is_owner_email, body.email):
        return JSONResponse({"code": "INVALID_SIGN_UP", "message": "这个邮箱已经有账号了"}, status_code=422)
    try:
        invitation = MemberRegistry(root).invitation(request.headers.get("x-invitation-token", ""))
        if invitation is None or (invitation.get("email") and invitation["email"] != login_name(body.email)):
            return closed
        accounts = MemberAccounts(root)
        account_id, token = await asyncio.to_thread(
            accounts.create_with_activation_session, body.email, body.name, body.password,
        )
    except ValueError as error:
        return JSONResponse({"code": "INVALID_SIGN_UP", "message": str(error)}, status_code=422)
    except OSError:
        return JSONResponse({"code": "UNAVAILABLE", "message": "账号暂时建不了"}, status_code=409)
    member_auth.set_cookie(request, response, token)
    return {"token": None, "user": {"id": account_id, "name": body.name.strip(), "email": login_name(body.email)}}


class _OwnerClaim(BaseModel):
    token: str = ""
    name: str = ""
    email: str = ""
    password: str = ""


@router.post("/api/auth/owner-claim")
async def owner_claim(body: _OwnerClaim, request: Request, response: Response):
    """装好时的一次性认领链接：第一个凭它填名字、邮箱、密码的人成为所有者，链接随即作废。
    链接不对、过期、用过回 401；已经有所有者回 409。"""
    from src.web.auth import get_api_token
    from src.web.routers.auth_routes import LoginBody, login
    from src.workflow.members.accounts import MemberAccounts
    from src.workflow.members.owner_account import OwnerAccount, Taken, WrongClaim
    root = os.getcwd()
    if not get_api_token() or not member_auth.same_origin(request):
        return JSONResponse({"code": "INVALID_TOKEN", "message": "认领链接不对"}, status_code=401)
    try:
        if await asyncio.to_thread(MemberAccounts(root).has_email, body.email):
            return JSONResponse({"code": "INVALID_CLAIM", "message": "这个邮箱已经是成员的账号"}, status_code=422)
        await asyncio.to_thread(OwnerAccount(root).claim, body.token, body.name, body.email, body.password)
    except WrongClaim as error:
        return JSONResponse({"code": "INVALID_TOKEN", "message": str(error)}, status_code=401)
    except Taken as error:
        return JSONResponse({"code": "ALREADY_CLAIMED", "message": str(error)}, status_code=409)
    except ValueError as error:
        return JSONResponse({"code": "INVALID_CLAIM", "message": str(error)}, status_code=422)
    except (OSError, RuntimeError):
        return JSONResponse({"code": "UNAVAILABLE", "message": "暂时认领不了"}, status_code=409)
    await login(LoginBody(token=get_api_token()), request, response)
    return {"redirect": False, "token": None, "user": views.me(root)["user"]}


class _ResetRequest(BaseModel):
    email: str = ""
    redirectTo: str = ""        # noqa: N815 —— 界面的字段名；不用它拼链接（防跳到别处），只用本服务的地址


@router.post("/api/auth/request-password-reset")
async def request_password_reset(body: _ResetRequest, request: Request):
    """自己找回密码：邮箱有成员账号就发一封重置链接。有没有这个账号都回同一句话。只在配好发信时可用。"""
    from src.workflow.members import join_codes
    from src.workflow.members.accounts import MemberAccounts
    if not views.owner_connection() or not member_auth.same_origin(request):
        return JSONResponse({"code": "FORBIDDEN", "message": "这里现在不能找回密码"}, status_code=403)
    if not join_codes.enabled():
        return JSONResponse({"code": "MAIL_OFF", "message": "这个家没有配好发信：请让所有者在成员资料里给你重置链接"},
                            status_code=409)
    origin = _invitation_origin(request)

    def send():
        accounts = MemberAccounts(os.getcwd())
        account_id = accounts.account_with_email(body.email)
        if account_id is None:
            return
        token, _expires = accounts.reset_link(account_id)
        join_codes.CODES.mail(accounts.get(account_id)["email"], "重置你的密码",
                              f"打开这个链接设一个新密码：\n{origin}/reset-password?token={token}\n"
                              "不是你在找回密码的话，忽略这封邮件就好。")

    try:
        await asyncio.to_thread(send)
    except (OSError, join_codes.CodeError):          # smtplib 的错误都是 OSError
        logging.getLogger(__name__).warning("找回密码的邮件没有发出", exc_info=True)
    return {"status": True}


class _Reset(BaseModel):
    token: str = ""
    newPassword: str = ""       # noqa: N815 —— 界面的字段名


@router.post("/api/auth/reset-password")
async def reset_password(body: _Reset, request: Request):
    """凭重置链接里的令牌改密码。改完这个账号在各处的登录全部退出，要用新密码重新登录。"""
    from src.workflow.ai_apps.store import Damaged as AiAppsDamaged
    from src.workflow.members.accounts import Damaged, MemberAccounts
    if not views.owner_connection() or not member_auth.same_origin(request):
        return JSONResponse({"code": "INVALID_TOKEN", "message": "重置链接不对或已过期"}, status_code=400)
    try:
        accounts = MemberAccounts(os.getcwd())
        await asyncio.to_thread(accounts.reset_password, body.token, body.newPassword)
    except LookupError:
        return JSONResponse({"code": "INVALID_TOKEN", "message": "重置链接不对或已过期"}, status_code=400)
    except (AiAppsDamaged, Damaged, OSError):
        return JSONResponse({"code": "UNAVAILABLE", "message": "暂时改不了密码"}, status_code=409)
    except ValueError as error:
        return JSONResponse({"code": "INVALID_PASSWORD", "message": str(error)}, status_code=422)
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
    from src.web.auth import get_api_token
    from src.web.routers.auth_routes import set_owner_cookie
    expires = _HANDOVERS.pop(_handover_key(code), None)
    token = get_api_token()
    if expires is None or expires <= time.monotonic() or not token:
        return RedirectResponse("/login", status_code=303)
    redirect_to = "/"
    try:
        state = await _local_setup(request)
        if state and state["step"] != "done":
            redirect_to = "/onboarding"
    except (OSError, ValueError):
        # Keep authentication available so the onboarding page can display and retry this read failure.
        redirect_to = "/onboarding"
    response = RedirectResponse(redirect_to, status_code=303)
    set_owner_cookie(request, response, token)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


# ── 一次性登录：给自己的另一台设备（手机扫码、连接链接）──────────────────────────
# 已经登录的人开，只换得到开它的那个人（src/workflow/members/one_time.py）。

@router.post("/api/app/phone-sign-in")
async def phone_sign_in(request: Request):
    """界面「在手机上打开」：给调用人自己开一个 5 分钟、只能用一次的登录。server 是别的设备该连的地址。"""
    from src.web.routers.ai_apps import server_url
    from src.workflow.members.accounts import MemberAccounts, ReauthenticationRequired
    from src.workflow.members.one_time import OWNER, PENDING, TooMany
    if not views.owner_connection() or not member_auth.same_origin(request):
        return _error(403, "forbidden", "这里开不了一次性登录")
    account = getattr(request.state, "member_account", None)

    def mint():
        if account is None:
            return PENDING.mint(OWNER)
        accounts = MemberAccounts(os.getcwd())
        return accounts.mint_one_time(request.cookies.get(member_auth.MEMBER_COOKIE, ""), account["id"])

    try:
        token, seconds = await asyncio.to_thread(mint)
    except ReauthenticationRequired:
        return _error(401, "unauthorized", "请重新登录")
    except TooMany as error:
        return _error(429, "too_many", str(error))
    except (ValueError, OSError):
        return _error(503, "unavailable", "暂时开不了一次性登录")
    return {"token": token, "server": server_url(request), "expiresIn": seconds}


class _OneTime(BaseModel):
    token: str = ""


@router.post("/api/auth/sign-in/one-time-token")
async def one_time_sign_in(body: _OneTime, request: Request, response: Response):
    """另一台设备凭一次性登录换自己的登录。错的、过期的、用过的回同一句话。"""
    from src.web.auth import get_api_token
    from src.web.routers.auth_routes import set_owner_cookie
    from src.workflow.members.accounts import MemberAccounts
    from src.workflow.members.one_time import OWNER
    refused = JSONResponse({"code": "INVALID_TOKEN", "message": "这个登录链接不对、过期或已经用过"}, status_code=401)
    owner_token = get_api_token()
    if not owner_token or not member_auth.same_origin(request):
        return refused
    root = os.getcwd()
    try:
        who, session, account = await asyncio.to_thread(MemberAccounts(root).redeem_one_time, body.token)
    except (LookupError, ValueError, OSError):
        return refused
    if who == OWNER:
        set_owner_cookie(request, response, owner_token)
        return {"redirect": False, "token": None, "user": views.me(root)["user"]}
    if session is None:
        return refused
    member_auth.set_cookie(request, response, session)
    return {"redirect": False, "token": None,
            "user": {"id": account["member"] or account["id"], "name": account["name"], "email": account["email"]}}


@router.post("/api/auth/sign-out")
async def sign_out(request: Request, response: Response):
    """所有者的登录和成员的会话都退出。成员的会话在服务端也作废，不只是清 Cookie。"""
    from src.web.routers.auth_routes import logout
    from src.workflow.members.accounts import MemberAccounts
    await logout(request, response)
    token = request.cookies.get(member_auth.MEMBER_COOKIE, "")
    if token and member_auth.same_origin(request):
        try:
            MemberAccounts(os.getcwd()).sign_out(token)
        except (ValueError, OSError):
            pass                       # 账号文件读写不了：Cookie 照清，会话到期自己失效
    member_auth.clear_cookie(response)
    return {"success": True}


class _DeviceAccountSignOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    userId: str | None = Field(default=None, min_length=1, max_length=200)


@router.post("/api/app/device-accounts/switch")
async def switch_device_account(request: Request):
    """这里一个浏览器只登一个账号：没有别的账号可以切过去，要换人就退出再登录。"""
    return _error(409, "account.single_session", "这个浏览器只登一个账号：要换账号，请先退出再登录")


@router.post("/api/app/device-accounts/sign-out")
async def device_sign_out(request: Request, response: Response, body: _DeviceAccountSignOut | None = None):
    """退出当前账号。明确提供账号时，先核对它就是当前会话，防止旧页面退出后来登录的人。"""
    if body is not None and "userId" in body.model_fields_set:
        if body.userId is None:
            return _error(422, "invalid", "账号编号必须是非空字符串")
        account = getattr(request.state, "member_account", None)
        if account is None:
            if not app_staff.owner_proof(request):
                return _error(403, "owner_proof_required", "退出指定账号需要已认证的同源会话")
            current = views.owner_id(os.getcwd())
        else:
            current = member_auth.member_id(account) or account["id"]
        if body.userId != current:
            return _error(404, "not_found", "这个浏览器的当前会话不是指定账号")
    await sign_out(request, response)
    return {"signedIn": False}


# ── 主人与空间 ────────────────────────────────────────────────────────────────

async def _current_profile(root: str, request: Request, actor):
    if actor.is_owner:
        result = views.me(root)
    else:
        result = await _members_call(lambda: app_members.me(root, actor, request.state.member_account))
    if isinstance(result, dict) and result["organizations"] and (not actor.is_owner or app_staff.owner_proof(request)):
        result["organizations"][0]["logo"] = await asyncio.to_thread(app_org_logo.current, root)
    return result


def _local_setup_enabled(request: Request) -> bool:
    from src.web.routers.computers import direct_local_request
    from src.workflow import desktop_setup
    return desktop_setup.enabled() and direct_local_request(request)


async def _local_setup(request: Request):
    if _local_setup_enabled(request):
        from src.workflow import desktop_setup
        return await asyncio.to_thread(desktop_setup.read, os.getcwd())
    return None


def _setup_view(state: dict) -> dict:
    from src.workflow.desktop_setup import current_step
    configured = views.model_connected()
    fields = ("mode", "name", "context", "workArea")
    return {**{key: state[key] for key in fields if key in state},
            "step": current_step(state, configured), "modelConfigured": configured}


@router.get("/api/app/me")
async def me(request: Request, response: Response):
    from src.web.routers.computers import has_computer_owner_proof

    response.headers["Cache-Control"] = "no-store"
    root = os.getcwd()
    actor = member_auth.principal(request, root)
    result = await _current_profile(root, request, actor)
    if actor.is_owner and has_computer_owner_proof(request):
        try:
            setup = await _local_setup(request)
        except (OSError, ValueError):
            return _error(409, "setup_unavailable", "本机引导进度无法读取")
        if setup:
            result["desktopManaged"] = True
            if setup.get("modelDeferred") is True:
                result["desktopModelDeferred"] = True
            if setup["step"] != "done":
                result["desktopSetup"] = _setup_view(setup)
    return result


class _LocalSetupAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    step: Literal["purpose", "name", "business", "about", "team", "model", "invite", "presets"]
    mode: Literal["personal", "team"] | None = None
    name: str | None = Field(default=None, max_length=80)
    context: str | None = Field(default=None, max_length=600)
    workArea: str | None = Field(default=None, max_length=200)
    skipModel: bool = False


@router.patch("/api/app/desktop-setup")
async def save_desktop_setup(body: _LocalSetupAnswer, request: Request):
    from src.utils.home_maintenance import HomeMaintenanceError
    from src.web import app_credentials
    from src.web.routers.computers import has_local_owner_browser_session
    from src.workflow import desktop_setup

    if not _local_setup_enabled(request):
        return _error(404, "not_found", "本机引导不可用")
    root = os.getcwd()
    if not member_auth.principal(request, root).is_owner or not has_local_owner_browser_session(request):
        return _error(403, "forbidden", "请在本机工作区完成引导")

    try:
        answer = body.model_dump(exclude={"step", "skipModel"}, exclude_none=True)
        target = None
        if body.step == "model" and not body.skipModel:
            if answer:
                raise ValueError("试连使用这台服务已保存的配置")
            state = await _local_setup(request)
            if state is None:
                return _error(404, "not_found", "本机引导不可用")
            if desktop_setup.current_step(state, views.model_connected()) != "model" and not state.get("modelDeferred"):
                raise desktop_setup.SetupChanged
            target = app_credentials.desktop_model_probe_target()
            if target is None:
                raise desktop_setup.ModelRequired
            checked = await app_credentials.check_model(*target, trust_env=bool(target[1]))
            if checked.get("check") not in {"ok", "no_tools"}:
                return _error(502, "setup_model_probe_failed", "模型未通过连接测试，请检查地址、模型和 API Key 后重试")

        def model_configured() -> bool:
            if target is not None and app_credentials.desktop_model_probe_target() != target:
                raise desktop_setup.SetupChanged
            return views.model_connected()

        result = await asyncio.to_thread(desktop_setup.advance, root, body.step, answer, model_configured,
                                         skip_model=body.skipModel)
    except desktop_setup.SetupChanged:
        return _error(409, "setup_changed", "引导进度已更新，请重试")
    except HomeMaintenanceError:
        return _error(409, "setup_busy", "本机引导正在保存，请重试")
    except desktop_setup.ModelRequired:
        return _error(409, "setup_model_required", "先配置模型，或选择稍后设置并进入工作区")
    except ValueError:
        return _error(400, "invalid_setup_answer", "请检查当前步骤填写的信息")
    except OSError:
        return _error(503, "setup_unavailable", "本机引导进度无法保存")
    if result is None:
        return _error(404, "not_found", "本机引导不可用")
    view = _setup_view(result)
    if body.step == "presets" and result["mode"] == "personal" and view["modelConfigured"]:
        view["rightHandId"] = views.main_agent_id(root)
    return JSONResponse(view, headers={"Cache-Control": "no-store"})


def _workspace_unread_counts(root: str, actor) -> list[dict[str, Any]]:
    service = app_channels.channels(root)
    unread = sum(row.get("unreadCount", 0) for row in service.list(actor) if not row.get("muted"))
    unread += service.unread_thread_count(actor)
    notices = app_notice_inbox.count(root) if actor.is_owner else {"unread": 0, "waiting": 0}
    return [{"orgId": views.home_id(root), "unread": unread + notices["unread"], "waiting": notices["waiting"]}]


@router.get("/api/app/device-accounts")
async def device_accounts(request: Request, response: Response):
    """Report the account of this session. Each browser session holds at most one account."""
    response.headers["Cache-Control"] = "no-store"
    root = os.getcwd()
    actor = member_auth.principal(request, root)
    if actor.is_owner:
        if not views.owner_connection():
            return {"accounts": []}
        if not app_staff.owner_proof(request):
            return _error(403, "owner_proof_required", "读取当前账号需要已认证的同源会话")
    profile = await _current_profile(root, request, actor)
    if isinstance(profile, JSONResponse):
        return profile
    user = profile["user"]
    unread = []
    if profile["organizations"]:
        try:
            unread = await asyncio.to_thread(_workspace_unread_counts, root, actor)
        except (Refused, Damaged, app_notice_inbox.InboxUnavailable, HomeMaintenanceError, OSError):
            # An unavailable badge should not prevent the client from reading the active account.
            pass
    return {"accounts": [{"current": True, "user": {key: user[key] for key in ("id", "name", "email", "image")},
                          "organizations": profile["organizations"], "unread": unread}]}


@router.get("/api/app/me/unread")
async def workspace_unread(request: Request, response: Response):
    """Aggregate only the unread activity this session can already see."""
    response.headers["Cache-Control"] = "no-store"
    root = os.getcwd()
    who = _who(request, root)
    if who is None:
        return {"organizations": []}
    if who.is_owner and not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", "读取工作区未读数需要已认证的同源会话")

    try:
        return {"organizations": await asyncio.to_thread(_workspace_unread_counts, root, who)}
    except Refused:
        return {"organizations": []}
    except (Damaged, app_notice_inbox.InboxUnavailable, HomeMaintenanceError, OSError):
        return _error(409, "unavailable", "暂时无法读取工作区未读数")


@router.patch("/api/app/me", status_code=204)
async def update_me(body: Dict[str, Any], request: Request):
    """界面语言。只存给调用人自己（所有者或成员各一份），不碰名字、邮箱这些账号资料。"""
    root = os.getcwd()
    account = getattr(request.state, "member_account", None)
    key = views.owner_id(root) if account is None else app_members.member_auth_key(account)
    try:
        await asyncio.to_thread(app_prefs.set_locale, root, key, body)
    except (ValueError, OSError) as error:
        return _error(400, "invalid", str(error))
    return Response(status_code=204)


@router.post("/api/app/me/password", status_code=204)
async def replace_member_password(body: Dict[str, Any], request: Request):
    from src.workflow.ai_apps.store import Damaged as AiAppsDamaged
    from src.workflow.members.accounts import Damaged, MemberAccounts, ReauthenticationRequired

    root = os.getcwd()
    actor = member_auth.principal(request, root)
    if actor.is_owner:
        if not app_staff.owner_proof(request):
            return _error(403, "forbidden", "更换密码需要已认证的会话")
        return _error(409, "account.password_unsupported", "所有者的邮箱密码由本机所有者认领流程管理")
    account = request.state.member_account
    if set(body) != {"password"}:
        return _error(422, "invalid_password", "只需提供自己的新密码")
    try:
        await asyncio.to_thread(MemberAccounts(root).change_password, account["id"],
                                request.cookies.get(member_auth.MEMBER_COOKIE, ""), body["password"])
    except ReauthenticationRequired:
        return _error(403, "reauth.sign_in", "请用当前密码重新登录")
    except (AiAppsDamaged, Damaged, OSError):
        return _error(503, "unavailable", "暂时改不了密码")
    except ValueError as error:
        return _error(422, "invalid_password", str(error))
    return Response(status_code=204)


# ── 注销自己的账号（主人 2026-11-24 定：成员可以注销自己的；整个家不删）──────────

_DELETION_CODE_SUBJECT = "注销账号的验证码"
_DELETION_CODE_TEXT = "你在注销这个家里的账号，验证码是 {code}，10 分钟内有效。\n不是你在操作的话，改一下密码。"


def _deletion_code_key(account_id: str) -> str:
    return "account-delete:" + account_id


def _reauth_code_error(error):
    """验证码的拒绝换成界面认的 reauth.code_*。"""
    code = {"join.code_wait": "reauth.code_wait", "join.code_expired": "reauth.code_expired",
            "join.code_wrong": "reauth.code_wrong", "join.codes_off": "reauth.no_email"}.get(error.code, error.code)
    body = {"code": code, "message": str(error), **({"retryIn": error.retry_in} if error.retry_in else {})}
    return JSONResponse({"error": body}, status_code=error.status, headers={"Cache-Control": "no-store"})


def _owner_cannot_leave(request: Request):
    if not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", _NEEDS_OWNER)
    return _error(409, "sole_owner", "你是这个家唯一的所有者，所有者账号不能注销")


@router.get("/api/app/account/deletion")
async def account_deletion_plan(request: Request):
    root = os.getcwd()
    actor = member_auth.principal(request, root)
    if actor.is_owner and not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", _NEEDS_OWNER)
    plan = await _members_call(lambda: app_members.deletion_plan(root, actor))
    if isinstance(plan, JSONResponse):
        return plan
    return JSONResponse(plan, headers={"Cache-Control": "no-store"})


@router.post("/api/app/account/deletion/code")
async def account_deletion_code(request: Request):
    from src.workflow.members import join_codes

    actor = member_auth.principal(request, os.getcwd())
    if actor.is_owner:
        return _owner_cannot_leave(request)
    if not join_codes.enabled():
        return _error(409, "reauth.no_email", "这个家不发邮件：用你的密码确认")
    email = str(request.state.member_account["email"])
    try:
        wait = await asyncio.to_thread(join_codes.CODES.send_code, _deletion_code_key(request.state.member_account["id"]),
                                       email, _DELETION_CODE_SUBJECT, _DELETION_CODE_TEXT)
    except join_codes.CodeError as error:
        return _reauth_code_error(error)
    except OSError:
        return _error(503, "mail_unavailable", "验证码没有发出去，请稍后再试")
    return JSONResponse({"email": email, "resendIn": wait}, headers={"Cache-Control": "no-store"})


class _DeletionProof(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    code: str | None = Field(default=None, max_length=16)
    password: str | None = Field(default=None, max_length=256)


@router.delete("/api/app/account", status_code=204)
async def delete_my_account(request: Request, body: _DeletionProof | None = None):
    """成员注销自己的账号：发信开着要邮件验证码，没开要自己的密码。7 天后移出这个家，所有登录现在就结束。"""
    from src.workflow.members import join_codes
    from src.workflow.members.accounts import Damaged, MemberAccounts, ReauthenticationRequired, WrongCredentials

    root = os.getcwd()
    actor = member_auth.principal(request, root)
    if actor.is_owner:
        return _owner_cannot_leave(request)
    account = request.state.member_account
    proof = body or _DeletionProof()
    password = None
    if join_codes.enabled():
        if not proof.code:
            return _error(403, "reauth.code", "先发一封验证码到你的邮箱")
        try:
            join_codes.CODES.check(_deletion_code_key(account["id"]), str(account["email"]), proof.code)
        except join_codes.CodeError as error:
            return _reauth_code_error(error)
    else:
        if not proof.password:
            return _error(403, "reauth.password", "用你的密码确认")
        password = proof.password
    try:
        await asyncio.to_thread(MemberAccounts(root).request_deletion, account["id"],
                                request.cookies.get(member_auth.MEMBER_COOKIE, ""), password=password)
    except WrongCredentials:
        return _error(403, "reauth.password", "密码不对")
    except ReauthenticationRequired:
        return _error(401, "unauthorized", "请重新登录")
    except (Damaged, OSError):
        return _error(503, "unavailable", "暂时注销不了")
    response = Response(status_code=204)
    member_auth.clear_cookie(response)
    return response


@router.delete("/api/app/account/deletion", status_code=204)
async def restore_my_account(request: Request):
    from src.workflow.members.accounts import Damaged, MemberAccounts, NotRestorable, ReauthenticationRequired

    root = os.getcwd()
    actor = member_auth.principal(request, root)
    if actor.is_owner:
        if not app_staff.owner_proof(request):
            return _error(403, "owner_proof_required", _NEEDS_OWNER)
        return Response(status_code=204)               # 所有者账号不会注销：没有要恢复的
    try:
        await asyncio.to_thread(MemberAccounts(root).restore, request.state.member_account["id"],
                                request.cookies.get(member_auth.MEMBER_COOKIE, ""))
    except NotRestorable:
        return _error(409, "account.not_restorable", "注销已经到期，账号回不来了")
    except ReauthenticationRequired:
        return _error(401, "unauthorized", "请重新登录")
    except (Damaged, OSError):
        return _error(503, "unavailable", "暂时恢复不了")
    return Response(status_code=204)


@router.post(_ORG + "/deletion/code")
@router.post(_ORG + "/deletion")
async def delete_workspace(org_id: str, request: Request):
    """删除整个家（频道、成员、agent、Wiki、文件、记录）不在这里做（主人 2026-11-24 定）：不可恢复的整家删除
    如实拒绝，要删请在服务器上先备份再手动删。不发验证码，免得像是能删。"""
    root = _root(org_id)
    who = _who(request, root)
    if who is None or not who.is_owner:
        return _error(403, "forbidden", "只有所有者能处理这个家")
    if not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", _NEEDS_OWNER)
    return _error(409, "org.delete_unsupported", "这个家不能在这里删除：要删，请先在服务器上备份，再手动删")


@router.delete(_ORG + "/deletion", status_code=204)
async def restore_workspace(org_id: str, request: Request):
    root = _root(org_id)
    who = _who(request, root)
    if who is None or not who.is_owner:
        return _error(403, "forbidden", "只有所有者能处理这个家")
    if not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", _NEEDS_OWNER)
    return Response(status_code=204)                   # 这个家从不进入删除：没有要恢复的


@router.put("/api/app/me/work-area", status_code=204)
async def set_work_area(body: Dict[str, Any], request: Request):
    root = os.getcwd()
    actor = member_auth.principal(request, root)
    if actor.is_owner:
        if not app_staff.owner_proof(request):
            return _error(403, "forbidden", "保存工作领域需要已认证的所有者会话")
        key = actor.id
    else:
        key = app_members.member_auth_key(request.state.member_account)
    try:
        await asyncio.to_thread(app_prefs.set_work_area, root, key, body)
    except ValueError as error:
        return _error(400, "invalid", str(error))
    except OSError:
        return _error(503, "unavailable", "暂时无法保存工作领域")
    return Response(status_code=204)


@router.get("/api/app/models")
async def models():
    """所有者加的模型端点上的模型（见 app_credentials.models）；没加过就是空的，agent 用服务配置的默认模型。"""
    from src.web import app_credentials
    return await asyncio.to_thread(app_credentials.models, os.getcwd())


def _who(request: Request, root: str):
    """调用人。所有者照旧；成员要在册，账号还没加入或已被移除的返回 None。"""
    who = member_auth.principal(request, root)
    if who.is_owner:
        return who
    return who if member_auth.member_id(getattr(request.state, "member_account", None)) else None


_NOT_MEMBER = "还不是这个空间的成员"


@router.get(_ORG)
async def workspace_details(org_id: str, request: Request, response: Response):
    """Return the caller's workspace summary and show its context to the owner."""
    from src.memory.assistant_profile import load_home_context

    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    if who.is_owner and not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", "读取空间详情需要已认证的同源会话")
    profile = await _current_profile(root, request, who)
    if isinstance(profile, JSONResponse):
        return profile
    summary = next((org for org in profile["organizations"] if org["id"] == org_id), None)
    if summary is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    result = dict(summary)
    if who.is_owner:
        result["context"] = await asyncio.to_thread(load_home_context, root)
    response.headers["Cache-Control"] = "no-store"
    return result


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


_STORAGE = _ORG + "/storage"


def _storage_owner(org_id: str, request: Request):
    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner or not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", "读取或管理存储需要已认证的所有者")
    return None


@router.get(_STORAGE)
async def storage_status(org_id: str, request: Request):
    if (refused := _storage_owner(org_id, request)) is not None:
        return refused
    # ArtifactStore writes to the server's disk. The client calls that option "agentes".
    return JSONResponse({
        "kind": "agentes",
        "platform": {"kind": "disk", "folder": None},
        "s3": None,
        "migration": None,
    }, headers={"Cache-Control": "no-store"})


@router.get(_STORAGE + "/migration")
async def storage_migration_status(org_id: str, request: Request):
    if (refused := _storage_owner(org_id, request)) is not None:
        return refused
    return JSONResponse(None, headers={"Cache-Control": "no-store"})


@router.put(_STORAGE)
@router.post(_STORAGE + "/test")
@router.post(_STORAGE + "/migrate")
async def unavailable_s3_storage(org_id: str, request: Request):
    if (refused := _storage_owner(org_id, request)) is not None:
        return refused
    # The server has no bucket to test, activate or migrate. Do not parse credentials.
    return _error(409, "storage.s3_unavailable", "当前工作区使用服务器磁盘，S3 存储尚未开放")


# 空间设置里有对应能力的：名字（主助理的名字，也就是这个家显示的名字）、默认时区（例行工作用的那个）、
# 背景信息（进所有 agent 的系统提示，下一个会话起生效）、
# GIF 开关和分级（团队频道里的 GIF）、语音消息开关。其余开关（团队巡视……）没有对应能力，改了也不会生效，
# 所以拒绝而不是假装存下。
_SETTINGS_WRITABLE = {"name", "timezone", "context"}
_HOME_SWITCHES = {"gifs", "gifRating", "voiceMessages"}


def _write_settings(root: str, body: Dict[str, Any]) -> Dict[str, Any]:
    from src.memory import assistant_profile
    from src.workflow import desktop_setup
    from src.workflow.staff.routines import Routines
    current = views.settings(root)
    unsupported = sorted(key for key, value in body.items()
                         if key not in _SETTINGS_WRITABLE | _HOME_SWITCHES and current.get(key, object()) != value)
    if unsupported:
        raise ValueError(f"这些设置这里还不能改：{unsupported}")
    switches = {key: body[key] for key in _HOME_SWITCHES if key in body}
    app_prefs.check_home_settings(switches)
    if "name" in body:
        name = body["name"]
        if not isinstance(name, str) or not " ".join(name.split()):
            raise ValueError("名字不能为空")
        limit = desktop_setup.MAX_NAME if desktop_setup.read(root) is not None else assistant_profile.MAX_NAME
        if len(" ".join(name.split())) > limit:
            raise ValueError(f"名字最多 {limit} 个字")
    if "context" in body and (not isinstance(body["context"], str)
                              or len(body["context"].strip()) > assistant_profile.MAX_CONTEXT):
        raise ValueError(f"背景信息最多 {assistant_profile.MAX_CONTEXT} 个字")
    if "timezone" in body and body["timezone"] != current["timezone"]:
        zone = body["timezone"]
        if not isinstance(zone, str) or not zone:
            raise ValueError("时区不能为空")
        registry = Routines(root)
        registry.configure_timezone(zone, registry.read()["revision"])     # 时区名不对由登记处拒绝
    if "name" in body and not desktop_setup.rename_workspace(root, " ".join(body["name"].split())):
        assistant_profile.save_profile(root, name=body["name"])
    if "context" in body and body["context"] != current["context"]:
        assistant_profile.save_home_context(root, body["context"])
    if switches:
        app_prefs.set_home_settings(root, switches)
    return views.settings(root)


async def _patch_settings(org_id: str, body: Dict[str, Any], request: Request):
    from src.utils.home_maintenance import HomeMaintenanceError
    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner:
        return _error(403, "forbidden", "空间设置只有所有者能改")
    if not views.owner_connection():
        return _error(403, "forbidden", "改空间设置需要已认证的主人连接")
    try:
        return await asyncio.to_thread(_write_settings, root, body)
    except HomeMaintenanceError:
        return _error(409, "settings_busy", "工作区设置正在保存，请重试")
    except (ValueError, OSError) as error:
        return _error(400, "invalid", str(error))


@router.patch(_ORG + "/settings")
async def update_settings(org_id: str, body: Dict[str, Any], request: Request):
    return await _patch_settings(org_id, body, request)


@router.patch(_ORG, status_code=204)
async def update_home(org_id: str, body: Dict[str, Any], request: Request):
    """界面「改空间名字与背景」的旧写法，和 PATCH …/settings 同一套规矩。"""
    result = await _patch_settings(org_id, body, request)
    return result if isinstance(result, JSONResponse) else Response(status_code=204)


async def _change_workspace_logo(org_id: str, request: Request, *, remove: bool = False):
    from src.utils.home_maintenance import HomeMaintenanceError

    root = _root(org_id)
    refused = await _owner_write(request, root, "工作区 Logo")
    if refused:
        return refused
    if not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", "更新工作区 Logo 需要所有者的同源会话")
    try:
        if remove:
            await asyncio.to_thread(app_org_logo.remove_logo, root)
            return Response(status_code=204)
        if request.headers.get("content-type", "").partition(";")[0].strip().lower() != "application/json":
            return _error(415, "logo.invalid", "工作区 Logo 只接受 JSON 请求")
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > app_org_logo.MAX_REQUEST_BYTES:
                return _error(413, "logo.too_large", "工作区 Logo 请求过大")
            raw.extend(chunk)
        try:
            body = json.loads(raw)
        except (ValueError, RecursionError):
            return _error(400, "logo.invalid", "工作区 Logo 请求格式无效")
        logo = await asyncio.to_thread(app_org_logo.set_logo, root, body)
        return JSONResponse({"logo": logo}, headers={"Cache-Control": "no-store"})
    except app_org_logo.LogoTooLarge as error:
        return _error(413, "logo.too_large", str(error))
    except app_org_logo.InvalidLogo as error:
        return _error(400, "logo.invalid", str(error))
    except (HomeMaintenanceError, OSError):
        return _error(409, "logo.unavailable", "无法保存或删除工作区 Logo")


@router.put(_ORG + "/logo")
async def set_workspace_logo(org_id: str, request: Request):
    return await _change_workspace_logo(org_id, request)


@router.delete(_ORG + "/logo", status_code=204)
async def delete_workspace_logo(org_id: str, request: Request):
    return await _change_workspace_logo(org_id, request, remove=True)


@router.get(_ORG + "/members")
async def members(org_id: str, request: Request):
    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner and not member_auth.member_id(request.state.member_account):
        return _error(403, "forbidden", "还不是这个空间的成员")
    return await _members_call(lambda: {"members": app_members.members(root)})


@router.get(_ORG + "/people")
async def people(org_id: str, request: Request, response: Response):
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    response.headers["Cache-Control"] = "no-store"
    return await _members_call(lambda: {"people": app_members.people(root, who)})


_AVATAR_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def _avatar_error(status: int, code: str, message: str) -> JSONResponse:
    result = _error(status, code, message)
    result.headers.update(_AVATAR_HEADERS)
    return result


def _avatar_actor(request: Request, root: str):
    who = _who(request, root)
    return who if who and (not who.is_owner or app_staff.owner_proof(request)) else None


async def _avatar_body(request: Request):
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise ValueError("头像上传需要 JSON 正文")
    limit = app_profile_avatar.MAX_REQUEST_BYTES
    try:
        declared = int(request.headers.get("content-length", "0"))
    except ValueError as error:
        raise ValueError("头像正文长度无效") from error
    if declared < 0:
        raise ValueError("头像正文长度无效")
    if declared > limit:
        raise app_profile_avatar.AvatarTooLarge("头像最大 768 KiB")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > limit:
            raise app_profile_avatar.AvatarTooLarge("头像最大 768 KiB")
        raw.extend(chunk)
    try:
        return json.loads(raw)
    except (ValueError, RecursionError) as error:
        raise ValueError("头像 JSON 无效") from error


@router.put(_ORG + "/me/avatar")
async def set_person_avatar(org_id: str, request: Request, response: Response):
    from src.utils.home_maintenance import HomeMaintenanceError

    root = _root(org_id)
    who = _avatar_actor(request, root)
    if who is None:
        return _avatar_error(403, "forbidden", "更改头像需要已认证的空间成员")
    try:
        body = await _avatar_body(request)
        avatar_url = await asyncio.to_thread(app_profile_avatar.set_avatar, root, org_id, who.id, body)
    except app_profile_avatar.AvatarTooLarge as error:
        return _avatar_error(413, "avatar_too_large", str(error))
    except ValueError as error:
        return _avatar_error(400, "invalid", str(error))
    except (OSError, HomeMaintenanceError):
        return _avatar_error(409, "unavailable", "无法保存头像")
    response.headers.update(_AVATAR_HEADERS)
    return {"avatarUrl": avatar_url}


@router.delete(_ORG + "/me/avatar", status_code=204)
async def delete_person_avatar(org_id: str, request: Request):
    from src.utils.home_maintenance import HomeMaintenanceError

    root = _root(org_id)
    who = _avatar_actor(request, root)
    if who is None:
        return _avatar_error(403, "forbidden", "更改头像需要已认证的空间成员")
    try:
        await asyncio.to_thread(app_profile_avatar.remove_avatar, root, who.id)
    except (OSError, HomeMaintenanceError):
        return _avatar_error(409, "unavailable", "无法删除头像")
    return Response(status_code=204, headers=_AVATAR_HEADERS)


@router.get(_ORG + "/people/{person_id}/avatar")
async def person_avatar(org_id: str, person_id: str, request: Request, v: str = Query("", max_length=24)):
    from src.workflow.members.registry import Damaged, Missing

    root = _root(org_id)
    if _avatar_actor(request, root) is None:
        return _avatar_error(403, "forbidden", "读取头像需要已认证的空间成员")
    try:
        if person_id != views.owner_id(root):
            await asyncio.to_thread(MemberRegistry(root).standing, person_id)
        content_type, data = await asyncio.to_thread(app_profile_avatar.media, root, person_id, v)
    except (Missing, FileNotFoundError):
        return _avatar_error(404, "not_found", "没有这个头像")
    except (Damaged, OSError, ValueError):
        return _avatar_error(409, "unavailable", "无法读取头像")
    headers = {**_AVATAR_HEADERS, "Cross-Origin-Resource-Policy": "same-origin"}
    return Response(data, media_type=content_type, headers=headers)


@router.put(_ORG + "/me/status")
async def set_person_status(org_id: str, body: Dict[str, Any], request: Request, response: Response):
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    if who.is_owner and views.owner_connection() and not app_staff.owner_proof(request):
        return _error(403, "forbidden", "需要所有者证明")
    response.headers["Cache-Control"] = "no-store"
    return await _members_call(lambda: app_members.set_person_status(root, who, body))


@router.patch(_ORG + "/me")
async def update_person_profile(org_id: str, body: Dict[str, Any], request: Request, response: Response):
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    if who.is_owner and not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", "保存个人资料需要已认证的同源会话")
    response.headers["Cache-Control"] = "no-store"
    return await _members_call(lambda: app_members.update_person_profile(root, who, body))


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


class _PermissionRowHolder(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=128)
    level: int


class _PermissionRow(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    everyone: int
    roles: List[_PermissionRowHolder] = Field(max_length=MAX_GROUPS)


@router.put(_ORG + "/permissions/{row}")
async def set_role_permission_row(org_id: str, row: str, body: _PermissionRow, request: Request,
                                  response: Response):
    root = _root(org_id)
    who = member_auth.principal(request, root)
    response.headers["Cache-Control"] = "no-store"
    if not who.is_owner or not app_staff.owner_proof(request):
        result = _error(403, "owner_proof_required", "批量改身份组权限需要已认证的所有者会话")
    else:
        result = await _members_call(lambda: app_members.set_role_row(
            root, who, row, body.everyone, [(holder.id, holder.level) for holder in body.roles]), write=True)
    if isinstance(result, JSONResponse):
        result.headers["Cache-Control"] = "no-store"
    return result


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


class _RoleMembersPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    userIds: List[str] = Field(max_length=MAX_MEMBERS)


@router.post(_ORG + "/roles/{role_id}/members/preview")
async def preview_role_members(org_id: str, role_id: str, body: _RoleMembersPreview, request: Request,
                               response: Response):
    root = _root(org_id)
    who = member_auth.principal(request, root)
    response.headers["Cache-Control"] = "no-store"
    if not who.is_owner or not app_staff.owner_proof(request):
        result = _error(403, "forbidden", "预查授予身份组需要已认证的所有者连接")
    else:
        result = await _members_call(lambda: app_members.role_mail_loss(root, who, role_id, body.userIds))
    if isinstance(result, JSONResponse):
        result.headers["Cache-Control"] = "no-store"
    return result


@router.put(_ORG + "/members/{user_id}/roles")
async def set_member_roles(org_id: str, user_id: str, body: Dict[str, Any], request: Request):
    root = _root(org_id)
    who = member_auth.principal(request, root)
    return await _members_call(
        lambda: {"roleIds": app_members.set_member_roles(root, who, user_id, body.get("roleIds"))}, write=True)


@router.get(_ORG + "/members/{user_id}/removal")
async def member_removal_preview(org_id: str, user_id: str, request: Request, response: Response):
    root = _root(org_id)
    who = member_auth.principal(request, root)
    response.headers["Cache-Control"] = "no-store"
    if not who.is_owner or not app_staff.owner_proof(request):
        result = _error(403, "forbidden", "移除成员预览需要已认证的所有者连接")
    else:
        result = await _members_call(lambda: app_members.removal_preview(root, who, user_id))
    if isinstance(result, JSONResponse):
        result.headers["Cache-Control"] = "no-store"
    return result


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


def _join_codes_on() -> bool:
    """界面凭这一项判断这台服务能不能发信：开着邮箱验证码（且配好了发信）时才走验证码那一套。"""
    from src.workflow.members.join_codes import enabled
    return enabled()


def _invitation_origin(request: Request) -> str:
    """邀请优先走配置的 HTTPS 源。和 Desktop 一样，只收主机和可选端口。"""
    configured = os.getenv("VORTOONE_PUBLIC_URL", "").strip()
    fallback = str(request.base_url).rstrip("/")   # 协议由 ASGI 提供，不从任意转发头拼链接
    if not re.fullmatch(r"https://[^/?#@\\\s\x00-\x1f\x7f]+/?", configured, flags=re.IGNORECASE):
        return fallback
    try:
        url = AnyHttpUrl(configured)              # 规范化浏览器接受的 IP、端口和 IDN 表示法
    except ValueError:
        return fallback
    if not url.host or not url.port or url.username is not None or url.password is not None:
        return fallback
    host = url.host.strip("[]").rstrip(".")
    try:
        address = ip_address(host)
    except ValueError:
        if host == "localhost" or host.endswith(".localhost"):
            return fallback
        if not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")):
            return fallback
    else:
        if isinstance(address, IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        if address.is_loopback or address.is_unspecified:
            return fallback
    return str(url).rstrip("/")


@router.get(_ORG + "/invitations")
async def invitations(org_id: str, request: Request):
    root = _root(org_id)
    who = member_auth.principal(request, root)
    return await _members_call(lambda: {"invitations": app_members.invitations(root, who)})


@router.post(_ORG + "/invitations")
async def invite(org_id: str, body: Dict[str, Any], request: Request):
    root = _root(org_id)
    who, origin = member_auth.principal(request, root), _invitation_origin(request)
    return await _members_call(lambda: app_members.invite(root, who, body, origin), write=True)


@router.delete(_ORG + "/invitations/{invitation_id}", status_code=204)
async def revoke_invitation(org_id: str, invitation_id: str, request: Request):
    root = _root(org_id)
    who = member_auth.principal(request, root)
    result = await _members_call(lambda: app_members.revoke_invitation(root, who, invitation_id), write=True)
    return result if isinstance(result, JSONResponse) else Response(status_code=204)


@router.get(_ORG + "/invitations/all")
async def all_invitations(org_id: str, request: Request):
    root = _root(org_id)
    who = member_auth.principal(request, root)
    owner_proven = who.is_owner and app_staff.owner_proof(request)
    return await _members_call(lambda: {"invitations": app_members.all_invitations(root, who,
                                                                                   owner_proven=owner_proven)})


@router.post(_ORG + "/invitations/batch")
async def invite_many(org_id: str, body: Dict[str, Any], request: Request):
    root = _root(org_id)
    who = member_auth.principal(request, root)
    return await _members_call(lambda: {"results": app_members.invite_many(root, who, body, _invitation_origin(request))},
                               write=True)


@router.patch(_ORG + "/invitations/{invitation_id}")
async def update_invitation(org_id: str, invitation_id: str, body: Dict[str, Any], request: Request):
    root = _root(org_id)
    who = member_auth.principal(request, root)
    return await _members_call(lambda: app_members.update_invitation(root, who, invitation_id, body), write=True)


@router.post(_ORG + "/invitations/{invitation_id}/resend")
async def resend_invitation(org_id: str, invitation_id: str, body: Dict[str, Any], request: Request,
                            response: Response):
    """更换有效邮箱邀请的单次链接，由所有者复制转交。"""
    root = _root(org_id)
    who = member_auth.principal(request, root)
    response.headers["Cache-Control"] = "no-store"
    if not who.is_owner or not app_staff.owner_proof(request):
        result = _error(403, "forbidden", "只有已认证的所有者能更换邮箱邀请链接")
    elif body:
        result = _error(400, "invalid", "换链只接受空 JSON 对象")
    else:
        result = await _members_call(
            lambda: app_members.resend_invitation(root, who, invitation_id, _invitation_origin(request)), write=True)
    if isinstance(result, JSONResponse):
        result.headers["Cache-Control"] = "no-store"
    return result


_JOIN_REQUESTS = _ORG + "/join-requests"


async def _join_suggestion_call(org_id: str, request: Request, response: Response, action, *,
                                owner: bool, write: bool = False, empty: bool = False):
    from src.workflow.members.registry import JoinSuggestionConflict

    root = _root(org_id)
    who = _who(request, root)
    response.headers["Cache-Control"] = "no-store"
    if who is None or who.is_owner != owner or (owner and not app_staff.owner_proof(request)):
        result = _error(403, "forbidden", "此操作需要已认证的所有者" if owner else "此操作需要已加入空间的成员")
    else:
        try:
            result = await _members_call(lambda: action(root, who), write=write)
        except JoinSuggestionConflict as error:
            status = 400 if error.code == "join.invalid_email" else 409
            result = _error(status, error.code, str(error))
    if empty and result is None:
        result = Response(status_code=204)
    if isinstance(result, Response):
        result.headers["Cache-Control"] = "no-store"
    return result


@router.get(_JOIN_REQUESTS)
async def list_join_suggestions(org_id: str, request: Request, response: Response):
    """所有者的加入申请：成员的推荐，加上凭邀请链接来的申请（主人 2026-11-23 定）。"""
    from src.workflow.members.invite_links import links_for
    return await _join_suggestion_call(org_id, request, response, owner=True, action=lambda root, who: {
        "requests": app_members.join_suggestions(root, who) + links_for(root).inbox(who)})


@router.get(_JOIN_REQUESTS + "/mine")
async def my_join_suggestions(org_id: str, request: Request, response: Response):
    return await _join_suggestion_call(org_id, request, response, owner=False, action=lambda root, who: {
        "suggestions": app_members.join_suggestions(root, who)})


@router.post(_JOIN_REQUESTS + "/suggest")
async def suggest_join(org_id: str, body: Dict[str, Any], request: Request, response: Response):
    return await _join_suggestion_call(org_id, request, response, owner=False, write=True,
                                       action=lambda root, who: app_members.suggest_join(root, who, body))


@router.delete(_JOIN_REQUESTS + "/{suggestion_id}", status_code=204)
async def cancel_join_suggestion(org_id: str, suggestion_id: str, request: Request, response: Response):
    from src.workflow.members.registry import MemberRegistry

    return await _join_suggestion_call(org_id, request, response, owner=False, write=True, empty=True,
                                       action=lambda root, who: MemberRegistry(root).close_join_suggestion(
                                           who, suggestion_id))


@router.post(_JOIN_REQUESTS + "/{suggestion_id}/approve")
async def approve_join_suggestion(org_id: str, suggestion_id: str, body: Dict[str, Any],
                                  request: Request, response: Response):
    from src.workflow.members.invite_links import links_for
    links = links_for(_root(org_id))
    if links.is_request(suggestion_id):             # 凭邀请链接来的：按链接给的身份组批准
        if body:
            return _error(400, "invalid", "凭邀请链接的申请按链接给的身份组批准，这里不另选")
        return await _join_suggestion_call(org_id, request, response, owner=True, write=True,
                                           action=lambda root, who: links.approve(who, suggestion_id,
                                                                                  _invitation_origin(request)))
    return await _join_suggestion_call(org_id, request, response, owner=True, write=True,
                                       action=lambda root, who: app_members.approve_join_suggestion(
                                           root, who, suggestion_id, body, _invitation_origin(request)))


@router.post(_JOIN_REQUESTS + "/{suggestion_id}/deny", status_code=204)
async def deny_join_suggestion(org_id: str, suggestion_id: str, body: Dict[str, Any], request: Request,
                               response: Response):
    from src.workflow.members.invite_links import links_for
    links = links_for(_root(org_id))
    if links.is_request(suggestion_id):
        if set(body) - {"reason"} or (body.get("reason") or "").strip():
            return _error(400, "invalid", "这里发不了文字理由给对方，请清空理由后拒绝")
        return await _join_suggestion_call(org_id, request, response, owner=True, write=True, empty=True,
                                           action=lambda root, who: links.deny(who, suggestion_id))
    return await _join_suggestion_call(org_id, request, response, owner=True, write=True, empty=True,
                                       action=lambda root, who: app_members.deny_join_suggestion(
                                           root, who, suggestion_id, body))


@router.patch(_ORG + "/members/{user_id}", status_code=204)
async def change_member_role(org_id: str, user_id: str, body: Dict[str, Any], request: Request):
    """这里的成员不分「管理员 / 普通成员」两档：权限按身份组给（PUT …/members/{id}/roles）。
    只接受「成员」这一档（不变），其它的如实拒绝，不悄悄换成身份组。"""
    root = _root(org_id)
    if body != {"role": "member"}:
        return _error(400, "unsupported", "成员的权限按身份组给，请在成员资料里改身份组")
    if user_id not in {row["userId"] for row in app_members.members(root)} or user_id == views.owner_id(root):
        return _error(404, "not_found", "没有这个成员")
    return Response(status_code=204)


@router.post(_ORG + "/members/{user_id}/password-link")
async def password_link(org_id: str, user_id: str, request: Request):
    root = _root(org_id)
    who, origin = member_auth.principal(request, root), _origin(request)
    return await _members_call(lambda: app_members.password_link(root, who, user_id, origin), write=True)


# 下面几条不过鉴权中间件：预览和过期状态仅凭邀请令牌，接受和读取回执要核对成员会话。

@router.get("/api/app/invitations/{token}")
async def invitation_preview(token: str):
    from src.workflow.members.invite_links import links_for
    root = os.getcwd()

    def read():
        return app_members.invitation_preview(root, token) or links_for(root).preview(token, views.overview(root)["name"])

    result = await _members_call(read)
    return result if result is not None else _error(404, "not_found", "邀请不存在或已失效")


# ── 邀请链接（主人 2026-11-23 定）：所有者开链接；拿到的人只能申请，进门要所有者批准 ──

def _link_error(error):
    body = {"code": error.code, "message": str(error)}
    return JSONResponse({"error": body}, status_code=error.status, headers={"Cache-Control": "no-store"})


async def _invite_link_call(org_id: str, request: Request, action, *, status: int = 200):
    from src.workflow.members.invite_links import LinkError, links_for
    root = _root(org_id)
    who = _who(request, root)
    if who is None or not who.is_owner or not app_staff.owner_proof(request):
        return _error(403, "forbidden", "邀请链接只有已认证的所有者能管")
    try:
        result = await _members_call(lambda: action(links_for(root), who, _invitation_origin(request)), write=True)
    except LinkError as error:
        return _link_error(error)
    if isinstance(result, Response):
        result.headers["Cache-Control"] = "no-store"
        return result
    return JSONResponse(result, status_code=status, headers={"Cache-Control": "no-store"})


@router.get(_ORG + "/invite-links")
async def invite_links(org_id: str, request: Request):
    return await _invite_link_call(org_id, request, lambda links, who, origin: {"links": links.listing(who, origin)})


@router.post(_ORG + "/invite-links")
async def create_invite_link(org_id: str, body: Dict[str, Any], request: Request):
    return await _invite_link_call(org_id, request, lambda links, who, origin: links.create(who, body, origin))


@router.patch(_ORG + "/invite-links/{link_id}")
async def edit_invite_link(org_id: str, link_id: str, body: Dict[str, Any], request: Request):
    return await _invite_link_call(org_id, request, lambda links, who, origin: links.edit(who, link_id, body, origin))


@router.delete(_ORG + "/invite-links/{link_id}", status_code=204)
async def turn_off_invite_link(org_id: str, link_id: str, request: Request):
    return await _invite_link_call(org_id, request, lambda links, who, origin: (
        links.turn_off(who, link_id), Response(status_code=204))[1])


@router.post(_ORG + "/invite-links/{link_id}/renew")
async def renew_invite_link(org_id: str, link_id: str, request: Request):
    return await _invite_link_call(org_id, request, lambda links, who, origin: links.renew(who, link_id))


async def _public_join_call(request: Request, action):
    """凭链接的公开接口：不要求登录，但要同源（浏览器里本服务自己的页面）。"""
    from src.workflow.members.invite_links import LinkError
    from src.workflow.members.join_codes import CodeError
    if not views.owner_connection() or not member_auth.same_origin(request):
        return _error(403, "forbidden", "这里现在不能申请加入")
    try:
        result = await _members_call(action)
    except LinkError as error:
        return _link_error(error)
    except CodeError as error:
        body = {"code": error.code, "message": str(error), **({"retryIn": error.retry_in} if error.retry_in else {})}
        return JSONResponse({"error": body}, status_code=error.status, headers={"Cache-Control": "no-store"})
    if isinstance(result, Response):
        result.headers["Cache-Control"] = "no-store"
        return result
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


def _link_of(root: str, token: str):
    """凭 token 找到开着的邀请链接（找不到、关了、过期都按没有）。返回 (链接 id, InviteLinks)。"""
    from src.workflow.members.invite_links import LinkError, links_for
    from src.workflow.members.registry import Missing
    links = links_for(root)
    shown = links.preview(token, "")
    if shown is None:
        raise Missing("没有这条邀请链接")
    if shown.get("closed"):
        raise LinkError("join.closed", "这条邀请链接已经关掉或过期了")
    state = links.registry.snapshot()
    link_id, _ = links._by_token(state, token)
    return link_id, links


def _join_email(body: Dict[str, Any]) -> str:
    from src.workflow.members.invite_links import LinkError
    from src.workflow.members.registry import login_name
    try:
        return login_name(body.get("email"))
    except ValueError as error:
        raise LinkError("join.invalid_email", "邮箱格式不对", 400) from error


@router.post("/api/app/invitations/{token}/code")
async def send_join_code(token: str, body: Dict[str, Any], request: Request):
    """发邮箱验证码。开关关着（默认）或没配发信时如实 409，申请页直接提交就行。"""
    from src.workflow.members.join_codes import CODES
    root = os.getcwd()

    def send():
        link_id, _ = _link_of(root, token)
        email = _join_email(body)
        return {"email": email, "resendIn": CODES.send(link_id, email, views.overview(root)["name"])}

    return await _public_join_call(request, send)


@router.post("/api/app/invitations/{token}/code/check")
async def check_join_code(token: str, body: Dict[str, Any], request: Request):
    from src.workflow.members.join_codes import CODES, enabled
    from src.workflow.members.join_codes import CodeError
    root = os.getcwd()

    def check():
        if not enabled():
            raise CodeError("join.codes_off", "这个家没有开邮箱验证码，直接提交申请就行")
        link_id, _ = _link_of(root, token)
        CODES.check(link_id, _join_email(body), str(body.get("code") or ""))
        return {"verified": True}

    return await _public_join_call(request, check)


@router.post("/api/app/invitations/{token}/requests")
async def ask_to_join(token: str, body: Dict[str, Any], request: Request):
    """凭链接申请加入。已登录的成员、所有者不用申请。开关开着时要先证实邮箱。"""
    from src.web.auth import is_authed
    from src.workflow.members.invite_links import LinkError
    from src.workflow.members.join_codes import CODES, enabled
    root = os.getcwd()
    if is_authed(request) or member_auth.account(request) is not None:
        return _error(409, "join.already_in", "你已经登录了这个家，不用再申请")

    def ask():
        link_id, links = _link_of(root, token)
        verified = False
        if enabled():
            verified = CODES.proven(link_id, _join_email(body), body.get("code"))
            if not verified:
                raise LinkError("join.code_required", "请先用邮箱验证码证实这个邮箱", 400)
        return links.ask(token, body, verified=verified)

    return await _public_join_call(request, ask)


class _JoinClaimBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    key: str = Field(min_length=1, max_length=200)


@router.post("/api/app/invitations/{token}/requests/claim")
async def claim_join_request(token: str, body: _JoinClaimBody, request: Request):
    """申请页凭申请时拿到的 key 看结果；批准了就给一次性登录。key 对不上回 404。"""
    from src.workflow.members.invite_links import links_for
    from src.workflow.members.registry import Missing
    root = os.getcwd()

    def claim():
        found = links_for(root).claim(token, body.key, views.home_id(root))
        if found is None:
            raise Missing("没有这份申请")
        return found

    return await _public_join_call(request, claim)


@router.post("/api/app/invitations/{token}/request-new")
async def request_new_invitation(token: str, request: Request):
    """过期的邮件邀请想要一份新的：这里没法替你转告邀请人，如实说明。"""
    if not member_auth.same_origin(request):
        return _error(403, "forbidden", "这里现在不能申请")
    return _error(409, "join.ask_inviter", "这里没法替你转告：请直接联系邀请你的人再发一份")


@router.get("/api/app/invitations/{token}/state")
async def invitation_state(token: str, request: Request, response: Response, receipt: str = ""):
    from src.web.auth import is_authed

    root = os.getcwd()
    response.headers["Cache-Control"] = "no-store"

    def read():
        account = None
        # 旧界面退出时会保留邀请缓存，只对已更新缓存策略的客户端启用回执。
        if receipt == "1" and not is_authed(request) and member_auth.same_origin(request):
            account = member_auth.account(request)
        return app_members.invitation_state(root, token, account)

    result = await _members_call(read)
    if result is None:
        result = _error(404, "not_found", "邀请不存在或已失效")
    if isinstance(result, JSONResponse):
        result.headers["Cache-Control"] = "no-store"
    return result


@router.post("/api/app/invitations/{token}/accept")
async def accept_invitation(token: str, request: Request):
    """已登录的成员账号接受邀请、成为成员。所有者不需要、也不能接受邀请。"""
    from src.web.auth import is_authed
    from src.workflow.members.accounts import MemberAccounts
    from src.workflow.members.registry import MemberRegistry
    root = os.getcwd()
    # Use the same identity ordering as the authenticated routes when both sessions are present.
    account = None if is_authed(request) else member_auth.account(request)
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
    return await _members_call(lambda: {"agents": app_members.directory_agents(root, who)})


@router.get(_ORG + "/agent-status")
async def agent_status(org_id: str, request: Request):
    root = _root(org_id)
    try:
        shown = await _visible_ids(request, root)
    except PermissionError as error:
        return _error(403, "forbidden", str(error))
    return {"agents": [row for row in views.agent_statuses(root) if shown is None or row["agentId"] in shown]}


@router.get(_ORG + "/team")
async def agent_team_directory(org_id: str, request: Request, response: Response):
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    owner_activity = who.is_owner and app_staff.owner_proof(request)
    bridge = existing_bridge(root) if owner_activity else None
    main_running = bridge is not None and any(run["status"] == "running" for run in bridge.active_runs())
    response.headers["Cache-Control"] = "no-store"
    return await _members_call(lambda: app_members.team_directory(
        root, who, activity=who.is_member or owner_activity, main_running=main_running))


class _AgentLineChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    agentId: str = Field(min_length=1, max_length=128)
    reportsToAgentId: str | None = Field(min_length=1, max_length=128)


class _TeamLinesChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    changes: list[_AgentLineChange] = Field(max_length=500)


@router.put(_ORG + "/agent-lines")
async def set_agent_lines(org_id: str, body: _TeamLinesChange, request: Request, response: Response):
    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner or not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", "设置团队图的汇报关系需要已认证的所有者")
    response.headers["Cache-Control"] = "no-store"
    changes = [row.model_dump() for row in body.changes]
    return await _members_call(lambda: app_members.set_agent_lines(root, changes))


@router.get(_ORG + "/ai-status")
async def ai_status(org_id: str, request: Request):
    root = _root(org_id)
    try:
        shown = await _visible_ids(request, root)
    except PermissionError as error:
        return _error(403, "forbidden", str(error))
    return await asyncio.to_thread(views.ai_status, root, shown)


# ── 私聊与消息 ────────────────────────────────────────────────────────────────

@router.get(_ORG + "/dms")
async def dms(org_id: str, request: Request):
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    if who.is_owner and not app_staff.owner_proof(request):
        return {"dms": [views.main_dm(root)]}

    def listing(direct, actor):
        existing = [views.main_dm(root)] if actor.is_owner else app_members.member_dms(root, actor)
        return {"dms": existing + direct.list(actor)}

    return await _person_dm_call(request, org_id, listing)


class _OpenDm(BaseModel):
    agentId: str = ""
    userId: str = ""


@router.post(_ORG + "/dms")
async def open_dm(org_id: str, body: _OpenDm, request: Request):
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    if body.userId:
        if body.agentId:
            return _error(400, "invalid", "请选择一个私聊对象")

        def open_person(direct, actor):
            dm = direct.open(actor, body.userId)
            return {"channelId": dm["channelId"], "dm": dm}

        return await _person_dm_call(request, org_id, open_person)
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


async def _call_channel_agents(request: Request, org_id: str, channel_id: str, message: Dict[str, Any]) -> None:
    """消息已经发出：叫到的 agent 接活。出了什么问题都不影响这条消息本身（agent 会在话题里说明）。
    留在事件循环线程：派活要把任务登记进执行池。"""
    root = _root(org_id)
    actor = _who(request, root)
    if actor is None:
        return
    try:
        await _dispatch_ready(root)
        app_channel_agents.call(root, app_channels.channels(root), actor, channel_id, message)
    except Exception:  # noqa: BLE001 —— 消息已存好，叫 agent 失败不能让发消息报错
        logging.getLogger(__name__).warning("频道里叫 agent 失败", exc_info=True)


async def _dispatch_ready(root: str) -> None:
    """派活要用主要聊天的会话当发起身份；新界面里还没打开过主要聊天时（直接去 agent 私聊派活）先登记上。"""
    if not views.main_chat_key(root):
        await main_chat_bridge(root)


async def _as_member(request: Request, org_id: str, channel_id: str, answer, *, dispatch: bool = False):
    """成员会话走这里：answer(家, 专员, 成员) 给出回答。不是成员会话返回 None，交回原有逻辑。
    dispatch：answer 会把事交进执行池——执行池只能在事件循环所在的线程里登记任务，所以就地调用，不挪到工作线程。"""
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
        if dispatch:
            await _dispatch_ready(found[0])
            return answer(*found)
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


async def _team_call(request, org_id, action, *, channel_id=None, empty_listing=None):
    from src.gateway.session_store import ChannelNameConflict
    from src.workflow.members.registry import Damaged, Missing, Refused

    if channel_id is not None and not is_team_channel(channel_id):
        return None
    root = _root(org_id)
    actor = _who(request, root)
    if actor is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    if actor.is_owner and not app_staff.owner_proof(request):
        if empty_listing is not None and not views.owner_connection():
            return empty_listing
        return _error(403, "owner_proof_required", "团队频道需要已认证的同源会话")
    try:
        return await asyncio.to_thread(lambda: action(app_channels.channels(root), actor))
    except (Refused, PermissionError) as error:
        return _error(403, "forbidden", str(error))
    except (Missing, LookupError) as error:
        return _error(404, "not_found", str(error))
    except (Damaged, OSError):
        return _error(409, "unavailable", "读取或保存频道记录失败")
    except ChannelNameConflict as error:
        return _error(409, "channel.name_taken", str(error))
    except ChannelCategoryNameConflict as error:
        return _error(409, "channel_category.name_taken", str(error))
    except ThreadArchived as error:
        return _error(409, "thread.archived", str(error))
    except ThreadInUse as error:
        return _error(409, "thread.in_use", str(error))
    except ThreadRangeUnavailable as error:
        return _error(422, "invalid", str(error))
    except ValueError as error:
        return _error(400, "invalid", str(error))


async def _person_dm_call(request, org_id, action):
    result = await _team_call(request, org_id, lambda _, actor:
                              action(app_channels.person_dms(_root(org_id)), actor))
    response = result if isinstance(result, Response) else JSONResponse(result)
    response.headers["Cache-Control"] = "no-store"
    return response


def _person_dm_empty(direct, actor, channel_id, payload):
    if not direct.can_read(actor, channel_id):
        raise LookupError("没有这个私聊")
    return payload


def _team_response(response):
    return _error(404, "not_found", "没有这个频道") if response is None else response


async def _issue_chat_call(request, org_id, action):
    result = await _team_call(request, org_id, lambda _, actor: action(
        app_issue_chat.IssueChat(_root(org_id)), actor))
    if isinstance(result, Response):
        result.headers["Cache-Control"] = "no-store"
        return result
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


def _team_receipt(action):
    action()
    return Response(status_code=204)


class _TeamChannelChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    topic: str | None = None
    visibility: str | None = None
    audienceRoleIds: list[str] | None = Field(default=None, max_length=64)


class _TeamChannelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    topic: str = ""
    visibility: str = "public"
    audienceRoleIds: list[str] = Field(default_factory=list, max_length=64)
    userIds: list[str] = Field(default_factory=list, max_length=500)
    categoryId: str | None = None


class _CategoryName(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str


class _CategoryCreate(_CategoryName):
    channelIds: list[str] = Field(default_factory=list, max_length=500)


class _CategoryPlacement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    beforeCategoryId: str | None


class _ChannelPlacement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    categoryId: str | None
    beforeChannelId: str | None = None


class _ChannelPerson(BaseModel):
    model_config = ConfigDict(extra="forbid")
    userId: str | None = None
    agentId: str | None = None


class _AgentAutoReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    autoReply: StrictBool


class _AgentChannel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channelId: str
    autoReply: StrictBool | None = None


class _AgentChannels(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channels: list[_AgentChannel] = Field(max_length=500)


_AUTO_REPLY_OFF = "不 @ 也回答第一期不做：频道里的 agent 只在被 @ 时回答"
_AGENT_NOT_IN_CHANNELS = "这个 agent 不能进团队频道：主助理、用本机 CLI 或连着登记项目的 agent 第一期只在私聊里"


def _channel_agent(root: str, agent_id: str):
    """(专员 id, 错误)：主助理和不能进频道的回 409，不认识的回 404。"""
    if agent_id == views.main_agent_id(root):
        return None, _error(409, "agent_channels.unsupported", _AGENT_NOT_IN_CHANNELS)
    row = next((row for row in app_channels.channel_agents(root).values() if row["id"] == agent_id), None)
    if row is None:
        return None, _error(404, "not_found", "没有这个 agent")
    if not row["joinable"]:
        return None, _error(409, "agent_channels.unsupported", _AGENT_NOT_IN_CHANNELS)
    return row["handle"], None


class _ChannelOverrideTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    id: str


class _ChannelOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: _ChannelOverrideTarget
    allow: list[str] = Field(max_length=3)
    deny: list[str] = Field(max_length=3)


@router.get(_ORG + "/channels")
async def list_team_channels(org_id: str, request: Request):
    return await _team_call(request, org_id, lambda service, actor: {"channels": service.list(actor)},
                            empty_listing={"channels": []})


@router.get(_ORG + "/archived-channels")
async def list_archived_team_channels(org_id: str, request: Request):
    return await _team_call(request, org_id, lambda service, actor: {"channels": service.list(actor, archived=True)},
                            empty_listing={"channels": []})


@router.get(_ORG + "/channel-categories")
async def list_channel_categories(org_id: str, request: Request):
    return await _team_call(request, org_id, lambda service, actor: {"categories": service.list_categories(actor)},
                            empty_listing={"categories": []})


@router.post(_ORG + "/channel-categories", status_code=201)
async def create_channel_category(org_id: str, body: _CategoryCreate, request: Request):
    return await _team_call(request, org_id, lambda service, actor:
                            service.create_category(actor, body.name, body.channelIds))


@router.patch(_ORG + "/channel-categories/{category_id}")
async def rename_channel_category(org_id: str, category_id: str, body: _CategoryName, request: Request):
    return await _team_call(request, org_id, lambda service, actor:
                            service.rename_category(actor, category_id, body.name))


@router.delete(_ORG + "/channel-categories/{category_id}", status_code=204)
async def delete_channel_category(org_id: str, category_id: str, request: Request):
    return await _team_call(request, org_id, lambda service, actor:
                            _team_receipt(lambda: service.delete_category(actor, category_id)))


@router.put(_ORG + "/channel-categories/{category_id}/place", status_code=204)
async def place_channel_category(org_id: str, category_id: str, body: _CategoryPlacement, request: Request):
    return await _team_call(request, org_id, lambda service, actor:
                            _team_receipt(lambda: service.place_category(actor, category_id, body.beforeCategoryId)))


@router.post(_ORG + "/channels", status_code=201)
async def create_team_channel(org_id: str, body: _TeamChannelCreate, request: Request):
    return await _team_call(request, org_id, lambda service, actor: service.create(
        actor, body.name, body.topic, body.visibility, body.audienceRoleIds, body.userIds, category_id=body.categoryId))


@router.put(_ORG + "/channels/{channel_id}/place", status_code=204)
async def place_team_channel(org_id: str, channel_id: str, body: _ChannelPlacement, request: Request):
    return _team_response(await _team_call(request, org_id, lambda service, actor:
        _team_receipt(lambda: service.place_channel(actor, channel_id, body.categoryId, body.beforeChannelId)),
        channel_id=channel_id))


@router.patch(_ORG + "/channels/{channel_id}")
async def update_team_channel(org_id: str, channel_id: str, body: _TeamChannelChange, request: Request):
    return _team_response(await _team_call(request, org_id, lambda service, actor:
        service.update(actor, channel_id, body.model_dump(exclude_none=True)) or Response(status_code=204),
        channel_id=channel_id))


@router.get(_ORG + "/channels/{channel_id}/card")
async def team_channel_card(org_id: str, channel_id: str, request: Request):
    return _team_response(await _team_call(request, org_id, lambda service, actor: service.card(
        actor, channel_id, agents=app_channels.channel_agents(_root(org_id))), channel_id=channel_id))


@router.delete(_ORG + "/channels/{channel_id}", status_code=204)
@router.post(_ORG + "/channels/{channel_id}/unarchive", status_code=204)
async def archive_team_channel(org_id: str, channel_id: str, request: Request):
    return _team_response(await _team_call(request, org_id, lambda service, actor:
        _team_receipt(lambda: service.archive(actor, channel_id, request.method == "DELETE")), channel_id=channel_id))


class _ChannelDelete(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=200)


@router.post(_ORG + "/channels/{channel_id}/delete", status_code=204)
async def delete_team_channel(org_id: str, channel_id: str, body: _ChannelDelete, request: Request):
    """所有者永久删掉一个频道：消息、话题、话题上的 Mission、投票卡一起删，找不回来。要打一遍频道名确认；
    频道里还有 agent 在做事时拒绝。删之前先写一条持久审计。"""
    from src.gateway.audit import append_audit
    root = _root(org_id)

    def delete(service, actor):
        if not actor.is_owner:
            return _error(403, "forbidden", "删除频道只有所有者能做")
        busy = bool(app_channel_agents.runs(root, channel_id))
        try:
            service.delete(actor, channel_id, body.name, busy=busy, dry_run=True)
        except ValueError as error:
            return _error(409, "channel.delete_refused", str(error))
        if not append_audit(root, {"category": "decision", "decision": "allowed", "decision_source": "user",
                                   "operation": "永久删除团队频道", "operation_kind": "irreversible",
                                   "channel": channel_id}, durable=True):
            return _error(503, "audit_unavailable", "审计未能保存，没有删除")
        try:
            service.delete(actor, channel_id, body.name, busy=busy)
        except ValueError as error:
            return _error(409, "channel.delete_refused", str(error))
        return Response(status_code=204)

    return _team_response(await _team_call(request, org_id, delete, channel_id=channel_id))


@router.get(_ORG + "/channels/{channel_id}/consultations")
async def channel_consultations(org_id: str, channel_id: str, request: Request):
    """agent 之间互相咨询还没有：能读这个频道的人看到空列表。"""
    def read(service, actor):
        if not service.can_read(actor, channel_id):
            raise LookupError("没有这个频道")
        return {"consultations": []}

    return _team_response(await _team_call(request, org_id, read, channel_id=channel_id))


@router.get(_ORG + "/channels/{channel_id}/consultations/{consultation_id}")
async def channel_consultation(org_id: str, channel_id: str, consultation_id: str, request: Request):
    def read(service, actor):
        if not service.can_read(actor, channel_id):
            raise LookupError("没有这个频道")
        raise LookupError("没有这次咨询")

    return _team_response(await _team_call(request, org_id, read, channel_id=channel_id))


@router.get(_ORG + "/channels/{channel_id}/follow-ups")
async def channel_follow_ups(org_id: str, channel_id: str, request: Request):
    """agent 约好之后回来跟进（回传）还没有：空列表。"""
    def read(service, actor):
        if not service.can_read(actor, channel_id):
            raise LookupError("没有这个频道")
        return {"followUps": []}

    return _team_response(await _team_call(request, org_id, read, channel_id=channel_id))


@router.post(_ORG + "/channels/{channel_id}/join", status_code=204)
@router.post(_ORG + "/channels/{channel_id}/leave", status_code=204)
async def join_team_channel(org_id: str, channel_id: str, request: Request):
    return _team_response(await _team_call(request, org_id, lambda service, actor:
        _team_receipt(lambda: service.join(actor, channel_id, request.url.path.endswith("/join"))), channel_id=channel_id))


@router.post(_ORG + "/channels/{channel_id}/members", status_code=204)
async def add_team_channel_member(org_id: str, channel_id: str, body: _ChannelPerson, request: Request):
    if (body.userId is None) == (body.agentId is None):
        return _error(422, "invalid", "要么加一个人（userId），要么加一个 agent（agentId）")
    if body.agentId is None:
        return _team_response(await _team_call(request, org_id, lambda service, actor:
            _team_receipt(lambda: service.set_person(actor, channel_id, body.userId, True)), channel_id=channel_id))
    if not is_team_channel(channel_id):
        return _error(404, "not_found", "没有这个频道")
    staff, refused = await asyncio.to_thread(_channel_agent, _root(org_id), body.agentId)
    if refused is not None:
        return refused
    return _team_response(await _team_call(request, org_id, lambda service, actor:
        _team_receipt(lambda: service.set_agent(actor, channel_id, staff, True)), channel_id=channel_id))


@router.delete(_ORG + "/channels/{channel_id}/members/agents/{agent_id}", status_code=204)
async def remove_team_channel_agent(org_id: str, channel_id: str, agent_id: str, request: Request):
    staff = await asyncio.to_thread(_staff_of, _root(org_id), agent_id)      # 删掉的 agent 也能拿出来
    if staff is None:
        return _error(404, "not_found", "没有这个 agent")
    return _team_response(await _team_call(request, org_id, lambda service, actor:
        _team_receipt(lambda: service.set_agent(actor, channel_id, staff, False)), channel_id=channel_id))


@router.patch(_ORG + "/channels/{channel_id}/members/agents/{agent_id}", status_code=204)
async def set_team_channel_agent(org_id: str, channel_id: str, agent_id: str, body: _AgentAutoReply,
                                 request: Request):
    staff = await asyncio.to_thread(_staff_of, _root(org_id), agent_id)

    def change(service, actor):
        if staff is None or not service.has_agent(actor, channel_id, staff):
            raise LookupError("这个 agent 不在这个频道里")
        if body.autoReply:
            return _error(409, "agent_channels.auto_reply_unavailable", _AUTO_REPLY_OFF)
        return Response(status_code=204)                    # 本来就只在被 @ 时回答
    return _team_response(await _team_call(request, org_id, change, channel_id=channel_id))


@router.delete(_ORG + "/channels/{channel_id}/members/people/{person_id}", status_code=204)
async def remove_team_channel_member(org_id: str, channel_id: str, person_id: str, request: Request):
    return _team_response(await _team_call(request, org_id, lambda service, actor:
        _team_receipt(lambda: service.set_person(actor, channel_id, person_id, False)), channel_id=channel_id))


@router.get(_ORG + "/channels/{channel_id}/roster")
async def team_channel_roster(org_id: str, channel_id: str, request: Request):
    return _team_response(await _team_call(request, org_id, lambda service, actor:
        {"members": service.roster(actor, channel_id, agents=app_channels.channel_agents(_root(org_id)))},
        channel_id=channel_id))


@router.get(_ORG + "/channels/{channel_id}/permissions")
async def team_channel_permissions(org_id: str, channel_id: str, request: Request):
    return _team_response(await _team_call(request, org_id, lambda service, actor:
        {"overrides": service.overrides(actor, channel_id)}, channel_id=channel_id))


@router.put(_ORG + "/channels/{channel_id}/permissions")
async def set_team_channel_permission(org_id: str, channel_id: str, body: _ChannelOverride, request: Request):
    if body.target.type not in ("person", "role"):
        return _error(400, "invalid", "频道权限例外的对象必须是成员或身份组")
    return _team_response(await _team_call(request, org_id, lambda service, actor:
        service.set_override(actor, channel_id, body.target.model_dump(), body.allow, body.deny)
        or Response(status_code=204), channel_id=channel_id))


@router.post(_ORG + "/channels/{channel_id}/read", status_code=204)
async def mark_team_channel_read(org_id: str, channel_id: str, request: Request):
    if is_person_dm(channel_id):
        return await _person_dm_call(request, org_id, lambda direct, actor:
                                     _team_receipt(lambda: direct.mark_read(actor, channel_id)))
    return _team_response(await _team_call(request, org_id, lambda service, actor:
        _team_receipt(lambda: service.read(actor, channel_id)), channel_id=channel_id))


@router.get(_ORG + "/channels/{channel_id}/reads")
async def direct_message_reads(org_id: str, channel_id: str, request: Request):
    if not is_person_dm(channel_id):
        return _error(404, "not_found", "没有这个私聊")
    return await _person_dm_call(request, org_id, lambda direct, actor: {"reads": direct.reads(actor, channel_id)})


class _ChannelMuteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.put(_ORG + "/channels/{channel_id}/mute", status_code=204)
@router.delete(_ORG + "/channels/{channel_id}/mute", status_code=204)
async def mute_team_channel(org_id: str, channel_id: str, request: Request, body: _ChannelMuteBody | None = None):
    return _team_response(await _team_call(request, org_id, lambda service, actor:
        _team_receipt(lambda: service.set_muted(actor, channel_id, request.method == "PUT")), channel_id=channel_id))


@router.get(_ORG + "/refs/{ref_id}")
async def id_ref(org_id: str, ref_id: str, request: Request):
    """文字里的内部编号显示成小标签：看板任务、频道消息、制品。每一种都按它原有的可见性核对，
    看不到的和不存在的一律 404（不说是哪一种）。"""
    from src.web import app_board
    from src.workflow.board.store import Board, NotFound
    from src.workflow.members.registry import Damaged, Missing, Refused
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    if who.is_owner and not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", _NEEDS_OWNER)
    if not 1 <= len(ref_id) <= 128:
        return _error(404, "not_found", "没有这个编号")

    def resolve():
        try:
            issue = Board(root).issue(ref_id)
            if app_board.can_see(issue, who):
                return {"kind": "task", "id": issue["id"], "number": issue["number"], "title": issue["title"]}
        except (NotFound, ValueError, OSError):
            pass
        try:
            card = app_channels.channels(root).message_details(
                who, ref_id, load_agents=lambda: app_channels.channel_agents(root))
            return {"kind": "message", "message": card}
        except (Missing, Refused, Damaged, LookupError, ValueError, OSError):
            pass
        if who.is_owner:
            try:
                found = views.artifact(root, ref_id, None)
            except (OSError, ValueError):
                found = None
            if found is not None:
                return {"kind": "file", "id": found["id"], "title": found.get("title") or found["id"],
                        "artifactKind": found.get("kind") or "file", "fileName": None, "contentType": None}
        return None

    found = await asyncio.to_thread(resolve)
    if found is None:
        return _error(404, "not_found", "没有这个编号")
    return JSONResponse(found, headers={"Cache-Control": "no-store"})


@router.get(_ORG + "/messages/{message_id}/card")
async def team_message_card(org_id: str, message_id: str, request: Request):
    return await _team_call(request, org_id, lambda service, actor: service.message_details(
        actor, message_id, load_agents=lambda: app_channels.channel_agents(_root(org_id))))


class _MessageReaction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reaction: str = Field(min_length=1, max_length=REACTION_MAX_LENGTH)


@router.get(_ORG + "/messages/{message_id}/reactions")
async def team_message_reactions(org_id: str, message_id: str, request: Request):
    return await _team_call(request, org_id, lambda service, actor:
        {"reactions": service.message_reactions(actor, message_id)})


@router.post(_ORG + "/messages/{message_id}/reactions")
async def add_team_message_reaction(org_id: str, message_id: str, body: _MessageReaction, request: Request):
    return await _team_call(request, org_id, lambda service, actor:
        {"reactions": service.message_reactions(actor, message_id, reaction=body.reaction)})


@router.delete(_ORG + "/messages/{message_id}/reactions")
async def remove_team_message_reaction(org_id: str, message_id: str, request: Request,
                                       reaction: str = Query(min_length=1, max_length=REACTION_MAX_LENGTH)):
    return await _team_call(request, org_id, lambda service, actor:
        {"reactions": service.message_reactions(actor, message_id, reaction=reaction, enabled=False)})


@router.get(_ORG + "/threads")
async def list_team_threads(org_id: str, request: Request, tab: str = "all", channelId: str = "",  # noqa: N803
                            agentId: str = "", needsYou: bool = False, mine: bool = False,  # noqa: N803
                            q: str = Query(default="", max_length=512),
                            cursor: str = Query(default="", max_length=64), limit: int = 100):
    return await _team_call(request, org_id, lambda service, actor: service.organization_threads(
        actor, tab=tab, channel_id=channelId, agent_id=agentId, needs_you=needsYou, mine=mine, query=q, cursor=cursor,
        limit=limit), empty_listing={"threads": [], "nextCursor": None})


@router.get(_THREAD)
async def team_thread_detail(org_id: str, thread_id: str, request: Request):
    if app_issue_chat.is_thread(thread_id):
        return await _issue_chat_call(request, org_id, lambda chat, actor: chat.thread(actor, thread_id))
    return await _team_call(request, org_id, lambda service, actor: service.message_details(actor, thread_id, thread=True))


_ThreadMessageId = Annotated[str, Field(min_length=1, max_length=64)]


class _ThreadCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    channelId: _ThreadMessageId
    contextChannelId: _ThreadMessageId | None = None
    firstMessageId: _ThreadMessageId
    lastMessageId: _ThreadMessageId
    excludedMessageIds: list[_ThreadMessageId] = Field(default_factory=list, max_length=200)
    title: str | None = Field(default=None, max_length=200)


@router.post(_ORG + "/threads", status_code=201)
async def create_team_thread(org_id: str, body: _ThreadCreate, request: Request):
    return _team_response(await _team_call(
        request, org_id, lambda service, actor: service.create_thread_range(
            actor, body.channelId, body.contextChannelId, body.firstMessageId, body.lastMessageId,
            body.excludedMessageIds, body.title), channel_id=body.channelId,
    ))


class _ThreadPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    firstMessageId: _ThreadMessageId | None = None
    lastMessageId: _ThreadMessageId | None = None
    excludedMessageIds: list[_ThreadMessageId] | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=200)


@router.patch(_THREAD)
async def rename_team_thread(org_id: str, thread_id: str, body: _ThreadPatch, request: Request):
    patch = body.model_dump(exclude_unset=True, exclude={"title"})
    if patch:
        if None in patch.values():
            return _error(422, "invalid", "选段起点、终点和排除项不能为 null")
        return await _team_call(request, org_id, lambda service, actor: service.update_thread_range(
            actor, thread_id, patch, title=body.title, set_title="title" in body.model_fields_set))
    if "title" not in body.model_fields_set:
        return _error(422, "invalid", "需要话题标题或频道选段")
    return await _team_call(request, org_id, lambda service, actor:
                            service.change_thread(actor, thread_id, "title", title=body.title))


@router.delete(_THREAD, status_code=204)
async def undo_team_thread_range(org_id: str, thread_id: str, request: Request):
    return await _team_call(request, org_id, lambda service, actor:
                            _team_receipt(lambda: service.remove_thread_range(actor, thread_id)))


class _ThreadAction(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.post(_THREAD + "/move")
@router.post(_THREAD + "/return")
@router.post(_THREAD + "/close")
@router.post(_THREAD + "/reopen")
@router.post(_THREAD + "/archive")
@router.post(_THREAD + "/unarchive")
async def act_team_thread(org_id: str, thread_id: str, request: Request, body: _ThreadAction | None = None):
    action = request.url.path.rsplit("/", 1)[1]
    return await _team_call(request, org_id, lambda service, actor: service.change_thread(actor, thread_id, action))


@router.put(_THREAD + "/follow")
@router.delete(_THREAD + "/follow")
async def follow_team_thread(org_id: str, thread_id: str, request: Request, body: _ThreadAction | None = None):
    return await _team_call(request, org_id, lambda service, actor:
                            service.set_thread_follow(actor, thread_id, request.method == "PUT"))


@router.post(_THREAD + "/read", status_code=204)
async def mark_team_thread_read(org_id: str, thread_id: str, request: Request):
    if app_issue_chat.is_thread(thread_id):
        return await _issue_chat_call(request, org_id, lambda chat, actor:
                                      _team_receipt(lambda: chat.mark_read(actor, thread_id)))
    return await _team_call(request, org_id, lambda service, actor:
                            _team_receipt(lambda: service.mark_thread_read(actor, thread_id)))


@router.get(_ORG + "/channels/{channel_id}/members")
async def channel_members(org_id: str, channel_id: str, request: Request):
    if app_issue_chat.is_channel(channel_id):
        return await _issue_chat_call(request, org_id, lambda chat, actor: {
            "members": chat.members(actor, channel_id)})
    if is_person_dm(channel_id):
        return await _person_dm_call(request, org_id, lambda direct, actor:
                                     {"members": direct.roster(actor, channel_id)})
    team = await _team_call(request, org_id, lambda service, actor: {"members": [
        {"type": person["type"], "id": person["id"]}
        for person in service.roster(actor, channel_id, agents=app_channels.channel_agents(_root(org_id)))]},
        channel_id=channel_id)
    if team is not None:
        return team
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
    if app_issue_chat.is_channel(channel_id):
        return await _issue_chat_call(request, org_id, lambda chat, actor: {
            "messages": chat.messages(actor, channel_id, before=before, limit=limit, thread=threadId or None)})
    if is_person_dm(channel_id):
        if threadId:
            return await _person_dm_call(request, org_id, lambda direct, actor:
                                         _person_dm_empty(direct, actor, channel_id, {"messages": []}))
        return await _person_dm_call(request, org_id, lambda direct, actor:
                                     {"messages": direct.messages(actor, channel_id, before=before, limit=limit)})
    team = await _team_call(request, org_id, lambda service, actor: {"messages": service.messages(
        actor, channel_id, before=before, limit=limit, thread=threadId or None)}, channel_id=channel_id)
    if team is not None:
        return team
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
    if app_staff.owner_proof(request):
        messages = await asyncio.to_thread(views.with_proposal_cards, root, messages, latest=not before)
    bridge = existing_bridge(root)
    if bridge is not None and not before:
        messages += [views.approval_card(root, item) for item in reversed(bridge.pending_confirmations())]
    return {"messages": messages}


class _ChannelRecommendationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000)


@router.post(_ORG + "/channels/{channel_id}/recommendation")
async def preview_channel_recommendation(org_id: str, channel_id: str, body: _ChannelRecommendationDraft, request: Request):
    from src.web import app_channel_dispatch, app_channel_recommendations, app_credentials

    text = body.text.strip()
    if not text or "\0" in text or "@" in text or "＠" in text or text.startswith("/"):
        return _error(400, "invalid", "请使用未提及成员或智能体的普通文字草稿")
    root = _root(org_id)
    inputs = _team_response(await _team_call(request, org_id, lambda service, actor:
        (actor, app_channel_recommendations.candidates_in_channel(root, service, actor, channel_id, text)),
        channel_id=channel_id))
    if isinstance(inputs, Response):
        return inputs
    actor, (candidates, owner_id) = inputs
    try:
        backup_key = await asyncio.to_thread(app_credentials.decisions_backup_key, root)
    except app_credentials.CredentialError:
        backup_key = None
    try:
        recommendation = await app_channel_recommendations.recommend_channel_assignee(
            text, candidates, authoritative_owner_id=owner_id, decisions_api_key=backup_key)
    except app_channel_recommendations.RecommendationUnavailable:
        return _error(503, "unavailable", "暂时无法生成接手建议")
    token = app_channel_dispatch.preview_token(org_id, channel_id, actor, text, owner_id, recommendation)
    if token is not None:
        recommendation["confirmationToken"] = token
    return JSONResponse(recommendation, headers={"Cache-Control": "no-store"})


class _ChannelRecommendationConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    confirmationToken: str = Field(min_length=1, max_length=32000)


@router.post(_ORG + "/channels/{channel_id}/recommendation/confirm")
async def confirm_channel_recommendation(org_id: str, channel_id: str, body: _ChannelRecommendationConfirm, request: Request):
    from src.web import app_channel_dispatch

    def resolve(service, actor):
        service.roster(actor, channel_id)
        return service, actor

    root = _root(org_id)
    access = _team_response(await _team_call(request, org_id, resolve, channel_id=channel_id))
    if isinstance(access, Response):
        return access
    service, actor = access
    try:
        plan = app_channel_dispatch.open_confirmation(body.confirmationToken, org_id, channel_id, actor)
        receipt = await app_channel_dispatch.confirm(root, service, actor, plan)
    except app_channel_dispatch.DispatchError as error:
        return _error(error.status, error.code, str(error))
    except (Refused, PermissionError) as error:
        return _error(403, "forbidden", str(error))
    except LookupError:
        return _error(404, "not_found", "没有这条接手建议")
    except (Damaged, OSError, sqlite3.Error, ValueError):
        return _error(503, "unavailable", "暂时无法派发，请重试同一条建议")
    return JSONResponse(receipt, headers={"Cache-Control": "no-store"})


class _Post(BaseModel):
    model_config = ConfigDict(extra="allow")
    body: str = ""
    clientId: str | None = None
    threadId: str | None = None
    replyTo: str | None = None
    openThread: bool = False


@router.post(_ORG + "/channels/{channel_id}/messages")
async def post_message(org_id: str, channel_id: str, body: _Post, request: Request):
    """主人在私聊里发一条：交给主要聊天开一个回合（前面还有回合就排队）。应答是这条消息本身，
    助理的回复之后经实时连接或下一次读取出现。"""
    team_channel = is_team_channel(channel_id)
    person_dm = is_person_dm(channel_id)
    task_channel = app_issue_chat.is_channel(channel_id)
    human_channel = team_channel or person_dm or task_channel
    root = None if human_channel else _main_channel(org_id, channel_id)
    member = _member_channel(org_id, channel_id) if root is None and not human_channel else None
    if not human_channel and root is None and member is None:
        return _error(404, "not_found", "没有这个对话")
    text = body.body.strip()
    extra = dict(body.model_extra or {})
    wanted_media = extra.pop("media", None)
    if not text and not wanted_media:
        return _error(400, "empty_message", "消息不能为空")
    if len(text) > 20000:
        return _error(413, "message_too_long", "消息太长")
    client_id = (body.clientId or "")[:120] or None
    if task_channel:
        if set(extra) - {"attachments", "steer"}:
            return _error(400, "invalid", "任务消息包含不认识的字段")
        if (wanted_media is not None or extra.get("attachments") not in (None, [])
                or extra.get("steer") is not None or body.openThread):
            return _error(422, "message.media_off", "任务对话目前支持文字和同一话题的引用")
        return await _issue_chat_call(request, org_id, lambda chat, actor: chat.post(
            actor, channel_id, text, client_id=client_id, thread_id=body.threadId, reply_to=body.replyTo))
    if person_dm:
        if wanted_media is not None or extra or body.threadId or body.openThread:
            return _error(422, "message.media_off", "成员私聊支持文字和同一对话中的引用")
        return await _person_dm_call(request, org_id, lambda direct, actor:
                                     direct.send(actor, channel_id, text, client_id=client_id, reply_to=body.replyTo))
    if wanted_media and not team_channel:
        # GIF 只在人和人的团队频道里：发给 agent 的私聊不带图片（不进模型上下文）
        return _error(422, "message.media_off", "这里不能发 GIF")
    if team_channel:
        if any(value for value in extra.values()):
            return _error(400, "invalid", "团队频道目前支持文字、GIF、引用和线程回复")
        media = None
        if wanted_media:
            media = await _gif_for_message(org_id, wanted_media)
            if isinstance(media, JSONResponse):
                return media
        posted = await _team_call(request, org_id, lambda service, actor: service.post(
            actor, channel_id, text, client_id=client_id, thread=body.threadId or None,
            reply_to=body.replyTo or None, opened=bool(body.threadId and body.openThread), media=media))
        if isinstance(posted, dict) and posted.get("threadId"):
            await _note_mission_suggestion(request, org_id, posted)
        if isinstance(posted, dict) and "@" in text:
            await _call_channel_agents(request, org_id, channel_id, posted)
        return posted
    if body.threadId:
        return _error(400, "threads_unsupported", "这里还不支持在话题里回复")
    answer = await _as_member(request, org_id, channel_id,
                              lambda root, staff, who: app_members.member_send(root, who, staff, text, client_id),
                              dispatch=True)
    if answer is not None:
        return answer
    if member:
        if len(text) > 4000:
            return _error(413, "message_too_long", "交给专员的一件事最多 4000 字")
        if not app_staff.owner_proof(request):
            return _error(403, "owner_proof_required", _NEEDS_OWNER)
        from src.web.routers.computers import direct_local_request, has_computer_owner_proof
        local_owner = direct_local_request(request) and has_computer_owner_proof(request)
        if not local_owner:
            from src.workflow.staff.registry import StaffRegistry
            staff = _staff_read(lambda: StaffRegistry(member[0]).member(member[1]))
            if isinstance(staff, JSONResponse):
                return staff
            if staff.get("engine") == "codex":
                return _error(403, "local_owner_required", "请在运行 Codex 的 Mac 上打开本机工作区")
        # 执行池只能在事件循环线程里登记任务（见 _as_member 的 dispatch）
        await _dispatch_ready(member[0])
        return _staff_read(lambda: app_staff.send(member[0], member[1], text, client_id, local_owner=local_owner))
    try:
        from src.web.routers.computers import (
            has_computer_owner_proof, has_local_owner_browser_session, has_owner_browser_session,
        )
        return await (await main_chat_bridge(root)).post(
            text, client_id, file_owner=has_computer_owner_proof(request),
            local_browser=has_local_owner_browser_session(request),
            paired_browser=has_owner_browser_session(request),
        )
    except NoModel:
        return _error(409, "no_ai", "还没有连接模型，没法回复")


@router.get(_ORG + "/channels/{channel_id}/active-runs")
async def channel_active_runs(org_id: str, channel_id: str, request: Request):
    if app_issue_chat.is_channel(channel_id):
        return await _issue_chat_call(request, org_id, lambda chat, actor: chat.active_runs(actor, channel_id))
    if is_person_dm(channel_id):
        return await _person_dm_call(request, org_id, lambda direct, actor:
                                     _person_dm_empty(direct, actor, channel_id, {"runs": []}))
    def team_runs(service, actor):
        if not service.can_read(actor, channel_id):
            raise LookupError("没有这个频道")
        return {"runs": app_channel_agents.runs(_root(org_id), channel_id)}

    team = await _team_call(request, org_id, team_runs, channel_id=channel_id)
    if team is not None:
        return team
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
async def active_runs(org_id: str, request: Request):
    """主要聊天和 agent 此刻在跑的；agent 私聊里的事要所有者证明才列。"""
    from src.web import app_work
    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner:
        return {"runs": []}
    bridge, staff = existing_bridge(root), app_staff.owner_proof(request)
    main = bridge.active_runs() if bridge else []
    approving = {task.id for task, _card in _staff_actions(root)} if staff else set()
    return {"runs": await asyncio.to_thread(app_work.active_runs, root, main, approving, staff=staff)}


@router.get(_ORG + "/task-manager")
async def task_manager(org_id: str, request: Request, since: str = "", doneToday: str = ""):  # noqa: N803 —— 界面的查询参数名
    from src.web import app_work
    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner:
        return _error(403, "forbidden", "任务管理器只有所有者能看")
    try:
        after = app_work.since_time(since)
    except ValueError:
        return _error(400, "invalid", "since 要是 ISO 时间")
    bridge, staff = existing_bridge(root), app_staff.owner_proof(request)
    main = bridge.active_runs() if bridge else []
    approving = {task.id for task, _card in _staff_actions(root)} if staff else set()
    view = await asyncio.to_thread(app_work.task_manager, root, main, approving, after, staff=staff,
                                   done_today=doneToday == "true")
    return JSONResponse(view, headers={"Cache-Control": "no-store"})


@router.get(_ORG + "/task-manager/{run_id}/steps")
async def task_manager_steps(org_id: str, run_id: str, request: Request):
    from src.web import app_work

    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner or not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", "任务步骤只有已认证的所有者能看")
    if len(run_id) > 160:
        return _error(404, "not_found", "没有此回合的任务步骤记录")
    bridge = existing_bridge(root)
    running = bridge is not None and any(row["runId"] == run_id for row in bridge.active_runs())
    value = await asyncio.to_thread(app_work.task_steps, root, run_id, running=running)
    if value is None:
        return _error(404, "not_found", "没有此回合的任务步骤记录")
    return JSONResponse(value, headers={"Cache-Control": "no-store"})


@router.get(_ORG + "/waiting")
async def waiting_on_me(org_id: str, request: Request, channelId: str = ""):  # noqa: N803 —— 界面的查询参数名
    """等所有者处理的：审批页里那些，加上问他的问题。"""
    from src.web import app_work
    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner:
        return _error(403, "forbidden", "这些待办属于所有者")
    bridge, staff = existing_bridge(root), app_staff.owner_proof(request)
    rows = [views.approval(root, item) for item in bridge.pending_confirmations()] if bridge else []
    if staff:
        rows += [views.staff_action_approval(root, task, card) for task, card in _staff_actions(root)]
        rows += await asyncio.to_thread(_staff_proposal_approvals, root)
    view = await asyncio.to_thread(app_work.waiting, root, rows, staff=staff, channel_id=channelId)
    return JSONResponse(view, headers={"Cache-Control": "no-store"})


class _StopReason(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reason: str = Field(min_length=1, max_length=500)


@router.post(_ORG + "/runs/{run_id}/stop-reason", status_code=204)
async def stop_reason(org_id: str, run_id: str, body: _StopReason, request: Request):
    """停下一件事之后说一句为什么（只能一次）：这个人下次再找这个 agent 时，它在「之前的对话」里读得到。"""
    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner or not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", _NEEDS_OWNER)
    try:
        await asyncio.to_thread(app_staff.record_stop_reason, root, run_id, body.reason)
    except LookupError:
        return _error(404, "run.not_found", "没有这件事")
    except (ValueError, OSError) as error:
        return _error(409, "run.stop_reason_refused", str(error))
    return Response(status_code=204)


class _DeviceWait(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["retry", "stop"]


@router.post(_ORG + "/runs/{run_id}/device-wait", status_code=204)
async def device_wait(org_id: str, run_id: str, body: _DeviceWait, request: Request):
    """等某台设备（连接的电脑、手机）回来的事：这里没有，如实说明。"""
    if not member_auth.principal(request, _root(org_id)).is_owner or not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", _NEEDS_OWNER)
    return _error(404, "run.not_waiting", "这件事没有在等设备")


@router.post(_ORG + "/runs/{run_id}/cancel", status_code=204)
async def cancel_run(org_id: str, run_id: str, request: Request):
    """「停止」：主要聊天的回合走现有 Desktop 的停止路径；专员手上的事走主人的停止决定。"""
    root = _root(org_id)
    if run_id.startswith("task-"):
        if not app_staff.owner_proof(request):
            return _error(403, "owner_proof_required", _NEEDS_OWNER)
        try:
            from src.web import app_missions
            await asyncio.to_thread(app_missions.runs(root).request_step_stop, run_id)   # Mission 的一步：记下是人要停的
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
async def approvals(org_id: str, request: Request, response: Response, status: str = ""):
    root = _root(org_id)
    response.headers["Cache-Control"] = "no-store"
    if status not in {"", "pending"}:
        return {"approvals": []}
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    bridge = existing_bridge(root)
    if not who.is_owner:
        # 成员只在替所有者代批时看得到，而且只看能代批的那些
        value = app_covers.covering(root, who)
        if value is None or bridge is None:
            return {"approvals": []}
        return {"approvals": [app_covers.mark(views.approval(root, item), root, who.id)
                              for item in app_covers.visible(root, bridge.pending_confirmations(), value,
                                                             _needs_owner_file_proof)]}
    live = [views.approval(root, item) for item in bridge.pending_confirmations()] if bridge else []
    return {"approvals": live + [views.staff_action_approval(root, task, card) for task, card in _staff_actions(root)]
            + await asyncio.to_thread(_staff_proposal_approvals, root)}


def _staff_actions(root: str) -> List[Any]:
    """agent 在后台等批准的那一步：（任务, 审批卡）。执行池没连上或台账读不出来时不列。
    只列这个进程里真的还有人在等的卡（重启前的卡显示为中断，批了也没人接着做）。"""
    from src.scheduling.staff_runner import get_staff_service
    from src.workflow.tasks.tasks import TaskLedger
    try:
        actions = get_staff_service(root).actions
        rows = []
        for task in TaskLedger(root).list(limit=500):
            card = (task.collaboration or {}).get("approval") or {}
            if task.status != "blocked" or card.get("status") != "pending":
                continue
            card = actions.view(task.id)
            if card["status"] == "pending":
                rows.append((task, card))
        return rows
    except (ValueError, OSError):
        return []


def _staff_action(root: str, approval_id: str):
    """（任务, 审批卡, 是否还是界面上看到的那一版）；找不到返回 None。"""
    action_id, _, shown = approval_id.partition("~")
    for task, card in _staff_actions(root):
        if card["payload"]["id"] == action_id:
            return task, card, card["digest"][:16] == shown
    return None


async def _decide_staff_action(root: str, approval_id: str, approve: bool, request: Request, reason: str = ""):
    """主人在新界面批准或拒绝 agent 的一步：按界面上看到的那一版核对，变了就不批。
    决定要在事件循环线程里做：它会叫醒正在等这一步的 agent。"""
    from src.scheduling.staff_runner import get_staff_service
    from src.workflow.staff.owner_confirmation import owner_route
    if not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", "批准 agent 的操作需要已认证的所有者")
    found = _staff_action(root, approval_id)
    if found is None:
        return _error(409, "approval_decided", "这一步已经不在等待")
    task, card, current = found
    if not current:
        return _error(409, "approval_changed", "这一步的内容已经变了，请刷新后重新核对")
    p = card["payload"]
    try:
        with owner_route(root):
            get_staff_service(root).actions.decide(
                task.id, p["id"], approve=approve, expected_digest=card["digest"],
                # 拒绝要给 agent 一个理由；界面上没写就如实告诉它是主人拒的
                reason=str(reason or "").strip()[:500] or ("" if approve else "主人在审批里拒绝了这一步"),
                nonce=p["nonce"], round=p["round"], instance=p["instance"], runtime=p["runtime"])
    except (ValueError, OSError) as error:
        return _error(409, "approval_decided", str(error))
    return views.staff_action_approval(root, task, card, status="approved" if approve else "denied")


def _needs_owner_file_proof(confirmation_id: str) -> bool:
    from src.web.routers import realtime
    return realtime.requires_owner_confirmation(confirmation_id)


def _staff_proposal_approvals(root: str) -> List[Dict[str, Any]]:
    """待批的专员提议（助理提的、旧版 Desktop 里提的）。登记处读不出来时不列，审批页照常显示实时确认。"""
    try:
        return [views.staff_proposal_approval(root, item) for item in views.pending_staff_proposals(root)]
    except (ValueError, OSError):
        return []


def _staff_proposal(root: str, approval_id: str):
    """（提议, 是否还是界面上看到的那一版）；不是专员提议的 id 返回 None。"""
    if not approval_id.startswith(views.STAFF_PROPOSAL_PREFIX) or "~" not in approval_id:
        return None
    proposal_id, _, shown = approval_id.partition("~")
    item = next((row for row in views.pending_staff_proposals(root) if row["id"] == proposal_id), None)
    return (item, item is not None and item["digest"][:16] == shown)


async def _decide_staff_proposal(root: str, approval_id: str, approve: bool, request: Request):
    """主人在新界面批准或拒绝一条专员提议：按界面上看到的那一版（id 里的摘要前缀）核对，变了就不批。"""
    from src.workflow.staff.approvals import StaffApprovals
    from src.workflow.staff.owner_confirmation import owner_route
    if not member_auth.principal(request, root).is_owner or not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", "批准提议需要已认证的所有者")

    def run():
        found = _staff_proposal(root, approval_id)
        if found is None or found[0] is None:
            return None
        item, current = found
        if not current:
            raise ValueError("这条提议的内容已经变了，请刷新后重新核对")
        view = views.staff_proposal_approval(root, item)
        with owner_route(root):
            StaffApprovals(root).decide(item["id"], approve, item["digest"], nonce=item["nonce"],
                                        round=item["round"], instance=item["instance"])
        return {**view, "status": "approved" if approve else "refused", "canDecide": False,
                "decidedByUserId": views.owner_id(root), "decidedAt": views.now_iso()}
    try:
        decided = await asyncio.to_thread(run)
    except (ValueError, LookupError, OSError) as error:
        return _error(409, "approval_decided", str(error) or "这条提议已经不在等待")
    return decided if decided is not None else _error(409, "approval_decided", "这条提议已经不在等待")


@router.get(_ORG + "/approvals/{approval_id}")
async def approval(org_id: str, approval_id: str, request: Request, response: Response):
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    if not who.is_owner:
        value = app_covers.covering(root, who)
        bridge = existing_bridge(root)
        pending = bridge.pending(approval_id) if bridge and value else None
        if pending is None or not app_covers.coverable(
                pending, value, needs_owner_proof=_needs_owner_file_proof(approval_id)):
            return _error(404, "not_found", "这个确认已经不在等待")
        response.headers["Cache-Control"] = "no-store"
        return app_covers.mark(views.approval(root, pending), root, who.id)
    if approval_id.startswith(views.STAFF_PROPOSAL_PREFIX):
        found = await asyncio.to_thread(_staff_proposal, root, approval_id)
        if not found or not found[1]:
            return _error(404, "not_found", "这条提议已经不在等待，或内容已经变了")
        response.headers["Cache-Control"] = "no-store"
        return views.staff_proposal_approval(root, found[0])
    if approval_id.startswith(views.STAFF_ACTION_PREFIX):
        action = _staff_action(root, approval_id)
        if action is None or not action[2]:
            return _error(404, "not_found", "这一步已经不在等待，或内容已经变了")
        response.headers["Cache-Control"] = "no-store"
        return views.staff_action_approval(root, action[0], action[1])
    bridge = existing_bridge(root)
    pending = bridge.pending(approval_id) if bridge else None
    if pending is None:
        return _error(404, "not_found", "这个确认已经不在等待")
    response.headers["Cache-Control"] = "no-store"
    return views.approval(root, pending)


@router.post(_APPROVAL_BROWSER_PREVIEW)
async def approval_browser_preview(org_id: str, approval_id: str, request: Request):
    from src.gateway.audit import append_audit
    from src.web.routers.computers import has_owner_browser_session

    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    if request.query_params or not has_owner_browser_session(request):
        return Response(status_code=403, headers=headers)
    root = _root(org_id)
    bridge = existing_bridge(root)
    if bridge is None or (preview := bridge.website_frame(approval_id)) is None:
        return Response(status_code=404, headers=headers)
    def allowed():
        return (has_owner_browser_session(request) and existing_bridge(root) is bridge
                and bridge.website_frame(approval_id) is preview)

    image = await preview.pull(allowed)
    if not allowed():
        return Response(status_code=410, headers=headers)
    if image is None:
        return Response(status_code=409, headers=headers)
    if not await asyncio.to_thread(append_audit, root, {
        "category": "decision", "decision": "allowed", "decision_source": "user", "operation_kind": "read",
        "operation": "查看当前回合的浏览器画面", "actor_id": views.owner_id(root), "approval_id": approval_id,
    }):
        return Response(status_code=503, headers=headers)
    if not allowed():
        return Response(status_code=410, headers=headers)
    return Response(image, media_type="image/png", headers=headers)


class _PairedBrowser(BaseModel):
    model_config = ConfigDict(extra="forbid")
    machineId: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


@router.post(_ORG + "/approvals/{approval_id}/paired-browser")
async def paired_approval_browser(org_id: str, approval_id: str, body: _PairedBrowser, request: Request):
    from src.gateway.audit import append_audit
    from src.gateway.computer_connections import connections
    from src.gateway.connected_computers import ComputerUnavailable
    from src.gateway.paired_browser_task import INVITE_SECONDS
    from src.web.routers.computers import has_owner_browser_session

    if request.query_params or not has_owner_browser_session(request):
        return _error(403, "owner_proof_required", "需要从当前家的所有者会话操作")
    root = _root(org_id)
    bridge = existing_bridge(root)
    task = bridge.paired_browser_task(approval_id) if bridge else None
    if task is None or not task.current():
        return _error(404, "not_found", "这次网页核对已经结束")
    headers = {"Cache-Control": "no-store"}
    try:
        if body.machineId is None:
            preview = task.preview()
            if preview is not None and await preview.live():
                controls = {"canControl": True, "controlling": preview.controlling} if preview.allow_control else {}
                return JSONResponse({"sharing": True, "machineId": preview.connection.binding["connector_id"], **controls},
                                    headers=headers)
            return JSONResponse({"sharing": False}, headers=headers)
        connection = connections.online_connection(root, body.machineId)
        if (connection is None or not await connection.valid()
                or not await asyncio.to_thread(connection.store.check_pairer, connection.binding, None)):
            return _error(409, "computer_unavailable", "请选一台由你配对并在线的电脑")
    except (ComputerUnavailable, OSError):
        return _error(503, "computer_unavailable", "暂时无法核对电脑状态")
    if not await asyncio.to_thread(append_audit, root, {
        "category": "decision", "decision": "allowed", "decision_source": "user", "operation_kind": "read",
        "operation": "邀请本机打开任务浏览器", "actor_id": views.owner_id(root),
        "approval_id": approval_id, "connector_id": body.machineId,
    }):
        return _error(503, "audit_unavailable", "暂时无法登记任务浏览器")
    if not has_owner_browser_session(request) or (code := task.invite(connection)) is None:
        return _error(409, "browser_task_unavailable", "核对已结束、电脑连接已变化或窗口已打开")
    return JSONResponse({"code": code, "expiresIn": INVITE_SECONDS}, headers=headers)


@router.websocket(_APPROVAL_BROWSER_PREVIEW + "/watch")
async def watch_approval_browser(socket: WebSocket, org_id: str, approval_id: str):
    from src.gateway.audit import append_audit
    from src.gateway.computer_browser_preview import serve_viewer
    from src.gateway.computer_browser_wire import login_submission
    from src.web.routers.computers import has_owner_browser_session

    root = os.getcwd()
    try:
        if org_id != views.home_id(root) or socket.query_params or not has_owner_browser_session(socket):
            return
        bridge = existing_bridge(root)
        task = bridge.paired_browser_task(approval_id) if bridge else None
        if task is not None:
            preview = task.preview()
            if preview is None:
                return

            async def allowed_on_device() -> bool:
                return (has_owner_browser_session(socket) and await preview.live()
                        and existing_bridge(root) is bridge and bridge.paired_browser_task(approval_id) is task)

            if not await allowed_on_device() or not preview.begin_viewing():
                return
            audit_fields = {
                "category": "decision", "decision": "allowed", "decision_source": "user",
                "actor_id": views.owner_id(root), "approval_id": approval_id,
                "connector_id": preview.connection.binding["connector_id"], "surface_id": preview.surface_id,
            }

            async def control(message: dict) -> str | None:
                if message == {"type": "take_control"} and preview.allow_control and not preview.controlling:
                    tracked = await asyncio.to_thread(append_audit, root, {
                        **audit_fields, "operation": "接管配对电脑的任务浏览器", "operation_kind": "interact",
                    })
                    if tracked and await allowed_on_device() and preview.begin_control():
                        return "control_ready"
                if set(message) == {"type", "action"} and message["type"] == "input" and preview.controlling:
                    return "input_applied" if await preview.interact(message["action"]) else "paused"
                if message.get("type") == "submit_login" and preview.allow_login and preview.controlling:
                    login_submission(message)
                    tracked = await asyncio.to_thread(append_audit, root, {
                        **audit_fields, "operation": "提交配对电脑的站点登录", "operation_kind": "interact",
                    })
                    if tracked and await allowed_on_device() and await preview.submit_login(message):
                        return "login_submitted"
                    return "paused"
                return None

            try:
                if await asyncio.to_thread(append_audit, root, {
                    **audit_fields, "operation": "观看配对电脑的任务浏览器", "operation_kind": "read",
                }):
                    await serve_viewer(socket, preview.capture, allowed_on_device,
                                       on_command=control if preview.allow_control else None)
            finally:
                if preview.end_viewing():
                    await asyncio.to_thread(append_audit, root, {
                        **audit_fields, "operation": "结束配对电脑的浏览器接管", "operation_kind": "interact",
                    })
            return
        if bridge is None or (preview := bridge.website_frame(approval_id)) is None:
            return
        viewer = preview.begin_viewing()
        if viewer is None:
            return

        def authorized() -> bool:
            return (has_owner_browser_session(socket) and existing_bridge(root) is bridge
                    and bridge.website_frame(approval_id) is preview)

        async def allowed() -> bool:
            return authorized()

        async def capture() -> bytes | None:
            return await preview.pull(authorized, viewer=viewer)

        try:
            if await asyncio.to_thread(append_audit, root, {
                "category": "decision", "decision": "allowed", "decision_source": "user", "operation_kind": "read",
                "operation": "观看当前回合的浏览器画面", "actor_id": views.owner_id(root), "approval_id": approval_id,
            }):
                await serve_viewer(socket, capture, allowed, cancel_on_disconnect=True)
        finally:
            preview.end_viewing(viewer)
    except (WebSocketDisconnect, RuntimeError, OSError, KeyError, ValueError, asyncio.TimeoutError):
        pass
    finally:
        with suppress(WebSocketDisconnect, RuntimeError, OSError):
            await socket.close(code=1008)


@router.get(_ORG + "/me/approval-cover")
async def approval_cover(org_id: str, request: Request):
    """谁在替我批（所有者），或我在替谁批（成员）。"""
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    return await asyncio.to_thread(app_covers.covers_for, root, who)


@router.put(_ORG + "/me/approval-cover")
async def set_approval_cover(org_id: str, request: Request):
    """所有者托付一个成员替自己批；body.userId 为 null 表示不再托付。成员没有要别人代批的确认。"""
    from src.workflow.members.registry import MemberRegistry
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    if not who.is_owner:
        return _error(409, "cover.unsupported", "成员这边没有要别人代批的确认")
    if not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", _NEEDS_OWNER)
    body = await _body(request)

    def run():
        if body.get("userId") is None and set(body) <= {"userId", "mode", "risky", "endsAt"}:
            app_covers.save(root, None)
        else:
            members = MemberRegistry(root).snapshot()["members"]
            app_covers.save(root, app_covers.check(body, members))
        return app_covers.covers_for(root, who)
    try:
        return await asyncio.to_thread(run)
    except app_covers.CoverError as error:
        return _error(400, "invalid", str(error))


@router.delete(_ORG + "/approval-covers/{user_id}", status_code=204)
async def end_approval_cover(org_id: str, user_id: str, request: Request):
    """收回代批：所有者收回给某人的托付；被托付的成员自己不再代批（user_id 是所有者）。"""
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    if who.is_owner and not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", _NEEDS_OWNER)

    def run():
        value = app_covers.setting(root)
        if value is None:
            return False
        mine = value["userId"] == user_id if who.is_owner else (value["userId"] == who.id
                                                                and user_id == views.owner_id(root))
        if mine:
            app_covers.save(root, None)
        return mine
    return Response(status_code=204) if await asyncio.to_thread(run) else _error(404, "not_found", "没有这个代批")


async def _decide_covering(root: str, who, approval_id: str, approve: bool):
    """成员替所有者批：只在代批生效时、只批能代批的、只批这一次；每次都记进审计。"""
    from src.gateway.audit import append_audit
    value = app_covers.covering(root, who)
    if value is None:
        return _error(403, "forbidden", "你现在没有替所有者代批")
    if approval_id.startswith((views.STAFF_PROPOSAL_PREFIX, views.STAFF_ACTION_PREFIX)):
        return _error(403, "forbidden", "专员提议和 agent 的操作要所有者本人决定")
    bridge = existing_bridge(root)
    pending = bridge.pending(approval_id) if bridge else None
    if pending is None:
        return _error(409, "approval_decided", "这个确认已经不在等待")
    if not app_covers.coverable(pending, value, needs_owner_proof=_needs_owner_file_proof(approval_id)):
        return _error(403, "forbidden", "这一条要所有者本人决定")
    if not append_audit(root, {"category": "event", "event": "approval_covered", "session": "app",
                               "data": {"approval": approval_id, "member": who.id, "approved": approve,
                                        "operation_kind": str(pending.get("operation_kind") or "")}},
                        durable=True):
        return _error(409, "unavailable", "审计记录写不进去，没有代批")
    decided = await bridge.decide(approval_id, approve)
    if decided is None:
        return _error(409, "approval_decided", "这个确认已经不在等待")
    return app_covers.mark(decided, root, who.id)


async def _decide(org_id: str, approval_id: str, approve: bool, request: Request, reason: str = ""):
    from src.gateway.paired_browser_task import PairedBrowserNotReady
    from src.web.routers.computers import has_computer_owner_proof, has_owner_browser_session
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    if not who.is_owner:
        return await _decide_covering(root, who, approval_id, approve)
    if approval_id.startswith(views.STAFF_PROPOSAL_PREFIX):
        return await _decide_staff_proposal(root, approval_id, approve, request)
    if approval_id.startswith(views.STAFF_ACTION_PREFIX):
        return await _decide_staff_action(root, approval_id, approve, request, reason)
    bridge = existing_bridge(root)
    try:
        decided = await bridge.decide(
            approval_id, approve, file_owner=has_computer_owner_proof(request),
            browser_owner=has_owner_browser_session(request),
        ) if bridge else None
    except PairedBrowserNotReady as error:
        return _error(409, "browser_task_not_ready", str(error) or "请在配对电脑的专用窗口回到原站点，再确认继续")
    if decided is None:
        return _error(409, "approval_decided", "这个确认已经不在等待")
    return decided


class _Approve(BaseModel):
    reason: str | None = None
    always: bool = False
    via: str | None = None


@router.post(_ORG + "/approvals/{approval_id}/approve")
async def approve(org_id: str, approval_id: str, body: _Approve, request: Request):
    if body.always:
        # 「以后都允许」在这里没有对应的授权：不把一次批准悄悄扩大成常设放行。
        return _error(400, "always_unsupported", "这里只能批准这一次")
    return await _decide(org_id, approval_id, True, request)


@router.post(_ORG + "/approvals/{approval_id}/refuse")
async def refuse(org_id: str, approval_id: str, body: _Approve, request: Request):
    return await _decide(org_id, approval_id, False, request, body.reason or "")


class _DecideMany(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] = Field(min_length=1, max_length=50)
    decision: str = Field(pattern=r"^(approve|refuse)$")
    reason: str | None = None
    via: str | None = None
    only: str | None = None


@router.post(_ORG + "/approvals/decide")
async def decide_many(org_id: str, body: _DecideMany, request: Request):
    """卡片上的「全部批准 / 全部拒绝」：只处理界面列出的这几条，每条照常单独决定、只批这一次。
    「只批例行工作的」在这里没有对应的分类，就一条也不动，全部跳过。
    专员提议和 agent 的操作要一条条核对内容，不参加批量，也跳过。"""
    _root(org_id)
    decided, skipped = [], []
    for approval_id in dict.fromkeys(body.ids):
        if body.only is not None or approval_id.startswith((views.STAFF_PROPOSAL_PREFIX, views.STAFF_ACTION_PREFIX)):
            skipped.append(approval_id)
            continue
        answer = await _decide(org_id, approval_id, body.decision == "approve", request)
        if isinstance(answer, JSONResponse):
            skipped.append(approval_id)
        else:
            decided.append(answer)
    return {"approvals": decided, "skipped": skipped}


@router.get(_ORG + "/channels/{channel_id}/pins")
async def channel_pins(org_id: str, channel_id: str, request: Request):
    if is_person_dm(channel_id):
        return await _person_dm_call(request, org_id, lambda direct, actor:
                                     _person_dm_empty(direct, actor, channel_id, {"pins": []}))
    team = await _team_call(request, org_id, lambda service, actor: {"pins": service.pins(actor, channel_id)},
                            channel_id=channel_id)
    if team is not None:
        return team
    answer = await _as_member(request, org_id, channel_id, lambda *_found: {"pins": []})
    if answer is not None:
        return answer
    if _main_channel(org_id, channel_id) is None and _member_channel(org_id, channel_id) is None:
        return _error(404, "not_found", "没有这个对话")
    return {"pins": []}


@router.post(_ORG + "/channels/{channel_id}/pins/{message_id}")
@router.delete(_ORG + "/channels/{channel_id}/pins/{message_id}", status_code=204)
async def set_team_channel_pin(org_id: str, channel_id: str, message_id: str, request: Request):
    return _team_response(await _team_call(request, org_id, lambda service, actor:
        service.set_pin(actor, channel_id, message_id, request.method == "POST") or Response(status_code=204),
        channel_id=channel_id))


@router.get(_ORG + "/channels/{channel_id}/threads")
async def channel_threads(org_id: str, channel_id: str, request: Request, tab: str = "all", limit: int = 100):
    if is_person_dm(channel_id):
        return await _person_dm_call(request, org_id, lambda direct, actor:
                                     _person_dm_empty(direct, actor, channel_id, {"threads": [], "nextCursor": None}))
    team = await _team_call(request, org_id, lambda service, actor: {
        "threads": service.threads(actor, channel_id, tab=tab, limit=limit), "nextCursor": None},
        channel_id=channel_id)
    if team is not None:
        return team
    answer = await _as_member(request, org_id, channel_id, lambda *_found: {"threads": [], "nextCursor": None})
    if answer is not None:
        return answer
    if _main_channel(org_id, channel_id) is None and _member_channel(org_id, channel_id) is None:
        return _error(404, "not_found", "没有这个对话")
    return {"threads": [], "nextCursor": None}


@router.get(_ORG + "/unread-threads")
async def unread_team_threads(org_id: str, request: Request):
    return await _team_call(request, org_id, lambda service, actor: {"count": service.unread_thread_count(actor)},
                            empty_listing={"count": 0})


# ── 启动时读取、目前没有对应记录的列表：返回空的合法结构 ─────────────────────────

_EMPTY: Dict[str, Any] = {
    "/unread-tasks": {"tasks": []},
    "/machines/outages": {"outages": [], "targets": []},
    "/connectors": {"connections": []},
    "/skill-settings": {"agentsImprove": False},
    "/dashboards": {"home": None, "dashboards": [], "homeDashboardId": None},
}


# ── 提议：agent 提改团队背景，所有者采纳才生效（主人 2026-11-24 定）────────────────

async def _proposal_call(org_id: str, request: Request, action, *, decide: bool = False):
    from src.web import app_proposals
    from src.workflow.members.context_proposals import ProposalError
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", "加入这个家以后才能看提议")
    if decide and (not who.is_owner or not app_staff.owner_proof(request)):
        return _error(403, "forbidden", "团队背景的提议只有所有者能决定")
    try:
        result = await asyncio.to_thread(action, root, who, app_proposals)
    except ProposalError as error:
        return _error(error.status, error.code, str(error))
    except OSError:
        return _error(503, "unavailable", "提议记录暂时读不出来")
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get(_ORG + "/proposals")
async def list_proposals(org_id: str, request: Request, ids: str = Query(default="", max_length=4000)):
    wanted = [item for item in ids.split(",") if item] if ids else None
    return await _proposal_call(org_id, request, lambda root, who, views_: {
        "proposals": views_.listing(root, who, wanted)})


class _ProposalText(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: Optional[str] = Field(default=None, max_length=4000)


@router.post(_ORG + "/proposals/{proposal_id}/accept")
async def accept_proposal(org_id: str, proposal_id: str, request: Request, body: _ProposalText | None = None):
    from src.workflow.members.context_proposals import ContextProposals
    text = (body or _ProposalText()).text
    return await _proposal_call(org_id, request, lambda root, who, views_: views_.one(
        root, who, ContextProposals(root).accept(proposal_id, who.id, text)), decide=True)


@router.post(_ORG + "/proposals/{proposal_id}/refuse")
async def refuse_proposal(org_id: str, proposal_id: str, request: Request):
    from src.workflow.members.context_proposals import ContextProposals
    return await _proposal_call(org_id, request, lambda root, who, views_: views_.one(
        root, who, ContextProposals(root).refuse(proposal_id, who.id)), decide=True)


@router.post(_ORG + "/proposals/{proposal_id}/ask")
async def ask_about_proposal(org_id: str, proposal_id: str, request: Request):
    """发起的成员请所有者看看。所有者的提议列表本来就列着每一条待决定的，这里记下时间，界面据此显示「已请所有者看」。"""
    from src.workflow.members.context_proposals import ContextProposals, ProposalError

    def ask(root, who, views_):
        if who.is_owner:
            raise ProposalError("proposal.yours", "你就是决定的人，直接采纳或拒绝")
        return views_.one(root, who, ContextProposals(root).ask(proposal_id, who.id))
    return await _proposal_call(org_id, request, ask)


# ── 等别的能力的几组：如实回答（2026-11-24，主人「剩下的你也继续推进」）──────────────
# 没有的就说没有，回真实的空；做不了的 409 说清楚；不存在的记录 404。不收下用不了的东西、不存不生效的设置。

def _honest_gate(request: Request, org_id: str, *, members_too: bool = False, write: bool = False):
    """读：所有者或（members_too 时）在册成员；改：所有者，且带所有者证明。"""
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    if who.is_member and not members_too:
        return _error(403, "forbidden", "这里只有所有者能用")
    if who.is_owner and write and not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", _NEEDS_OWNER)
    return None


def _agent_gate(request: Request, org_id: str, agent_id: str, **kwargs):
    refused = _honest_gate(request, org_id, **kwargs)
    if refused is not None:
        return refused
    if not _known_agent(org_id, agent_id):
        return _error(404, "not_found", "没有这个 agent")
    return None


# 密钥申请：合同里密钥只能交到 agent 那台电脑上（环境变量、文件、浏览器登录框），这里的 agent 没有电脑，
# 也就从不发起密钥申请。没有申请可看、可交、可拒。
_NO_SECRET_REQUEST = "这里的 agent 还没有电脑可用，从不要密钥：没有这条申请"


@router.get(_ORG + "/secret-requests/{request_id}")
@router.post(_ORG + "/secret-requests/{request_id}/decline")
@router.post(_ORG + "/secret-requests/{request_id}/deliver")
@router.post(_ORG + "/secret-requests/{request_id}/vault")
async def secret_request(org_id: str, request_id: str, request: Request):
    refused = _honest_gate(request, org_id, members_too=True)
    return refused or _error(404, "secret.not_found", _NO_SECRET_REQUEST)


@router.get(_ORG + "/agents/{agent_id}/secrets")
async def agent_secrets(org_id: str, agent_id: str, request: Request):
    refused = _agent_gate(request, org_id, agent_id)
    return refused or _no_store({"secrets": []})            # 没有电脑，也就没有交到电脑上的密钥


@router.delete(_ORG + "/agents/{agent_id}/secrets/{name}")
async def remove_agent_secret(org_id: str, agent_id: str, name: str, request: Request):
    refused = _agent_gate(request, org_id, agent_id, write=True)
    return refused or _error(404, "secret.not_found", "这个 agent 的电脑上没有这项密钥")


# 保险库连接（1Password、Bitwarden 之类的外部密码库同步）：这里没有
@router.post(_ORG + "/vault/connections")
async def connect_vault(org_id: str, request: Request):
    refused = _honest_gate(request, org_id, members_too=True, write=True)
    return refused or _error(409, "vault.connections_unavailable", "这里还不能连外部的密码库：用「导入密码」或「导入 .env」")


@router.patch(_ORG + "/vault/connections/{connection_id}")
@router.delete(_ORG + "/vault/connections/{connection_id}")
@router.post(_ORG + "/vault/connections/{connection_id}/sync")
async def vault_connection(org_id: str, connection_id: str, request: Request):
    refused = _honest_gate(request, org_id, members_too=True, write=True)
    return refused or _error(404, "not_found", "没有这个密码库连接")


# 每个 agent 一个浏览器、它的电脑和桌面：要等连接电脑那条线
_NO_MACHINE = "这个 agent 没有接电脑，没有浏览器和桌面可用"


@router.put(_ORG + "/agents/{agent_id}/browser")
async def set_agent_browser_sites(org_id: str, agent_id: str, request: Request):
    from src.agents.tools.server_browser import server_browser_enabled
    refused = _agent_gate(request, org_id, agent_id, members_too=True, write=True)
    if refused is not None:
        return refused
    if await asyncio.to_thread(server_browser_enabled):
        return _error(409, "browser.sites_unavailable",
                      "还不能按 agent 限定网站：服务器上的浏览器开放除受限站点外的所有公开网站，点击和输入每步都要确认")
    return _error(409, "computer.unavailable", _NO_MACHINE)


@router.post(_ORG + "/agents/{agent_id}/browser/open")
@router.post(_ORG + "/agents/{agent_id}/browser/stop")
@router.post(_ORG + "/agents/{agent_id}/browser/restart")
@router.post(_ORG + "/agents/{agent_id}/browser-profile/request")
@router.put(_ORG + "/agents/{agent_id}/computer/target")
async def agent_machine_action(org_id: str, agent_id: str, request: Request):
    refused = _agent_gate(request, org_id, agent_id, members_too=True, write=True)
    return refused or _error(409, "computer.unavailable", _NO_MACHINE)


@router.put(_ORG + "/agents/{agent_id}/browser-profile")
async def set_agent_browser_profile(org_id: str, agent_id: str, request: Request):
    refused = _agent_gate(request, org_id, agent_id, write=True)
    return refused or _error(404, "not_found", "没有浏览器档案可选")


@router.get(_ORG + "/agents/{agent_id}/{screen}/control")
async def agent_screen_control(org_id: str, agent_id: str, screen: Literal["browser", "desktop"], request: Request):
    refused = _agent_gate(request, org_id, agent_id, members_too=True)
    return refused or _no_store({"canControl": False})


@router.get(_ORG + "/agents/{agent_id}/desktop/picture")
async def agent_desktop_picture(org_id: str, agent_id: str, request: Request):
    refused = _agent_gate(request, org_id, agent_id, members_too=True)
    return refused or _error(404, "desktop.no_machine", _NO_MACHINE)


# 浏览器档案、人工接手、AI 订阅账号：都在连接的电脑上，现在没有
@router.get(_ORG + "/browser-profiles")
async def browser_profiles(org_id: str, request: Request):
    refused = _honest_gate(request, org_id, members_too=True)
    return refused or _no_store({"profiles": []})


@router.post(_ORG + "/browser-profiles")
async def create_browser_profile(org_id: str, request: Request):
    refused = _honest_gate(request, org_id, members_too=True, write=True)
    return refused or _error(409, "computer.unavailable", "共享的浏览器档案要在连接的电脑上，现在还没有")


@router.patch(_ORG + "/browser-profiles/{profile_id}")
@router.delete(_ORG + "/browser-profiles/{profile_id}")
@router.post(_ORG + "/browser-profiles/{profile_id}/takeover")
@router.post(_ORG + "/browser-profiles/{profile_id}/handback")
@router.post(_ORG + "/browser-profiles/{profile_id}/open")
@router.get(_ORG + "/browser/handoffs/{handoff_id}")
@router.post(_ORG + "/browser/handoffs/{handoff_id}/cancel")
@router.post(_ORG + "/browser/handoffs/{handoff_id}/control")
@router.post(_ORG + "/browser/handoffs/{handoff_id}/done")
@router.post(_ORG + "/browser/handoffs/{handoff_id}/failed")
async def browser_record_missing(org_id: str, request: Request):
    refused = _honest_gate(request, org_id, members_too=True)
    # 服务器上的浏览器没有屏幕，没人能接手；连接的电脑上的浏览器还没有
    return refused or _error(404, "not_found", "没有这个浏览器档案或接手请求")


@router.get(_ORG + "/ai-accounts")
async def ai_accounts(org_id: str, request: Request):
    refused = _honest_gate(request, org_id, members_too=True)
    return refused or _no_store({"accounts": [], "computers": []})


@router.post(_ORG + "/ai-accounts/refresh", status_code=204)
async def refresh_ai_accounts(org_id: str, request: Request):
    refused = _honest_gate(request, org_id, members_too=True, write=True)
    return refused or Response(status_code=204)            # 没有订阅账号，没有要刷新的


@router.put(_ORG + "/ai-accounts/{account_id}/serves")
@router.post(_ORG + "/ai-accounts/{account_id}/take-back")
async def ai_account_missing(org_id: str, account_id: str, request: Request):
    refused = _honest_gate(request, org_id, members_too=True, write=True)
    return refused or _error(404, "not_found", "没有这个 AI 订阅账号：订阅要在连接的电脑上登录，现在还没有")


@router.get("/api/app/machines/device/{user_code}")
@router.post("/api/app/machines/device/{user_code}/deny")
async def machine_device_missing(user_code: str, request: Request):
    return _error(404, "not_found", "没有这个设备码：电脑用配对码配对")


# 数据库：服务器没装 Postgres 驱动（依赖里没有），连不了；列表如实是空的
_NO_DATABASES = "这台服务器还不能连数据库（没装 Postgres 驱动）"


@router.get(_ORG + "/databases")
async def databases(org_id: str, request: Request):
    refused = _honest_gate(request, org_id)
    return refused or _no_store({"connections": []})


@router.post(_ORG + "/databases")
async def connect_database(org_id: str, request: Request):
    refused = _honest_gate(request, org_id, write=True)
    return refused or _error(409, "databases.unavailable", _NO_DATABASES)


@router.patch(_ORG + "/databases/{connection_id}")
@router.delete(_ORG + "/databases/{connection_id}")
@router.get(_ORG + "/databases/{connection_id}/tables")
@router.get(_ORG + "/databases/{connection_id}/databases")
async def database_connection(org_id: str, connection_id: str, request: Request):
    refused = _honest_gate(request, org_id, write=request.method != "GET")
    return refused or _error(404, "not_found", "没有这个数据库连接")


@router.get(_ORG + "/agents/{agent_id}/databases")
async def agent_databases(org_id: str, agent_id: str, request: Request):
    refused = _agent_gate(request, org_id, agent_id)
    return refused or _no_store({"databases": []})


@router.put(_ORG + "/agents/{agent_id}/databases/{connection_id}")
async def set_agent_database(org_id: str, agent_id: str, connection_id: str, request: Request):
    refused = _agent_gate(request, org_id, agent_id, write=True)
    return refused or _error(404, "not_found", "没有这个数据库连接")


# GitHub App：这台服务器没有注册 GitHub App（状态接口已如实回「装不了」）
_NO_GITHUB = "这台服务器还没有 GitHub App，连不了 GitHub"


@router.post(_ORG + "/github-app/install")
@router.post(_ORG + "/github-app/complete")
@router.post(_ORG + "/agent-imports/github")
async def github_app_action(org_id: str, request: Request):
    refused = _honest_gate(request, org_id, write=True)
    return refused or _error(409, "github.unavailable", _NO_GITHUB)


@router.delete(_ORG + "/github-app/installations/{installation_id}")
async def remove_github_installation(org_id: str, installation_id: str, request: Request):
    refused = _honest_gate(request, org_id, write=True)
    return refused or _error(404, "not_found", "没有这个 GitHub 安装")


@router.get(_ORG + "/github/repositories")
async def github_repositories(org_id: str, request: Request):
    refused = _honest_gate(request, org_id, members_too=True)
    return refused or _no_store({"connected": False, "accounts": [], "truncated": False})


@router.get(_ORG + "/github/access")
async def github_access(org_id: str, request: Request):
    refused = _honest_gate(request, org_id)
    return refused or _no_store({"orgs": None, "manageUrl": None})


def _server_owner(request: Request):
    """服务器级的 GitHub App 设置：只有所有者（带所有者证明）。"""
    root = os.getcwd()
    if not member_auth.principal(request, root).is_owner:
        return _error(403, "forbidden", "这里只有所有者能用")
    if not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", _NEEDS_OWNER)
    return None


@router.post("/api/app/github-app/manifest")
async def github_app_manifest(request: Request):
    return _server_owner(request) or _error(409, "github.unavailable", "这台服务器还不能注册 GitHub App")


@router.delete("/api/app/github-app/settings")
async def remove_github_app_settings(request: Request):
    # 本来就没有 App：如实回「关着」的设置
    return _server_owner(request) or _no_store({"source": None, "slug": None, "htmlUrl": None, "state": "off",
                                                "canCreate": False})


# GitHub / Sentry 的服务级 webhook（事件触发的例行工作）：这里没有事件触发
def _webhook_off(provider: str) -> dict:
    return {"provider": provider, "id": None, "url": None, "secret": None, "receivedAt": None, "refusedAt": None,
            "secretSet": False, "canManage": False, "oneClick": False}


@router.get(_ORG + "/webhooks/{provider}")
async def webhook_setup(org_id: str, provider: Literal["github", "sentry"], request: Request):
    refused = _honest_gate(request, org_id, members_too=True)
    return refused or _no_store(_webhook_off(provider))


@router.put(_ORG + "/webhooks/{provider}")
@router.put(_ORG + "/webhooks/{provider}/secret")
async def ensure_webhook(org_id: str, provider: Literal["github", "sentry"], request: Request):
    refused = _honest_gate(request, org_id, write=True)
    return refused or _error(409, "webhooks.unavailable",
                             "这里还没有按 GitHub / Sentry 事件触发的例行工作：用例行工作自己的 webhook 地址")


@router.post(_ORG + "/webhooks/github/repository")
async def add_github_webhook(org_id: str, request: Request):
    refused = _honest_gate(request, org_id, write=True)
    return refused or _error(409, "github.unavailable", _NO_GITHUB)


# 网页推送（Web Push）这里关着：登记浏览器订阅如实拒绝；取消登记只管自己的，没有就什么都不做
@router.post("/api/app/push/devices")
async def register_push_device(request: Request):
    return _error(404, "web_push_off", "这台服务器没有开网页推送：手机通知用 iPhone App")


class _PushForget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    token: str = Field(min_length=1, max_length=4000)


@router.post("/api/app/push/devices/forget", status_code=204)
async def forget_push_device(body: _PushForget, request: Request):
    return Response(status_code=204)                         # 网页推送没登记过任何订阅：没有要停的


class _PushStale(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ids: List[str] = Field(max_length=100)


@router.post("/api/app/push/stale")
async def stale_notices(body: _PushStale, request: Request):
    """这些通知里已经读过、完成、推迟或没有了的：浏览器据此关掉还挂着的通知。"""
    root = os.getcwd()
    owner = member_auth.principal(request, root).is_owner
    try:
        stale = await asyncio.to_thread(app_notice_inbox.stale, root, body.ids, owner=owner)
    except (OSError, ValueError):
        return _error(503, "unavailable", "通知记录暂时读不出来")
    return _no_store({"stale": stale})


def _register_empty(path: str, payload: Any) -> None:
    async def read(org_id: str):
        _root(org_id)
        return json.loads(json.dumps(payload))

    read.__name__ = "empty_" + path.strip("/").replace("/", "_").replace("-", "_")
    router.add_api_route(_ORG + path, read, methods=["GET"])


for _path, _payload in _EMPTY.items():
    _register_empty(_path, _payload)


# ── AI 密钥：所有者自己加的模型端点（见 app_credentials）──────────────────────

async def _credential_call(request: Request, org_id: str, action, *, sync: bool = False, read: bool = False):
    """只有所有者能看（只回末 4 位和地址）；改要带所有者证明。拒绝如实说明，密钥从不回给界面。"""
    from src.web import app_credentials
    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner:
        return _error(403, "forbidden", "AI 密钥只有所有者能看和改")
    if not read and not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", "改 AI 密钥需要已认证的所有者")
    try:
        result = await asyncio.to_thread(action, root) if sync else await action(root)
    except app_credentials.CredentialError as error:
        return _error(error.status, error.code, str(error))
    return JSONResponse(result, headers={"Cache-Control": "no-store"}) if result is not None \
        else Response(status_code=204)


@router.get(_ORG + "/credentials")
async def credentials(org_id: str, request: Request):
    from src.web import app_credentials
    return await _credential_call(request, org_id, lambda root: {"credentials": app_credentials.listing(root)},
                                  sync=True, read=True)


@router.post(_ORG + "/credentials")
async def add_credential(org_id: str, request: Request):
    from src.web import app_credentials
    body = await _body(request)
    return await _credential_call(request, org_id, lambda root: app_credentials.add(root, body))


@router.post(_ORG + "/credentials/endpoint-models")
async def credential_endpoint_models(org_id: str, request: Request):
    from src.web import app_credentials
    body = await _body(request)
    return await _credential_call(request, org_id, lambda _root: app_credentials.endpoint_models(body or {}))


@router.get(_ORG + "/credentials/decisions-backup")
async def get_decisions_backup(org_id: str, request: Request):
    from src.web import app_credentials
    return await _credential_call(request, org_id, lambda root: {
        "configured": app_credentials.decisions_backup_key(root) is not None,
    }, sync=True)


@router.put(_ORG + "/credentials/decisions-backup")
async def set_decisions_backup(org_id: str, request: Request):
    from src.web import app_credentials
    body = await _body(request)
    return await _credential_call(request, org_id, lambda root: app_credentials.set_decisions_backup(root, body),
                                  sync=True)


@router.put(_ORG + "/credentials/{credential_id}/models")
async def credential_models(org_id: str, credential_id: str, request: Request):
    from src.web import app_credentials
    body = await _body(request)
    return await _credential_call(request, org_id, lambda root: app_credentials.set_models(
        root, credential_id, (body or {}).get("models")))


@router.post(_ORG + "/credentials/{credential_id}/rotate")
async def rotate_credential(org_id: str, credential_id: str, request: Request):
    from src.web import app_credentials
    body = await _body(request)
    return await _credential_call(request, org_id, lambda root: app_credentials.rotate(root, credential_id, body or {}))


@router.post(_ORG + "/credentials/{credential_id}/check")
async def check_credential(org_id: str, credential_id: str, request: Request):
    from src.web import app_credentials
    return await _credential_call(request, org_id, lambda root: app_credentials.check(root, credential_id))


@router.post(_ORG + "/credentials/{credential_id}/make-default")
async def default_credential(org_id: str, credential_id: str, request: Request):
    from src.web import app_credentials
    return await _credential_call(request, org_id, lambda root: app_credentials.make_default(root, credential_id),
                                  sync=True)


@router.post(_ORG + "/credentials/{credential_id}/remove-copies")
async def credential_copies(org_id: str, credential_id: str, request: Request):
    from src.web import app_credentials
    return await _credential_call(request, org_id, lambda root: app_credentials.remove_copies(root, credential_id),
                                  sync=True)


@router.delete(_ORG + "/credentials/{credential_id}", status_code=204)
async def remove_credential(org_id: str, credential_id: str, request: Request, purge: str = "", release: str = ""):
    from src.web import app_credentials

    def run(root):
        app_credentials.remove(root, credential_id, purge=purge == "true", release=release == "true")
        return None
    return await _credential_call(request, org_id, run, sync=True)


# ── 连接器：所有者接的自定义 MCP 服务（见 workflow/workbench/custom_connectors）──

async def _connector_call(request: Request, org_id: str, action, *, read: bool = False):
    """只有所有者：地址和工具能看，改要带所有者证明。"""
    from src.workflow.workbench import custom_connectors
    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner:
        return _error(403, "forbidden", "连接器只有所有者能看和改")
    if not read and not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", "改连接器需要已认证的所有者")
    try:
        result = await action(root)
    except custom_connectors.ConnectorError as error:
        return _error(error.status, error.code, str(error))
    return JSONResponse(result, headers={"Cache-Control": "no-store"}) if result is not None \
        else Response(status_code=204)


@router.get(_ORG + "/connectors/custom")
async def custom_connector_list(org_id: str, request: Request):
    from src.web import app_connectors
    from src.workflow.workbench import custom_connectors

    async def run(root):
        return await asyncio.to_thread(
            lambda: {"servers": [app_connectors.server(item) for item in custom_connectors.servers(root)]})
    return await _connector_call(request, org_id, run, read=True)


@router.post(_ORG + "/connectors/custom")
async def custom_connector_connect(org_id: str, request: Request):
    from src.web import app_connectors
    from src.workflow.workbench import custom_connectors
    body = await _body(request)

    async def run(root):
        result = await custom_connectors.connect(root, body)
        if "server" in result:
            result = {**result, "server": app_connectors.server(result["server"])}
        return result
    return await _connector_call(request, org_id, run)


@router.post(_ORG + "/connectors/custom/{server_id}/refresh")
async def custom_connector_refresh(org_id: str, server_id: str, request: Request):
    from src.web import app_connectors
    from src.workflow.workbench import custom_connectors

    async def run(root):
        return app_connectors.server(await custom_connectors.refresh(root, server_id))
    return await _connector_call(request, org_id, run)


@router.put(_ORG + "/connectors/custom/{server_id}/tools")
async def custom_connector_tools(org_id: str, server_id: str, request: Request):
    from src.web import app_connectors
    from src.workflow.workbench import custom_connectors
    body = await _body(request)

    async def run(root):
        return app_connectors.server(await asyncio.to_thread(custom_connectors.save_tools, root, server_id, body))
    return await _connector_call(request, org_id, run)


@router.delete(_ORG + "/connectors/custom/{server_id}", status_code=204)
@router.delete(_ORG + "/connectors/{server_id}", status_code=204)
async def connector_disconnect(org_id: str, server_id: str, request: Request):
    from src.workflow.workbench import custom_connectors

    async def run(root):
        await asyncio.to_thread(custom_connectors.disconnect, root, server_id)
    return await _connector_call(request, org_id, run)


@router.patch(_ORG + "/connectors/{server_id}")
async def connector_rename(org_id: str, server_id: str, request: Request):
    from src.web import app_connectors
    from src.workflow.workbench import custom_connectors
    body = await _body(request)

    async def run(root):
        label = body.get("label") if isinstance(body, dict) else None
        return app_connectors.connection(await asyncio.to_thread(custom_connectors.rename, root, server_id, label))
    return await _connector_call(request, org_id, run)


async def _connector_unsupported(request: Request, org_id: str):
    from src.web import app_connectors
    from src.workflow.workbench import custom_connectors

    async def run(_root_path):
        raise custom_connectors.ConnectorError("connector.unsupported", app_connectors.UNSUPPORTED, 409)
    return await _connector_call(request, org_id, run)


@router.post(_ORG + "/connectors")
async def connector_with_key(org_id: str, request: Request):
    """官方目录是空的：不存一份用不上的连接。"""
    return await _connector_unsupported(request, org_id)


@router.post(_ORG + "/connectors/oauth/complete")
async def connector_oauth_complete(org_id: str, request: Request):
    return await _connector_unsupported(request, org_id)


@router.post(_ORG + "/connectors/{connector}/oauth/start")
async def connector_oauth_start(org_id: str, connector: str, request: Request):
    return await _connector_unsupported(request, org_id)


@router.get(_ORG + "/github-app")
async def github_app_info(org_id: str, request: Request):
    """Report the unsupported installation state so Desktop can show its off status."""
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    if who.is_owner and not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", "读取服务器的 GitHub App 状态需要已认证的所有者")
    setup = ({"source": None, "slug": None, "htmlUrl": None, "state": "off", "canCreate": False}
             if who.is_owner else None)
    return JSONResponse({"installable": False, "installations": [], "setup": setup, "ownerCreates": False},
                        headers={"Cache-Control": "no-store"})


# ── 个人 Vault：无原值读取，不参与 Agent 工具或共享 ────────────────────────────

_VAULT = _ORG + "/vault"
_VAULT_ITEMS = _VAULT + "/items"
_VAULT_ITEM = _VAULT_ITEMS + "/{item_id}"
_VAULT_HEADERS = {"Cache-Control": "no-store"}


async def _vault_body(request: Request, limit: int) -> dict:
    if request.headers.get("content-type", "").partition(";")[0].strip().lower() != "application/json":
        raise app_personal_vault.VaultError(415, "vault.invalid", "个人 Vault 只接受 JSON 请求")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > limit:
            raise app_personal_vault.too_large()
        raw.extend(chunk)
    try:
        body = json.loads(raw)
    except (ValueError, RecursionError):
        body = None
    if not isinstance(body, dict):
        raise app_personal_vault.VaultError(422, "vault.invalid", "个人 Vault 请求格式无效")
    return body


async def _vault_call(request: Request, org_id: str, action, *, body_limit: int = 0):
    root = _root(org_id)
    loopback = {"127.0.0.1", "::1"}
    local = (request.url.scheme == "http" and request.url.hostname in loopback and request.client
             and request.client.host in loopback and not any(header in request.headers for header in (
                 "forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto",
             )))
    if request.url.scheme != "https" and not local:
        return _error(403, "vault.secure_transport_required", "个人 Vault 需要 HTTPS 或本机回环连接")
    who = _who(request, root)
    if who is None or (who.is_owner and not app_staff.owner_proof(request)):
        return _error(403, "forbidden", "个人 Vault 需要已验证的空间身份")
    account = None if who.is_owner else request.state.member_account["id"]

    def call(body):
        if _who(request, root) != who:
            raise app_personal_vault.VaultError(403, "forbidden", "当前身份不能使用个人 Vault")
        return action(app_personal_vault.PersonalVault(root, who.id, member_account=account), body)

    try:
        body = await _vault_body(request, body_limit) if body_limit else None
        result = await asyncio.to_thread(call, body)
        if result is None:
            return Response(status_code=204, headers=_VAULT_HEADERS)
        return JSONResponse(result, headers=_VAULT_HEADERS)
    except app_personal_vault.VaultError as error:
        return _error(error.status, error.code, str(error))


@router.get(_VAULT)
async def vault_overview(org_id: str, request: Request):
    return await _vault_call(request, org_id, lambda vault, _: vault.overview())


@router.get(_VAULT_ITEM)
async def vault_item(org_id: str, item_id: str, request: Request):
    return await _vault_call(request, org_id, lambda vault, _: vault.item(item_id))


@router.post(_VAULT + "/import")
async def vault_import_csv(org_id: str, request: Request):
    return await _vault_call(request, org_id, lambda vault, body: vault.import_csv(body),
                             body_limit=app_personal_vault.MAX_CSV_BODY)


@router.post(_VAULT + "/keys")
async def vault_import_keys(org_id: str, request: Request):
    return await _vault_call(request, org_id, lambda vault, body: vault.import_keys(body),
                             body_limit=app_personal_vault.MAX_KEYS_BODY)


@router.post(_VAULT_ITEMS)
async def vault_create(org_id: str, request: Request):
    return await _vault_call(request, org_id, lambda vault, body: vault.create(body),
                             body_limit=app_personal_vault.MAX_BODY)


@router.patch(_VAULT_ITEM)
async def vault_update(org_id: str, item_id: str, request: Request):
    return await _vault_call(request, org_id, lambda vault, body: vault.update(item_id, body),
                             body_limit=app_personal_vault.MAX_BODY)


@router.delete(_VAULT_ITEM, status_code=204)
async def vault_remove(org_id: str, item_id: str, request: Request):
    return await _vault_call(request, org_id, lambda vault, _: vault.remove(item_id))


@router.get(_ORG + "/mail/providers")
async def mail_providers(org_id: str, request: Request):
    from src.workflow.members.mailboxes import PRESETS
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    # 团队邮箱只有所有者能接（主人 2026-11-24 定）；Gmail / Outlook（Composio）这里没有
    return {"composio": False, "imap": who.is_owner, "presets": PRESETS if who.is_owner else []}


async def _mail_call(org_id: str, request: Request, action, *, status: int = 200, read: bool = False):
    """团队邮箱：只给所有者；改要带所有者证明（读和 AI 密钥一样不要求，列表里没有授权码）。
    连接失败回 422，code 是界面认的 MailConnectError。"""
    from src.plugins.mail import MailError
    from src.workflow.members.mailboxes import MailboxError
    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner:
        return _error(403, "forbidden", "团队邮箱只有所有者能管")
    if not read and not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", "团队邮箱只有已认证的所有者能改")
    try:
        result = await asyncio.to_thread(action, root)
    except MailError as error:
        return _error(422, error.code, str(error))
    except MailboxError as error:
        return _error(error.status, error.code, str(error))
    except OSError:
        return _error(503, "unavailable", "邮箱登记暂时读不出来")
    if result is None:
        return Response(status_code=204)
    return JSONResponse(result, status_code=status, headers={"Cache-Control": "no-store"})


@router.get(_ORG + "/mail/accounts")
async def mail_accounts(org_id: str, request: Request):
    from src.web import app_mail
    return await _mail_call(org_id, request, lambda root: {"accounts": app_mail.accounts(root)}, read=True)


@router.post(_ORG + "/mail/accounts/imap")
async def connect_mail_imap(org_id: str, body: Dict[str, Any], request: Request):
    from src.web import app_mail
    return await _mail_call(org_id, request, lambda root: {"account": app_mail.connect(root, body)})


@router.post(_ORG + "/mail/accounts/composio")
async def connect_mail_composio(org_id: str, request: Request):
    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner or not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", "团队邮箱只有已认证的所有者能管")
    return _error(409, "mail.composio_unavailable", "这里没有 Gmail / Outlook 的一键连接：用 IMAP 和客户端专用密码接")


@router.patch(_ORG + "/mail/accounts/{account_id}")
async def update_mail_account(org_id: str, account_id: str, body: Dict[str, Any], request: Request):
    from src.web import app_mail
    return await _mail_call(org_id, request, lambda root: {"account": app_mail.update(root, account_id, body)})


@router.delete(_ORG + "/mail/accounts/{account_id}", status_code=204)
async def disconnect_mail_account(org_id: str, account_id: str, request: Request):
    from src.workflow.members.mailboxes import Mailboxes
    return await _mail_call(org_id, request, lambda root: Mailboxes(root).remove(account_id))


@router.get(_ORG + "/mail/accounts/{account_id}/folders")
async def mail_folders(org_id: str, account_id: str, request: Request):
    from src.web import app_mail
    return await _mail_call(org_id, request, lambda root: {"folders": app_mail.folders(root, account_id)},
                            read=True)


@router.get(_ORG + "/mail/accounts/{account_id}/suggested-addresses")
async def mail_suggested_addresses(org_id: str, account_id: str, request: Request):
    from src.web import app_mail
    return await _mail_call(org_id, request, lambda root: {
        "addresses": app_mail.suggested_addresses(root, account_id)}, read=True)


def _mailbox_staff(root: str, agent_id: str) -> str:
    from src.workflow.members.mailboxes import MailboxError
    staff_id = _staff_of(root, agent_id)
    if staff_id is None:
        raise MailboxError(404, "not_found", "没有这个 agent（主助理用的是自己的邮件插件）")
    return staff_id


@router.get(_ORG + "/agents/{agent_id}/mail")
async def agent_mail(org_id: str, agent_id: str, request: Request):
    from src.web import app_mail
    return await _mail_call(org_id, request, lambda root: {
        "mailboxes": app_mail.agent_mailboxes(root, _mailbox_staff(root, agent_id))}, read=True)


@router.put(_ORG + "/agents/{agent_id}/mail/{account_id}")
async def set_agent_mail(org_id: str, agent_id: str, account_id: str, body: Dict[str, Any], request: Request):
    from src.web import app_mail
    return await _mail_call(org_id, request, lambda root: app_mail.set_agent(
        root, _mailbox_staff(root, agent_id), account_id, body))


# ── 私人日历 ──────────────────────────────────────────────────────────────

_CALENDAR = _ORG + "/calendar"


async def _calendar_call(org_id, request, action, *, listing=False, create=False):
    root = _root(org_id)
    actor = _who(request, root)
    if actor is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    if actor.is_owner and not app_staff.owner_proof(request):
        if listing and not views.owner_connection():
            return app_calendar.calendar_range()
        return _error(403, "owner_proof_required", "私人日历需要已认证的同源会话")

    def execute():
        if actor.is_member:
            permissions = app_members.permission_keys(root, actor)
            if not permissions:
                return _error(403, "forbidden", _NOT_MEMBER)
            if create and "post" not in permissions:
                return _error(403, "forbidden", "你没有创建日程的权限")
        return action(PrivateCalendar(root, actor), actor)

    try:
        return await asyncio.to_thread(execute)
    except CalendarError as error:
        return _error(error.status, error.code, str(error))
    except (OSError, sqlite3.Error):
        return _error(409, "calendar.unavailable", "无法读取或保存日历记录")
    except OverflowError:
        return _error(400, "calendar.invalid", "日程日期超出范围")
    except ValueError as error:
        return _error(400, "calendar.invalid", str(error))


@router.get(_CALENDAR)
async def calendar_list(org_id: str, request: Request, start: Optional[str] = Query(None, alias="from"),
                        to: Optional[str] = None):
    return await _calendar_call(org_id, request, lambda calendar, actor: app_calendar.listing(
        calendar, actor, *date_range(start, to), app_calendar.workspace_timezone(calendar.root)), listing=True)


@router.post(_CALENDAR + "/events", status_code=201)
async def calendar_create(org_id: str, request: Request, body: Dict[str, Any]):
    return await _calendar_call(org_id, request, lambda calendar, _actor: calendar.create(
        body, app_calendar.workspace_timezone(calendar.root)), create=True)


@router.patch(_CALENDAR + "/events/{event_id}")
async def calendar_update(org_id: str, event_id: str, request: Request, body: Dict[str, Any]):
    return await _calendar_call(org_id, request, lambda calendar, _actor: calendar.update(event_id, body))


@router.delete(_CALENDAR + "/events/{event_id}")
async def calendar_delete(org_id: str, event_id: str, request: Request, scope: str | None = None,
                          occurrence_start: str | None = Query(None, alias="occurrenceStart")):
    result = await _calendar_call(org_id, request, lambda calendar, _actor: calendar.delete(
        event_id, scope=scope, occurrence_start=occurrence_start))
    return result if isinstance(result, Response) else Response(status_code=204)


# ── Wiki ───────────────────────────────────────────────────────────────────

_WIKI = _ORG + "/wiki"
# The client's 8 Mi UTF-16-code-unit batch budget can take three times as many UTF-8 bytes.
_MAX_WIKI_IMPORT_BYTES = 24 * 1024 * 1024 + 64 * 1024


class _WikiMessageSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["message"]
    channelId: str = Field(min_length=1, max_length=128)
    messageId: str = Field(min_length=1, max_length=128)


class _WikiAppend(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=MAX_BODY)
    source: _WikiMessageSource


def _wiki_for(root: str, actor):
    """这个人眼里的 Wiki（能不能写、能不能管、看得到哪些公开频道的来源）。"""
    state = MemberRegistry(root).snapshot()
    if actor.is_member and actor.id not in state["members"]:
        raise WikiError(403, "forbidden", _NOT_MEMBER)
    keys = app_members.permission_keys(root, actor)
    owner_id = views.owner_id(root)
    people = frozenset({owner_id, *state["members"]})
    return Wiki(root, WikiReader(actor, owner_id, people, "edit_wiki" in keys, "configure" in keys,
                                 TeamChannels.readable_public_names(state, actor)))


async def _wiki_call(request, org_id, action, *, listing=False, source=None, read_body=None):
    from src.workflow.members.registry import Damaged, Missing, Refused

    root = _root(org_id)
    actor = _who(request, root)
    if actor is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    if actor.is_owner and not app_staff.owner_proof(request):
        if listing and not views.owner_connection():
            return {"pages": []}
        return _error(403, "owner_proof_required", "Wiki 需要已认证的同源会话")

    def wiki_for_reader():
        return _wiki_for(root, actor)

    def execute():
        if source is None:
            wiki = wiki_for_reader()
            return action(wiki, raw) if read_body is not None else action(wiki)
        if not is_team_channel(source.channelId):
            return _error(409, "wiki_source_unsupported", "目前只能从加入的公开团队频道摘录消息")
        with app_channels.channels(root).public_message(actor, source.channelId, source.messageId) as message:
            return action(wiki_for_reader(), message)

    try:
        raw = await read_body(request) if read_body is not None else None
        return await asyncio.to_thread(execute)
    except WikiError as error:
        return _error(error.status, error.code, str(error))
    except Missing:
        return _error(404, "wiki_source_not_found", "没有这个来源消息")
    except Refused as error:
        return _error(409, "wiki_source_unsupported", str(error))
    except (Damaged, OSError, sqlite3.Error, ValueError):
        return _error(409, "unavailable", "无法读取或保存 Wiki 记录")


@router.get(_WIKI + "/pages")
async def wiki_pages(org_id: str, request: Request, deleted: bool = False):
    return await _wiki_call(request, org_id, lambda wiki: wiki.listing(deleted=deleted), listing=True)


@router.get(_WIKI + "/graph")
async def wiki_graph(org_id: str, request: Request):
    return await _wiki_call(request, org_id, lambda wiki: wiki.graph())


@router.get(_WIKI + "/reads")
async def wiki_reads(org_id: str, request: Request):
    # There is no agent Wiki reader, so no agent usage can contribute to this ranking.
    return await _wiki_call(request, org_id, lambda _wiki: [])


@router.get(_WIKI + "/search")
async def search_wiki(org_id: str, request: Request, q: str = ""):
    return await _wiki_call(request, org_id, lambda wiki: wiki.search(q))


@router.get(_WIKI + "/folder-audience")
async def wiki_folder_audience(org_id: str, request: Request, folder: str = ""):
    return await _wiki_call(request, org_id, lambda wiki: wiki.folder_audience(folder))


@router.put(_WIKI + "/folder-audience")
async def narrow_wiki_folder_audience(org_id: str, body: Dict[str, Any], request: Request):
    return await _wiki_call(request, org_id, lambda wiki: wiki.narrow_folder_audience(body))


async def _wiki_import_body(request: Request) -> bytearray:
    if request.headers.get("content-type", "").partition(";")[0].strip().lower() != "application/json":
        raise WikiError(415, "invalid", "Markdown 导入只接受 JSON 请求")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > _MAX_WIKI_IMPORT_BYTES:
            raise WikiError(413, "wiki_import_too_large", "Markdown 导入批次太大")
        raw.extend(chunk)
    return raw


@router.post(_WIKI + "/import")
async def import_wiki_notes(org_id: str, request: Request):
    def import_notes(wiki, raw):
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError):
            data = None
        if not isinstance(data, dict):
            raise WikiError(422, "invalid", "Markdown 导入请求格式无效")
        return wiki.import_notes(data)

    return await _wiki_call(request, org_id, import_notes, read_body=_wiki_import_body)


@router.get(_WIKI + "/import/github")
async def preview_wiki_github(org_id: str, request: Request, url: str = Query(..., max_length=2048)):
    from src.web import app_wiki_github

    def plan(wiki):
        if not wiki.reader.actor.is_owner:
            raise WikiError(403, "forbidden", "GitHub 导入需要通过认证的工作区所有者")
        return app_wiki_github.parse_link(url)

    target = await _wiki_call(request, org_id, plan)
    if isinstance(target, Response):
        return target
    try:
        data = await app_wiki_github.fetch_notes(target)
    except WikiError as error:
        return _error(error.status, error.code, str(error))
    return Response(data, media_type="application/zip",
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.post(_WIKI + "/pages", status_code=201)
async def create_wiki_page(org_id: str, body: Dict[str, Any], request: Request):
    return await _wiki_call(request, org_id, lambda wiki: wiki.create(body))


@router.get(_WIKI + "/pages/{slug}")
async def wiki_page(org_id: str, slug: str, request: Request):
    return await _wiki_call(request, org_id, lambda wiki: wiki.get(slug))


@router.put(_WIKI + "/pages/{slug}")
async def update_wiki_page(org_id: str, slug: str, body: Dict[str, Any], request: Request):
    return await _wiki_call(request, org_id, lambda wiki: wiki.update(slug, body))


@router.post(_WIKI + "/pages/{slug}/append")
async def append_wiki_page(org_id: str, slug: str, body: _WikiAppend, request: Request):
    return await _wiki_call(request, org_id, lambda wiki, message: wiki.append(slug, body.text, message),
                            source=body.source)


@router.delete(_WIKI + "/pages/{slug}", status_code=204)
async def remove_wiki_page(org_id: str, slug: str, request: Request):
    return await _wiki_call(request, org_id, lambda wiki:
                            _team_receipt(lambda: wiki.remove(slug)))


@router.post(_WIKI + "/pages/{slug}/rename")
@router.post(_WIKI + "/pages/{slug}/move")
@router.post(_WIKI + "/pages/{slug}/pin")
@router.post(_WIKI + "/pages/{slug}/lock")
@router.post(_WIKI + "/pages/{slug}/context")
async def change_wiki_page(org_id: str, slug: str, body: Dict[str, Any], request: Request):
    action = request.url.path.rsplit("/", 1)[1]
    attribute = {"rename": "title", "move": "folder", "pin": "pinned", "lock": "locked", "context": "on"}[action]
    if set(body) != {attribute}:
        return _error(400, "invalid", f"必须提供 {attribute}")
    value = body[attribute]
    if action == "context":
        if type(value) is not bool:
            return _error(400, "invalid", "必须指定是否进入上下文")
        return await _wiki_call(request, org_id, lambda wiki: wiki.context(slug, value))
    return await _wiki_call(request, org_id, lambda wiki: wiki.change(slug, attribute, value))


@router.put(_WIKI + "/pages/{slug}/audience")
async def narrow_wiki_audience(org_id: str, slug: str, body: Dict[str, Any], request: Request):
    return await _wiki_call(request, org_id, lambda wiki: wiki.narrow_audience(slug, body))


@router.get(_WIKI + "/pages/{slug}/history")
async def wiki_history(org_id: str, slug: str, request: Request):
    return await _wiki_call(request, org_id, lambda wiki: wiki.history(slug))


@router.get(_WIKI + "/pages/{slug}/versions/{version}")
async def wiki_version(org_id: str, slug: str, version: int, request: Request):
    return await _wiki_call(request, org_id, lambda wiki: wiki.version(slug, version))


@router.put(_WIKI + "/bulk/agents")
async def set_wiki_page_agents(org_id: str, body: Dict[str, Any], request: Request):
    root = _root(org_id)

    def known_agents():
        try:
            return frozenset(row["id"] for row in app_channels.channel_agents(root).values())   # 派活的 agent 才读得到
        except (OSError, ValueError):
            return frozenset()
    return await _wiki_call(request, org_id, lambda wiki: wiki.set_agents(body, known_agents=known_agents()))


@router.post(_WIKI + "/bulk/delete")
async def remove_wiki_pages(org_id: str, body: Dict[str, Any], request: Request):
    return await _wiki_call(request, org_id, lambda wiki: wiki.remove_many(body))


@router.post(_WIKI + "/trash/restore")
async def restore_wiki_pages(org_id: str, body: Dict[str, Any], request: Request):
    if set(body) != {"ids"}:
        return _error(400, "invalid", "请选择要恢复的页面")
    return await _wiki_call(request, org_id, lambda wiki: wiki.restore(body["ids"]))


@router.post(_WIKI + "/trash/purge")
async def purge_wiki_pages(org_id: str, body: Dict[str, Any], request: Request):
    return await _wiki_call(request, org_id, lambda wiki: wiki.purge(body))


# ── 后台任务已保存的输出 ──────────────────────────────────────────────────────

_TASK_OUTPUT_STATUS = {
    "queued": "queued", "running": "running", "done": "done", "failed": "failed",
    "cancelled": "canceled", "interrupted": "interrupted",
    # 暂停和阻塞都没有继续执行；界面的输出合同没有这两个更细的状态。
    "paused": "interrupted", "blocked": "interrupted",
}


@router.get(_ORG + "/tasks/{task_id}/output")
async def saved_task_output(org_id: str, task_id: str, request: Request, tail: int = Query(100, ge=1, le=200)):
    """主人可以读任务台账保留的进度尾部；成员私聊只显示交付结果。"""
    from src.workflow.tasks.tasks import MAX_SCAN_RECORD_BYTES, TaskLedger

    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner or not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", "查看后台任务输出需要已认证的所有者")
    task = await asyncio.to_thread(TaskLedger(root).load, task_id, max_bytes=MAX_SCAN_RECORD_BYTES)
    if task is None:
        return _error(404, "task.not_found", "没有这个任务的已保存输出")
    status = _TASK_OUTPUT_STATUS.get(task.status) if isinstance(task.status, str) else None
    if status is None or not isinstance(task.log, list) or not all(isinstance(entry, str) for entry in task.log):
        return _error(409, "task.output_unavailable", "无法读取任务的已保存输出")
    lines = [line for entry in task.log for line in entry.splitlines()]
    return JSONResponse({"taskId": task.id, "status": status, "source": "saved",
                         "lines": lines[-tail:], "truncated": len(lines) > tail},
                        headers={"Cache-Control": "no-store"})


# ── 所有者的任务草案 ────────────────────────────────────────────────────────

_MissionStatusFilter = Literal["open", "closed", "waiting_you", "proposed", "active", "paused",
                               "awaiting_close", "done", "canceled"]


class _NewMissionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    agentId: str = Field(min_length=1, max_length=128)
    goal: str = Field(min_length=1, max_length=2000)
    title: str | None = Field(None, max_length=120)
    threadId: str | None = Field(None, min_length=1, max_length=128)
    channelId: str | None = Field(None, min_length=1, max_length=128)
    folder: str | None = Field(None, max_length=1024)
    executor: str | None = Field(None, max_length=32)
    commandApproval: str | None = Field(None, max_length=32)


class _CloseMissionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    outcome: Literal["done", "canceled"]
    saveToWiki: bool = False
    note: str | None = Field(None, max_length=2000)
    deleteBranch: bool = False


def _mission_progress(items=()):
    statuses = [item["status"] for item in items]
    return {"done": statuses.count("done") + statuses.count("dropped"), "total": len(statuses),
            "doing": statuses.count("doing"), "waiting": statuses.count("waiting_you")}


def _mission_view(drafts, row, *, plan=None, running=0, pending=None, role="owner", asked=False, wiki_page=None):
    """plan：最新一版计划（{"version", "items"}，没有就 None）；running：正在做的步数；
    pending：还在等人的决定和交付（{decisions, provisional, deliveries}）。"""
    pending = pending or {}
    channels = drafts.channels
    status = row["current_status"]
    items = plan["items"] if plan else []
    started = row["started_at"] is not None
    return {
        "id": row["id"], "threadId": row["thread_id"], "channelId": row["channel_id"],
        "agentId": views.member_agent_id(channels.registry.root, row["staff_id"]),
        "ownerId": channels.history.person_id(row["owner"]),
        "title": row["title"], "goal": row["goal"], "status": status,
        "executor": "agent", "findings": [], "openQuestion": None,
        "repoDir": "", "resumeTasks": bool(row["resume_tasks"]), "commandApproval": "always",
        "plan": {"version": plan["version"] if plan else 0, **_mission_progress(items),
                 "items": [{"id": item["id"], "title": item["title"], "status": item["status"]} for item in items]},
        "pending": {"decisions": pending.get("decisions", 0), "provisional": pending.get("provisional", 0),
                    "reviews": 0, "suggestions": pending.get("suggestions", 0),
                    "joinRequests": pending.get("joinRequests", 0), "commands": 0,
                    "deliveries": pending.get("deliveries", 0)},
        "joinRequested": asked, "runningTasks": running, "machine": None, "needsComputer": False,
        # 所有者和「决定」成员能开始、暂停、拍板、验收；收尾和管人只有所有者（主人 2026-11-23 定）
        "role": role, "canAct": role in ("owner", "decide") and status not in ("done", "canceled"),
        "canManage": role == "owner" and started,
        "createdAt": row["created"],
        "canCancelDraft": role == "owner" and status == "proposed",
        "startedAt": row["started_at"], "pausedAt": row["paused_at"] if status == "paused" else None,
        "closedAt": row["closed_at"], "closeReason": row["note"],
        "wikiPageId": wiki_page,
    }


def _mission_full(drafts, actor, mission_id):
    """一个 Mission 的完整视图：带最新计划和正在做的步数。"""
    from src.web import app_missions
    from src.workflow.members.mission_runs import MissionRuns
    from src.workflow.members.mission_runs import MissionCards, MissionPeople
    runs = MissionRuns(drafts)
    people = MissionPeople(runs)
    _, role, asked = people.visible(actor, mission_id)
    row, plans, steps = runs.details(actor, mission_id)
    return _mission_view(drafts, row, plan=plans[0] if plans else None,
                         running=app_missions.running(drafts.channels.registry.root, mission_id, steps),
                         pending={**MissionCards(runs).counts(mission_id), **people.counts(actor, mission_id)},
                         role=role, asked=asked, wiki_page=MissionCards(runs).wiki_page_of(mission_id))


async def _mission_request(org_id, request, action, *, status=200, members=False):
    """members：成员会话也能进（处理函数里按他在这个 Mission 里的身份核对）。所有者照旧要所有者证明。"""
    from src.workflow.members.mission_drafts import AgentUnavailable, DraftClosed, DraftConflict, MissionDrafts
    from src.workflow.members.mission_runs import BadOption, WrongStatus
    from src.workflow.members.registry import Damaged, Missing, Refused

    root = _root(org_id)
    actor = member_auth.principal(request, root)
    if actor.is_owner and not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", "访问任务草案需要已认证的所有者")
    if not actor.is_owner and not (members and actor.is_member):
        return _error(403, "owner_proof_required", "访问任务草案需要已认证的所有者")
    try:
        value = await asyncio.to_thread(lambda: action(MissionDrafts(app_channels.channels(root)), actor))
    except (Missing, LookupError):
        return _error(404, "mission.not_found", "没有这个任务草案或团队频道线程")
    except Refused:
        return _error(403, "forbidden", "没有访问任务草案的权限")
    except DraftConflict as error:
        return _error(409, "mission.thread_in_use", str(error))
    except DraftClosed as error:
        return _error(409, "mission.closed", str(error))
    except AgentUnavailable as error:
        return _error(409, "mission.agent_unavailable", str(error))
    except WrongStatus as error:
        return _error(409, "mission.wrong_status", str(error))
    except BadOption as error:
        return _error(400, "mission.invalid_option", str(error))
    except ThreadArchived as error:
        return _error(409, "thread.archived", str(error))
    except (Damaged, OSError, ValueError):
        return _error(409, "mission.unavailable", "读取或保存任务草案失败")
    return value if isinstance(value, Response) else JSONResponse(value, status_code=status,
                                                                  headers={"Cache-Control": "no-store"})


@router.get(_ORG + "/missions")
async def list_mission_drafts(
    org_id: str, request: Request, status: _MissionStatusFilter = "open", q: str = Query("", max_length=200),
    channelId: str | None = Query(None, max_length=128), agentId: str | None = Query(None, max_length=128),
    limit: int = Query(50, ge=1, le=100),
):
    if not views.owner_connection():
        # 保留无令牌开发实例启动时的空列表，不读任务草案或实际计数。
        _root(org_id)
        return {"missions": [], "counts": {"active": 0, "waitingYou": 0, "closed": 0}}

    def read(drafts, actor):
        staff_id = None if agentId is None else _staff_of(drafts.channels.registry.root, agentId) or ""
        rows, counts = drafts.listing(actor, channel_id=channelId, staff_id=staff_id, query=q.strip(), limit=limit,
                                      status=status)
        from src.web import app_missions
        from src.workflow.members.mission_runs import MissionRuns
        from src.workflow.members.mission_runs import MissionCards, MissionPeople
        runs = MissionRuns(drafts)
        extra = runs.latest([row["id"] for row in rows])
        cards, people = MissionCards(runs), MissionPeople(runs)
        root = drafts.channels.registry.root
        shown = []
        for row in rows:
            _, role, asked = people.visible(actor, row["id"])
            shown.append(_mission_view(drafts, row, plan=extra[row["id"]]["plan"],
                                       running=app_missions.running(root, row["id"], extra[row["id"]]["steps"]),
                                       pending={**cards.counts(row["id"]), **people.counts(actor, row["id"])},
                                       role=role, asked=asked))
        return {"missions": shown, "counts": counts}

    return await _mission_request(org_id, request, members=True, action=read)


@router.post(_ORG + "/missions", status_code=201)
async def create_mission_draft(org_id: str, body: _NewMissionDraft, request: Request):
    def create(drafts, actor):
        root = drafts.channels.registry.root
        if body.threadId is None:
            return _error(400, "mission.thread_required", "请在已有团队频道线程里新建任务草案")
        if body.channelId is not None:
            return _error(400, "mission.invalid_location", "有线程时由服务端确定所属频道")
        if (body.folder is not None or body.executor not in (None, "agent")
                or body.commandApproval not in (None, "always")):
            return _error(400, "mission.unsupported", "任务草案仅记录原生专员，不配置工作目录或自动批准")
        goal = body.goal.strip()
        title = " ".join(goal.split())[:120] if body.title is None else body.title.strip()
        if not goal or not title:
            return _error(400, "mission.invalid_text", "任务目标和标题不能为空")
        if body.agentId == views.main_agent_id(root):
            return _error(400, "mission.unsupported", "请选择一位已登记的原生专员")
        staff_id = _staff_of(root, body.agentId)
        if staff_id is None:
            return _error(404, "mission.agent_not_found", "没有这个专员")
        return _mission_view(drafts, drafts.create(actor, body.threadId, staff_id, title, goal))

    return await _mission_request(org_id, request, create, status=201)


@router.get(_ORG + "/missions/eligible-agents")
async def mission_eligible_agents(org_id: str, request: Request):
    """新建 Mission 能选的 agent：原生、能在频道里办事、岗位不写代码（主人 2026-11-23 定）。
    写代码或用本机 CLI 的标为要连电脑；暂停、停用的标为不能办事。"""
    def read(drafts, actor):
        from src.scheduling.staff_runner import get_staff_service
        root = drafts.channels.registry.root
        joinable = app_channels.channel_agents(root)            # 按专员 id（也就是 handle）
        service = get_staff_service(root)
        rows = []
        for agent in views.agents(root):
            staff_id = _staff_of(root, agent["id"])
            if staff_id is None or agent["id"] == views.main_agent_id(root):
                continue
            reason = None
            try:
                member, role = service.assignment({"member": staff_id, "prompt": "x"})
                if member["engine"] != "vortocode" or role.get("project_binding"):
                    reason = "no_computer"
                else:
                    service.executable(role)
                    if not joinable.get(staff_id, {}).get("joinable"):
                        reason = "exec_off"
            except ValueError:
                reason = "exec_off"
            rows.append({"agentId": agent["id"], "handle": agent["handle"], "name": agent["name"],
                         "eligible": reason is None, "reason": reason, "canManage": False, "machine": None,
                         "ready": reason is None, "executors": ["agent"] if reason is None else [],
                         "defaultExecutor": "agent" if reason is None else None, "codexTooOld": False,
                         "commandApproval": "always"})
        return {"agents": rows}

    return await _mission_request(org_id, request, read)


@router.get(_ORG + "/missions/{mission_id}")
async def get_mission_draft(org_id: str, mission_id: str, request: Request):
    return await _mission_request(org_id, request, members=True, action=lambda drafts, actor: _mission_full(drafts, actor, mission_id))


@router.post(_ORG + "/missions/{mission_id}/close")
async def close_mission_draft(org_id: str, mission_id: str, body: _CloseMissionDraft, request: Request):
    """草案只能取消；开始以后可以标为完成或取消，正在做的那一步一起停下。不生成 Wiki 总结、不删分支。"""
    stop: Dict[str, Any] = {}

    def close(drafts, actor):
        from src.workflow.members.mission_runs import MissionRuns
        if body.saveToWiki or body.deleteBranch:
            return _error(409, "mission.unsupported", "收尾时暂不生成 Wiki 总结；Mission 没有自己的分支")
        note = (body.note or "").strip() or None
        if drafts.get(actor, mission_id)["started_at"] is None:
            if body.outcome != "canceled":
                return _error(409, "mission.unsupported", "还没开始的草案只能取消")
            return _mission_view(drafts, drafts.cancel(actor, mission_id, note))
        _, stop["task"] = MissionRuns(drafts).close(actor, mission_id, body.outcome, note)
        return _mission_full(drafts, actor, mission_id)

    response = await _mission_request(org_id, request, close)
    if stop.get("task"):
        await _stop_mission_step(_root(org_id), stop["task"])
    return response


async def _stop_mission_step(root: str, task_id: str) -> bool:
    try:
        return bool(app_staff.stop(root, task_id))
    except (ValueError, OSError, LookupError):
        logging.getLogger(__name__).warning("Mission 正在做的一步没能停下", exc_info=True)
        return False


async def _mission_chat_ready(root: str) -> None:
    """派活用主要聊天的会话当发起身份；还没打开过就先登记。登记不上时由派活那一步如实暂停。"""
    try:
        await _dispatch_ready(root)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).info("主要聊天没能登记", exc_info=True)


async def _mission_dispatched(org_id, request, response, claimed):
    """登记好的一步在事件循环线程里派出，再读一次给界面（派不出去时 Mission 已暂停）。"""
    from src.web import app_missions
    if not claimed.get("task") or response.status_code >= 300:
        return response
    root = _root(org_id)
    await _mission_chat_ready(root)
    await app_missions.dispatch(root, claimed["mission"], claimed["task"], claimed["trigger"])
    return await _mission_request(org_id, request, members=True,
                                  action=lambda drafts, actor: _mission_full(drafts, actor, claimed["mission"]))


def _may_drive(drafts, actor, row):
    """成员的这一下会让 agent 接着干活：他要在这个频道里有叫 agent 办事的权限，也要能用这个 agent。"""
    from src.web import app_members
    from src.workflow.members.registry import Refused
    if not actor.is_member:
        return
    if (not drafts.channels.can_call_agents(actor, row["channel_id"])
            or app_members.usable_level(drafts.channels.registry.root, row["staff_id"], actor) == "none"):
        raise Refused("你不能让这个 agent 办事")


@router.post(_ORG + "/missions/{mission_id}/start")
async def start_mission(org_id: str, mission_id: str, request: Request):
    """开始：绑定的 agent 在话题里一步接一步做（主人 2026-11-23 定）。"""
    claimed: Dict[str, Any] = {}

    def start(drafts, actor):
        from src.web import app_missions
        from src.workflow.members.mission_runs import MissionRuns
        row = drafts.get(actor, mission_id)
        if _mission_people(drafts).visible(actor, mission_id)[1] not in ("owner", "decide"):
            return _error(403, "forbidden", "你在这个 Mission 里没有这样做的权限")
        _may_drive(drafts, actor, row)
        try:
            app_missions.check_startable(drafts.channels.registry.root, row)
        except ValueError as error:
            return _error(409, "mission.agent_unavailable", str(error))
        _, task_id = MissionRuns(drafts).start(actor, mission_id)
        claimed.update(mission=mission_id, task=task_id, trigger="start")
        return _mission_full(drafts, actor, mission_id)

    return await _mission_dispatched(org_id, request, await _mission_request(org_id, request, members=True, action=start), claimed)


@router.post(_ORG + "/missions/{mission_id}/pause")
async def pause_mission(org_id: str, mission_id: str, request: Request):
    """暂停：不再派下一步；正在做的这一步做完为止（要立刻停下用「停止」）。"""
    def pause(drafts, actor):
        from src.workflow.members.mission_runs import MissionRuns
        MissionRuns(drafts).pause(actor, mission_id)
        return _mission_full(drafts, actor, mission_id)

    return await _mission_request(org_id, request, members=True, action=pause)


@router.post(_ORG + "/missions/{mission_id}/resume")
async def resume_mission(org_id: str, mission_id: str, request: Request):
    """继续：重新计步，接着做下一步。"""
    claimed: Dict[str, Any] = {}

    def resume(drafts, actor):
        from src.web import app_missions
        from src.workflow.members.mission_runs import MissionRuns
        row = drafts.get(actor, mission_id)
        if _mission_people(drafts).visible(actor, mission_id)[1] not in ("owner", "decide"):
            return _error(403, "forbidden", "你在这个 Mission 里没有这样做的权限")
        _may_drive(drafts, actor, row)
        try:
            app_missions.check_startable(drafts.channels.registry.root, row)
        except ValueError as error:
            return _error(409, "mission.agent_unavailable", str(error))
        _, task_id = MissionRuns(drafts).resume(actor, mission_id)
        if task_id:
            claimed.update(mission=mission_id, task=task_id, trigger="resume")
        return _mission_full(drafts, actor, mission_id)

    return await _mission_dispatched(org_id, request, await _mission_request(org_id, request, members=True, action=resume), claimed)


@router.post(_ORG + "/missions/{mission_id}/stop")
async def stop_mission(org_id: str, mission_id: str, request: Request):
    """停止：暂停，并停下正在做的那一步。"""
    stop: Dict[str, Any] = {}

    def halt(drafts, actor):
        from src.workflow.members.mission_runs import MissionRuns
        _, stop["task"] = MissionRuns(drafts).stop(actor, mission_id)
        return None

    response = await _mission_request(org_id, request, members=True, action=halt)
    if response.status_code >= 300:
        return response
    canceled = 1 if stop.get("task") and await _stop_mission_step(_root(org_id), stop["task"]) else 0
    view = await _mission_request(org_id, request, members=True, action=lambda drafts, actor: _mission_full(drafts, actor, mission_id))
    if view.status_code >= 300:
        return view
    return JSONResponse({**json.loads(view.body), "canceledTasks": canceled}, headers={"Cache-Control": "no-store"})


class _MissionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    resumeTasks: bool


@router.patch(_ORG + "/missions/{mission_id}")
async def patch_mission(org_id: str, mission_id: str, body: _MissionPatch, request: Request):
    """「恢复后自动接着做」：服务重启打断了一步时，开着就重新做这一步，关着就暂停等所有者。"""
    def patch(drafts, actor):
        from src.workflow.members.mission_runs import MissionRuns
        MissionRuns(drafts).set_resume_tasks(actor, mission_id, body.resumeTasks)
        return _mission_full(drafts, actor, mission_id)

    return await _mission_request(org_id, request, patch)


class _MissionMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=8000)


@router.post(_ORG + "/missions/{mission_id}/messages")
async def mission_message(org_id: str, mission_id: str, body: _MissionMessage, request: Request):
    """所有者在 Mission 话题里说一句。进行中、没有正在做的一步时，agent 马上读到并接着做（delivered）；
    否则下一步开始时读到。"""
    claimed: Dict[str, Any] = {}
    text = body.text.strip()
    if not text:
        return _error(400, "mission.invalid_text", "消息不能为空")

    def send(drafts, actor):
        from src.workflow.members.mission_runs import MissionRuns
        row = drafts.get(actor, mission_id)
        if row["current_status"] in ("done", "canceled"):
            return _error(409, "mission.wrong_status", "这个任务已经收尾")
        drafts.channels.post(actor, row["channel_id"], text, thread=row["thread_id"])
        if row["current_status"] == "active":
            task_id = MissionRuns(drafts).claim_next(mission_id, "message")
            if task_id:
                claimed.update(mission=mission_id, task=task_id, trigger="message")
        return None

    response = await _mission_request(org_id, request, send)
    if response.status_code >= 300:
        return response
    if not claimed:
        return JSONResponse({"delivered": False}, headers={"Cache-Control": "no-store"})
    from src.web import app_missions
    root = _root(org_id)
    await _mission_chat_ready(root)
    sent = await app_missions.dispatch(root, mission_id, claimed["task"], "message")
    return JSONResponse({"delivered": sent, **({"taskId": claimed["task"]} if sent else {})},
                        headers={"Cache-Control": "no-store"})


# ── Mission 的决定和交付（主人 2026-11-23 定）：agent 在一步里提出，人在话题里拍板、验收 ──

def _mission_cards(drafts):
    from src.workflow.members.mission_runs import MissionCards, MissionRuns
    return MissionCards(MissionRuns(drafts))


async def _mission_proceed(org_id, mission_id, proceed):
    """拍板或提出修改以后：能接着做就派下一步（在事件循环线程里）。"""
    from src.web import app_missions
    if not proceed:
        return
    root = _root(org_id)
    await _mission_chat_ready(root)
    await app_missions.proceed(root, mission_id, proceed)


@router.get(_ORG + "/missions/{mission_id}/decisions")
async def mission_decisions(org_id: str, mission_id: str, request: Request):
    return await _mission_request(org_id, request, members=True, action=lambda drafts, actor: {
        "decisions": _mission_cards(drafts).decisions(actor, mission_id)})


class _MissionOption(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    option: str = Field(min_length=1, max_length=120)


@router.post(_ORG + "/missions/{mission_id}/decisions/approve-provisional")
async def approve_mission_provisional(org_id: str, mission_id: str, request: Request):
    """agent 按建议先做了的决定：一次全部确认。"""
    return await _mission_request(org_id, request, members=True, action=lambda drafts, actor: {
        "decisions": _mission_cards(drafts).approve_provisional(actor, mission_id)})


@router.post(_ORG + "/missions/{mission_id}/decisions/{decision_id}/choose")
async def choose_mission_decision(org_id: str, mission_id: str, decision_id: str, body: _MissionOption,
                                  request: Request):
    """拍板。阻塞的问题都有了答案、没有在做的一步时，agent 接着做。"""
    proceed: Dict[str, Any] = {}

    def choose(drafts, actor):
        _may_drive(drafts, actor, drafts.get(actor, mission_id))
        view, go = _mission_cards(drafts).choose(actor, mission_id, decision_id, body.option)
        if go:
            proceed["trigger"] = "decision"
        return view

    response = await _mission_request(org_id, request, members=True, action=choose)
    await _mission_proceed(org_id, mission_id, proceed.get("trigger"))
    return response


@router.post(_ORG + "/missions/{mission_id}/decisions/{decision_id}/opinion")
async def mission_decision_opinion(org_id: str, mission_id: str, decision_id: str, body: _MissionOption,
                                   request: Request):
    """表态：只记下倾向，不拍板。"""
    return await _mission_request(org_id, request, members=True, action=lambda drafts, actor:
                                  _mission_cards(drafts).opinion(actor, mission_id, decision_id, body.option))


@router.get(_ORG + "/missions/{mission_id}/deliveries")
async def mission_deliveries(org_id: str, mission_id: str, request: Request):
    return await _mission_request(org_id, request, members=True, action=lambda drafts, actor:
                                  _mission_cards(drafts).deliveries(actor, mission_id))


class _MissionCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    checked: bool


@router.put(_ORG + "/missions/{mission_id}/deliveries/{delivery_id}/checks/{item_id}")
async def check_mission_delivery(org_id: str, mission_id: str, delivery_id: str, item_id: str, body: _MissionCheck,
                                 request: Request):
    return await _mission_request(org_id, request, members=True, action=lambda drafts, actor:
                                  _mission_cards(drafts).check(actor, mission_id, delivery_id, item_id, body.checked))


def _mission_wiki_page(drafts, actor, mission_id, delivery, note, reader):
    """交付写成的 Wiki 页面：目标、交付说明、核对清单、验收意见。私有频道的只给频道里的人看。"""
    row = drafts.get(actor, mission_id)
    lines = [f"# {row['title']}", "", f"**目标**：{row['goal']}" if row.get("goal") else "", "", "## 交付", "",
             delivery["summary"], ""]
    if delivery["checklist"]:
        lines += ["## 核对清单", ""] + [f"- [{'x' if item['checkedBy'] else ' '}] {item['text']}"
                                       for item in delivery["checklist"]] + [""]
    if note:
        lines += [f"**验收意见**：{note}", ""]
    state = drafts.channels.registry.snapshot()
    channel = state["channels"].get(row["channel_id"]) or {}
    audience = None
    if channel.get("kind") != "public":
        people = [person for person in [reader.owner_id, *(channel.get("joined") or {})]
                  if person in reader.known_people]
        audience = {"mode": "people", "userIds": list(dict.fromkeys(people)) or [reader.owner_id]}
    return {"title": f"Mission：{row['title']}"[:120], "folder": "Mission", "tags": ["mission"],
            "body": "\n".join(lines).strip() + "\n", "audience": audience}


class _MissionAccept(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    saveToWiki: bool = False
    note: str | None = Field(None, max_length=2000)


@router.post(_ORG + "/missions/{mission_id}/deliveries/{delivery_id}/accept")
async def accept_mission_delivery(org_id: str, mission_id: str, delivery_id: str, body: _MissionAccept,
                                  request: Request):
    """验收通过：Mission 完成。勾了「存到 Wiki」就把交付写成一页 Wiki（主人 2026-11-24 定：成员和 agent
    写的内容按资料处理，Wiki 可以开放）。私有频道里的 Mission，这页只给频道里的人看。"""
    from src.workflow.wiki import WikiError
    stop: Dict[str, Any] = {}

    def accept(drafts, actor):
        cards = _mission_cards(drafts)
        root = drafts.channels.registry.root
        wiki = None
        if body.saveToWiki:
            wiki = _wiki_for(root, actor)
            if not wiki.reader.can_write:
                return _error(403, "forbidden", "你没有编辑 Wiki 的权限：取消「存到 Wiki」再完成")
        note = (body.note or "").strip() or None
        delivery, _, stop["task"] = cards.conclude(actor, mission_id, delivery_id, "accepted", note)
        if wiki is not None and not cards.wiki_page_of(mission_id):
            try:
                page = wiki.create(_mission_wiki_page(drafts, actor, mission_id, delivery, note, wiki.reader))
                cards.mark_saved(delivery_id, page["id"])
            except (WikiError, OSError, ValueError) as error:
                return _error(409, "mission.wiki_failed", f"Mission 已经完成，但没能存到 Wiki：{error}")
        return _mission_full(drafts, actor, mission_id)

    response = await _mission_request(org_id, request, members=True, action=accept)
    if stop.get("task"):
        await _stop_mission_step(_root(org_id), stop["task"])
    return response


class _MissionChanges(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    note: str = Field(min_length=1, max_length=2000)


@router.post(_ORG + "/missions/{mission_id}/deliveries/{delivery_id}/changes")
async def request_mission_changes(org_id: str, mission_id: str, delivery_id: str, body: _MissionChanges,
                                  request: Request):
    """提出修改：Mission 回到进行中，agent 带着意见接着做。"""
    proceed: Dict[str, Any] = {}
    note = body.note.strip()
    if not note:
        return _error(400, "mission.delivery_note_required", "请写下要改什么")

    def changes(drafts, actor):
        _may_drive(drafts, actor, drafts.get(actor, mission_id))
        _, go, _ = _mission_cards(drafts).conclude(actor, mission_id, delivery_id, "changes_requested", note)
        if go:
            proceed["trigger"] = "changes"
        return None

    response = await _mission_request(org_id, request, members=True, action=changes)
    if response.status_code >= 300:
        return response
    await _mission_proceed(org_id, mission_id, proceed.get("trigger"))
    return await _mission_request(org_id, request, members=True, action=lambda drafts, actor: _mission_full(drafts, actor, mission_id))


# ── 参与者、加入申请、建议（主人 2026-11-23 定）：所有者管人；能看到频道的成员申请加入 ──

def _mission_people(drafts):
    from src.workflow.members.mission_runs import MissionPeople, MissionRuns
    return MissionPeople(MissionRuns(drafts))


def _grantable(drafts, mission_id, user_id, role):
    """给「决定」身份前：这个人要能在这个频道里叫这个 agent 办事（拍板会让 agent 接着干活）。"""
    from src.agents.principal import MEMBER, Principal
    from src.web import app_members
    from src.workflow.members.registry import Damaged, Missing, Refused
    if role != "decide":
        return None
    row = drafts.get(drafts_owner(drafts), mission_id)
    who = Principal(MEMBER, user_id, drafts.channels.registry.root)
    try:
        allowed = (drafts.channels.can_call_agents(who, row["channel_id"])
                   and app_members.usable_level(drafts.channels.registry.root, row["staff_id"], who) != "none")
    except (Missing, Refused, Damaged, ValueError):
        allowed = False
    if not allowed:
        return _error(409, "mission.cannot_decide", "这个人不能让这个 agent 办事，只能给「评论」身份")
    return None


def drafts_owner(drafts):
    from src.agents.principal import OWNER, Principal
    return Principal(OWNER, drafts.channels.owner_id, drafts.channels.registry.root)


class _MissionParticipant(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    userId: str = Field(min_length=1, max_length=128)
    role: Literal["decide", "comment"]


class _MissionParticipantRole(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    role: Literal["decide", "comment"]


@router.post(_ORG + "/missions/{mission_id}/participants")
async def add_mission_participant(org_id: str, mission_id: str, body: _MissionParticipant, request: Request):
    def add(drafts, actor):
        refused = _grantable(drafts, mission_id, body.userId, body.role)
        return refused or _mission_people(drafts).set_participant(actor, mission_id, body.userId, body.role, create=True)

    return await _mission_request(org_id, request, add)


@router.patch(_ORG + "/missions/{mission_id}/participants/{user_id}")
async def set_mission_participant(org_id: str, mission_id: str, user_id: str, body: _MissionParticipantRole,
                                  request: Request):
    def change(drafts, actor):
        refused = _grantable(drafts, mission_id, user_id, body.role)
        return refused or _mission_people(drafts).set_participant(actor, mission_id, user_id, body.role, create=False)

    return await _mission_request(org_id, request, change)


@router.delete(_ORG + "/missions/{mission_id}/participants/{user_id}", status_code=204)
async def remove_mission_participant(org_id: str, mission_id: str, user_id: str, request: Request):
    def remove(drafts, actor):
        _mission_people(drafts).remove_participant(actor, mission_id, user_id)
        return Response(status_code=204)

    return await _mission_request(org_id, request, remove)


@router.get(_ORG + "/missions/{mission_id}/join-requests")
async def mission_join_requests(org_id: str, mission_id: str, request: Request):
    """所有者看到全部待处理的；成员只看到自己的。"""
    return await _mission_request(org_id, request, members=True, action=lambda drafts, actor: {
        "requests": _mission_people(drafts).join_requests(actor, mission_id)})


@router.post(_ORG + "/missions/{mission_id}/join-requests")
async def request_mission_join(org_id: str, mission_id: str, request: Request):
    return await _mission_request(org_id, request, members=True,
                                  action=lambda drafts, actor: _mission_people(drafts).request_join(actor, mission_id))


class _MissionJoinDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    role: Literal["decide", "comment"] | None = None


@router.post(_ORG + "/missions/{mission_id}/join-requests/{user_id}/{outcome}")
async def decide_mission_join(org_id: str, mission_id: str, user_id: str, outcome: Literal["accept", "decline"],
                              body: _MissionJoinDecision, request: Request):
    def decide(drafts, actor):
        role = body.role or "comment"
        refused = _grantable(drafts, mission_id, user_id, role) if outcome == "accept" else None
        return refused or _mission_people(drafts).decide_join(actor, mission_id, user_id, outcome, role)

    return await _mission_request(org_id, request, decide)


class _MissionTransfer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    userId: str = Field(min_length=1, max_length=128)
    lendMachine: bool | None = None


@router.post(_ORG + "/missions/{mission_id}/transfer")
async def transfer_mission(org_id: str, mission_id: str, body: _MissionTransfer, request: Request):
    """Mission 的每一步以所有者的身份派活，只能归所有者：转交如实拒绝（可以把对方设成「决定」成员）。"""
    return await _mission_request(org_id, request, lambda drafts, actor: (
        drafts.get(actor, mission_id),
        _error(409, "mission.transfer_unavailable", "Mission 只能归所有者；可以把对方加成「决定」成员"))[1])


@router.post(_ORG + "/missions/{mission_id}/suggestions/{message_id}/{outcome}")
async def decide_mission_suggestion(org_id: str, mission_id: str, message_id: str,
                                    outcome: Literal["accept", "decline"], request: Request):
    """有拍板权的人采纳或谢绝一条建议。采纳的交给 agent；进行中、没有在做的一步时马上接着做。"""
    proceed: Dict[str, Any] = {}

    def decide(drafts, actor):
        if outcome == "accept":
            _may_drive(drafts, actor, drafts.get(actor, mission_id))
        answer, go = _mission_people(drafts).decide_suggestion(actor, mission_id, message_id, outcome)
        if go:
            proceed["trigger"] = "suggestion"
        return answer

    response = await _mission_request(org_id, request, members=True, action=decide)
    await _mission_proceed(org_id, mission_id, proceed.get("trigger"))
    return response


async def _note_mission_suggestion(request: Request, org_id: str, message: Dict[str, Any]) -> None:
    """有人在 Mission 的话题里发了一条：没有拍板权的记成建议（采纳前不进 agent 的提示）。"""
    thread = message.get("threadId")
    if not thread:
        return
    root = _root(org_id)
    actor = member_auth.principal(request, root)

    def note():
        from src.workflow.members.mission_drafts import MissionDrafts
        from src.workflow.members.registry import Missing
        drafts = MissionDrafts(app_channels.channels(root))
        try:
            row = drafts.get(actor, thread, by_thread=True)
        except Missing:
            return False
        if _mission_people(drafts).note_message(actor, row["id"], message["id"]):
            message.setdefault("meta", {})["missionSuggestion"] = {"status": "pending", "missionId": row["id"]}
        return True

    try:
        await asyncio.to_thread(note)
    except Exception:  # noqa: BLE001 —— 消息已经发出，记建议失败不能让发消息报错
        logging.getLogger(__name__).warning("Mission 建议没有记下", exc_info=True)


# ── 写代码的 Mission 要用的设置：等连接电脑那条线（主人 2026-11-23 定），如实拒绝或回空 ──

def _no_computer():
    return _error(409, "mission.no_computer", "Mission 暂时只在这个家的服务上由原生 agent 做，不连电脑、不写代码")


class _MissionMachine(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    machineId: str = Field(min_length=1, max_length=128)
    repoDir: str | None = Field(None, max_length=1024)


@router.put(_ORG + "/missions/{mission_id}/machine")
async def set_mission_machine(org_id: str, mission_id: str, body: _MissionMachine, request: Request):
    return await _mission_request(org_id, request, lambda drafts, actor: (drafts.get(actor, mission_id), _no_computer())[1])


class _MissionExecutor(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    executor: Literal["claude", "codex", "agent"]


@router.put(_ORG + "/missions/{mission_id}/executor")
async def set_mission_executor(org_id: str, mission_id: str, body: _MissionExecutor, request: Request):
    """只有原生 agent 自己做（executor 固定是 agent）；选 Claude 或 Codex 要连电脑。"""
    def choose(drafts, actor):
        view = _mission_full(drafts, actor, mission_id)
        return view if body.executor == "agent" else _no_computer()

    return await _mission_request(org_id, request, choose)


@router.post(_ORG + "/missions/{mission_id}/worktree/retry")
async def retry_mission_worktree(org_id: str, mission_id: str, request: Request):
    return await _mission_request(org_id, request, lambda drafts, actor: (
        drafts.get(actor, mission_id), _error(409, "mission.no_worktree", "Mission 没有项目副本，不需要重新准备"))[1])


class _MissionCommandApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    commandApproval: Literal["always", "low_risk_auto", "never"]


@router.put(_ORG + "/missions/{mission_id}/command-approval")
async def set_mission_command_approval(org_id: str, mission_id: str, body: _MissionCommandApproval, request: Request):
    """只有「每次都问」：Mission 里不新增免确认（确认门由内核决定）。"""
    def choose(drafts, actor):
        drafts.get(actor, mission_id)
        if body.commandApproval != "always":
            return _error(409, "mission.approval_fixed", "Mission 里需要确认的操作每次都问，不提供自动放行")
        return {"commandApproval": "always"}

    return await _mission_request(org_id, request, choose)


@router.delete(_ORG + "/missions/{mission_id}/command-rules/{rule_id}")
async def remove_mission_command_rule(org_id: str, mission_id: str, rule_id: str, request: Request):
    return await _mission_request(org_id, request, lambda drafts, actor: (
        drafts.get(actor, mission_id), _error(404, "mission.rule_not_found", "Mission 没有自动放行规则"))[1])


@router.get(_ORG + "/missions/{mission_id}/commands")
async def mission_commands(org_id: str, mission_id: str, request: Request):
    """原生 agent 在 Mission 里只用团队安全工具，不跑命令：没有命令卡，也没有放行规则。"""
    return await _mission_request(org_id, request, lambda drafts, actor: (
        drafts.get(actor, mission_id), {"commands": [], "rules": []})[1])


class _MissionCommandRetry(BaseModel):
    model_config = ConfigDict(extra="allow")


@router.post(_ORG + "/missions/{mission_id}/commands/retry")
async def retry_mission_command(org_id: str, mission_id: str, body: _MissionCommandRetry, request: Request):
    return await _mission_request(org_id, request, lambda drafts, actor: (
        drafts.get(actor, mission_id), _error(409, "mission.no_commands", "Mission 里不跑命令，没有可以重试的"))[1])


class _MissionCommandDecision(BaseModel):
    model_config = ConfigDict(extra="allow")


@router.post(_ORG + "/missions/{mission_id}/commands/{command_id}/decide")
async def decide_mission_command(org_id: str, mission_id: str, command_id: str, body: _MissionCommandDecision,
                                 request: Request):
    return await _mission_request(org_id, request, lambda drafts, actor: (
        drafts.get(actor, mission_id), _error(404, "mission.command_not_found", "没有这张命令卡"))[1])


@router.get(_ORG + "/missions/{mission_id}/reviews")
async def mission_reviews(org_id: str, mission_id: str, request: Request):
    """代码审查（PR）只来自写代码的 Mission：现在没有。"""
    return await _mission_request(org_id, request, lambda drafts, actor: (
        drafts.get(actor, mission_id), {"reviews": []})[1])


class _MissionReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    note: str | None = Field(None, max_length=2000)


@router.post(_ORG + "/missions/{mission_id}/reviews/{review_id}/{outcome}")
async def decide_mission_review(org_id: str, mission_id: str, review_id: str, outcome: str,
                                body: _MissionReviewDecision, request: Request):
    return await _mission_request(org_id, request, lambda drafts, actor: (
        drafts.get(actor, mission_id), _error(404, "mission.review_not_found", "没有这项审查"))[1])


class _MissionBrain(BaseModel):
    model_config = ConfigDict(extra="allow")


@router.put(_ORG + "/missions/{mission_id}/brain")
async def set_mission_brain(org_id: str, mission_id: str, body: _MissionBrain, request: Request):
    """Mission 用 agent 登记的模型；改模型去 agent 的设置（岗位改动要走核对）。"""
    return await _mission_request(org_id, request, lambda drafts, actor: (
        drafts.get(actor, mission_id),
        _error(409, "mission.brain_fixed", "Mission 用 agent 自己登记的模型，要换请去 agent 的设置里改"))[1])


@router.get(_ORG + "/threads/{thread_id}/mission")
async def thread_mission_draft(org_id: str, thread_id: str, request: Request):
    return await _mission_request(org_id, request, members=True, action=lambda drafts, actor: _mission_full(
        drafts, actor, drafts.get(actor, thread_id, by_thread=True)["id"]))


@router.get(_ORG + "/threads/{thread_id}/tasks")
async def mission_draft_thread_tasks(org_id: str, thread_id: str, request: Request):
    def read(drafts, actor):
        from src.web import app_missions
        from src.workflow.members.mission_runs import MissionRuns
        mission_id = drafts.get(actor, thread_id, by_thread=True)["id"]
        row, _, steps = MissionRuns(drafts).details(actor, mission_id)
        # 这个 Mission 派过的每一步；话题里别的事（比如被 @ 的回答）不在这里
        return {"tasks": app_missions.thread_tasks(drafts.channels.registry.root, row, steps)}

    return await _mission_request(org_id, request, members=True, action=read)


@router.get(_ORG + "/missions/{mission_id}/files")
async def mission_draft_files(org_id: str, mission_id: str, request: Request):
    def read(drafts, actor):
        drafts.get(actor, mission_id)
        # 草案存于频道数据库，尚未创建隔离目录或任何 Mission 文件。
        return {"files": [], "editedOnMachine": []}

    return await _mission_request(org_id, request, read)


@router.get(_ORG + "/missions/{mission_id}/plan")
async def mission_draft_plan(org_id: str, mission_id: str, request: Request):
    def read(drafts, actor):
        from src.workflow.members.mission_runs import MissionRuns
        row, plans, _ = MissionRuns(drafts).details(actor, mission_id)
        return {"missionId": row["id"], "current": plans[0] if plans else None, "history": plans[1:]}

    return await _mission_request(org_id, request, members=True, action=read)


@router.get(_ORG + "/missions/{mission_id}/summary")
async def mission_draft_summary(org_id: str, mission_id: str, request: Request):
    def read(drafts, actor):
        from src.web import app_missions
        from src.workflow.members.mission_runs import MissionRuns
        from src.workflow.members.mission_runs import MissionCards
        runs = MissionRuns(drafts)
        row, plans, steps = runs.details(actor, mission_id)
        pending = MissionCards(runs).counts(mission_id)
        text = row["goal"]
        note = app_missions.pending_note(row) if row["current_status"] in ("paused", "awaiting_close") else None
        if row["current_status"] == "awaiting_close":
            note = app_missions.pending_note({"pause_reason": "planned_done"})
        return {"missionId": row["id"], "text": text + ("\n\n" + note if note else ""),
                "plan": _mission_progress(plans[0]["items"] if plans else ()),
                "running": app_missions.running(drafts.channels.registry.root, mission_id, steps),
                "pendingDecisions": pending["decisions"], "provisionalDecisions": pending["provisional"],
                "pendingReviews": 0}

    return await _mission_request(org_id, request, members=True, action=read)


@router.get(_ORG + "/missions/{mission_id}/brain")
async def mission_draft_brain(org_id: str, mission_id: str, request: Request):
    def read(drafts, actor):
        from src.llm import endpoints
        from src.llm.client import LLMConfig
        from src.llm.providers import DEFAULT_PROVIDER, chat_target, providers
        from src.memory.write_policy import redact_secret_like

        configured = drafts.assigned_model(actor, mission_id)
        with endpoints.LOCK:
            target = chat_target(configured)
            if target is not None:
                available, model = True, target[1]
            elif endpoints.is_endpoint_model(configured):
                available, model = False, None
            else:
                available = providers()[DEFAULT_PROVIDER].usable
                model = configured or LLMConfig().model
        display_model = redact_secret_like(model)[0][:160] if model else None
        return {"missionId": mission_id, "brain": None, "model": None,
                "effectiveBrain": "model", "effectiveModel": display_model,
                "options": [{"kind": "model", "available": available, "defaultModel": display_model,
                             "models": [{"value": display_model, "label": display_model}] if display_model else []}],
                "canSetBrain": False, "canSetModel": False}

    return await _mission_request(org_id, request, read)


@router.get(_ORG + "/missions/{mission_id}/participants")
async def mission_draft_participants(org_id: str, mission_id: str, request: Request):
    return await _mission_request(org_id, request, members=True,
                                  action=lambda drafts, actor: _mission_people(drafts).participants(actor, mission_id))


@router.get(_ORG + "/missions/{mission_id}/settings")
async def mission_draft_settings(org_id: str, mission_id: str, request: Request):
    def read(drafts, actor):
        row = drafts.get(actor, mission_id)
        return {
            "missionId": row["id"],
            "rules": [],
            "stats": {"auto": 0, "asked": 0, "blocked": 0},
            "agentDefault": {
                "commandApproval": "always",
                "canSet": False,
                "ownerId": drafts.channels.history.person_id(row["owner"]),
            },
        }

    return await _mission_request(org_id, request, read)


# ── 日历的邀请与 agent 的日程提议、提醒、团队模板、沙箱设置（如实回答或照实读取）──────────

def _owner_only(request: Request, org_id: str):
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    if who.is_owner and not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", _NEEDS_OWNER)
    return None


class _Rsvp(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["accepted", "declined", "tentative", "yes", "no", "maybe"]


@router.post(_ORG + "/calendar/events/{event_id}/rsvp")
async def calendar_rsvp(org_id: str, event_id: str, body: _Rsvp, request: Request):
    """日历只有自己的日程，没有发给别人的邀请：没有可回复的邀请。"""
    return _owner_only(request, org_id) or _error(404, "calendar.invitation_not_found", "这里的日程都是自己的，没有要回复的邀请")


@router.post(_ORG + "/calendar/proposals/{proposal_id}/confirm")
@router.post(_ORG + "/calendar/proposals/{proposal_id}/dismiss")
async def calendar_proposal(org_id: str, proposal_id: str, request: Request):
    """agent 还不会提日程提议：没有这条提议。"""
    return _owner_only(request, org_id) or _error(404, "calendar.proposal_not_found", "没有这条日程提议")


@router.get(_ORG + "/reminders")
async def reminders(org_id: str, request: Request):
    """提醒还没有单独的存储：空列表（日程和例行工作在日历里）。"""
    return _owner_only(request, org_id) or JSONResponse({"reminders": []}, headers={"Cache-Control": "no-store"})


@router.post(_ORG + "/reminders")
async def create_reminder(org_id: str, body: Dict[str, Any], request: Request):
    return _owner_only(request, org_id) or _error(409, "reminder.unavailable",
                                                  "单独的提醒还没有：可以在日历里建日程，或让助理排一条例行工作")


@router.delete(_ORG + "/reminders/{reminder_id}")
async def delete_reminder(org_id: str, reminder_id: str, request: Request):
    return _owner_only(request, org_id) or _error(404, "reminder.not_found", "没有这条提醒")


@router.get(_ORG + "/team-templates")
async def team_templates(org_id: str, request: Request, locale: str = Query("", max_length=16)):
    """还没有现成的团队模板：空列表（agent 在新建页一个个建）。"""
    return _owner_only(request, org_id) or JSONResponse({"templates": []}, headers={"Cache-Control": "no-store"})


@router.post(_ORG + "/team-templates/{template_id}/use")
async def use_team_template(org_id: str, template_id: str, request: Request):
    return _owner_only(request, org_id) or _error(404, "team_template.not_found", "没有这个团队模板")


def _sandbox_settings() -> Dict[str, Any]:
    """照实：命令执行开关只在服务的环境里（VORTOONE_ENABLE_SHELL，全仓唯一一处）；跑命令前确认门每次都问人。"""
    from src.sandbox.policy import shell_enabled
    # 超时取命令工具的默认值（agents/shell.py:run_command 的 300 秒），这里没有另外的上限
    return {"enabled": shell_enabled(), "execTimeoutSeconds": 300, "maxTimeoutSeconds": 300, "requireApproval": True}


@router.get(_ORG + "/sandbox-settings")
async def sandbox_settings(org_id: str, request: Request):
    refused = _owner_only(request, org_id)
    if refused or _who(request, _root(org_id)).is_member:
        return refused or _error(403, "forbidden", "沙箱设置只有所有者能看")
    return JSONResponse(_sandbox_settings(), headers={"Cache-Control": "no-store"})


@router.put(_ORG + "/sandbox-settings")
async def save_sandbox_settings(org_id: str, body: Dict[str, Any], request: Request):
    """这里改不了：开关只在服务的环境里设，确认门由内核决定（不在界面上另开一个免确认的口子）。
    提交的和现在一样就照原样回。"""
    refused = _owner_only(request, org_id)
    if refused or _who(request, _root(org_id)).is_member:
        return refused or _error(403, "forbidden", "沙箱设置只有所有者能改")
    current = _sandbox_settings()
    if set(body) - {"enabled", "execTimeoutSeconds", "requireApproval"}:
        return _error(400, "invalid", "只接受 enabled、execTimeoutSeconds、requireApproval")
    if any(body[key] != current[key] for key in body):
        return _error(409, "sandbox.settings_fixed",
                      "命令执行的开关在服务的环境里设（VORTOONE_ENABLE_SHELL），跑命令前每次都要人确认，这里不能改")
    return JSONResponse(current, headers={"Cache-Control": "no-store"})


# ── 第二批如实回答（2026-11-24）：这里还没有对应能力的，回 4xx 或空，不存一份不生效的数据 ──

def _owner_gate(request: Request, org_id: str, *, members_too: bool = False):
    """所有者要所有者证明；成员只有 members_too 时能进。返回拒绝的响应或 None。"""
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    if who.is_owner and not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", _NEEDS_OWNER)
    if who.is_member and not members_too:
        return _error(403, "forbidden", "这里只有所有者能用")
    return None


def _no_store(value: Any) -> JSONResponse:
    return JSONResponse(value, headers={"Cache-Control": "no-store"})


def _report_prefs() -> Dict[str, Any]:
    """常驻助理的早报（每日卡）和周日晚报（带一周总结，每周卡）。时间在服务环境里设
    （VORTOONE_ASSISTANT_REPORT_TIMES），没有转发地址或 IM 时两张都不发。"""
    from src.scheduling.assistant_watch import report_times, watch_enabled
    times = report_times() or ["09:00", "21:00"]
    on = watch_enabled()
    first, last = times[0], times[-1]
    return {"weekOff": not on, "day": 0, "weekHour": int(last.split(":")[0]), "dayOff": not on,
            "dayHour": int(first.split(":")[0]), "digestOff": False, "remindOff": False,
            "timezone": "UTC", "dayTimezone": "UTC", "switches": []}


@router.get(_ORG + "/team-week")
async def team_week(org_id: str, request: Request):
    return _owner_gate(request, org_id) or _no_store(_report_prefs())


@router.put(_ORG + "/team-week")
async def set_team_week(org_id: str, body: Dict[str, Any], request: Request):
    """早晚报的时间和开关在服务环境里设：和现在一样照原样回，否则如实 409。"""
    refused = _owner_gate(request, org_id)
    if refused:
        return refused
    current = _report_prefs()
    if any(key not in current or body[key] != current[key] for key in body):
        return _error(409, "team_week.fixed", "早晚报的时间在服务的环境里设（VORTOONE_ASSISTANT_REPORT_TIMES），这里改不了")
    return _no_store(current)


@router.post(_ORG + "/team-week/{message_id}")
async def act_on_team_week(org_id: str, message_id: str, body: Dict[str, Any], request: Request):
    return _owner_gate(request, org_id) or _error(404, "team_week.not_found", "早晚报里没有可以直接照做的条目")


@router.get(_ORG + "/agents/{agent_id}/merge-repositories")
async def merge_repositories(org_id: str, agent_id: str, request: Request):
    """不经审批卡就合并的仓库：没有。合并一律要人确认。"""
    return _owner_gate(request, org_id) or _no_store({"repositories": []})


@router.put(_ORG + "/agents/{agent_id}/merge-repositories")
async def set_merge_repositories(org_id: str, agent_id: str, body: Dict[str, Any], request: Request):
    refused = _owner_gate(request, org_id)
    if refused:
        return refused
    if body.get("repositories") != []:
        return _error(409, "repositories.merge_needs_approval", "合并一律要人确认，这里不开免审批合并")
    return _no_store({"repositories": []})


class _AgentCommandApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    commandApproval: Literal["always", "low_risk_auto", "never"]


@router.put(_ORG + "/agents/{agent_id}/mission-command-approval")
async def set_agent_mission_command_approval(org_id: str, agent_id: str, body: _AgentCommandApproval,
                                             request: Request):
    """agent 以后的 Mission 默认怎么审批命令：只有「每次都问」，和 Mission 自己的设置一致。"""
    refused = _owner_gate(request, org_id)
    if refused:
        return refused
    if body.commandApproval != "always":
        return _error(409, "mission.approval_fixed", "Mission 里需要确认的操作每次都问，不提供自动放行")
    return _no_store({"commandApproval": "always"})


@router.delete(_ORG + "/agents/{agent_id}/holds/{hold_id}")
async def stop_agent_hold(org_id: str, agent_id: str, hold_id: str, request: Request):
    """agent 在连接的电脑上开着的程序：这里没有（等连接电脑线）。"""
    return _owner_gate(request, org_id) or _error(404, "hold.not_found", "这个 agent 没有开着的程序")


@router.get(_ORG + "/agents/{agent_id}/for-you")
async def agent_for_you(org_id: str, agent_id: str, request: Request, limit: int = Query(20, ge=1, le=100)):
    """agent 主动替人整理的东西还没有：空。"""
    return _owner_gate(request, org_id) or _no_store({"items": [], "monthCount": 0})


@router.get(_ORG + "/imports")
async def import_sources(org_id: str, request: Request):
    return _owner_gate(request, org_id) or _no_store({"sources": []})


@router.post(_ORG + "/imports")
async def apply_import(org_id: str, body: Dict[str, Any], request: Request):
    """从别的应用整包导入 agent 还没有：如实 409（Wiki、技能各有自己的导入）。"""
    return _owner_gate(request, org_id) or _error(409, "import.unavailable",
                                                  "整包导入还没有：Wiki、技能可以在各自的页面导入")


@router.post(_ORG + "/imports/files")
async def import_file(org_id: str, request: Request):
    return _owner_gate(request, org_id) or _error(409, "import.unavailable", "整包导入还没有")


@router.get(_ORG + "/blocks/{block_id}/state")
@router.put(_ORG + "/blocks/{block_id}/state")
@router.post(_ORG + "/blocks/{block_id}/draw")
async def block_state(org_id: str, block_id: str, request: Request):
    """可以多人一起改的互动块还没有（频道卡片是投票和选择卡，各有自己的接口）。"""
    return _owner_gate(request, org_id, members_too=True) or _error(404, "block.not_found", "没有这个互动块")


@router.post(_ORG + "/actions/{action_id}/undo")
@router.post(_ORG + "/actions/{action_id}/redo")
async def agent_action(org_id: str, action_id: str, request: Request):
    """agent 的操作回执还没有：没有可以撤销或重做的。"""
    return _owner_gate(request, org_id) or _error(404, "action.not_found", "没有这条操作回执")


@router.get(_ORG + "/action-cards/{message_id}")
async def action_card(org_id: str, message_id: str, request: Request):
    return _owner_gate(request, org_id) or _error(404, "action_card.not_found", "没有这张操作卡")


@router.post(_ORG + "/action-cards/{message_id}/{decision}")
async def decide_action_card(org_id: str, message_id: str, decision: str, request: Request):
    return _owner_gate(request, org_id) or _error(404, "action_card.not_found", "没有这张操作卡")


@router.post(_ORG + "/memories/{memory_id}/revert")
async def revert_memory(org_id: str, memory_id: str, request: Request):
    """记忆没有留旧版本：没法退回。要改就直接编辑或删掉。"""
    return _owner_gate(request, org_id) or _error(409, "memory.no_history", "记忆没有留旧版本，请直接编辑或删掉")


@router.post(_ORG + "/task-manager/continue")
async def task_manager_continue(org_id: str, body: Dict[str, Any], request: Request):
    """跳过额度、让同一件事接着做还没有：额度等待由额度设置决定。"""
    return _owner_gate(request, org_id) or _error(409, "task.continue_unavailable",
                                                  "还不能跳过额度接着做：可以调高额度，或停下再重新派")


@router.post(_ORG + "/team/channel-move")
async def propose_channel_move(org_id: str, body: Dict[str, Any], request: Request):
    return _owner_gate(request, org_id) or _error(409, "channel_move.unavailable",
                                                  "agent 还不会提议搬移频道：可以直接在频道设置里改分类")


@router.post("/api/app/organizations")
async def create_organization(request: Request):
    """一台服务只有一个家：这里不能再建新的空间。"""
    return _error(409, "organization.single_home", "这台服务只有一个家，不能再建新的空间")


@router.post("/api/app/feedback/files")
async def upload_feedback_file(request: Request):
    """反馈暂时只收文字：图片、视频请描述一下，或直接发给所有者。"""
    return _error(409, "feedback.files_unavailable", "反馈暂时只收文字，附件还不能上传")


@router.delete("/api/app/feedback/files/{file_id}")
async def drop_feedback_file(file_id: str, request: Request):
    return _error(404, "feedback.file_not_found", "没有这个附件")


@router.post("/api/app/feedback/offers/{message_id}")
async def feedback_offer(message_id: str, body: Dict[str, Any], request: Request):
    """agent 主动提出的反馈报告还没有：没有这条。"""
    return _error(404, "feedback.offer_not_found", "没有这条反馈提议")


# ── 零散的如实回答（2026-11-24）──────────────────────────────────────────────────

@router.post(_ORG + "/approvals/{approval_id}/cancel")
async def cancel_approval(org_id: str, approval_id: str, request: Request):
    """撤回一项提前批准、还没到时候执行的操作：这里没有提前批准（每次都当场问）。"""
    return _owner_gate(request, org_id) or _error(409, "approval.not_ahead", "这里没有提前批准的操作：每一项都在当时问你")


@router.post(_ORG + "/autonomy-proposals/{signal_id}")
async def decide_costly_proposal(org_id: str, signal_id: str, body: Dict[str, Any], request: Request):
    """agent 主动提出、没人要求的高花费工作：还没有这种提议。"""
    return _owner_gate(request, org_id) or _error(404, "autonomy_proposal.not_found", "没有这条提议")


@router.post(_ORG + "/routines/{routine_id}/continue")
async def continue_routine_usage(org_id: str, routine_id: str, request: Request):
    """额度快用完时暂停的那一次「现在接着做」：额度等待由额度设置决定，这里不跳过。"""
    return _owner_gate(request, org_id) or _error(409, "routine.continue_unavailable",
                                                  "不能跳过额度接着做：调高额度后它会自己接着做，或者停下再重新运行")


class _AgentStyle(BaseModel):
    model_config = ConfigDict(extra="allow")


@router.put(_ORG + "/me/agent-style")
async def set_agent_style(org_id: str, body: _AgentStyle, request: Request):
    """按个人改 agent 跟你说话的方式还没有：agent 的口吻在它自己的设置里（对所有人一样）。"""
    return _owner_gate(request, org_id, members_too=True) or _error(
        409, "agent_style.unavailable", "还不能按个人改 agent 说话的方式：口吻在 agent 自己的设置里")


@router.post(_ORG + "/vault/items/{item_id}/move")
async def move_vault_item(org_id: str, item_id: str, body: Dict[str, Any], request: Request):
    """保险库没有文件夹：没有可以移去的地方。"""
    return _owner_gate(request, org_id) or _error(409, "vault.no_folders", "保险库没有文件夹，条目不用移动")


class _EmailUndo(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    token: str = Field(min_length=1, max_length=200)


@router.post("/api/app/email-undo")
async def undo_email_change(body: _EmailUndo, request: Request):
    """「不是我改的」：从旧邮箱收到的通知点进来（可能没登录）。换回旧邮箱，所有登录都结束。"""
    from src.workflow.members.accounts import Damaged, EmailHeld, LinkGone, MemberAccounts
    if not member_auth.same_origin(request):
        return _error(403, "forbidden", "这里现在不能撤回")
    root = os.getcwd()
    try:
        _, member_id, restored = await asyncio.to_thread(MemberAccounts(root).undo_email, body.token)
        if member_id:
            await asyncio.to_thread(MemberRegistry(root).set_member_email, member_id, restored)
    except LinkGone as error:
        return _error(404, "email.undo_gone", str(error))
    except EmailHeld as error:
        return _error(409, "email.held", str(error))
    except (Damaged, OSError):
        return _error(503, "unavailable", "暂时撤回不了")
    return JSONResponse({"email": restored}, headers={"Cache-Control": "no-store"})


# ── 改登录邮箱（2026-11-24，主人「剩下的你也继续推进」）──────────────────────────
# 登录邮箱就是登录名。没发信：10 分钟内登录过就马上换。发信开着：先给现在的邮箱发验证码（或 10 分钟内登录过），
# 再给新邮箱发确认链接，打开了才换；换好以后旧邮箱收到「不是我改的」，点了就换回去、所有登录结束。

class _EmailChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    newEmail: str = Field(min_length=3, max_length=254)
    code: str | None = Field(default=None, max_length=16)


def _email_code_key(account_id: str) -> str:
    return "account-email:" + account_id


@router.post("/api/app/account/email")
async def change_login_email(body: _EmailChange, request: Request):
    from src.workflow.members import join_codes
    from src.workflow.members.accounts import Damaged, EmailHeld, MemberAccounts
    from src.workflow.members.registry import login_name

    root = os.getcwd()
    actor = member_auth.principal(request, root)
    if actor.is_owner:
        if not app_staff.owner_proof(request):
            return _error(403, "owner_proof_required", _NEEDS_OWNER)
        return _error(409, "account.owner_email", "所有者的登录由本机所有者认领管理，这里不能改")
    account = request.state.member_account
    try:
        address = login_name(body.newEmail)
    except ValueError:
        return _error(400, "account.invalid_email", "新邮箱地址无效")
    if address == account["email"]:
        return _error(400, "email.same", "和现在的登录邮箱一样")
    accounts = MemberAccounts(root)
    token = request.cookies.get(member_auth.MEMBER_COOKIE, "")
    try:
        if address != account["email"] and await asyncio.to_thread(accounts.account_with_email, address):
            return _error(409, "email.held", "这个邮箱已经是别的账号了")
        recent = await asyncio.to_thread(accounts.recently_signed_in, account["id"], token)
        if not join_codes.enabled():
            if not recent:
                return _error(403, "reauth.sign_in", "先用密码重新登录一次，10 分钟内回来改")
            await asyncio.to_thread(accounts.change_email, account["id"], address)
            if account.get("member"):
                await asyncio.to_thread(MemberRegistry(root).set_member_email, account["member"], address)
            return _no_store({"status": "changed", "email": address})
        if body.code:
            try:
                join_codes.CODES.check(_email_code_key(account["id"]), account["email"], body.code)
            except join_codes.CodeError as error:
                return _reauth_code_error(error)
        elif not recent:
            try:
                wait = await asyncio.to_thread(
                    join_codes.CODES.send_code, _email_code_key(account["id"]), account["email"], "更换登录邮箱的验证码",
                    "你在把这个家的登录邮箱换成别的地址，验证码是 {code}，10 分钟内有效。\n不是你在操作的话，改一下密码。")
            except join_codes.CodeError as error:
                return _reauth_code_error(error)
            except OSError:
                return _error(503, "mail_unavailable", "验证码没有发出去，请稍后再试")
            return _no_store({"status": "code", "email": account["email"], "resendIn": wait})
        link = await asyncio.to_thread(accounts.email_link, account["id"], address)
        origin = _invitation_origin(request)
        sent = await asyncio.to_thread(
            join_codes.CODES.mail, address, "确认新的登录邮箱",
            f"打开这个链接，把这个家的登录邮箱换成 {address}（24 小时内有效）：\n"
            f"{origin}/api/app/account/email/confirm?token={link}\n不是你在操作的话，忽略这封邮件就好。")
        if not sent:
            return _error(429, "reauth.code_wait", "发得太多了，请过一会儿再试")
    except EmailHeld as error:
        return _error(409, "email.held", str(error))
    except (Damaged, OSError, join_codes.CodeError):
        return _error(503, "unavailable", "暂时改不了登录邮箱")
    return _no_store({"status": "sent", "email": address})


@router.get("/api/app/account/email/confirm")
async def confirm_login_email(request: Request, token: str = Query(default="", max_length=200)):
    """新邮箱里的确认链接（不要求登录，凭链接里的令牌）。换好以后给旧邮箱发「不是我改的」。"""
    from fastapi.responses import RedirectResponse
    from src.workflow.members import join_codes
    from src.workflow.members.accounts import Damaged, EmailHeld, LinkGone, MemberAccounts
    root = os.getcwd()
    try:
        _, member_id, old, new, undo = await asyncio.to_thread(MemberAccounts(root).confirm_email, token)
        if member_id:
            await asyncio.to_thread(MemberRegistry(root).set_member_email, member_id, new)
    except (LinkGone, EmailHeld):
        return RedirectResponse("/?email=failed", status_code=303, headers={"Cache-Control": "no-store"})
    except (Damaged, OSError):
        return RedirectResponse("/?email=failed", status_code=303, headers={"Cache-Control": "no-store"})
    origin = _invitation_origin(request)
    try:
        await asyncio.to_thread(
            join_codes.CODES.mail, old, "你的登录邮箱换了",
            f"这个家的登录邮箱刚换成了 {new}。\n不是你改的话，7 天内打开这个链接换回来，所有登录都会结束：\n"
            f"{origin}/email-undo?token={undo}", notice=True)
    except (OSError, join_codes.CodeError):
        logging.getLogger(__name__).warning("旧邮箱的「邮箱换了」通知没有发出", exc_info=True)
    return RedirectResponse("/?email=changed", status_code=303, headers={"Cache-Control": "no-store"})


class _OnboardingStep(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    step: Literal["workspace", "context", "import", "agents", "computer", "ai", "team", "done"] | None = None
    finished: Literal[True] | None = None


@router.patch(_ORG + "/onboarding")
async def save_onboarding(org_id: str, body: _OnboardingStep, request: Request):
    """新建空间的引导：这个家只有一个、早已设好（本机安装走 /api/app/desktop-setup），没有要接着走的引导。
    照实回「已经完成」，带上主助理和当前的 /me；不另存一份不生效的进度。"""
    root = _root(org_id)
    who = _who(request, root)
    if who is None or not who.is_owner:
        return _error(403, "forbidden", "引导只有所有者能走")
    if not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", _NEEDS_OWNER)
    if (body.step is None) == (body.finished is None):
        return _error(400, "invalid", "要么给 step，要么给 finished: true")
    profile = await _current_profile(root, request, who)
    if isinstance(profile, JSONResponse):
        return profile
    return JSONResponse({"step": "done", "finished": True, "rightHandId": views.main_agent_id(root), "me": profile},
                        headers={"Cache-Control": "no-store"})


# ── GIF（GIPHY）──────────────────────────────────────────────────────────────
# 密钥只有所有者能看能改；搜索和取图给空间里所有人（成员也能在团队频道里发）。图片经本服务转发。

def _gif_error(error: "app_gifs.GifError") -> JSONResponse:
    return _error(error.status, error.code, str(error))


async def _gif_key_owner(request: Request):
    """第三方服务的 Key（GIPHY、语音）只有所有者能看能改，且要已认证的主人连接。"""
    root = os.getcwd()
    if not member_auth.principal(request, root).is_owner:
        return None, _error(403, "forbidden", "只有所有者能管第三方服务的 Key")
    if not views.owner_connection():
        return None, _error(403, "forbidden", "管第三方服务的 Key 需要已认证的主人连接")
    return root, None


@router.get("/api/app/gif-key")
async def gif_key(request: Request):
    root, refused = await _gif_key_owner(request)
    return refused or app_gifs.key_view(root)


@router.put("/api/app/gif-key")
async def save_gif_key(body: Dict[str, Any], request: Request):
    """先拿这个 Key 向 GIPHY 试一次，能用才存。"""
    root, refused = await _gif_key_owner(request)
    if refused:
        return refused
    if app_gifs.env_managed():
        return _error(409, "gif.env_key", f"这台服务用环境变量 {app_gifs.ENV_KEY} 的 Key，这里不能改")
    try:
        key = app_gifs.check_key_shape(body.get("apiKey"))
    except ValueError as error:
        return _error(400, "gif.key_invalid", str(error))
    try:
        await app_gifs.verify_key(key)
    except app_gifs.GifError as error:
        return _gif_error(error)
    await asyncio.to_thread(app_gifs.save_key, root, key)
    return app_gifs.key_view(root)


@router.delete("/api/app/gif-key")
async def remove_gif_key(request: Request):
    root, refused = await _gif_key_owner(request)
    if refused:
        return refused
    if app_gifs.env_managed():
        return _error(409, "gif.env_key", f"这台服务用环境变量 {app_gifs.ENV_KEY} 的 Key，这里不能改")
    await asyncio.to_thread(app_gifs.remove_key, root)
    return app_gifs.key_view(root)


def _gifs_on(root: str) -> bool:
    settings = views.settings(root)
    return bool(settings["gifsAvailable"] and settings["gifs"])


@router.get(_ORG + "/gifs")
async def search_gifs(org_id: str, request: Request, q: str = "", category: str = "", offset: int = 0,
                      limit: int = 24, lang: str = ""):
    root = _root(org_id)
    if _who(request, root) is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    if not _gifs_on(root):
        return _error(422, "gif.off", "这个空间没有开 GIF")
    try:
        return await app_gifs.search(root, q=q, category=category, offset=offset, limit=limit, lang=lang,
                                     rating=views.settings(root)["gifRating"])
    except app_gifs.GifError as error:
        return _gif_error(error)


@router.get(_ORG + "/gifs/{gif_id}/media")
async def gif_media(org_id: str, gif_id: str, request: Request, size: str = "full"):
    root = _root(org_id)
    if _who(request, root) is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    if not app_gifs.available(root):
        return _error(422, "gif.off", "这台服务还没有设置 GIPHY 的 API Key")
    try:
        content, kind = await app_gifs.media(root, gif_id, size)
    except app_gifs.GifError as error:
        return _gif_error(error)
    return Response(content=content, media_type=kind,
                    headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


async def _gif_for_message(org_id: str, wanted: Any):
    """界面只给 id；标题和尺寸重新向 GIPHY 查。GIF 关着回 422 message.media_off（合同）。"""
    root = _root(org_id)
    if not isinstance(wanted, dict) or wanted.get("kind") != "gif" or set(wanted) != {"kind", "id"} \
            or not isinstance(wanted.get("id"), str):
        return _error(422, "message.media_off", "这里只能发 GIF")
    if not _gifs_on(root):
        return _error(422, "message.media_off", "这个空间没有开 GIF")
    try:
        return await app_gifs.message_media(root, wanted["id"])
    except app_gifs.GifError as error:
        if error.status == 404:
            return _error(422, "message.media_not_found", "GIPHY 上没有这个 GIF")
        return _gif_error(error)


# ── 语音消息 ──────────────────────────────────────────────────────────────────
# 服务商和 Key 只有所有者能管。录音转成文字作为消息正文（给 agent 看），人看到的是语音条。
# 主助理私聊只有所有者能发（和发文字一样）；团队频道里所有能发言的人都能发。

_MAX_VOICE_BODY = app_voice.MAX_UPLOAD_BYTES + 64 * 1024


def _voice_error(error: "app_voice.VoiceError") -> JSONResponse:
    return _error(error.status, error.code, str(error))


@router.get("/api/app/speech-key")
async def speech_key(request: Request):
    root, refused = await _gif_key_owner(request)
    return refused or app_voice.key_view(root)


@router.put("/api/app/speech-key")
async def save_speech_key(body: Dict[str, Any], request: Request):
    """先拿这个 Key 向服务商试一次（不花钱的查询），能用才存。"""
    root, refused = await _gif_key_owner(request)
    if refused:
        return refused
    try:
        record = app_voice.check_input(body)
        await app_voice.verify(record)
    except app_voice.VoiceError as error:
        return _voice_error(error)
    await asyncio.to_thread(app_voice.save, root, record)
    return app_voice.key_view(root)


@router.delete("/api/app/speech-key")
async def remove_speech_key(request: Request):
    root, refused = await _gif_key_owner(request)
    if refused:
        return refused
    await asyncio.to_thread(app_voice.remove, root)
    return app_voice.key_view(root)


def _voice_on(root: str) -> bool:
    settings = views.settings(root)
    return bool(settings["voiceAvailable"] and settings["voiceMessages"])


@router.get(_ORG + "/voice/allowed")
async def voice_allowed(org_id: str, request: Request):
    root = _root(org_id)
    if _who(request, root) is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    return {"allowed": _voice_on(root)}


async def _upload_body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _MAX_VOICE_BODY:
        raise app_voice.VoiceError(413, "voice.too_large", "录音太大了")
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > _MAX_VOICE_BODY:
            raise app_voice.VoiceError(413, "voice.too_large", "录音太大了")
    return body


async def _can_read_team(root: str, who, channel_id: str) -> bool:
    return await asyncio.to_thread(lambda: app_channels.channels(root).can_read(who, channel_id))


@router.post(_ORG + "/channels/{channel_id}/voice")
async def send_voice(org_id: str, channel_id: str, request: Request):
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    if not _voice_on(root):
        return _error(422, "voice.off", "这个空间没有开语音消息")
    team = is_team_channel(channel_id)
    main = not team and _main_channel(org_id, channel_id) is not None
    if not team and not main:
        return _error(422, "voice.off_here", "这里不能发语音")
    if main:
        if not who.is_owner:
            return _error(403, "forbidden", "只有所有者能和主助理说话")
        if not views.model_connected():
            return _error(409, "no_ai", "还没有连接模型，没法回复")
    else:
        if who.is_owner and not app_staff.owner_proof(request):
            return _error(403, "owner_proof_required", "团队频道需要已认证的同源会话")
        if not await _can_read_team(root, who, channel_id):
            return _error(404, "not_found", "没有这个频道")
    try:
        fields, upload = app_voice.parse_form(request.headers.get("content-type", ""), await _upload_body(request))
        wanted = app_voice.check_fields(fields)
        if upload is None or not upload[0]:
            raise app_voice.VoiceError(400, "invalid", "没有录音文件")
        kind = app_voice.sniff(upload[0])
        if kind is None:
            raise app_voice.VoiceError(400, "voice.format", "认不出这个录音的格式")
    except app_voice.VoiceError as error:
        return _voice_error(error)
    if main and wanted["threadId"]:
        return _error(400, "threads_unsupported", "这里还不支持在话题里回复")
    again = await asyncio.to_thread(app_voice.find_clip, root, channel=channel_id, author=who.id,
                                    client=wanted["clientId"])
    if team and wanted["threadId"]:
        check = await _team_call(request, org_id, lambda service, actor: service.check_thread_voice(
            actor, channel_id, wanted["threadId"], wanted["replyTo"], wanted["clientId"], again["id"] if again else None))
        if isinstance(check, JSONResponse):
            return check
    if again is not None:                                   # 界面重试：已经发出去了
        return {"clipId": again["id"]}
    record = await asyncio.to_thread(app_voice.store_clip, root, upload[0], kind, channel=channel_id,
                                     scope="team" if team else "main", author=who.id, client=wanted["clientId"])
    no_words = False
    try:
        text = await app_voice.transcribe(root, upload[0], kind, wanted["language"])
    except app_voice.VoiceError as error:
        if not team:                                        # 主助理听不到就没法回答：如实告诉发的人
            await asyncio.to_thread(app_voice.drop_clip, root, record["id"])
            return _voice_error(error)
        text, no_words = "", True                           # 团队频道里的人照样能听
    if not text and not no_words:
        await asyncio.to_thread(app_voice.drop_clip, root, record["id"])
        return _error(422, "voice.silent", "没听到说话")
    meta = app_voice.voice_meta(record, wanted, no_words=no_words)
    if team:
        answer = await _team_call(request, org_id, lambda service, actor: service.post(
            actor, channel_id, text, client_id=wanted["clientId"], thread=wanted["threadId"],
            reply_to=wanted["replyTo"], media={"kind": "voice", **meta}))
        if isinstance(answer, JSONResponse):
            await asyncio.to_thread(app_voice.drop_clip, root, record["id"])
            return answer
        return {"clipId": record["id"]}
    import uuid
    rid = uuid.uuid4().hex
    await asyncio.to_thread(app_voice.remember_main_voice, root, views.message_id(rid, "user"), meta)
    try:
        from src.web.routers.computers import (
            has_computer_owner_proof, has_local_owner_browser_session, has_owner_browser_session,
        )
        await (await main_chat_bridge(root)).post(
            text, wanted["clientId"], rid=rid, file_owner=has_computer_owner_proof(request),
            local_browser=has_local_owner_browser_session(request),
            paired_browser=has_owner_browser_session(request))
    except NoModel:
        return _error(409, "no_ai", "还没有连接模型，没法回复")
    return {"clipId": record["id"]}


@router.get(_ORG + "/voice/{clip_id}/audio")
async def voice_audio(org_id: str, clip_id: str, request: Request):
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    record = await asyncio.to_thread(app_voice.clip, root, clip_id)
    if record is None:
        return _error(404, "not_found", "没有这段录音")
    if record.get("scope") == "main":
        allowed = who.is_owner
    else:
        allowed = await _can_read_team(root, who, str(record.get("channel") or ""))
    if not allowed:
        return _error(404, "not_found", "没有这段录音")
    if app_voice.expired(record):
        return _error(410, "voice.expired", "这段录音已经过期")
    content = await asyncio.to_thread(app_voice.audio, root, record)
    if content is None:
        return _error(410, "voice.expired", "这段录音已经不在了")
    return Response(content=content, media_type=record["contentType"],
                    headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


# ── 任务看板 ──────────────────────────────────────────────────────────────────
# 规矩在 web/app_board.py：所有者全部；成员看可见范围里的、凭「编辑资料」新建和编辑、只删自己建的。
# 交给 agent 走现有派活入口，不另起执行器。

def _issue_card(root, who, issue):
    issue_id = issue["id"]
    return {**issue, "conversation": app_issue_chat.IssueChat(root).cards(who, {issue_id}).get(issue_id)}


async def _board(org_id: str, request: Request, action, *, edit: bool = False, dispatch: bool = False,
                 card: bool = False):
    from src.workflow.board.store import BoardError, NotFound
    from src.workflow.members.registry import Damaged, Refused
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    if edit and not app_board.can_edit(root, who):
        return _error(403, "forbidden", "你没有编辑看板的权限")
    try:
        if dispatch:
            await _dispatch_ready(root)
            result = action(root, who)
        else:
            result = await asyncio.to_thread(action, root, who)
        if card:
            # Card details may carry task conversation summaries. Apply the same owner proof as chat.
            if not who.is_owner or app_staff.owner_proof(request):
                try:
                    result = await asyncio.to_thread(_issue_card, root, who, result)
                except (Damaged, OSError):
                    if not edit:
                        raise
                    # The board mutation has already completed. Retrying it cannot repair a failed summary read.
                    logging.getLogger(__name__).warning("看板已更新，本次响应省略任务对话摘要")
                    result.pop("conversation", None)
                    return JSONResponse(result, headers={"Cache-Control": "no-store"})
                if result["conversation"] is not None:
                    return JSONResponse(result, headers={"Cache-Control": "no-store"})
        return result
    except (NotFound, LookupError) as error:
        return _error(404, "not_found", str(error))
    except (Refused, PermissionError) as error:
        return _error(403, "forbidden", str(error))
    except (Damaged, OSError):
        return _error(409, "unavailable", "看板记录读写失败")
    except (BoardError, ValueError) as error:
        return _error(400, "invalid", str(error))


@router.get(_ORG + "/issues")
async def list_issues(org_id: str, request: Request, q: str = "", projectId: str = ""):  # noqa: N803
    def listing(root, who):
        rows = app_board.listing(root, who, q=q, project_id=projectId)
        if not who.is_owner or app_staff.owner_proof(request):
            summaries = app_issue_chat.IssueChat(root).cards(who, {row["id"] for row in rows})
            for row in rows:
                row["conversation"] = summaries.get(row["id"])
            if summaries:
                return JSONResponse({"issues": rows}, headers={"Cache-Control": "no-store"})
        return {"issues": rows}
    return await _board(org_id, request, listing)


@router.get(_ORG + "/issues/by-number/{number}")
async def issue_by_number(org_id: str, number: int, request: Request):
    return await _board(org_id, request, lambda root, who: app_board.get(root, who, number), card=True)


@router.post(_ORG + "/issues")
async def create_issue(org_id: str, body: Dict[str, Any], request: Request):
    return await _board(org_id, request, lambda root, who: app_board.create(root, who, body), edit=True)


@router.patch(_ORG + "/issues/{issue_id}")
async def update_issue(org_id: str, issue_id: str, body: Dict[str, Any], request: Request):
    return await _board(org_id, request, lambda root, who: app_board.update(root, who, issue_id, body),
                        edit=True, card=True)


@router.delete(_ORG + "/issues/{issue_id}", status_code=204)
async def delete_issue(org_id: str, issue_id: str, request: Request):
    result = await _board(org_id, request, lambda root, who: app_board.delete(root, who, issue_id), edit=True)
    return result if isinstance(result, JSONResponse) else Response(status_code=204)


@router.post(_ORG + "/issues/{issue_id}/share")
async def share_issue(org_id: str, issue_id: str, request: Request):
    return await _board(org_id, request, lambda root, who: app_board.set_audience(
        root, who, issue_id, {"mode": "everyone"}), edit=True, card=True)


@router.put(_ORG + "/issues/{issue_id}/audience")
async def set_issue_audience(org_id: str, issue_id: str, body: Dict[str, Any], request: Request):
    return await _board(org_id, request, lambda root, who: app_board.set_audience(root, who, issue_id, body),
                        edit=True, card=True)


@router.put(_ORG + "/issues/{issue_id}/criteria/{criterion_id}")
async def set_issue_criterion(org_id: str, issue_id: str, criterion_id: str, body: Dict[str, Any],
                              request: Request):
    return await _board(org_id, request, lambda root, who: app_board.set_criterion(
        root, who, issue_id, criterion_id, body.get("done")), edit=True, card=True)


@router.get(_ORG + "/issues/{issue_id}/activity")
async def issue_activity(org_id: str, issue_id: str, request: Request):
    return await _board(org_id, request, lambda root, who: {"activity": app_board.activity(root, who, issue_id)})


@router.post(_ORG + "/issues/{issue_id}/comments")
async def comment_issue(org_id: str, issue_id: str, body: Dict[str, Any], request: Request):
    return await _board(org_id, request, lambda root, who: app_board.comment(root, who, issue_id, body), edit=True)


class _OpenIssueConversation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


@router.post(_ORG + "/issues/{issue_id}/conversation")
async def open_issue_conversation(org_id: str, issue_id: str, request: Request,
                                  body: _OpenIssueConversation | None = None):
    return await _issue_chat_call(request, org_id, lambda chat, actor: chat.open(actor, issue_id))


@router.post(_ORG + "/issues/{issue_id}/ask-agent")
async def ask_agent_on_issue(org_id: str, issue_id: str, body: Dict[str, Any], request: Request):
    proof = app_staff.owner_proof(request)
    return await _board(org_id, request, lambda root, who: app_board.ask_agent(
        root, who, issue_id, body.get("agentId"), owner_proof=proof, staff_of=_staff_of),
                        edit=True, dispatch=True, card=True)


@router.get(_ORG + "/issue-columns")
async def issue_columns(org_id: str, request: Request):
    from src.workflow.board.store import Board
    return await _board(org_id, request, lambda root, who: {"columns": Board(root).columns()})


@router.post(_ORG + "/issue-columns")
async def add_issue_column(org_id: str, body: Dict[str, Any], request: Request):
    from src.workflow.board.store import Board
    return await _board(org_id, request, lambda root, who: {"columns": Board(root).add_column(body)}, edit=True)


@router.patch(_ORG + "/issue-columns/{column_id}")
async def update_issue_column(org_id: str, column_id: str, body: Dict[str, Any], request: Request):
    from src.workflow.board.store import Board
    return await _board(org_id, request, lambda root, who: {"columns": Board(root).update_column(column_id, body)},
                        edit=True)


@router.delete(_ORG + "/issue-columns/{column_id}")
async def delete_issue_column(org_id: str, column_id: str, request: Request, moveTo: str = ""):  # noqa: N803
    from src.workflow.board.store import Board
    return await _board(org_id, request, lambda root, who: {
        "columns": Board(root).delete_column(column_id, moveTo or None)}, edit=True)


@router.get(_ORG + "/issue-projects")
async def issue_projects(org_id: str, request: Request):
    def read(root, who):
        from src.workflow.board.store import Board
        issues = [issue for issue in Board(root).issues() if app_board.can_see(issue, who)]
        open_ones = [issue for issue in issues if issue["status"] not in ("done", "canceled")]
        projects = [{**project, "open": sum(1 for issue in open_ones if issue["projectId"] == project["id"])}
                    for project in Board(root).projects()]
        return {"projects": projects, "unfiled": sum(1 for issue in open_ones if not issue["projectId"])}
    return await _board(org_id, request, read)


@router.post(_ORG + "/issue-projects")
async def create_issue_project(org_id: str, body: Dict[str, Any], request: Request):
    from src.workflow.board.store import Board
    return await _board(org_id, request, lambda root, who: {**Board(root).create_project(body), "open": 0},
                        edit=True)


@router.patch(_ORG + "/issue-projects/{project_id}")
async def update_issue_project(org_id: str, project_id: str, body: Dict[str, Any], request: Request):
    from src.workflow.board.store import Board
    return await _board(org_id, request, lambda root, who: Board(root).update_project(project_id, body), edit=True)


@router.delete(_ORG + "/issue-projects/{project_id}", status_code=204)
async def delete_issue_project(org_id: str, project_id: str, request: Request):
    from src.workflow.board.store import Board
    result = await _board(org_id, request, lambda root, who: Board(root).delete_project(project_id), edit=True)
    return result if isinstance(result, JSONResponse) else Response(status_code=204)


@router.get(_ORG + "/me/issues-view")
async def issues_view(org_id: str, request: Request):
    return await _sidebar(org_id, request, lambda root, who: {"view": app_prefs.issues_view(root, who)})


@router.put(_ORG + "/me/issues-view")
async def set_issues_view(org_id: str, body: Dict[str, Any], request: Request):
    if set(body) != {"view"}:
        return _error(400, "invalid", "要给 view")
    return await _sidebar(org_id, request, lambda root, who: {
        "view": app_prefs.set_issues_view(root, who, body["view"])})


# ── 每个人自己的侧栏 ──────────────────────────────────────────────────────────
# 所有者和成员各存各的（app_prefs），只是界面摆放，不涉及权限。账号还没加入或已被移除的成员没有侧栏可存。

async def _sidebar(org_id: str, request: Request, change):
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    try:
        return await asyncio.to_thread(change, root, who.id)
    except (ValueError, OSError) as error:
        return _error(400, "invalid", str(error))



class _MyActivity(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    show: bool


class _MyStart(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    page: Literal["home", "last", "channel"]
    channelId: str | None = Field(default=None, max_length=128)


async def _change_my_display(org_id, request, action):
    root = _root(org_id)
    actor = _who(request, root)
    if actor is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    if actor.is_owner and not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", "请登录后更改界面偏好")

    def update():
        if actor.is_member and actor.id not in MemberRegistry(root).snapshot()["members"]:
            return _error(403, "forbidden", _NOT_MEMBER)
        return action(root, actor)

    try:
        return await asyncio.to_thread(update)
    except (ValueError, OSError) as error:
        return _error(400, "invalid", str(error))


@router.put(_ORG + "/me/activity")
async def set_my_activity(org_id: str, body: _MyActivity, request: Request):
    return await _change_my_display(org_id, request, lambda root, actor:
                                    app_prefs.set_show_activity(root, actor.id, body.show))


@router.put(_ORG + "/me/start")
async def set_my_start(org_id: str, body: _MyStart, request: Request):
    if (body.page == "channel") != bool(body.channelId) or body.channelId == "":
        return _error(400, "invalid", "只有频道启动页需要填写频道")
    def change(root, actor):
        if body.channelId and not app_channels.channels(root).can_read(actor, body.channelId):
            return _error(404, "not_found", "没有你能打开的这个频道")
        return app_prefs.set_start(root, actor.id, body.page, body.channelId)

    return await _change_my_display(org_id, request, change)


@router.put(_ORG + "/me/chat")
async def set_my_chat_look(org_id: str, body: Dict[str, Any], request: Request):
    def change(root, actor):
        if "feedbackAuto" in body:
            return _error(409, "unavailable", "自动发送反馈暂不可用")
        return app_prefs.set_chat_look(root, actor.id, body)

    return await _change_my_display(org_id, request, change)


@router.get(_ORG + "/me/sidebar")
async def sidebar_items(org_id: str, request: Request):
    return await _sidebar(org_id, request, lambda root, who: {"items": app_prefs.sidebar_items(root, who)})


@router.patch(_ORG + "/me/sidebar")
async def move_sidebar_item(org_id: str, body: Dict[str, Any], request: Request):
    if set(body) != {"item", "place"}:
        return _error(400, "invalid", "要给 item 和 place")
    return await _sidebar(org_id, request, lambda root, who: {
        "items": app_prefs.set_sidebar_item(root, who, body["item"], body["place"])})


@router.put(_ORG + "/me/sidebar")
async def set_sidebar_items(org_id: str, body: Dict[str, Any], request: Request):
    if set(body) != {"items"}:
        return _error(400, "invalid", "要给 items")
    return await _sidebar(org_id, request, lambda root, who: {
        "items": app_prefs.set_sidebar_items(root, who, body["items"])})


@router.get(_ORG + "/me/sidebar/order")
async def sidebar_order(org_id: str, request: Request):
    return await _sidebar(org_id, request, lambda root, who: {"order": app_prefs.sidebar_order(root, who)})


@router.put(_ORG + "/me/sidebar/order")
async def set_sidebar_order(org_id: str, body: Dict[str, Any], request: Request):
    if set(body) != {"order"}:
        return _error(400, "invalid", "要给 order")
    return await _sidebar(org_id, request, lambda root, who: {
        "order": app_prefs.set_sidebar_order(root, who, body["order"])})


@router.get(_ORG + "/me/sidebar/tools")
async def sidebar_tools(org_id: str, request: Request):
    return await _sidebar(org_id, request, lambda root, who: {"order": app_prefs.sidebar_tools(root, who)})


@router.put(_ORG + "/me/sidebar/tools")
async def set_sidebar_tools(org_id: str, body: Dict[str, Any], request: Request):
    if set(body) != {"order"}:
        return _error(400, "invalid", "要给 order")
    return await _sidebar(org_id, request, lambda root, who: {
        "order": app_prefs.set_sidebar_tools(root, who, body["order"])})


@router.get(_ORG + "/me/sidebar/view")
async def sidebar_view(org_id: str, request: Request):
    return await _sidebar(org_id, request, lambda root, who: {"view": app_prefs.sidebar_view(root, who)})


@router.put(_ORG + "/me/sidebar/view")
async def set_sidebar_view(org_id: str, body: Dict[str, Any], request: Request):
    if set(body) != {"view"}:
        return _error(400, "invalid", "要给 view")
    return await _sidebar(org_id, request, lambda root, who: {
        "view": app_prefs.set_sidebar_view(root, who, body["view"])})


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


_INBOX = _ORG + "/notifications"


class _AgentCreationAsk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    what: Literal["create_agents"]


class _ChannelAgentAsk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    what: Literal["channel_agent"]
    channelId: str = Field(min_length=1, max_length=128)
    agentId: str = Field(min_length=1, max_length=128)


class _WebhookAsk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    what: Literal["webhook"]
    provider: Literal["github", "sentry"]


@router.post(_ORG + "/admin-asks")
async def ask_owner(org_id: str, body: _AgentCreationAsk | _ChannelAgentAsk | _WebhookAsk, request: Request):
    root = _root(org_id)
    who = _who(request, root)
    if who is None or not who.is_member or not views.owner_connection():
        return _error(403, "forbidden", "只有当前在册成员能向所有者发送申请")
    if isinstance(body, _WebhookAsk):
        # 服务级 webhook 这里没有，所有者也接不了：不发一条做不到的申请
        return _error(409, "webhooks.unavailable", "这里还没有按 GitHub / Sentry 事件触发的例行工作，所有者也接不了")

    def submit():
        if isinstance(body, _AgentCreationAsk):
            MemberRegistry(root).request_agent_creation(who)
            return None
        if not is_team_channel(body.channelId):
            return _error(404, "not_found", "没有这个频道")
        if app_members.member_staff(root, who, body.agentId) is None:
            return _error(404, "not_found", "没有这个 agent")
        staff, refused = _channel_agent(root, body.agentId)
        if refused is not None:
            return refused
        try:
            app_channels.channels(root).request_agent(who, body.channelId, staff)
        except ChannelAgentAskConflict as error:
            return _error(409, "channel_agent_ask.conflict", str(error))
        return None

    result = await _members_call(submit, write=True)
    return result if isinstance(result, JSONResponse) else {"sent": 1}


class _ReadNotice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    read: StrictBool


class _DoneNotice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    done: StrictBool


class _SnoozeNotice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    until: str | None


class _InboxFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filter: app_notice_inbox.NoticeFilter = "all"


async def _notifications_call(request: Request, org_id: str, action, *, write=False):
    from src.utils.home_maintenance import HomeMaintenanceError

    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner:
        return _error(403, "forbidden", "这些通知属于所有者")
    if write and (not views.owner_connection() or not app_staff.owner_proof(request)):
        return _error(403, "owner_proof_required", "更新通知需要已认证的同源会话")
    try:
        return await asyncio.to_thread(action, root)
    except app_notice_inbox.NoticeMissing as error:
        return _error(404, "not_found", str(error))
    except (app_notice_inbox.InboxUnavailable, HomeMaintenanceError, OSError):
        return _error(409, "unavailable", "无法读取或保存通知状态")
    except ValueError as error:
        return _error(400, "invalid", str(error))


class _NoticePreferenceChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    category: PushCategory
    on: bool


async def _notification_preferences(
    org_id: str, request: Request, response: Response, change: _NoticePreferenceChange | None = None,
):
    from src.gateway import mobile_push
    from src.gateway.mobile_devices import MobileDevices, MobileStoreError

    def refused(status: int, code: str, message: str) -> JSONResponse:
        error = _error(status, code, message)
        error.headers["Cache-Control"] = "no-store"
        return error

    root = _root(org_id)
    who = _who(request, root)
    response.headers["Cache-Control"] = "no-store"
    if who is None or (who.is_owner and not app_staff.owner_proof(request)):
        return refused(403, "forbidden", "通知设置需要已认证的空间成员")
    if change is not None and not who.is_owner:
        return refused(403, "forbidden", "只有所有者能更新手机通知设置")
    if not who.is_owner or not mobile_push.configured():
        if change is not None:
            return refused(409, "unavailable", "此工作区尚未配置手机推送")
        return {"categories": [], "phones": 0}

    def snapshot():
        store = MobileDevices(root)
        if change is None:
            phones = len(store.push_targets())
            prefs = store.push_preferences()
        else:
            with home_maintenance(root):
                phones = len(store.push_targets())
                prefs = store.set_push_preference(change.category, change.on)
        return {"categories": [{"id": name, "on": enabled} for name, enabled in prefs.items()],
                "phones": phones}

    try:
        return await asyncio.to_thread(snapshot)
    except (MobileStoreError, HomeMaintenanceError, OSError):
        message = "无法读取手机通知登记" if change is None else "无法保存手机通知设置"
        return refused(409, "unavailable", message)


@router.get(_ORG + "/notification-prefs")
async def notification_preferences(org_id: str, request: Request, response: Response):
    return await _notification_preferences(org_id, request, response)


@router.put(_ORG + "/notification-prefs")
async def set_notification_preference(org_id: str, body: _NoticePreferenceChange, request: Request, response: Response):
    return await _notification_preferences(org_id, request, response, body)


@router.get(_INBOX)
async def notifications(
    org_id: str,
    request: Request,
    filter_name: app_notice_inbox.NoticeFilter | Literal[""] = Query("all", alias="filter"),
    cursor: int = Query(0, ge=0),
    limit: int = 50,
):
    return await _notifications_call(request, org_id, lambda root:
        app_notice_inbox.page(root, filter_name or "all", cursor, max(1, min(limit, 100))))


@router.get(_INBOX + "/count")
async def notifications_count(org_id: str, request: Request):
    return await _notifications_call(request, org_id, app_notice_inbox.count)


@router.post(_INBOX + "/{notice_id}/read")
async def read_notice(org_id: str, notice_id: str, body: _ReadNotice, request: Request):
    return await _notifications_call(request, org_id, lambda root:
        app_notice_inbox.set_status(root, notice_id, "read", body.read), write=True)


@router.post(_INBOX + "/{notice_id}/done")
async def done_notice(org_id: str, notice_id: str, body: _DoneNotice, request: Request):
    return await _notifications_call(request, org_id, lambda root:
        app_notice_inbox.set_status(root, notice_id, "done", body.done), write=True)


@router.post(_INBOX + "/{notice_id}/snooze")
async def snooze_notice(org_id: str, notice_id: str, body: _SnoozeNotice, request: Request):
    return await _notifications_call(request, org_id, lambda root:
        app_notice_inbox.set_status(root, notice_id, "snooze", app_notice_inbox.snooze_time(body.until)), write=True)


@router.post(_INBOX + "/read-all")
async def read_all_notices(org_id: str, body: _InboxFilter, request: Request):
    return await _notifications_call(request, org_id, lambda root:
        app_notice_inbox.clear(root, body.filter, done=False), write=True)


@router.post(_INBOX + "/done-all")
async def done_all_notices(org_id: str, body: _InboxFilter, request: Request):
    return await _notifications_call(request, org_id, lambda root:
        app_notice_inbox.clear(root, body.filter, done=True), write=True)


@router.get(_ORG + "/runs")
async def runs(org_id: str, agentId: str = "", limit: int = 50):  # noqa: N803 —— 界面的查询参数名
    root = _root(org_id)
    return _listing(lambda: {"runs": views.runs(root, agent_id=agentId, limit=limit)})


@router.get(_ORG + "/runs/{run_id}")
async def run_detail(org_id: str, run_id: str):
    """一件事的详情。专员的过程只留在任务台账的进度尾部（主人在「输出」里看），这里没有逐步记录，``steps`` 为空。"""
    root = _root(org_id)

    def read():
        row = next((item for item in views.runs(root, limit=200) if item["id"] == run_id), None)
        return None if row is None else {"run": row, "steps": []}
    result = await asyncio.to_thread(read)
    return result if result is not None else _error(404, "not_found", "没有这件事")


@router.get(_ORG + "/audit")
async def audit(org_id: str, request: Request, actorType: str = "", actorId: str = "",  # noqa: N803 —— 界面的查询参数名
                via: str = "", outcome: str = "", waiting: str = "", kind: str = "", before: str = "",
                limit: int | None = None):
    """审计页：共享审计台账的投影，只给所有者（参数里有主人对话里的路径和命令）。"""
    from src.web import app_audit
    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner or not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", "查看审计记录需要已认证的所有者")

    def read():
        me = views.me(root)["user"]
        main = views.main_agent_id(root)
        everyone = views.agents(root)
        agent = next((item for item in everyone if item["id"] == main), {})
        return app_audit.page(root, owner={"id": me["id"], "name": me["name"]},
                              agent={"id": main, "name": agent.get("name")}, actor_type=actorType,
                              agents={item["handle"]: {"id": item["id"], "name": item["name"]} for item in everyone},
                              actor_id=actorId, via=via, outcome=outcome, waiting=waiting == "true", kind=kind,
                              before=before, limit=limit)
    try:
        result = await asyncio.to_thread(read)
    except app_audit.AuditQueryError as error:
        return _error(400, "invalid", str(error))
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get(_ORG + "/search")
async def search(org_id: str, request: Request, q: str = "", kinds: str = "", limit: int | None = None):
    """搜索：团队频道消息与任务卡片，各按调用人能看到的范围（见 app_search 的说明）。"""
    from src.web import app_search
    filters = {key: request.query_params.get(key, "") for key in (
        "in", "fromUsers", "fromAgents", "mentionsUsers", "mentionsAgents", "has", "author", "pinned", "after",
        "before", "sort")}
    try:
        parsed = app_search.parse(q=q, kinds=kinds, limit=limit, **filters)
    except app_search.SearchQueryError as error:
        return _error(400, "invalid", str(error))
    root = _root(org_id)
    result = await _team_call(request, org_id, lambda service, actor:
                              {"hits": app_search.query(root, service, actor, parsed)})
    return JSONResponse(result, headers={"Cache-Control": "no-store"}) if isinstance(result, dict) else result


async def _question_reply(request: Request, org_id: str, question_id: str, answer):
    """专员问的问题：只有被问的那个人能回答或跳过；回答后按返工路径接着做下一轮。"""
    from src.workflow.staff import questions
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    if who.is_owner and not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", _NEEDS_OWNER)

    def run():
        task, item = questions.respond(root, question_id, None if who.is_owner else who.id, answer)
        view = task.to_dict()
        member = str(task.collaboration["dispatch"]["member"])
        carrier = views.message_id(task.id, "assistant", item["round"])
        return views.staff_question(root, member, view, item, carrier)
    try:
        await _dispatch_ready(root)
        return run()          # 接着做会把事交进执行池，只能在事件循环线程里登记
    except questions.QuestionMissing as error:
        return _error(404, "not_found", str(error))
    except questions.QuestionError as error:
        ended = str(error) in {"这个问题已经回答过了", "agent 已经不在等这个回答"}
        return _error(409 if ended else 400, "question.ended" if ended else "invalid", str(error))
    except (ValueError, OSError) as error:
        return _error(409, "staff_unavailable", str(error))


@router.post(_ORG + "/questions/{question_id}/answer")
async def answer_question(org_id: str, question_id: str, request: Request):
    body = await _body(request)
    value = body.get("choice", body.get("text")) if isinstance(body, dict) else None
    if not isinstance(value, str):
        return _error(400, "invalid", "回答要是文字")
    return await _question_reply(request, org_id, question_id, value)


@router.post(_ORG + "/questions/{question_id}/dismiss")
async def dismiss_question(org_id: str, question_id: str, request: Request):
    return await _question_reply(request, org_id, question_id, None)


# ── 新建 agent 页面要的几项 ───────────────────────────────────────────────────

@router.get(_ORG + "/agent-setup")
async def new_agent_setup(org_id: str):
    """新 agent 从哪套 AI 设置开始：这里不存上一次的设置，新 agent 都用默认模型，所以是 null。"""
    _root(org_id)
    return {"setup": None}


@router.post(_ORG + "/agent-setup")
async def apply_agent_setup(org_id: str):
    """给一批 agent 换 AI：和 agent 的「大脑」页一样，换模型要经提议核对，这里不直接改。"""
    _root(org_id)
    return _error(409, "agent_setup.by_proposal", "给 agent 换模型要经提议、在审批里批准，请让助理提议")


@router.get(_ORG + "/credentials/platform")
async def platform_key(org_id: str):
    """平台有没有现成的模型可用（不用自己填 Key）：就是这台服务有没有接上模型。"""
    _root(org_id)
    return {"available": bool(views.model_connected())}


@router.post(_ORG + "/agents/{agent_id}/audience/mail-loss")
async def agent_mail_loss(org_id: str, agent_id: str):
    """改 agent 服务谁时会失去哪些人的邮箱：这里 agent 不接邮箱，所以没有。"""
    if _agent(org_id, agent_id) is None:
        return _error(404, "not_found", "没有这个角色")
    return {"mailboxes": []}


# 「右手代批」：新版界面里是主助理替人处理别的 agent 提的新建 agent、改 agent 设置的提议。
# 这里会提这类提议的只有主助理自己（专员不提议），代批就等于助理批自己的提议，违反「助理不能自己批」
# （2026-11-22 放开提议时定的）。所以如实是关着的，打开回 409 并说明原因；等专员也能提议再接。
_RIGHT_HAND_OFF = "现在会提议的只有助理自己，让它代批就是自己批自己的提议，所以不能打开"
_RIGHT_HAND_FIELDS = {"enabled", "newAgents", "agentChanges", "budgetCapNanoUsd", "guidance"}


def _right_hand(root: str, who) -> Dict[str, Any]:
    main = views.main_agent_id(root)
    name = next((a["name"] for a in views.agents(root) if a["id"] == main), None)
    return {"enabled": False, "newAgents": False, "agentChanges": False, "budgetCapNanoUsd": 0, "guidance": "",
            "pausedAt": None, "rightHand": {"id": main, "name": name} if who.is_owner else None,
            "mayDecide": {"newAgents": who.is_owner, "agentChanges": who.is_owner},
            "caps": {"decisionsPerDay": 0, "newAgentsPerDay": 0, "monthNanoUsd": 0},
            "used": {"decisionsToday": 0, "newAgentsToday": 0, "monthNanoUsd": 0}}


@router.get(_ORG + "/me/right-hand-approvals")
async def right_hand_approvals(org_id: str, request: Request):
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    return await asyncio.to_thread(_right_hand, root, who)


@router.put(_ORG + "/me/right-hand-approvals")
async def set_right_hand_approvals(org_id: str, request: Request):
    """只接受「保持关着」；要打开的如实拒绝，不存一份不生效的设置。"""
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    body = await _body(request)
    if not body or set(body) - _RIGHT_HAND_FIELDS:
        return _error(400, "invalid", "只能改开关、两类提议、预算上限和说明")
    current = await asyncio.to_thread(_right_hand, root, who)
    if any(body[key] != current[key] for key in body):
        return _error(409, "right_hand.unavailable", _RIGHT_HAND_OFF)
    return current


@router.post(_ORG + "/right-hand-decisions/{decision_id}/undo", status_code=204)
async def undo_right_hand(org_id: str, decision_id: str):
    _root(org_id)
    return _error(404, "not_found", "没有这个代批决定（代批没有打开过）")


# ── agent 详情页的几项：如实给现在的状态 ─────────────────────────────────────

_GROUPS_BY_PROPOSAL = "agent 分组就是部门；部门的调整要经提议、在审批里批准，请让助理提议"


@router.get(_ORG + "/agent-groups")
async def agent_groups(org_id: str, request: Request):
    """agent 分组：就是已登记的部门（只读）。"""
    root = _root(org_id)
    if _who(request, root) is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    try:
        return {"groups": await asyncio.to_thread(views.agent_groups, root)}
    except (ValueError, OSError) as error:
        return _error(409, "unavailable", str(error))


@router.post(_ORG + "/agent-groups")
async def create_agent_group(org_id: str):
    _root(org_id)
    return _error(409, "agent_group.by_proposal", _GROUPS_BY_PROPOSAL)


@router.patch(_ORG + "/agent-groups/{group_id}")
async def update_agent_group(org_id: str, group_id: str):
    _root(org_id)
    return _error(409, "agent_group.by_proposal", _GROUPS_BY_PROPOSAL)


@router.delete(_ORG + "/agent-groups/{group_id}")
async def remove_agent_group(org_id: str, group_id: str):
    _root(org_id)
    return _error(409, "agent_group.by_proposal", _GROUPS_BY_PROPOSAL)


@router.put(_ORG + "/agents/{agent_id}/group")
async def move_agent_group(org_id: str, agent_id: str):
    if _agent(org_id, agent_id) is None:
        return _error(404, "not_found", "没有这个角色")
    return _error(409, "agent_group.by_proposal", _GROUPS_BY_PROPOSAL)


@router.get(_ORG + "/agents/{agent_id}/approval-rules")
async def approval_rules(org_id: str, agent_id: str):
    """「以后都允许」的规则：这里每次批准都只批这一次（approve 拒绝 always），所以没有规则。"""
    if _agent(org_id, agent_id) is None:
        return _error(404, "not_found", "没有这个角色")
    return {"rules": []}


@router.delete(_ORG + "/agents/{agent_id}/approval-rules/{rule_id}", status_code=204)
async def remove_approval_rule(org_id: str, agent_id: str, rule_id: str):
    if _agent(org_id, agent_id) is None:
        return _error(404, "not_found", "没有这个角色")
    return _error(404, "not_found", "没有这条规则")


@router.get(_ORG + "/agents/{agent_id}/learned-skills")
async def learned_skills(org_id: str, agent_id: str):
    """agent 自己学会的技能：这里 agent 不自己写技能（skill-settings 的 agentsImprove 固定关着），所以是空的。"""
    if _agent(org_id, agent_id) is None:
        return _error(404, "not_found", "没有这个角色")
    return {"skills": []}


@router.get(_ORG + "/agents/{agent_id}/channels")
async def agent_channels(org_id: str, agent_id: str, request: Request):
    root = _root(org_id)
    if agent_id == views.main_agent_id(root):
        return {"channels": []}
    staff = await asyncio.to_thread(_staff_of, root, agent_id)
    if staff is None or _agent(org_id, agent_id) is None:
        return _error(404, "not_found", "没有这个角色")
    return await _team_call(request, org_id, lambda service, actor: {"channels": service.agent_channels(actor, staff)},
                            empty_listing={"channels": []})


@router.put(_ORG + "/agents/{agent_id}/channels")
async def set_agent_channels(org_id: str, agent_id: str, request: Request):
    """整组替换 agent 所在的团队频道（只有所有者）。有一项不行就什么都不改。"""
    from pydantic import ValidationError
    root = _root(org_id)
    try:
        body = _AgentChannels.model_validate(await _body(request))
    except ValidationError:
        return _error(400, "invalid", "要给出 agent 所在的频道列表")
    if any(entry.autoReply for entry in body.channels):
        return _error(409, "agent_channels.auto_reply_unavailable", _AUTO_REPLY_OFF)
    wanted = [entry.channelId for entry in body.channels]
    if any(not is_team_channel(channel_id) for channel_id in wanted):
        return _error(404, "not_found", "没有这个频道")
    main = agent_id == views.main_agent_id(root)
    if not wanted:              # 拿出所有频道：主助理本来就不在，不能再进的 agent 也要能拿出来
        staff = None if main else await asyncio.to_thread(_staff_of, root, agent_id)
        if not main and staff is None:
            return _error(404, "not_found", "没有这个角色")

        def clear(service, actor):
            service.require_owner(actor)
            if staff is not None:
                service.replace_agent_channels(actor, staff, [])
            return {"channels": []}
        return await _team_call(request, org_id, clear)
    staff, refused = await asyncio.to_thread(_channel_agent, root, agent_id)
    if refused is not None:
        return refused

    def replace(service, actor):
        service.replace_agent_channels(actor, staff, wanted)
        return {"channels": service.agent_channels(actor, staff)}
    return await _team_call(request, org_id, replace)


# ── 用量页（主人 2026-11-23 定：按价格折成美元、可给成员设上限）──────────────────────

async def _usage_call(org_id: str, request: Request, action, *, members: bool = False):
    """所有者（要所有者证明）看全部；members 为真时成员也能进，处理函数只给他自己的。"""
    from src.workflow.members.registry import Damaged, Missing, Refused
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    if who.is_owner and not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", "看用量需要已认证的所有者")
    if who.is_member and not members:
        return _error(403, "forbidden", "这一页只有所有者能看")
    try:
        result = await asyncio.to_thread(action, root, who)
    except Refused as error:
        return _error(403, "forbidden", str(error))
    except (Missing, LookupError) as error:
        return _error(404, "not_found", str(error))
    except (Damaged, OSError):
        return _error(409, "unavailable", "用量或成员登记读不出来")
    except ValueError as error:
        return _error(400, "invalid", str(error))
    if isinstance(result, Response):
        result.headers["Cache-Control"] = "no-store"
        return result
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get(_ORG + "/usage/report")
async def usage_report(org_id: str, request: Request, since: str = Query("", max_length=40)):
    from src.web import app_usage
    return await _usage_call(org_id, request, lambda root, who: app_usage.report(root, since))


@router.get(_ORG + "/usage/plans")
async def usage_plans(org_id: str, request: Request):
    from src.web import app_usage
    return await _usage_call(org_id, request, lambda root, who: app_usage.plans(root))


@router.get(_ORG + "/usage/people")
async def usage_people(org_id: str, request: Request, month: str = Query("", max_length=10)):
    from src.web import app_usage
    return await _usage_call(org_id, request, lambda root, who: app_usage.people(root, month))


@router.get(_ORG + "/usage/people/{user_id}")
async def usage_person(org_id: str, user_id: str, request: Request, month: str = Query("", max_length=10)):
    """所有者看任何人（带金额）；成员只看自己（占上限的百分比）。"""
    from src.web import app_usage

    def read(root, who):
        if who.is_member and who.id != user_id:
            return _error(403, "forbidden", "只能看自己的用量")
        found = app_usage.person(root, user_id, money=who.is_owner, month=month)
        return found if found is not None else _error(404, "not_found", "没有这个人")

    return await _usage_call(org_id, request, read, members=True)


@router.get(_ORG + "/usage/limits")
async def usage_limits(org_id: str, request: Request):
    from src.workflow.members.registry import MemberRegistry
    from src.workflow import usage_month

    def read(root, who):
        current = usage_month.limits(MemberRegistry(root))
        return {"defaultUsd": current.get("defaultUsd"), "roles": dict(current.get("roles") or {}),
                "people": dict(current.get("people") or {}), "onLimit": current.get("onLimit", "block"),
                "warn": bool(current.get("warn", True))}

    return await _usage_call(org_id, request, read)


@router.put(_ORG + "/usage/limits")
async def set_usage_limits(org_id: str, body: Dict[str, Any], request: Request):
    from src.workflow.members.registry import MemberRegistry
    from src.workflow import usage_month

    def write(root, who):
        current = usage_month.set_limits(MemberRegistry(root), who, body)
        return {"defaultUsd": current.get("defaultUsd"), "roles": dict(current.get("roles") or {}),
                "people": dict(current.get("people") or {}), "onLimit": current.get("onLimit", "block"),
                "warn": bool(current.get("warn", True))}

    return await _usage_call(org_id, request, write)


@router.post(_ORG + "/usage/limits/request")
async def request_more_usage(org_id: str, request: Request):
    """成员申请这个月加额度（所有者在用量页看到并答复）。"""
    from src.workflow.members.registry import MemberRegistry
    from src.workflow import usage_month

    def ask(root, who):
        if who.is_owner:
            return _error(409, "usage.owner", "所有者不用申请")
        return {"requestedAt": usage_month.ask_more(MemberRegistry(root), who)}

    return await _usage_call(org_id, request, ask, members=True)


class _RaiseLimit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limitUsd: float = Field(ge=0, le=100000)


@router.post(_ORG + "/usage/people/{user_id}/raise", status_code=204)
async def raise_person_limit(org_id: str, user_id: str, body: _RaiseLimit, request: Request):
    from src.workflow.members.registry import MemberRegistry
    from src.workflow import usage_month
    return await _usage_call(org_id, request, lambda root, who: (
        usage_month.answer(MemberRegistry(root), who, user_id, raise_to=body.limitUsd), Response(status_code=204))[1])


@router.post(_ORG + "/usage/people/{user_id}/refuse", status_code=204)
async def refuse_more_usage(org_id: str, user_id: str, request: Request):
    from src.workflow.members.registry import MemberRegistry
    from src.workflow import usage_month
    return await _usage_call(org_id, request, lambda root, who: (
        usage_month.answer(MemberRegistry(root), who, user_id, raise_to=None), Response(status_code=204))[1])


@router.get(_ORG + "/usage/composio")
async def composio_usage(org_id: str, request: Request):
    """这里没接 Composio：这个月 0 次、不限。"""
    from src.workflow.usage_month import month_start
    return await _usage_call(org_id, request, lambda root, who: {
        "calls": 0, "limit": None, "month": month_start().strftime("%Y-%m")})


@router.get(_ORG + "/agents/{agent_id}/usage")
async def agent_plan_usage(org_id: str, agent_id: str, request: Request):
    from src.web import app_usage
    return await _usage_call(org_id, request, lambda root, who: app_usage.agent_usage(root, agent_id)
                             or _error(404, "not_found", "没有这个 agent"))


@router.get(_ORG + "/agents/{agent_id}/usage-queue")
async def agent_usage_queue(org_id: str, agent_id: str, request: Request):
    from src.web import app_usage
    return await _usage_call(org_id, request, lambda root, who: app_usage.usage_queue(root, agent_id)
                             or _error(404, "not_found", "没有这个 agent"))


@router.post(_ORG + "/agents/{agent_id}/usage-queue/cancel")
async def cancel_agent_usage_queue(org_id: str, agent_id: str, request: Request):
    """「都别做了」：在等额度的事全部停下（走主人的停止决定，有审计）。"""
    from src.web import app_usage
    root = _root(org_id)
    found = await _usage_call(org_id, request, lambda root, who: app_usage.usage_queue(root, agent_id)
                              or _error(404, "not_found", "没有这个 agent"))
    if found.status_code != 200:
        return found
    canceled = skipped = 0
    for row in json.loads(found.body)["rows"]:
        try:
            stopped = app_staff.stop(root, row["runId"])
        except (ValueError, OSError, LookupError):
            stopped = False
        canceled += bool(stopped)
        skipped += not stopped
    return JSONResponse({"canceled": canceled, "skipped": skipped}, headers={"Cache-Control": "no-store"})


@router.get(_ORG + "/usage")
async def usage(org_id: str):
    root = _root(org_id)
    return _listing(lambda: {"usage": views.usage(root)})


@router.get(_ORG + "/routines")
async def routines(org_id: str):
    root = _root(org_id)
    return _listing(lambda: {"routines": views.routines(root)})


@router.get(_ORG + "/routines/{routine_id}/runs")
async def routine_runs(org_id: str, routine_id: str, request: Request, response: Response):
    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner:
        return _error(403, "forbidden", "只有所有者能读取例行工作记录")
    response.headers["Cache-Control"] = "no-store"

    def read():
        rows = views.routine_runs(root, routine_id)
        return {"runs": rows} if rows is not None else _error(404, "not_found", "没有这条例行工作")

    return await asyncio.to_thread(_listing, read)


def _routine_proposal_row(item: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = item["snapshot"]
    data = snapshot["data"]
    return {"id": item["id"], "name": data["name"], "agentName": snapshot["member_snapshot"]["name"],
            "schedule": data["schedule"], "timezone": data["timezone"],
            "createdAt": item["created_at"], "status": item["status"]}


@router.get(_ORG + "/routines/proposals")
async def member_routine_proposals(org_id: str, request: Request):
    """只给成员自己的提议状态，不返回所有者的审批凭证或岗位登记。"""
    root = _root(org_id)
    who = _who(request, root)
    if who is None or not who.is_member:
        return _error(403, "forbidden", "需要已加入空间的成员会话")

    def read():
        from src.workflow.staff.approvals import StaffApprovals
        return {"proposals": [_routine_proposal_row(item)
                              for item in StaffApprovals(root).member_routine_proposals(who.id)]}

    return await _members_call(read)


@router.post(_ORG + "/routines/proposals", status_code=201)
async def propose_member_routine(org_id: str, body: Dict[str, Any], request: Request):
    """成员只提交提议。发起人来自登录，排期仍须所有者按原流程批准。"""
    root = _root(org_id)
    who = _who(request, root)
    if who is None or not who.is_member:
        return _error(403, "forbidden", "需要已加入空间的成员会话")
    definition = body.get("definition")
    if (set(body) != {"agentId", "definition", "reason"} or not isinstance(body["agentId"], str)
            or not isinstance(definition, dict) or "member" in definition):
        return _error(400, "invalid", "需要指定智能体、例行工作内容和提议理由")

    def propose():
        from src.gateway.main_conversation import main_chat_key
        from src.workflow.members.registry import Refused
        from src.workflow.staff.approvals import StaffApprovals
        if "manage_routines" not in app_members.permission_keys(root, who):
            raise Refused("你没有管理例行工作的权限")
        staff_id = app_members.member_staff(root, who, body["agentId"])
        if staff_id is None:
            raise LookupError("没有这个智能体")
        owner = main_chat_key(root)
        if not owner:
            raise ValueError("所有者尚未登记主要聊天，无法提交例行工作提议")
        result = StaffApprovals(root).propose(
            owner, "routine", {**definition, "member": staff_id}, body["reason"], initiator=who.id)
        return {"id": result["id"], "status": result["status"]}

    return await _members_call(propose, write=True)


# ── 新建例行工作：先成为待核对的提议 ──────────────────────────────────────────
# 例行工作到点会在没人看着时运行，所以和助理、成员提的一样，要主人在「核对例行工作提议」里按摘要确认后
# 才登记（ADR-019 之后的既有流程）。界面新建的那一条在列表里显示为「等你核对」，确认后变成同一个 id 的真例行工作。

def _schedule_error(error: "app_schedule.ScheduleError") -> Dict[str, Any]:
    return {"ok": False, "error": str(error), "code": error.code}


def _home_timezone(root: str, wanted: Any) -> str:
    return wanted if isinstance(wanted, str) and wanted else (views.settings(root)["timezone"] or "UTC")


@router.post(_ORG + "/routines/parse-schedule")
async def parse_schedule(org_id: str, body: Dict[str, Any]):
    root = _root(org_id)
    try:
        info = app_schedule.parse_phrase(str(body.get("phrase") or ""), _home_timezone(root, body.get("timezone")))
    except (app_schedule.ScheduleError, ValueError) as error:
        return {"ok": False, "error": str(error)}
    return {"ok": True, "cron": info["cron"], "description": info["description"], "timezone": info["rule"]["timezone"]}


@router.post(_ORG + "/routines/parse-rule")
async def parse_rule(org_id: str, body: Dict[str, Any]):
    root = _root(org_id)
    try:
        return app_schedule.parse_phrase(str(body.get("phrase") or ""), _home_timezone(root, body.get("timezone")))
    except app_schedule.ScheduleError as error:
        return _schedule_error(error)
    except ValueError as error:
        return {"ok": False, "error": str(error), "code": "invalid"}


@router.post(_ORG + "/routines/describe-rule")
async def describe_rule(org_id: str, body: Dict[str, Any]):
    _root(org_id)
    try:
        return app_schedule.rule_info(body.get("rule"))
    except app_schedule.ScheduleError as error:
        return _schedule_error(error)


def _routine_schedule(root: str, trigger: Any) -> tuple:
    """界面的 trigger → (排期写法, 时区)。认按时间的三种和 webhook；事件、邮件、连接器触发没有对应能力。"""
    if isinstance(trigger, dict) and trigger.get("type") == "webhook":
        from src.workflow.staff.routine_hooks import methods_of
        if set(trigger) - {"type", "methods"}:
            raise app_schedule.ScheduleError("invalid", "webhook 触发只能选方法")
        methods_of(trigger.get("methods"))
        return "webhook", _home_timezone(root, None)
    if not isinstance(trigger, dict) or trigger.get("type") not in ("schedule", "once"):
        raise app_schedule.ScheduleError("routine.trigger_unsupported", "这里的例行工作只能按时间或 webhook 触发")
    if trigger["type"] == "once":
        at = trigger.get("at")
        tz = _home_timezone(root, trigger.get("timezone"))
        from src.scheduling.routine_schedule import instant, zone
        zone(tz)
        try:
            moment = instant(at)
        except (ValueError, TypeError) as error:
            raise app_schedule.ScheduleError("invalid", "一次性的时间要带时区") from error
        return f"once {moment.isoformat()}", tz
    if "rule" in trigger:
        info = app_schedule.rule_info(trigger["rule"])
    else:
        info = app_schedule.parse_phrase(str(trigger.get("phrase") or ""),
                                         _home_timezone(root, trigger.get("timezone")))
    return info["cron"], info["rule"]["timezone"]


def _submit_desktop_routine(root: str, data: Dict[str, Any], reason: str, *, expected=None) -> Dict[str, Any]:
    from src.gateway.main_conversation import main_chat_key
    from src.workflow.staff.approvals import StaffApprovals

    owner = main_chat_key(root)
    if not owner:
        raise ValueError("主要聊天还没登记，暂时不能提议例行工作")
    proposal = StaffApprovals(root).propose(owner, "routine", data, reason, expected=expected)
    app_prefs.remember_ui_routine_proposal(root, proposal["id"])
    row = next((row for row in views.routines(root) if row["id"] == data["id"]), None)
    if row is None:
        raise ValueError("提议已保存，暂时无法读取待核对记录")
    return row


def _propose_routine(root: str, body: Dict[str, Any]) -> Dict[str, Any]:
    import uuid

    allowed = {"name", "agentId", "prompt", "target", "trigger", "access", "report"}
    if set(body) - allowed:
        raise ValueError(f"这些选项这里还不支持：{sorted(set(body) - allowed)}")
    agent_id = body.get("agentId")
    if not isinstance(agent_id, str):
        raise ValueError("要选一位 agent")
    staff_id = _staff_of(root, agent_id)
    if staff_id is None:
        if agent_id == views.main_agent_id(root):
            raise ValueError("例行工作要交给一位专员；主助理可以替你提议")
        raise LookupError("没有这个 agent")
    target = body.get("target") or {"type": "dm"}
    if target != {"type": "dm"}:
        raise ValueError("例行工作的结果目前只能发到和这位 agent 的私聊")
    if body.get("report") not in (None, "always", "when_new"):
        raise ValueError("report 只能是 always 或 when_new")
    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("要写这件事要做什么")
    name = body.get("name") if isinstance(body.get("name"), str) and body["name"].strip() else prompt.strip()[:30]
    schedule, timezone = _routine_schedule(root, body.get("trigger"))
    data = {"id": "routine-" + uuid.uuid4().hex[:12], "name": name.strip()[:80], "member": staff_id,
            "prompt": prompt.strip(), "schedule": schedule, "timezone": timezone,
            "delivery": "notify" if body.get("report") == "always" else "review", "enabled": True,
            "token_budget": 0, "acceptance": []}
    if schedule == "webhook":
        from src.workflow.staff.routine_hooks import RoutineHooks, methods_of
        RoutineHooks(root).prepare(data["id"], methods_of(body["trigger"].get("methods")))
    return _submit_desktop_routine(root, data, f"主人在界面上新建例行工作「{data['name']}」")


@router.post(_ORG + "/routines")
async def create_routine(org_id: str, body: Dict[str, Any], request: Request):
    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner or not app_staff.owner_proof(request):
        return _error(403, "forbidden", "新建例行工作需要已认证的所有者")
    try:
        return await asyncio.to_thread(_propose_routine, root, body)
    except app_schedule.ScheduleError as error:
        return _error(400, error.code, str(error))
    except LookupError as error:
        return _error(404, "not_found", str(error))
    except (ValueError, OSError) as error:
        return _error(400, "invalid", str(error))


class _RoutineCopyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: Optional[str] = Field(default=None, max_length=120)


def _routine_copy_name(original: str, requested: Optional[str]) -> str:
    original = original.strip()
    if requested is None:
        return original[:76].rstrip() + "（副本）"
    name = requested.strip()
    if 0 < len(name) <= 80:
        return name
    # Keep the locale's copy indicator when the client adds it to a maximum-length source name.
    prefix, source_name, suffix = name.partition(original)
    room = 80 - len(prefix) - len(suffix)
    if source_name and (prefix or suffix) and room > 0:
        return prefix + source_name[:room].rstrip() + suffix
    raise app_schedule.ScheduleError("routine.copy_name", "副本名称须为 1～80 字符")


def _propose_routine_copy(root: str, routine_id: str, requested_name: Optional[str]) -> Dict[str, Any]:
    import uuid
    from src.workflow.staff.routines import Routines

    registry = Routines(root)
    source = next((item for item in registry.list() if item["id"] == routine_id and not item["retired"]), None)
    if source is None:
        raise LookupError("没有这条例行工作")
    if source.get("initiator") or source["legacy"] is not None or source["state"].get("takeover"):
        raise ValueError("成员发起、接手或迁移的例行工作暂不支持复制")
    registry.check_binding(source)
    data = {key: source[key] for key in ("member", "prompt", "acceptance", "delivery", "schedule", "timezone",
                                        "token_budget")}
    data.update(id="routine-" + uuid.uuid4().hex[:12], name=_routine_copy_name(source["name"], requested_name),
                enabled=False, revision=1)
    binding = source["binding"]
    expected = {"kind": "routine", "data": data, "previous": None, "member_snapshot": binding["member"],
                "role_snapshot": binding["role"]}
    reason = f"主人在界面上复制例行工作「{source['name']}」，副本默认暂停"
    return _submit_desktop_routine(root, data, reason, expected=expected)


@router.post(_ORG + "/routines/{routine_id}/duplicate")
async def duplicate_routine(org_id: str, routine_id: str, body: _RoutineCopyInput, request: Request):
    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner or not app_staff.owner_proof(request):
        return _error(403, "forbidden", "复制例行工作需要已认证的所有者")
    try:
        return await asyncio.to_thread(_propose_routine_copy, root, routine_id, body.name)
    except app_schedule.ScheduleError as error:
        return _error(400, error.code, str(error))
    except LookupError as error:
        return _error(404, "not_found", str(error))
    except ValueError as error:
        return _error(409, "routine.copy_unavailable", str(error))
    except OSError:
        return _error(409, "unavailable", "暂时无法保存例行工作副本")


def _routine_row(root: str, routine_id: str):
    return next((row for row in views.routines(root) if row["id"] == routine_id), None)


@router.post(_ORG + "/routines/{routine_id}/run")
async def run_routine(org_id: str, routine_id: str, request: Request):
    """立即运行一次，留在事件循环线程把任务登记进执行池。"""
    from src.workflow.staff.routines import RoutineRunRejected, Routines
    root = _root(org_id)
    actor = member_auth.principal(request, root)
    if not actor.is_owner or not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", "立即运行例行工作需要已认证的所有者")
    try:
        Routines(root).run_now(routine_id, owner=actor.id)
        return _routine_row(root, routine_id)
    except RoutineRunRejected as error:
        if error.reason == "not_found":
            return _error(404, "not_found", str(error))
        if error.reason == "audit_unavailable":
            return _error(503, "audit_unavailable", str(error))
        return _error(409, f"routine.{error.reason}", str(error))
    except (ValueError, OSError) as error:
        return _error(409, "routine.unavailable", str(error))


# ── 例行工作的 webhook 触发（主人 2026-11-24 定：做，带密钥；触发进来的内容按外部内容）────────

def _hook_routine(root: str, routine_id: str):
    from src.workflow.staff.routines import Routines
    item = next((row for row in Routines(root).list() if row["id"] == routine_id and not row["retired"]), None)
    return item if item is not None and item["schedule"] == "webhook" else None


async def _hook_call(org_id: str, routine_id: str, request: Request, action):
    from src.workflow.staff.routine_hooks import HookDamaged, RoutineHooks
    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner or not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", "webhook 设置只有已认证的所有者能看能改")
    try:
        if await asyncio.to_thread(_hook_routine, root, routine_id) is None:
            return _error(404, "not_found", "没有这条 webhook 触发的例行工作")
        result = await asyncio.to_thread(action, RoutineHooks(root), _origin(request))
    except (HookDamaged, OSError, ValueError):
        return _error(503, "unavailable", "webhook 记录暂时读不出来")
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get(_ORG + "/routines/{routine_id}/hook")
async def routine_hook(org_id: str, routine_id: str, request: Request):
    return await _hook_call(org_id, routine_id, request, lambda hooks, origin: hooks.view(routine_id, origin))


@router.post(_ORG + "/routines/{routine_id}/hook/address")
async def new_routine_hook_address(org_id: str, routine_id: str, request: Request):
    return await _hook_call(org_id, routine_id, request, lambda hooks, origin: hooks.new_address(routine_id, origin))


@router.post(_ORG + "/routines/{routine_id}/hook/secret")
async def new_routine_hook_secret(org_id: str, routine_id: str, request: Request):
    def make(hooks, origin):
        secret, hook = hooks.new_secret(routine_id, origin)
        return {"secret": secret, "hook": hook}
    return await _hook_call(org_id, routine_id, request, make)


@router.delete(_ORG + "/routines/{routine_id}/hook/secret")
async def remove_routine_hook_secret(org_id: str, routine_id: str, request: Request):
    return await _hook_call(org_id, routine_id, request, lambda hooks, origin: hooks.remove_secret(routine_id, origin))


_HOOK_SAMPLE = '{"text":"这是一次测试调用","test":true}'


@router.post(_ORG + "/routines/{routine_id}/hook/test")
async def test_routine_hook(org_id: str, routine_id: str, request: Request):
    """「测试」：用一份样例内容叫一次，agent 会被告知这是测试。暂停着的也能试。"""
    import uuid
    from src.workflow.staff.routine_hooks import HookDamaged, RoutineHooks
    from src.workflow.staff.routines import RoutineRunRejected, Routines
    root = _root(org_id)
    actor = member_auth.principal(request, root)
    if not actor.is_owner or not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", "webhook 设置只有已认证的所有者能看能改")
    if _hook_routine(root, routine_id) is None:
        return _error(404, "not_found", "没有这条 webhook 触发的例行工作")
    hooks = RoutineHooks(root)
    try:
        run = Routines(root).run_now(routine_id, owner=actor.id,
                                     hook={"call": uuid.uuid4().hex[:16], "data": _HOOK_SAMPLE, "test": True})
    except RoutineRunRejected as error:
        if error.reason == "audit_unavailable":
            return _error(503, "audit_unavailable", str(error))
        return _error(409, f"routine.{error.reason}", str(error))
    except (ValueError, OSError) as error:
        return _error(409, "routine.unavailable", str(error))
    try:
        hooks.record(routine_id, "accepted", 202, len(_HOOK_SAMPLE.encode()), run, True)
        hook = hooks.view(routine_id, _origin(request))
    except (HookDamaged, OSError):
        return _error(503, "unavailable", "webhook 记录暂时读不出来")
    return JSONResponse({"runId": run, "hook": hook}, headers={"Cache-Control": "no-store"})


def _hook_answer(status: int, code: str, message: str, **extra):
    return JSONResponse({"status": code, "message": message, **extra}, status_code=status,
                        headers={"Cache-Control": "no-store", **({"Retry-After": "60"} if status == 429 else {})})


@router.api_route("/api/routine-hooks/{address}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def call_routine_hook(address: str, request: Request):
    """例行工作的触发地址：不登录，凭地址（和签名，设了密钥时）。对方发来的内容整段交给 agent 当资料。"""
    import uuid
    from src.workflow.staff.routine_hooks import MAX_BYTES, HookDamaged, RoutineHooks
    from src.workflow.staff.routines import RoutineRunRejected, Routines
    root = os.getcwd()
    hooks = RoutineHooks(root)
    try:
        routine_id = hooks.routine_at(address)
        if routine_id is None or _hook_routine(root, routine_id) is None:
            return _hook_answer(404, "not_found", "没有这个触发地址")
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_BYTES:
            data = b"x" * (MAX_BYTES + 1)              # 不读进来：按「太大」记一次
        elif request.method == "GET":
            data = request.url.query.encode("utf-8")
        else:
            data = await request.body()
        refused = hooks.admit(routine_id, request.method, data, request.headers.get("x-vortoone-signature", ""),
                              request.headers.get("idempotency-key", "").strip())
    except (HookDamaged, OSError):
        return _hook_answer(503, "unavailable", "暂时收不了")
    if refused is not None:
        outcome, status = refused
        words = {"method": "这个地址不接受这种请求", "too_large": "内容太大", "bad_signature": "签名不对",
                 "rate_limited": "调用太频繁，请稍后再试", "duplicate": "这次调用之前收到过，没有再运行"}
        return _hook_answer(status, outcome, words[outcome])
    call = uuid.uuid4().hex[:16]
    try:
        run = Routines(root).run_now(routine_id, owner="webhook",
                                     hook={"call": call, "data": data.decode("utf-8", "replace"), "test": False})
    except RoutineRunRejected as error:
        if error.reason in ("running", "awaiting_review"):
            hooks.record(routine_id, "rate_limited", 429, len(data), None, False)
            return _hook_answer(429, "rate_limited", str(error))
        return _hook_answer(409, "not_started", str(error))
    except (ValueError, OSError) as error:
        return _hook_answer(409, "not_started", str(error))
    hooks.record(routine_id, "accepted", 202, len(data), run, False)
    return _hook_answer(202, "accepted", "收到了，例行工作开始运行", runId=run)


@router.get(_ORG + "/routines/proposals/review")
async def routine_proposal_reviews(org_id: str, request: Request):
    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner or not app_staff.owner_proof(request):
        return _error(403, "forbidden", "核对例行工作提议需要已认证的所有者")

    def read():
        from src.workflow.staff.roles import unattended_role
        from src.workflow.members.registry import MemberRegistry
        from src.workflow.staff.approvals import StaffApprovals
        members = MemberRegistry(root).snapshot()["members"]
        proposals = []
        from_ui = app_prefs.ui_routine_proposals(root)
        for item in StaffApprovals(root).pending_routine_reviews():
            if not item["snapshot"].get("initiator") and item["id"] not in from_ui:
                continue                     # 助理替主人提的不在这里核对
            snapshot = item["snapshot"]
            data = snapshot["data"]
            grants = unattended_role(snapshot["role_snapshot"])["grants"]      # 给主人看的是真正会跑的权限
            submitter = members.get(snapshot.get("initiator") or "")
            proposals.append({**_routine_proposal_row(item),
                              "submitterName": submitter["name"] if submitter else None, "reason": item["reason"],
                              "prompt": data["prompt"], "acceptance": data["acceptance"],
                              "delivery": data["delivery"], "tokenBudget": data["token_budget"],
                              "enabled": data["enabled"],
                              "operations": grants["operations"], "tools": grants["tools"],
                              "proof": {key: item[key] for key in ("digest", "nonce", "round", "instance")}})
        return JSONResponse({"proposals": proposals}, headers={"Cache-Control": "no-store"})

    try:
        return await asyncio.to_thread(read)
    except (ValueError, OSError):
        return _error(409, "unavailable", "暂时无法读取例行工作提议")


class _RoutineProposalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    approve: bool
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    nonce: str = Field(min_length=1, max_length=64)
    round: int = Field(ge=1)
    instance: str = Field(min_length=1, max_length=64)


@router.post(_ORG + "/routines/proposals/{proposal_id}/decision")
async def decide_routine_proposal(org_id: str, proposal_id: str, body: _RoutineProposalDecision, request: Request):
    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner or not app_staff.owner_proof(request):
        return _error(403, "forbidden", "审批例行工作提议需要已认证的所有者")

    def decide():
        from src.workflow.staff.approvals import StaffApprovals
        from src.workflow.staff.owner_confirmation import owner_route
        with owner_route(root):
            approvals = StaffApprovals(root)
            if proposal_id in app_prefs.ui_routine_proposals(root):
                item = approvals.decide_routine(proposal_id, body.approve, body.digest, nonce=body.nonce,
                                                round=body.round, instance=body.instance)
            else:
                item = approvals.decide_member_routine(proposal_id, body.approve, body.digest, nonce=body.nonce,
                                                       round=body.round, instance=body.instance)
        return {"id": item["id"], "status": item["status"]}

    try:
        return await asyncio.to_thread(decide)
    except LookupError as error:
        return _error(404, "not_found", str(error))
    except (ValueError, OSError) as error:
        return _error(409, "conflict", str(error))


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

@router.get(_ORG + "/team-memories")
async def team_memories(org_id: str, request: Request):
    """团队记忆：所有 agent 都读的那一份，所有者和成员都能看（内容本来就会出现在所有 agent 的回答里）。"""
    from src.memory import team_memory
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    can_change = who.is_owner or "review_memory" in app_members.permission_keys(root, who)
    try:
        rows = await asyncio.to_thread(team_memory.items, root)
    except team_memory.TeamMemoryError as error:
        return _error(409, "unavailable", str(error))
    return {"memories": [views.team_memory_view(root, row, can_change=can_change, owner=who.is_owner)
                         for row in rows if not row.get("archived")]}


@router.post(_ORG + "/memories/{memory_id}/team")
async def share_memory(org_id: str, memory_id: str, request: Request):
    """把所有者自己的一条记忆设成团队记忆（所有 agent 都会知道）。只有所有者能做。"""
    from src.memory import team_memory
    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner or not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", "设成团队记忆需要已认证的所有者")

    def run():
        mine = next((m for m in views.memories(root, views.main_agent_id(root))
                     if m["id"] == memory_id and not m["archived"]), None)
        if mine is None:
            return None
        item = team_memory.share(root, memory_id, mine["text"], views.owner_id(root))
        return views.team_memory_view(root, item, can_change=True, owner=True)
    try:
        result = await asyncio.to_thread(run)
    except team_memory.TeamMemoryError as error:
        return _error(409, "team_memory.unavailable", str(error))
    return result if result is not None else _error(404, "not_found", "没有这条记忆")


async def _patch_team_memory(request: Request, root: str, memory_id: str, body: Dict[str, Any]):
    """团队记忆：所有者可以改文字、移除、恢复；有「审核 agent 记住的东西」权限的成员只能移除、恢复，
    这样进系统提示的文字都出自所有者。"""
    from src.memory import team_memory
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    if who.is_owner and not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", _NEEDS_OWNER)
    if not who.is_owner and "review_memory" not in app_members.permission_keys(root, who):
        return _error(403, "forbidden", "需要「审核 agent 记住的东西」权限")
    if set(body) == {"archived"} and type(body["archived"]) is bool:
        change = lambda: team_memory.set_archived(root, memory_id, body["archived"])  # noqa: E731
    elif set(body) == {"text"} and isinstance(body["text"], str):
        if not who.is_owner:
            return _error(403, "forbidden", "团队记忆的文字只有所有者能改")
        change = lambda: team_memory.edit(root, memory_id, body["text"], views.owner_id(root))  # noqa: E731
    else:
        return _error(400, "unsupported", "团队记忆只能改文字、移除或恢复")
    try:
        item = await asyncio.to_thread(change)
    except LookupError:
        return _error(404, "not_found", "没有这条团队记忆")
    except team_memory.TeamMemoryError as error:
        return _error(400, "invalid", str(error))
    return views.team_memory_view(root, item, can_change=True, owner=who.is_owner)


@router.patch(_ORG + "/memories/{memory_id}")
async def patch_memory(org_id: str, memory_id: str, body: Dict[str, Any], request: Request):
    """只接归档与恢复。改写内容、置顶、确认没有对应能力：拒绝，而不是假装改了。团队记忆另有规矩。"""
    root = _root(org_id)
    if memory_id.startswith("team-"):
        return await _patch_team_memory(request, root, memory_id, body)
    if not member_auth.principal(request, root).is_owner:
        return _error(403, "forbidden", "只有所有者能改自己的记忆")
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


class _StopRoutine(BaseModel):
    model_config = ConfigDict(extra="forbid")


@router.post(_ORG + "/routines/{routine_id}/stop")
async def stop_routine(org_id: str, routine_id: str, body: _StopRoutine, request: Request):
    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner or not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", "停止例行执行需要已认证的所有者")
    try:
        if not app_staff.stop_routine(root, routine_id):
            return _error(409, "routine.not_running", "这条例行工作没有正在执行的任务")
        row = await asyncio.to_thread(_routine_row, root, routine_id)
        return row if row is not None else _error(404, "not_found", "没有这条例行工作")
    except LookupError:
        return _error(404, "not_found", "没有这条例行工作")
    except (ValueError, OSError) as error:
        return _error(409, "conflict", str(error))


def _routine_takeover(root: str, routine_id: str, request: Request, approval: Optional[str]):
    from src.gateway.main_conversation import main_chat_key
    from src.workflow.staff.owner_confirmation import owner_route
    from src.workflow.staff.routines import Routines
    who = member_auth.principal(request, root)
    if not who.is_owner or not app_staff.owner_proof(request):
        return _error(403, "forbidden", "接手例行工作需要已认证的所有者")
    owner_session = main_chat_key(root)
    if not owner_session:
        return _error(409, "conflict", "所有者尚未登记主要聊天")
    registry = Routines(root)
    try:
        if approval is None:
            return registry.takeover_preview(routine_id, who.id, owner_session)
        with owner_route(root):
            registry.take_over(routine_id, who.id, owner_session, approval)
        return next((row for row in views.routines(root) if row["id"] == routine_id),
                    _error(404, "not_found", "没有这条例行工作"))
    except LookupError:
        return _error(404, "not_found", "没有这条例行工作")
    except (ValueError, OSError) as error:
        return _error(409, "conflict", str(error))


@router.get(_ORG + "/routines/{routine_id}/take-over")
async def routine_takeover_preview(org_id: str, routine_id: str, request: Request):
    return await asyncio.to_thread(_routine_takeover, _root(org_id), routine_id, request, None)


@router.post(_ORG + "/routines/{routine_id}/take-over")
async def routine_takeover_confirm(org_id: str, routine_id: str, body: Dict[str, Any], request: Request):
    root = _root(org_id)
    approval = body.get("digest")
    if set(body) != {"digest"} or not isinstance(approval, str) or len(approval) != 64:
        return _error(400, "invalid", "请先核对这条例行工作的接手内容")
    return await asyncio.to_thread(_routine_takeover, root, routine_id, request, approval)


@router.get(_ORG + "/skills/{skill_id}")
async def skill(org_id: str, skill_id: str, request: Request):
    root = _root(org_id)
    try:
        detail = await asyncio.to_thread(views.skill_detail, root, skill_id,
                                         owner_verified=app_staff.owner_proof(request))
    except PermissionError as error:
        return _error(403, "owner_proof_required", str(error))
    return detail if detail is not None else _error(404, "not_found", "没有这条技能")


async def _owner_write(request: Request, root: str, what: str):
    """只有所有者、且是已认证的主人连接才能改的东西。"""
    if not member_auth.principal(request, root).is_owner:
        return _error(403, "forbidden", f"{what}只有所有者能改")
    if not views.owner_connection():
        return _error(403, "forbidden", f"改{what}需要已认证的主人连接")
    return None


async def _skill_write(request: Request, org_id: str, action):
    from src.workflow.workbench.skill_import import SkillImportError
    from src.workflow.workbench.personal_skills import SkillConflict
    root = _root(org_id)
    refused = await _owner_write(request, root, "技能")
    if refused:
        return refused
    try:
        return await asyncio.to_thread(action, root)
    except SkillImportError as error:
        return _error(error.status, error.code, str(error))
    except SkillConflict as error:
        return _error(409, "conflict", str(error))
    except ValueError as error:
        return _error(400, "invalid", str(error))
    except OSError:
        return _error(409, "unavailable", "个人技能无法保存，请检查存储权限")


@router.post(_ORG + "/skills")
async def create_skill(org_id: str, body: Dict[str, Any], request: Request):
    """主人写一条新技能。只是给主助理的说明，不执行脚本、不增加工具权限。"""
    return await _skill_write(request, org_id, lambda root: views.create_skill(root, body))


async def _skill_upload_body(request: Request) -> dict:
    from src.workflow.workbench.skill_import import MAX_IMPORT_BODY, SkillImportError

    oversized = SkillImportError("本地技能 ZIP 最大 128 KiB", status=413, code="skill.local_upload_too_large")
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise SkillImportError("技能导入需要 JSON 正文", status=415)
    try:
        declared = int(request.headers.get("content-length", "0"))
    except ValueError as error:
        raise SkillImportError("无效的正文长度") from error
    if declared < 0:
        raise SkillImportError("无效的正文长度")
    if declared > MAX_IMPORT_BODY:
        raise oversized
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_IMPORT_BODY:
            raise oversized
        raw.extend(chunk)
    try:
        body = json.loads(raw)
    except (ValueError, RecursionError) as error:
        raise SkillImportError("技能导入 JSON 无效") from error
    if not isinstance(body, dict):
        raise SkillImportError("技能导入需要 JSON 对象")
    return body


@router.post(_ORG + "/skills/import")
async def import_skill(org_id: str, request: Request):
    """上传私人技能 ZIP，启用沿用原有正文审阅与版本核对。"""
    from src.workflow.workbench.skill_import import SkillImportError

    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner or not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", "导入个人技能需要已认证的同源所有者会话")
    try:
        body = await _skill_upload_body(request)
    except SkillImportError as error:
        return _error(error.status, error.code, str(error))
    return await _skill_write(request, org_id, lambda root: views.import_skill(root, body))


@router.patch(_ORG + "/skills/{skill_id}")
async def update_skill(org_id: str, skill_id: str, body: Dict[str, Any], request: Request):
    result = await _skill_write(request, org_id, lambda root: views.update_skill(root, skill_id, body))
    return _error(404, "not_found", "没有这条技能") if result is None else result


@router.delete(_ORG + "/skills/{skill_id}", status_code=204)
async def delete_skill(org_id: str, skill_id: str, request: Request):
    result = await _skill_write(request, org_id, lambda root: views.remove_skill(root, skill_id))
    if isinstance(result, JSONResponse):
        return result
    return Response(status_code=204) if result else _error(404, "not_found", "没有这条技能")


# ── 技能的来源更新、撤回、还原（如实回答）──────────────────────────────────────────
# 这里的技能是主人手写或上传 ZIP 的：没有可以重新读取的网址来源；「agent 自己改进技能」固定关着，
# 也就没有 agent 改过、因不用被归档的技能可以撤回或还原。

async def _skill_without_source(request: Request, org_id: str, skill_id: str, code: str, message: str):
    root = _root(org_id)
    refused = await _owner_write(request, root, "技能")
    if refused:
        return refused
    # 只看在不在列表里：不读正文，也就不会把这一版记成「主人看过」（启用只认看过的版本）
    known = {row["id"] for row in await asyncio.to_thread(views.skills, root)}
    if skill_id not in known:
        return _error(404, "not_found", "没有这条技能")
    return _error(409, code, message)


_NO_SOURCE = "这条技能是手写或上传的，没有可以重新读取的网址来源"


@router.post(_ORG + "/skills/{skill_id}/reimport")
async def reimport_skill(org_id: str, skill_id: str, request: Request):
    return await _skill_without_source(request, org_id, skill_id, "skill.no_source", _NO_SOURCE + "；要更新请重新上传")


@router.post(_ORG + "/skills/{skill_id}/update")
@router.post(_ORG + "/skills/{skill_id}/update/approve")
@router.post(_ORG + "/skills/{skill_id}/update/dismiss")
@router.post(_ORG + "/skills/{skill_id}/update/reopen")
@router.post(_ORG + "/skills/{skill_id}/update/undo")
async def skill_source_update(org_id: str, skill_id: str, request: Request):
    return await _skill_without_source(request, org_id, skill_id, "skill.no_source", _NO_SOURCE + "，也就没有待处理的更新")


@router.post(_ORG + "/skills/{skill_id}/revert")
async def revert_skill(org_id: str, skill_id: str, request: Request):
    return await _skill_without_source(request, org_id, skill_id, "skill.nothing_to_revert",
                                       "agent 不会自己改技能，这条技能没有 agent 的改动可以撤回")


@router.post(_ORG + "/skills/{skill_id}/restore")
async def restore_skill(org_id: str, skill_id: str, request: Request):
    return await _skill_without_source(request, org_id, skill_id, "skill.not_archived",
                                       "这条技能没有被归档，不需要还原")


# ── 消息上的几个动作（如实回答）─────────────────────────────────────────────────

async def _message_unavailable(request: Request, org_id: str, code: str, message: str, *, status: int = 409):
    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner or not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", _NEEDS_OWNER)
    return _error(status, code, message)


@router.post(_ORG + "/messages/{message_id}/answer-now")
async def answer_message_now(org_id: str, message_id: str, request: Request):
    """没接模型时消息不会发出、也不会存下等着回答：没有可以「立即回答」的。"""
    return await _message_unavailable(request, org_id, "message.nothing_waiting",
                                      "没接模型时消息不会发出，没有等待回答的消息；接上模型后请重新发送")


@router.post(_ORG + "/messages/{message_id}/cancel-follow-up")
async def cancel_message_follow_up(org_id: str, message_id: str, request: Request):
    return await _message_unavailable(request, org_id, "follow_up.not_pending", "没有等着回来的跟进")


class _MessageCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    body: str = Field(min_length=1, max_length=8000)


@router.post(_ORG + "/messages/{message_id}/correction")
async def correct_agent_message(org_id: str, message_id: str, body: _MessageCorrection, request: Request):
    """把纠正存成 agent 的经验还没有：如实拒绝，不存一份不生效的数据。"""
    return await _message_unavailable(request, org_id, "message.correction_unavailable",
                                      "纠正还不能存成 agent 的经验；可以直接在对话里告诉它")


@router.post(_ORG + "/messages/{message_id}/github-answer")
async def answer_github_message(org_id: str, message_id: str, request: Request):
    return await _message_unavailable(request, org_id, "github_event.unavailable", "这里没有接 GitHub 事件")


@router.post(_ORG + "/messages/{message_id}/actions/undo")
async def undo_message_actions(org_id: str, message_id: str, request: Request):
    """agent 的回答下面没有可撤销的操作回执：什么都不用撤。"""
    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner or not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", _NEEDS_OWNER)
    return JSONResponse({"actions": [], "failed": []}, headers={"Cache-Control": "no-store"})


@router.put(_ORG + "/skill-settings")
async def save_skill_settings(org_id: str, body: Dict[str, Any], request: Request):
    """「agent 自己改进技能」在这里没有对应能力（技能改动都要主人核对）：只接受关着。"""
    root = _root(org_id)
    refused = await _owner_write(request, root, "技能设置")
    if refused:
        return refused
    if body != {"agentsImprove": False}:
        return _error(400, "unsupported", "这里的技能改动都要主人核对，不能让 agent 自己改")
    return {"agentsImprove": False}


@router.put(_ORG + "/agents/{agent_id}/skills")
async def set_agent_skills(org_id: str, agent_id: str, body: Dict[str, Any], request: Request):
    """主助理启用哪些个人技能。专员按岗位装配，不在这里改。"""
    from src.workflow.workbench.personal_skills import SkillConflict
    root = _root(org_id)
    owner_verified = app_staff.owner_proof(request)
    if not member_auth.principal(request, root).is_owner or (views.owner_connection() and not owner_verified):
        return _error(403, "owner_proof_required", "启停个人技能需要已认证的同源所有者会话")
    if agent_id != views.main_agent_id(root):
        return _error(404 if not _known_agent(org_id, agent_id) else 400, "unsupported", "专员的技能按岗位装配")
    rows = body.get("skills")
    if set(body) != {"skills"} or not isinstance(rows, list):
        return _error(400, "invalid", "需要技能 id 列表")
    referenced = None
    if all(isinstance(row, str) for row in rows):
        wanted = rows
    elif all(isinstance(row, dict) and set(row) == {"skillId", "enabled"}
             and isinstance(row["skillId"], str) and type(row["enabled"]) is bool for row in rows):
        if not owner_verified:
            return _error(403, "owner_proof_required", "启停个人技能需要已认证的同源所有者会话")
        referenced = [row["skillId"] for row in rows]
        if len(set(referenced)) != len(referenced):
            return _error(400, "invalid", "同一个技能只能指定一次")
        wanted = [row["skillId"] for row in rows if row["enabled"]]
    else:
        return _error(400, "invalid", "需要技能 id 列表或技能启停状态")
    try:
        return {"skills": await asyncio.to_thread(views.set_main_skills, root, wanted, referenced=referenced,
                                                 owner_verified=owner_verified)}
    except LookupError:
        return _error(404, "not_found", "没有这条技能")
    except PermissionError as error:
        return _error(403, "owner_proof_required", str(error))
    except SkillConflict as error:
        return _error(409, "conflict", str(error))
    except (ValueError, OSError):
        return _error(409, "unavailable", "个人技能无法处理，请检查格式、版本和存储权限")


def _may_access_desktop_uploads(request: Request, root: str) -> bool:
    return member_auth.principal(request, root).is_owner and app_staff.owner_proof(request)


@router.get(_ORG + "/artifacts")
async def artifacts(org_id: str, request: Request, response: Response, q: str = "", kind: str = "", kinds: str = ""):
    root = _root(org_id)
    wanted = {value for value in [kind, *kinds.split(",")] if value} or None
    include_uploads = _may_access_desktop_uploads(request, root)
    if include_uploads:
        response.headers["Cache-Control"] = "no-store"
    return _listing(lambda: views.artifacts(root, query=q, kinds=wanted, include_uploads=include_uploads))


@router.post(_ORG + "/artifacts/files", status_code=201)
async def upload_artifact(org_id: str, request: Request, response: Response):
    return await _create_private_artifact(org_id, request, response, app_artifact_upload.read_form)


@router.post(_ORG + "/artifacts/{artifact_id}/files", status_code=201)
async def upload_artifact_version(org_id: str, artifact_id: str, request: Request, response: Response):
    root = _root(org_id)
    if not _may_access_desktop_uploads(request, root):
        return _error(403, "owner_proof_required", "上传制品版本需要已认证的所有者")
    try:
        note, content = await app_artifact_upload.read_form(request, for_version=True)
        if not isinstance(content, app_artifact_upload.FileUpload):
            return _error(415, "artifact.upload_format", "只可追加私有文本制品的版本")
        saved = await asyncio.to_thread(app_artifacts.upload_version, root, artifact_id, content, note or None)
    except (app_artifact_upload.UploadError, app_artifacts.ArtifactReadError) as error:
        return _error(error.status, error.code, str(error))
    except HomeMaintenanceError:
        return _error(409, "unavailable", "工作区正在处理其他变更，请稍后重试")
    except (OSError, ValueError, KeyError, TypeError):
        return _error(503, "unavailable", "暂时无法保存制品版本")
    response.headers["Cache-Control"] = "no-store"
    return saved


@router.post(_ORG + "/artifacts", status_code=201)
async def create_artifact(org_id: str, request: Request, response: Response):
    """Create a private document, table, or HTML artifact from JSON."""
    return await _create_private_artifact(org_id, request, response, app_artifact_upload.read_json)


async def _create_private_artifact(org_id: str, request: Request, response: Response, read):
    root = _root(org_id)
    if not _may_access_desktop_uploads(request, root):
        return _error(403, "owner_proof_required", "创建或上传制品需要已认证的所有者")
    try:
        title, content = await read(request)
        artifact = await asyncio.to_thread(views.upload_artifact, root, title, content)
    except app_artifact_upload.UploadError as error:
        return _error(error.status, error.code, str(error))
    except HomeMaintenanceError:
        return _error(409, "unavailable", "工作区正在处理其他变更，请稍后重试")
    except (OSError, ValueError):
        return _error(503, "unavailable", "暂时无法保存上传文件")
    response.headers["Cache-Control"] = "no-store"
    return artifact


_CARD = re.compile(r"blk_[a-f0-9]{32}")


async def _card_call(request: Request, org_id: str, card_id: str, action):
    """频道里的投票、选择卡：按卡片所在频道判定谁能看、能投、能答。"""
    from src.workflow.members.channel_cards import CardConflict
    if not _CARD.fullmatch(card_id):
        return _error(404, "not_found", "没有这张卡片")

    def guarded(service, actor):
        try:
            return action(service, actor)
        except CardConflict as error:
            return _error(409, error.code, str(error))
    result = await _team_call(request, org_id, guarded)
    if isinstance(result, dict):
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    return result


@router.get(_ORG + "/polls/{poll_id}")
async def channel_poll(org_id: str, poll_id: str, request: Request):
    def read(service, actor):
        card = service.card_view(actor, poll_id)
        if card["kind"] != "poll":
            raise LookupError("没有这个投票")
        return app_channel_agents.poll(service, card)
    return await _card_call(request, org_id, poll_id, read)


class _PollVote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    options: list[int] = Field(max_length=10)


@router.put(_ORG + "/polls/{poll_id}/vote")
async def vote_channel_poll(org_id: str, poll_id: str, body: _PollVote, request: Request):
    def vote(service, actor):
        service.vote(actor, poll_id, body.options)
        return app_channel_agents.poll(service, service.card_view(actor, poll_id))
    return await _card_call(request, org_id, poll_id, vote)


@router.get(_ORG + "/choices/{choices_id}")
async def channel_choices(org_id: str, choices_id: str, request: Request):
    def read(service, actor):
        card = service.card_view(actor, choices_id)
        if card["kind"] != "choices":
            raise LookupError("没有这张选择卡")
        return app_channel_agents.choices(service, card)
    return await _card_call(request, org_id, choices_id, read)


class _ChoicesAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    picks: list[int] = Field(max_length=4)
    clientId: str = Field(min_length=1, max_length=120)
    others: list[str | None] | None = Field(default=None, max_length=4)


@router.post(_ORG + "/choices/{choices_id}/answer")
async def answer_channel_choices(org_id: str, choices_id: str, body: _ChoicesAnswer, request: Request):
    """先答的算：答案作为这个人的消息发在话题里，再交给发卡的 agent 接着做。"""
    answered = {}

    def answer(service, actor):
        card, message = service.answer_choices(actor, choices_id, body.picks, body.others, body.clientId)
        answered.update(card=card, message=message, actor=actor, service=service)
        return {"choices": app_channel_agents.choices(service, service.card_view(actor, choices_id)),
                "messageId": message["id"]}
    result = await _card_call(request, org_id, choices_id, answer)
    if answered:
        root = _root(org_id)
        try:
            await _dispatch_ready(root)
            app_channel_agents.answered(root, answered["service"], answered["actor"], answered["card"],
                                        answered["message"])
        except Exception:  # noqa: BLE001 —— 答案已存好；交给 agent 失败时它会在话题里说明，或在下一次 @ 时接上
            logging.getLogger(__name__).warning("选择卡的答案没能交给 agent", exc_info=True)
    return result


class _CardReply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=4000)
    clientId: str = Field(min_length=1, max_length=120)


@router.post(_ORG + "/artifacts/{artifact_id}/reply")
async def reply_to_card(org_id: str, artifact_id: str, body: _CardReply, request: Request):
    """在 agent 发的投票、选择卡上回复：这句话作为这个人在话题里的一条消息，交给发卡的 agent 接着做。
    别的制品不是谁发的卡片，没有「回复给 agent」这回事。"""
    if not _CARD.fullmatch(artifact_id):
        return _error(404, "not_found", "只有频道里 agent 发的卡片能回复")
    replied = {}

    def reply(service, actor):
        card, message = service.reply_card(actor, artifact_id, body.text, body.clientId)
        replied.update(card=card, message=message, actor=actor, service=service)
        return {"id": message["id"]}

    result = await _card_call(request, org_id, artifact_id, reply)
    if replied:
        root = _root(org_id)
        try:
            await _dispatch_ready(root)
            app_channel_agents.answered(root, replied["service"], replied["actor"], replied["card"], replied["message"])
        except Exception:  # noqa: BLE001 —— 回复已经发出；交给 agent 失败时它会在话题里说明
            logging.getLogger(__name__).warning("卡片上的回复没能交给 agent", exc_info=True)
    return result


@router.post(_ORG + "/artifacts/{artifact_id}/keep")
async def keep_artifact(org_id: str, artifact_id: str, request: Request):
    """频道里的卡片跟着话题走，不进 Drive；Drive 里的制品本来就在。如实说明。"""
    if not member_auth.principal(request, _root(org_id)).is_owner or not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", _NEEDS_OWNER)
    return _error(409, "artifact.keep_unavailable", "频道里的卡片跟着话题保存，不另存进 Drive")


@router.put(_ORG + "/artifacts/{artifact_id}/sharing")
async def set_artifact_sharing(org_id: str, artifact_id: str, body: Dict[str, Any], request: Request):
    """这里的制品是所有者私有的（频道卡片跟着话题的人），还不能另外共享给别人。"""
    if not member_auth.principal(request, _root(org_id)).is_owner or not app_staff.owner_proof(request):
        return _error(403, "artifact.sharing_forbidden", "只有所有者能改共享范围")
    return _error(409, "artifact.sharing_unavailable", "制品还不能另外共享：频道里的卡片跟着话题的人，Drive 里的只给所有者")


@router.get(_ORG + "/artifacts/{artifact_id}")
async def artifact(org_id: str, artifact_id: str, request: Request, response: Response, version: int | None = None):
    if _CARD.fullmatch(artifact_id):                    # 频道里 agent 发的投票、选择卡
        if version not in (None, 1):
            return _error(404, "not_found", "没有这个制品")
        return await _card_call(request, org_id, artifact_id, lambda service, actor: app_channel_agents.artifact(
            _root(org_id), service.card_view(actor, artifact_id)))
    root = _root(org_id)
    include_uploads = _may_access_desktop_uploads(request, root)
    if include_uploads:
        response.headers["Cache-Control"] = "no-store"
    try:
        found = views.artifact(root, artifact_id, version, include_uploads=include_uploads)
    except (OSError, ValueError):
        return _error(503, "unavailable", "暂时无法读取制品")
    return found if found is not None else _error(404, "not_found", "没有这个制品")



@router.get(_ORG + "/artifacts/{artifact_id}/versions")
async def desktop_artifact_versions(org_id: str, artifact_id: str, request: Request):
    return await _artifact_read(org_id, artifact_id, request, app_artifacts.versions)


@router.post(_ORG + "/artifacts/{artifact_id}/versions")
async def append_artifact_version(org_id: str, artifact_id: str, request: Request):
    from src.agents.tools.artifacts import ArtifactVersionConflict
    root = _root(org_id)
    if not _may_access_desktop_uploads(request, root):
        return _error(403, "owner_proof_required", "编辑制品需要已认证的所有者")
    try:
        base_version, content, note, title = await app_artifact_upload.read_version(request)
        result = await asyncio.to_thread(views.append_structured_artifact, root, artifact_id,
                                         base_version, content, note, title)
    except app_artifact_upload.UploadError as error:
        return _error(error.status, error.code, str(error))
    except ArtifactVersionConflict as error:
        return _error(409, "artifact.version_conflict", str(error))
    except FileNotFoundError:
        return _error(404, "not_found", "没有这个制品")
    except HomeMaintenanceError:
        return _error(409, "unavailable", "工作区正在处理其他变更，请稍后重试")
    except ValueError:
        return _error(422, "artifact.edit_format", "这里只能编辑已有的私有文档或表格")
    except OSError:
        return _error(503, "unavailable", "暂时无法保存制品版本")
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.get(_ORG + "/artifacts/{artifact_id}/download")
async def desktop_artifact_download(org_id: str, artifact_id: str, request: Request, version: int | None = None):
    return await _artifact_read(org_id, artifact_id, request,
                                lambda root, aid: app_artifacts.download(root, org_id, aid, version))


@router.get(_ORG + "/artifacts/{artifact_id}/file")
async def desktop_artifact_file(org_id: str, artifact_id: str, request: Request, version: int | None = None,
                                format: str = "", expires: int | None = None, signature: str = ""):
    return await _artifact_read(
        org_id, artifact_id, request,
        lambda root, aid: app_artifacts.file(root, org_id, aid, version, format, expires, signature),
    )


async def _artifact_read(org_id, artifact_id, request, action):
    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner or not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", "查看制品文件和历史需要已认证的所有者")
    try:
        return await asyncio.to_thread(action, root, artifact_id)
    except app_artifacts.ArtifactReadError as error:
        return _error(error.status, error.code, str(error))
    except (OSError, ValueError, TypeError):
        return _error(409, "artifact.unavailable", "无法读取已保存的制品")

@router.delete(_ORG + "/artifacts/{artifact_id}", status_code=204)
async def delete_artifact(org_id: str, artifact_id: str, request: Request):
    """Drive 中的删除保留完整版本历史，可从回收站恢复。"""
    root = _root(org_id)
    refused = await _artifact_trash_owner(request, root)
    if refused:
        return refused
    try:
        removed = await asyncio.to_thread(_apply_to_artifact_trash, root, _trash_artifact, artifact_id)
    except HomeMaintenanceError:
        return _artifact_trash_busy()
    except TrashConflict as error:
        return _error(409, "conflict", str(error))
    except (OSError, ValueError):
        return _error(503, "unavailable", "无法将制品移入回收站")
    return Response(status_code=204) if removed else _error(404, "not_found", "没有这个制品")


def _apply_to_artifact_trash(root: str, action, *args):
    # Acquire the outer lease before resolving or creating state. The store takes its own inner lock.
    with home_maintenance(root):
        # 两个库各有各的回收站，所有者上传的私有库在前（与详情、下载同序）
        return action((ArtifactTrash(root, desktop_uploads=True), ArtifactTrash(root)), *args)


def _trash_artifact(stores, artifact_id: str) -> bool:
    uploads, made = stores
    return (uploads if uploads.exists(artifact_id) else made).move_to_trash(artifact_id)


def _trashed_artifacts(stores):
    rows = [(*row, store is stores[0]) for store in stores for row in store.trashed()]
    return sorted(rows, key=lambda row: (row[1], row[0]["id"]), reverse=True)


def _restore_trashed(stores, artifact_id: str) -> str:
    for store in stores:
        outcome = store.restore(artifact_id)
        if outcome != "not_found":
            return outcome
    return "not_found"


def _purge_trashed(stores, artifact_id: str) -> bool:
    return any(store.purge(artifact_id) for store in stores)


def _artifact_trash_busy():
    # Drive Undo interprets 409 as expiry.
    return _error(423, "unavailable", "工作区正在处理其他变更，请稍后重试")


async def _artifact_trash_owner(request: Request, root: str):
    refused = await _owner_write(request, root, "制品回收站")
    if refused:
        return refused
    if not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", "制品回收站需要主人令牌或同源登录")
    return None


@router.get(_ORG + "/artifacts-trash")
async def list_artifact_trash(org_id: str, request: Request):
    root = _root(org_id)
    refused = await _artifact_trash_owner(request, root)
    if refused:
        return refused
    try:
        deleted = await asyncio.to_thread(_apply_to_artifact_trash, root, _trashed_artifacts)
    except HomeMaintenanceError:
        return _artifact_trash_busy()
    except (OSError, ValueError):
        return _error(503, "unavailable", "无法读取制品回收站")
    return JSONResponse({"files": [views.trashed_artifact(root, meta, deleted_at, purge_at, desktop_upload=uploaded)
                                   for meta, deleted_at, purge_at, uploaded in deleted]},
                        headers={"Cache-Control": "no-store"})


@router.post(_ORG + "/artifacts/{artifact_id}/restore", status_code=204)
async def restore_artifact(org_id: str, artifact_id: str, request: Request):
    root = _root(org_id)
    refused = await _artifact_trash_owner(request, root)
    if refused:
        return refused
    try:
        outcome = await asyncio.to_thread(_apply_to_artifact_trash, root, _restore_trashed, artifact_id)
    except HomeMaintenanceError:
        return _artifact_trash_busy()
    except TrashConflict as error:
        # The Drive's Undo maps HTTP 409 to expiration; a live duplicate is a different refusal.
        return _error(422, "artifact.restore_id_taken", str(error))
    except (OSError, ValueError):
        return _error(503, "unavailable", "无法恢复这个制品")
    if outcome == "expired":
        return _error(409, "artifact.purged", "这个制品已超过 30 天还原期限")
    if outcome == "purged":
        return _error(410, "artifact.permanently_deleted", "这个制品已被永久删除")
    return Response(status_code=204) if outcome == "restored" else _error(404, "not_found", "没有这个制品")


@router.delete(_ORG + "/artifacts-trash/{artifact_id}", status_code=204)
async def purge_artifact(org_id: str, artifact_id: str, request: Request):
    root = _root(org_id)
    refused = await _artifact_trash_owner(request, root)
    if refused:
        return refused
    try:
        removed = await asyncio.to_thread(_apply_to_artifact_trash, root, _purge_trashed, artifact_id)
    except HomeMaintenanceError:
        return _artifact_trash_busy()
    except (OSError, ValueError):
        return _error(503, "unavailable", "无法永久删除这个制品")
    return Response(status_code=204) if removed else _error(404, "not_found", "没有这个已删除的制品")


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
    return {"mode": "pair", "memberPairing": True, "state": None, "step": None, "reason": None, "since": None, "requestedAt": None,
            "expectedReadyAt": None, "secondsLeft": None, "secondsWaited": None, "notifyByEmail": False,
            "specs": None, "machineId": None}


def _known_agent(org_id: str, agent_id: str) -> bool:
    return any(agent["id"] == agent_id for agent in views.agents(_root(org_id)))


@router.get(_ORG + "/agents/{agent_id}")
async def agent_detail(org_id: str, agent_id: str, request: Request):
    root = _root(org_id)
    if getattr(request.state, "member_account", None) is not None:
        who = _who(request, root)
        if who is None:
            return _error(403, "forbidden", _NOT_MEMBER)
        detail = await asyncio.to_thread(app_members.member_agent_detail, root, who, agent_id)
        return detail if detail is not None else _error(404, "not_found", "没有这个角色")
    roster = app_members.directory_agents(root, member_auth.principal(request, root))
    agent = next((a for a in roster if a["id"] == agent_id), None)
    if agent is None:
        return _error(404, "not_found", "没有这个角色")
    return views.agent_detail(root, agent)


# ── 新建、修改、归档、恢复、删除 agent（src/workflow/staff/agent_lifecycle.py）──────────
# 现在只有所有者能做；成员会话进不了这些接口（不在成员白名单里）。写入要主人证明：Bearer，或登录 Cookie 加同源。

def _staff_of(root: str, agent_id: str) -> Optional[str]:
    """界面的 agent id 对应哪个专员（含归档的和删掉的）；主助理和不认识的返回 None。"""
    from src.workflow.staff.registry import StaffRegistry
    try:
        members = StaffRegistry(root).members()
    except (OSError, ValueError):
        return None
    return next((m["id"] for m in members if views.member_agent_id(root, m["id"]) == agent_id), None)


@router.get(_ORG + "/agents/{agent_id}/member-action-levels")
async def agent_member_action_levels(org_id: str, agent_id: str, request: Request):
    root = _root(org_id)
    who = _who(request, root)
    if who is None or (who.is_owner and not app_staff.owner_proof(request)):
        return _error(403, "forbidden", "需要已认证的成员或所有者会话")

    def run():
        staff_id = _staff_of(root, agent_id) if who.is_owner else app_members.member_staff(root, who, agent_id)
        if staff_id is None:
            raise LookupError("没有这个智能体")
        return app_members.agent_member_action_levels(root, who, staff_id)

    return await _members_call(run)


@router.put(_ORG + "/agents/{agent_id}/member-action-levels/{member_id}", status_code=204)
async def set_agent_member_action_level(org_id: str, agent_id: str, member_id: str, body: Dict[str, Any],
                                        request: Request):
    root = _root(org_id)
    who = _who(request, root)
    if who is None or (who.is_owner and not app_staff.owner_proof(request)):
        return _error(403, "forbidden", "需要已认证的成员或所有者会话")

    def run():
        staff_id = _staff_of(root, agent_id) if who.is_owner else app_members.member_staff(root, who, agent_id)
        if staff_id is None:
            raise LookupError("没有这个智能体")
        app_members.set_agent_member_action_level(root, who, staff_id, member_id, body)

    result = await _members_call(run, write=True)
    return result if isinstance(result, JSONResponse) else Response(status_code=204)


@router.get(_ORG + "/agents/{agent_id}/member-policy")
@router.put(_ORG + "/agents/{agent_id}/member-policy")
async def agent_member_policy(org_id: str, agent_id: str, request: Request, body: Optional[Dict[str, Any]] = None):
    root = _root(org_id)
    who = _who(request, root)
    if who is None or (who.is_owner and not app_staff.owner_proof(request)):
        return _error(403, "forbidden", "需要已认证的成员或所有者会话")
    writing = request.method == "PUT"
    if writing and body is None:
        return _error(400, "invalid", "缺少成员权限设置")

    def run():
        staff_id = _staff_of(root, agent_id) if who.is_owner else app_members.member_staff(root, who, agent_id)
        if staff_id is None:
            raise LookupError("没有这个智能体")
        return app_members.agent_member_policy(root, who, staff_id, body if writing else None)

    return await _members_call(run, write=writing)


async def _body(request: Request) -> Dict[str, Any]:
    try:
        value = await request.json()
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


async def _lifecycle(request: Request, action):
    from src.workflow.members.registry import Refused
    from src.workflow.staff.agent_lifecycle import NotAllowed
    if not app_staff.owner_proof(request):
        return _error(403, "forbidden", "管理 agent 需要所有者")
    try:
        return await asyncio.to_thread(action)
    except (NotAllowed, Refused) as error:
        return _error(403, "forbidden", str(error))
    except ValueError as error:
        return _error(422, "invalid", str(error))
    except OSError:
        return _error(409, "unavailable", "agent 登记暂时写不了")


def _agent_view(root: str, staff_id: str) -> Optional[Dict[str, Any]]:
    return next((a for a in views.agents(root) if a["handle"] == staff_id), None)


# ── 聊天里的新建 agent 提议卡片（主人 2026-11-22 定）：和审批列表是同一份提议、同一套核对 ──────────

async def _agent_proposal_call(request: Request, org_id: str, message_id: str, action, *, kind: str = "member"):
    root = _root(org_id)
    if not member_auth.principal(request, root).is_owner or not app_staff.owner_proof(request):
        return _error(403, "owner_proof_required", "提议卡片只有已认证的所有者能看和决定")

    def run():
        item = views.find_agent_proposal(root, message_id, kind=kind)
        return None if item is None else action(root, item)
    try:
        result = await asyncio.to_thread(run)
    except (ValueError, LookupError, OSError) as error:
        return _error(409, "agent_proposal.unavailable", str(error) or "这份提议已经不在等待")
    return result if result is not None else _error(404, "not_found", "没有这份提议")


def _decide_card(root: str, item: Dict[str, Any], approve: bool) -> Dict[str, Any]:
    from src.workflow.staff.approvals import StaffApprovals
    from src.workflow.staff.owner_confirmation import owner_route
    if item["status"] != "pending":
        raise ValueError("这份提议已经处理过了")
    if views.proposal_expired(item):
        raise ValueError("这份提议已经过期，请让助理重新提")
    with owner_route(root):
        StaffApprovals(root).decide(item["id"], approve, item["digest"], nonce=item["nonce"],
                                    round=item["round"], instance=item["instance"])
    return next(row for row in StaffApprovals(root).list() if row["id"] == item["id"])


@router.get(_ORG + "/agent-proposals/{message_id}")
async def agent_proposal(org_id: str, message_id: str, request: Request):
    return await _agent_proposal_call(request, org_id, message_id,
                                      lambda root, item: views.agent_proposal(root, item, can_decide=True))


@router.post(_ORG + "/agent-proposals/{message_id}/create")
async def create_proposed_agent(org_id: str, message_id: str, request: Request):
    """「创建」：按卡片上这一版批准提议，登记处照常核对岗位、实例和摘要。"""
    def run(root, item):
        decided = _decide_card(root, item, True)
        card = views.agent_proposal(root, decided, can_decide=True)
        return {"agent": _agent_view(root, item["snapshot"]["data"]["id"]), "proposal": card, "ai": "model",
                "testChannelId": None}
    return await _agent_proposal_call(request, org_id, message_id, run)


@router.post(_ORG + "/agent-proposals/{message_id}/deny")
async def deny_proposed_agent(org_id: str, message_id: str, request: Request):
    return await _agent_proposal_call(request, org_id, message_id, lambda root, item: views.agent_proposal(
        root, _decide_card(root, item, False), can_decide=True))


@router.post(_ORG + "/agent-proposals/{message_id}/ask")
async def ask_about_proposed_agent(org_id: str, message_id: str, request: Request):
    """「请管理员决定」：这里只有所有者能决定，而所有者已经看得到这张卡片。"""
    def run(root, item):
        raise ValueError("这份提议只有所有者能决定，所有者已经看得到")
    return await _agent_proposal_call(request, org_id, message_id, run)


@router.post(_ORG + "/agent-proposals/{message_id}/routine")
async def proposed_agent_routine(org_id: str, message_id: str, request: Request):
    def run(root, item):
        raise ValueError("这份提议没有附带例行工作；需要的话请让助理另提一份例行工作提议")
    return await _agent_proposal_call(request, org_id, message_id, run)


@router.post(_ORG + "/team-proposals/{message_id}/create")
async def create_proposed_team(org_id: str, message_id: str, request: Request):
    """「创建」：按卡片上这一版批准整张开通单（岗位、agent、部门、例行工作一起登记）。"""
    return await _agent_proposal_call(request, org_id, message_id, lambda root, item: {
        "card": views.team_proposal(root, _decide_card(root, item, True)), "noAi": []}, kind="bundle")


@router.post(_ORG + "/team-proposals/{message_id}/deny")
async def deny_proposed_team(org_id: str, message_id: str, request: Request):
    return await _agent_proposal_call(request, org_id, message_id, lambda root, item: {
        "card": views.team_proposal(root, _decide_card(root, item, False))}, kind="bundle")


@router.post(_ORG + "/team-proposals/{message_id}/undo")
async def undo_proposed_team(org_id: str, message_id: str, request: Request):
    def run(root, item):
        raise ValueError("登记了就不能一键撤回；要停用这些 agent 或例行工作，请让助理提议")
    return await _agent_proposal_call(request, org_id, message_id, run, kind="bundle")


@router.patch(_ORG + "/team-proposals/{message_id}/agents/{handle}")
async def adjust_proposed_team_agent(org_id: str, message_id: str, handle: str, request: Request):
    def run(root, item):
        raise ValueError("批的是卡片上的这一版；要改内容，请让助理重新提")
    return await _agent_proposal_call(request, org_id, message_id, run, kind="bundle")


class _PersonalCodexCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=80)
    handle: str = Field(min_length=2, max_length=32)
    project: str = Field(default="", max_length=100)


@router.get(_ORG + "/personal-codex-agent")
async def personal_codex_availability(org_id: str, request: Request):
    from src.agents.external_workers import personal_codex_status
    from src.web.routers.computers import direct_local_request, has_computer_owner_proof
    from src.workflow.workbench.resident_projects import load_resident_projects

    root = _root(org_id)
    if (not direct_local_request(request) or not member_auth.principal(request, root).is_owner
            or not has_computer_owner_proof(request)):
        return _error(403, "local_owner_required", "请在运行 Codex 的 Mac 上打开本机工作区")
    status = await asyncio.to_thread(personal_codex_status)
    projects = await asyncio.to_thread(load_resident_projects)
    return _no_store({"status": status, "projects": [project["name"] for project in projects]})


@router.post(_ORG + "/personal-codex-agent")
async def create_personal_codex_agent(org_id: str, request: Request, body: _PersonalCodexCreate):
    from src.agents.external_workers import personal_codex_status
    from src.web.routers.computers import direct_local_request, has_computer_owner_proof
    from src.workflow.staff.agent_lifecycle import create_personal_codex

    root = _root(org_id)
    who = member_auth.principal(request, root)
    if not direct_local_request(request) or not who.is_owner or not has_computer_owner_proof(request):
        return _error(403, "local_owner_required", "请在运行 Codex 的 Mac 上打开本机工作区")

    def run():
        if personal_codex_status() != "ready":
            raise ValueError("请在当前服务所在电脑的官方 Codex 中登录自己的 ChatGPT 账号，再试一次")
        staff_id = create_personal_codex(root, who, name=body.name, handle=body.handle, project=body.project)
        return _agent_view(root, staff_id)

    return await _lifecycle(request, run)


@router.post(_ORG + "/agents")
async def agent_create(org_id: str, request: Request):
    from src.workflow.staff import agent_lifecycle
    root, body = _root(org_id), await _body(request)
    who = member_auth.principal(request, root)

    def run():
        staff_id = agent_lifecycle.create(
            root, who, name=body.get("name"), handle=body.get("handle"), title=body.get("role", ""),
            instructions=body.get("instructions", ""), model=body.get("model", ""), look=body.get("appearance"),
            setup=body.get("setup"), audience=body.get("audience"))
        return _agent_view(root, staff_id)
    return await _lifecycle(request, run)


@router.patch(_ORG + "/agents/{agent_id}")
async def agent_update(org_id: str, agent_id: str, request: Request):
    from src.workflow.staff import agent_lifecycle
    root, body = _root(org_id), await _body(request)
    staff_id = _staff_of(root, agent_id)
    if staff_id is None:
        return _error(404, "not_found", "没有这个角色")
    who = member_auth.principal(request, root)
    change = {key: body[key] for key in ("name", "role", "instructions", "model", "appearance", "audience")
              if key in body}

    def run():
        agent_lifecycle.update(root, who, staff_id, change)
        return _agent_view(root, staff_id)
    return await _lifecycle(request, run)


@router.delete(_ORG + "/agents/{agent_id}")
async def agent_archive(org_id: str, agent_id: str, request: Request, purge: str = ""):
    from src.workflow.staff import agent_lifecycle
    root = _root(org_id)
    staff_id = _staff_of(root, agent_id)
    if staff_id is None:
        return _error(404, "not_found", "没有这个角色")
    who = member_auth.principal(request, root)
    action = agent_lifecycle.purge if purge == "true" else agent_lifecycle.archive
    result = await _lifecycle(request, lambda: action(root, who, staff_id))
    return result if isinstance(result, JSONResponse) else Response(status_code=204)


@router.post(_ORG + "/agents/{agent_id}/unarchive")
async def agent_unarchive(org_id: str, agent_id: str, request: Request):
    from src.workflow.staff import agent_lifecycle
    root = _root(org_id)
    staff_id = _staff_of(root, agent_id)
    if staff_id is None:
        return _error(404, "not_found", "没有这个角色")
    who = member_auth.principal(request, root)
    result = await _lifecycle(request, lambda: agent_lifecycle.unarchive(root, who, staff_id))
    return result if isinstance(result, JSONResponse) else Response(status_code=204)


@router.post(_ORG + "/agents/{agent_id}/setup/{step}")
async def agent_setup_step(org_id: str, agent_id: str, step: str, request: Request):
    from src.workflow.staff import agent_lifecycle
    root = _root(org_id)
    staff_id = _staff_of(root, agent_id)
    if staff_id is None:
        return _error(404, "not_found", "没有这个角色")
    who = member_auth.principal(request, root)

    def run():
        agent_lifecycle.confirm_step(root, who, staff_id, step)
        return _agent_view(root, staff_id)
    return await _lifecycle(request, run)


@router.get(_ORG + "/former-agents")
async def former_agents(org_id: str, request: Request):
    root = _root(org_id)
    who = _who(request, root)
    if who is None:
        return _error(403, "forbidden", _NOT_MEMBER)
    return {"agents": await asyncio.to_thread(views.former_agents, root)}


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
    return await asyncio.to_thread(views.agent_brain, _root(org_id), agent)


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


async def _screen_status(org_id: str, agent_id: str, request: Request, surface: str, status: dict, *, project=None):
    root = _root(org_id)
    actor = _who(request, root)
    if actor is None:
        response = _error(403, "forbidden", _NOT_MEMBER)
    else:
        response = await _members_call(lambda: app_members.require_agent_screen_level(root, actor, agent_id, surface))
        if response is None:
            extra = await project(root, agent_id, request) if project is not None and actor.is_owner else {}
            response = JSONResponse({**status, **extra})
    response.headers["Cache-Control"] = "no-store"
    return response


def _paired_desktop_run(root: str, agent_id: str):
    if agent_id != views.main_agent_id(root):
        return None
    bridge = existing_bridge(root)
    return bridge.paired_browser_run() if bridge is not None else None


def _run_display(browser):
    from src.gateway.computer_browser_preview import browser_previews

    connection = browser.connection
    display = browser_previews.find(connection.root, connection.binding["connector_id"], "desktop")
    return display if display is not None and display.connection is connection else None


async def _paired_desktop_status(root: str, agent_id: str, request: Request):
    from src.gateway.connected_computers import ComputerDenied, ComputerUnavailable
    from src.web.routers.computers import live_private_preview_allowed, private_screen_viewer

    if request.query_params:
        return {}
    binding = _paired_desktop_run(root, agent_id)
    if binding is None:
        return {}
    run_id, browser = binding
    try:
        viewer = await asyncio.to_thread(private_screen_viewer, request, root)
        if viewer is None or not viewer[0].is_owner:
            return {}
        connection = browser.connection
        machine_id = connection.binding["connector_id"]
        row = next((item for item in await asyncio.to_thread(connection.store.list)
                    if item["connector_id"] == machine_id and item["active"]), None)
        if row is None or not await live_private_preview_allowed(request, root, viewer, browser):
            return {}
        display = _run_display(browser)
        sharing = display is not None and await live_private_preview_allowed(request, root, viewer, display)
        if _paired_desktop_run(root, agent_id) != binding:
            return {}
        return {"pairedRun": {"runId": run_id, "machineId": machine_id, "machineName": row["name"],
                              "displayShared": sharing}}
    except (ComputerDenied, ComputerUnavailable, OSError, ValueError):
        return {}


@router.get(_ORG + "/agents/{agent_id}/desktop")
async def agent_desktop(org_id: str, agent_id: str, request: Request):
    return await _screen_status(org_id, agent_id, request, "desktop", {
        "available": False, "reason": "no_desktop", "detail": None, "running": False, "startedAt": None,
        "controllerUserId": None, "handoff": None, "viewers": 0, "canControl": False, "held": False,
        "browserOnDesktop": False,
    }, project=_paired_desktop_status)


@router.websocket(_ORG + "/agents/{agent_id}/desktop/paired-preview/{run_id}")
async def watch_paired_role_desktop(socket: WebSocket, org_id: str, agent_id: str, run_id: str):
    from src.gateway.audit import append_audit
    from src.gateway.computer_browser_preview import serve_viewer
    from src.gateway.connected_computers import ComputerDenied, ComputerUnavailable
    from src.web.routers.computers import (
        has_owner_browser_session, live_private_preview_allowed, private_screen_viewer,
    )

    root = os.getcwd()
    viewing = False
    try:
        if org_id != views.home_id(root) or socket.query_params or not has_owner_browser_session(socket):
            return
        current = _paired_desktop_run(root, agent_id)
        if current is None or current[0] != run_id:
            return
        _, browser = current
        display = _run_display(browser)
        viewer = private_screen_viewer(socket, root)
        if display is None or viewer is None or not viewer[0].is_owner:
            return

        async def allowed():
            return (has_owner_browser_session(socket) and _paired_desktop_run(root, agent_id) == current
                    and _run_display(browser) is display
                    and await live_private_preview_allowed(socket, root, viewer, display)
                    and _paired_desktop_run(root, agent_id) == current)

        if not await allowed() or not display.begin_viewing():
            return
        viewing = True
        if await asyncio.to_thread(append_audit, root, {
            "category": "decision", "decision": "allowed", "decision_source": "user", "operation_kind": "read",
            "operation": "观看当前回合配对电脑的显示器", "actor_id": viewer[0].id, "run_id": run_id,
            "connector_id": display.connection.binding["connector_id"], "surface_id": display.surface_id,
        }):
            await serve_viewer(socket, display.capture, allowed)
    except (WebSocketDisconnect, ComputerDenied, ComputerUnavailable, RuntimeError, ValueError, OSError, asyncio.TimeoutError):
        pass
    finally:
        if viewing:
            display.end_viewing()
        with suppress(WebSocketDisconnect, RuntimeError, OSError):
            await socket.close(code=1008)


@router.get(_ORG + "/agents/{agent_id}/browser")
async def agent_browser(org_id: str, agent_id: str, request: Request):
    from src.agents.tools.server_browser import server_browser_enabled
    on = agent_id != views.main_agent_id(_root(org_id)) and await asyncio.to_thread(server_browser_enabled)
    # 开着时：在服务器本机用浏览器，只在所有者发起的事里、点击输入每步确认；任何公开网站（受限站点除外）
    return await _screen_status(org_id, agent_id, request, "browser", {
        "allowedDomains": None, "available": on, "reason": None if on else "no_machine",
        "detail": "在服务器上用无头浏览器：只在所有者发起的事里，点击和输入每步都要确认" if on else None,
        "machine": None,
    })


@router.get(_ORG + "/agents/{agent_id}/browser/handoff")
async def agent_browser_handoff(org_id: str, agent_id: str, request: Request):
    return await _screen_status(org_id, agent_id, request, "browser", {"handoff": None})


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
    from src.web import app_member_realtime, app_presence
    from src.agents.principal import OWNER, Principal
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
    bridge = None
    watches: Dict[str, asyncio.Task] = {}
    actor = Principal(OWNER, views.owner_id(root), root)

    def team_session_valid():
        return app_staff.owner_proof(websocket)

    presence = app_presence.connected(root, actor.id) if team_session_valid() or not views.owner_connection() else None
    try:
        await websocket.send_json(F.make_app_frame(F.READY, orgId=views.home_id(root), userId=actor.id))
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
                if app_channels.is_streamed_channel(channel):
                    if not team_session_valid():
                        await websocket.send_json(F.make_app_frame(F.ERROR, code="owner_proof_required", channelId=channel))
                    else:
                        await app_channels.subscribe_channel(websocket, root, actor, channel, watches,
                                                             session_valid=team_session_valid)
                elif views.is_main_dm(root, channel):
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
        if presence is not None:
            app_presence.disconnected(presence)
        for watch in watches.values():
            watch.cancel()
        await asyncio.gather(*watches.values(), return_exceptions=True)
        if bridge is not None:
            bridge.unsubscribe(websocket)
