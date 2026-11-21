"""AI 应用接入（docs/AI_APPS.md）：发现文档、本服务自己的 OAuth 2.1 授权服务器、``/mcp``，
以及设置页「AI 应用」和授权确认页 ``/autorizar`` 用的接口。

- 发现文档、``/api/oauth/*`` 和 ``/mcp`` 不走主人令牌的鉴权（在 auth.py 的豁免清单里）：它们各自核对
  授权码、令牌。``/mcp`` 不读 Cookie、不看 Origin，只认 ``Authorization: Bearer``。
- 没设主人令牌的服务不开放这一套：那种服务上分不清谁是谁，谁连上来都会是所有者。
- 设置页和确认页的接口走原来的鉴权（主人令牌或成员会话），写操作要从本服务自己的页面发出。
- 所有地址由 ``server_url`` 一处给出，发现文档、401 的提示和设置页上显示的地址不会对不上。
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from src.web import app_views as views
from src.web import mcp_server
from src.web.routers.app_api import _ORG, _error, _origin, _root, _who

router = APIRouter(tags=["ai-apps"])

MCP_MAX_BODY = 128 * 1024
REGISTER_MAX_BODY = 16 * 1024
FORM_MAX_BODY = 64 * 1024
_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_CORS = {"Access-Control-Allow-Origin": "*",
         "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
         "Access-Control-Allow-Headers": "Authorization, Content-Type, Mcp-Protocol-Version, Mcp-Session-Id",
         "Access-Control-Expose-Headers": "WWW-Authenticate, Retry-After",
         "Access-Control-Max-Age": "600"}
mcp_server.LIMITS.update({"authorize_ip": (30, 60), "register_ip": (60, 3600), "register_total": (2000, 3600),
                          "token_fail_ip": (30, 60)})


def _enabled() -> bool:
    return views.owner_connection()


def _store():
    from src.workflow.ai_apps.store import AiApps
    return AiApps(os.getcwd())


def server_url(request: Request) -> str:
    """本服务对外的地址（不带结尾斜杠）。设了 VORTOONE_PUBLIC_URL 就用它，否则用这次请求自己用的地址。"""
    configured = os.getenv("VORTOONE_PUBLIC_URL", "").strip().rstrip("/")
    return configured or _origin(request)


def urls(issuer: str) -> Dict[str, str]:
    return {"issuer": issuer, "resource": f"{issuer}/mcp",
            "resource_metadata": f"{issuer}/.well-known/oauth-protected-resource/mcp",
            "authorize": f"{issuer}/api/oauth/authorize", "token": f"{issuer}/api/oauth/token",
            "register": f"{issuer}/api/oauth/register", "revoke": f"{issuer}/api/oauth/revoke",
            "consent": f"{issuer}/autorizar"}


_PUBLIC: Dict[str, bool] = {}


def server_public(url: str) -> bool:
    """claude.ai、ChatGPT 这类从自己服务器连过来的应用够不够得着：https、公网域名、解析到公网地址。"""
    if url in _PUBLIC:
        return _PUBLIC[url]
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or "." not in host or host.endswith((".local", ".lan", ".internal")):
        return False
    try:
        ipaddress.ip_address(host)
        return False                                   # 直接写 IP 的地址拿不到 https 证书，按不可达
    except ValueError:
        pass
    try:
        found = {item[4][0] for item in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)}
    except OSError:
        return True                                    # 查不到先当可达，下次再查（不缓存）
    public = bool(found) and all(ipaddress.ip_address(address.split("%")[0]).is_global for address in found)
    _PUBLIC[url] = public
    return public


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _oauth_error(status: int, error: str, description: str = "") -> JSONResponse:
    body = {"error": error, **({"error_description": description} if description else {})}
    return JSONResponse(body, status_code=status, headers={**_NO_STORE, **_CORS})


def _unavailable() -> JSONResponse:
    return _oauth_error(404, "not_found", "AI apps need an owner token on this server")


# ── 发现文档 ──────────────────────────────────────────────────────────────────

def _protected_resource(issuer: str) -> Dict[str, Any]:
    from src.workflow.ai_apps.store import ADVERTISED_SCOPES
    u = urls(issuer)
    return {"resource": u["resource"], "authorization_servers": [issuer], "scopes_supported": list(ADVERTISED_SCOPES),
            "bearer_methods_supported": ["header"], "resource_name": "VortoOne"}


def _authorization_server(issuer: str) -> Dict[str, Any]:
    from src.workflow.ai_apps.store import ADVERTISED_SCOPES
    u = urls(issuer)
    return {"issuer": issuer, "authorization_endpoint": u["authorize"], "token_endpoint": u["token"],
            "registration_endpoint": u["register"], "revocation_endpoint": u["revoke"],
            "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"], "token_endpoint_auth_methods_supported": ["none"],
            "revocation_endpoint_auth_methods_supported": ["none"], "scopes_supported": list(ADVERTISED_SCOPES),
            "authorization_response_iss_parameter_supported": True,
            # 应用可以直接拿一个 https 地址当 client_id（那里放着它的说明文件）：ChatGPT 这样连
            "client_id_metadata_document_supported": True}


def _document(request: Request, build) -> Response:
    if not _enabled():
        return _unavailable()
    return JSONResponse(build(server_url(request)), headers={"Cache-Control": "public, max-age=3600", **_CORS})


@router.get("/.well-known/oauth-protected-resource", include_in_schema=False)
async def protected_resource(request: Request):
    return _document(request, _protected_resource)


@router.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
async def protected_resource_mcp(request: Request):
    return _document(request, _protected_resource)


@router.get("/.well-known/oauth-authorization-server", include_in_schema=False)
async def authorization_server(request: Request):
    return _document(request, _authorization_server)


@router.get("/.well-known/openid-configuration", include_in_schema=False)
async def openid_configuration(request: Request):
    return _document(request, _authorization_server)


# ── OAuth 端点 ────────────────────────────────────────────────────────────────

@router.get("/api/oauth/authorize", include_in_schema=False)
async def oauth_authorize(request: Request):
    """参数核对通过就把浏览器送到确认页 /autorizar?req=…；不通过停在 /autorizar?erro=…，不送回应用。"""
    from src.workflow.ai_apps.store import Damaged, StartError
    page = urls(server_url(request))["consent"]
    if not _enabled():
        return RedirectResponse(f"{page}?erro=indisponivel", status_code=302)
    if not mcp_server.allow("authorize_ip", _client_ip(request)):
        return RedirectResponse(f"{page}?erro=muitas_tentativas", status_code=302)
    try:
        request_id = await asyncio.to_thread(_store().start, dict(request.query_params),
                                             urls(server_url(request))["resource"])
    except StartError as error:
        return RedirectResponse(f"{page}?erro={error.page}", status_code=302)
    except (Damaged, OSError):
        return RedirectResponse(f"{page}?erro=indisponivel", status_code=302)
    return RedirectResponse(f"{page}?req={request_id}", status_code=302)


async def _small_body(request: Request, limit: int) -> Optional[bytes]:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        return None
    body = await request.body()
    return body if len(body) <= limit else None


@router.post("/api/oauth/token", include_in_schema=False)
async def oauth_token(request: Request):
    from urllib.parse import parse_qsl

    from src.workflow.ai_apps.store import Damaged, OAuthError
    if not _enabled():
        return _unavailable()
    if not request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
        return _oauth_error(400, "invalid_request", "Send application/x-www-form-urlencoded")
    raw = await _small_body(request, FORM_MAX_BODY)
    if raw is None:
        return _oauth_error(413, "invalid_request", "Body too large")
    form = dict(parse_qsl(raw.decode("utf-8", "replace")))
    grant_type = form.get("grant_type")
    try:
        if grant_type == "authorization_code":
            body = await asyncio.to_thread(_store().exchange, form)
        elif grant_type == "refresh_token":
            body = await asyncio.to_thread(_store().refresh, form)
        else:
            raise OAuthError(400, "unsupported_grant_type")
    except OAuthError as error:
        if not mcp_server.allow("token_fail_ip", _client_ip(request)):
            return _oauth_error(429, "temporarily_unavailable", "Too many failed requests")
        return _oauth_error(error.status, error.error, error.description)
    except (Damaged, OSError, ValueError):
        return _oauth_error(500, "server_error")
    return JSONResponse(body, headers={**_NO_STORE, **_CORS})


@router.post("/api/oauth/register", include_in_schema=False)
async def oauth_register(request: Request):
    from src.workflow.ai_apps.clients import InvalidClient
    from src.workflow.ai_apps.store import Damaged
    if not _enabled():
        return _unavailable()
    if not request.headers.get("content-type", "").startswith("application/json"):
        return _oauth_error(400, "invalid_client_metadata", "Send JSON")
    if not mcp_server.allow("register_ip", _client_ip(request)) or not mcp_server.allow("register_total", "all"):
        return _oauth_error(429, "temporarily_unavailable", "Too many registrations")
    raw = await _small_body(request, REGISTER_MAX_BODY)
    if raw is None:
        return _oauth_error(413, "invalid_client_metadata", "Body too large")
    try:
        body = json.loads(raw)
    except ValueError:
        return _oauth_error(400, "invalid_client_metadata", "Send a JSON object")
    try:
        client = await asyncio.to_thread(_store().register, body)
    except InvalidClient as error:
        return _oauth_error(400, "invalid_redirect_uri" if "redirect_uris" in str(error)
                            else "invalid_client_metadata", str(error))
    except (Damaged, OSError, ValueError):
        return _oauth_error(500, "server_error")
    return JSONResponse(client, status_code=201, headers={**_NO_STORE, **_CORS})


@router.post("/api/oauth/revoke", include_in_schema=False)
async def oauth_revoke(request: Request):
    from urllib.parse import parse_qsl
    if not _enabled():
        return _unavailable()
    raw = await _small_body(request, FORM_MAX_BODY) or b""
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            form = json.loads(raw) if raw else {}
        except ValueError:
            form = {}
    else:
        form = dict(parse_qsl(raw.decode("utf-8", "replace")))
    token = form.get("token") if isinstance(form, dict) else None
    if isinstance(token, str) and token:
        try:
            await asyncio.to_thread(_store().revoke_token, token)
        except (OSError, ValueError):
            pass                                       # RFC 7009：撤销端点总是 200
    return Response(status_code=200, headers={**_NO_STORE, **_CORS})


for _path in ("/api/oauth/token", "/api/oauth/register", "/api/oauth/revoke", "/mcp", "/mcp/",
              "/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp",
              "/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"):
    router.add_api_route(_path, lambda: Response(status_code=204, headers=_CORS), methods=["OPTIONS"],
                         include_in_schema=False)


# ── /mcp ──────────────────────────────────────────────────────────────────────

def _rpc_error(id_: Any, status: int, code: int, message: str, headers: Optional[Dict[str, str]] = None,
               **extra: Any) -> JSONResponse:
    error = {"code": code, "message": message, **extra}
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "error": error}, status_code=status,
                        headers={**_CORS, **(headers or {})})


async def _mcp(request: Request):
    from src.workflow.ai_apps.store import ADVERTISED_SCOPES
    raw = await _small_body(request, MCP_MAX_BODY)
    if raw is None:
        return _rpc_error(None, 413, -32600, "Request body too large")
    try:
        message = json.loads(raw)
    except ValueError:
        return _rpc_error(None, 400, -32700, "Parse error")
    if isinstance(message, list):
        return _rpc_error(None, 400, -32600, "JSON-RPC batches are not supported")
    id_ = message.get("id") if isinstance(message, dict) else None
    challenge = (f'Bearer resource_metadata="{urls(server_url(request))["resource_metadata"]}", '
                 f'scope="{" ".join(ADVERTISED_SCOPES)}"')
    if not _enabled():
        return _rpc_error(id_, 404, -32000, "AI apps need an owner token on this server")
    header = request.headers.get("authorization", "")
    connection = None
    if header[:7].lower() == "bearer ":
        try:
            connection = await asyncio.to_thread(_store().authenticate, header[7:].strip())
        except (OSError, ValueError):
            return _rpc_error(id_, 503, -32000, "VortoOne couldn't check the token right now")
    if connection is None:
        return _rpc_error(id_, 401, -32010, "Authorization required", {"WWW-Authenticate": challenge},
                          data={"www_authenticate": challenge}, _meta={"mcp/www_authenticate": challenge})
    for kind in ("grant_minute", "grant_day"):
        if not mcp_server.allow(kind, connection["grant"]):
            seconds = mcp_server.LIMITS[kind][1]
            return _rpc_error(id_, 429, -32013, "Too many requests", {"Retry-After": str(min(seconds, 60))},
                              data={"retry_after": min(seconds, 60)})
    answer = await mcp_server.handle(os.getcwd(), connection, message, version=_version())
    if answer is None:
        return Response(status_code=202, headers=_CORS)
    error = answer.get("error") or {}
    if error.get("code") == -32010:                    # 连接的人已不在空间里
        return _rpc_error(id_, 401, -32010, "Authorization required", {"WWW-Authenticate": challenge},
                          data={"www_authenticate": challenge})
    return JSONResponse(answer, headers=_CORS)


def _version() -> str:
    from importlib import metadata
    for package in ("vortoone", "vortocode"):
        try:
            return metadata.version(package)
        except metadata.PackageNotFoundError:
            continue
    return "dev"


@router.post("/mcp", include_in_schema=False)
async def mcp(request: Request):
    return await _mcp(request)


@router.post("/mcp/", include_in_schema=False)
async def mcp_slash(request: Request):
    return await _mcp(request)


def _method_not_allowed():
    return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32000, "message": "Method not allowed."}},
                        status_code=405, headers={"Allow": "POST", **_CORS})


for _path in ("/mcp", "/mcp/"):
    router.add_api_route(_path, _method_not_allowed, methods=["GET", "DELETE"], include_in_schema=False)


# ── 设置页「AI 应用」 ─────────────────────────────────────────────────────────

def _sees_all(root: str, who) -> bool:
    from src.web import app_members
    return who.is_owner or "manage_members" in app_members.permission_keys(root, who)


def _names(root: str) -> Dict[str, str]:
    from src.web import app_members
    try:
        return {row["userId"]: row["name"] for row in app_members.members(root)}
    except (OSError, ValueError):
        return {views.owner_id(root): views.OWNER_NAME}


def _row(grant: Dict[str, Any], names: Optional[Dict[str, str]]) -> Dict[str, Any]:
    from src.workflow.ai_apps.store import access_of, iso
    person = {"person": {"id": grant["person"], "name": names.get(grant["person"], grant["person"])}} \
        if names is not None else {}
    if grant["kind"] == "token":
        return {"id": grant["id"], "name": grant["name"], "access": access_of(grant["scopes"]),
                "createdAt": iso(grant["connected"]), "expiresAt": iso(grant.get("expires")),
                "lastUsedAt": iso(grant.get("last_used")), **person}
    shown = grant.get("display") or {}
    return {"id": grant["id"], "kind": "oauth", "displayKind": shown.get("displayKind", "https"),
            "name": grant["name"], "host": shown.get("host"), "brand": shown.get("brand"),
            "access": access_of(grant["scopes"]), "connectedAt": iso(grant["connected"]),
            "lastUsedAt": iso(grant.get("last_used")), **person}


def _guard(request: Request, root: str, *, write: bool = False):
    """调用人，或一个该直接回的错误。写操作要求从本服务自己的页面发出（Cookie 登录时）。"""
    from src.web.app_staff import _same_origin
    from src.web.auth import ct_eq, get_api_token
    if not _enabled():
        return None, _error(409, "owner_token_required", "这台服务没有设主人令牌，AI 应用不能接入")
    who = _who(request, root)
    if who is None:
        return None, _error(403, "not_member", "还不是这个空间的成员")
    header = request.headers.get("authorization", "")
    bearer = header.startswith("Bearer ") and ct_eq(header[7:].strip(), get_api_token())   # 主人令牌本身才算
    if write and not bearer and not _same_origin(request):
        return None, _error(403, "forbidden", "请从 VortoOne 自己的页面操作")
    return who, None


@router.get(_ORG + "/apps")
async def apps(org_id: str, request: Request, all: str = ""):  # noqa: A002 —— 界面的查询参数名
    root = _root(org_id)
    who, refused = _guard(request, root)
    if refused is not None:
        return refused
    whole = all == "1" and _sees_all(root, who)
    try:
        grants = await asyncio.to_thread(_store().grants, None if whole else who.id)
    except (OSError, ValueError) as error:
        return _error(409, "apps_unavailable", str(error))
    names = _names(root) if whole else None
    url = server_url(request)
    return {"serverUrl": urls(url)["resource"], "serverPublic": await asyncio.to_thread(server_public, url),
            "apps": [_row(g, names) for g in grants if g["kind"] == "oauth"],
            "tokens": [_row(g, names) for g in grants if g["kind"] == "token"]}


async def _disconnect(org_id: str, grant_id: str, request: Request, kind: str):
    from src.gateway.audit import append_audit
    root = _root(org_id)
    who, refused = _guard(request, root, write=True)
    if refused is not None:
        return refused
    store = _store()
    grant = await asyncio.to_thread(store.grant, grant_id)
    if grant is None or grant["kind"] != kind or (grant["person"] != who.id and not _sees_all(root, who)):
        return _error(404, "not_found", "没有这个连接")
    await asyncio.to_thread(store.disconnect, grant_id, kind)
    append_audit(root, {"category": "ai_app", "operation": "断开 AI 应用" if kind == "oauth" else "作废个人令牌",
                        "grant": grant_id, "app": grant["name"], "person": grant["person"], "by": who.id})
    return Response(status_code=204)


@router.delete(_ORG + "/apps/{grant_id}", status_code=204)
async def disconnect_app(org_id: str, grant_id: str, request: Request):
    return await _disconnect(org_id, grant_id, request, "oauth")


@router.delete(_ORG + "/tokens/{grant_id}", status_code=204)
async def revoke_personal_token(org_id: str, grant_id: str, request: Request):
    return await _disconnect(org_id, grant_id, request, "token")


@router.post(_ORG + "/tokens")
async def create_personal_token(org_id: str, body: Dict[str, Any], request: Request):
    from src.gateway.audit import append_audit
    from src.workflow.ai_apps.store import access_of, iso
    root = _root(org_id)
    who, refused = _guard(request, root, write=True)
    if refused is not None:
        return refused
    days = body.get("expiresInDays")
    try:
        grant, secret = await asyncio.to_thread(_store().create_token, who.id, body.get("name"),
                                                str(body.get("access") or ""), days if days is None else int(days))
    except OverflowError:
        return _error(409, "token_limit", "每人最多 10 个个人令牌")
    except (TypeError, ValueError) as error:
        return _error(400, "invalid", str(error))
    append_audit(root, {"category": "ai_app", "operation": "创建个人令牌", "grant": grant["id"],
                        "app": grant["name"], "person": who.id}, durable=True)
    return JSONResponse({"token": {"id": grant["id"], "name": grant["name"], "access": access_of(grant["scopes"]),
                                   "expiresAt": iso(grant.get("expires"))}, "secret": secret},
                        headers=_NO_STORE)


# ── 授权确认页 /autorizar ─────────────────────────────────────────────────────

@router.get("/api/app/authorize-requests", include_in_schema=False)
async def recent_local_requests(request: Request):
    """Desktop 外壳轮询：本机应用刚发起、还没答复的授权请求，外壳在它已登录的窗口里打开确认页。

    主人在系统浏览器里登录不了（主人令牌只在外壳手里），Claude Code 这类本机程序打开的确认页
    因此由 Desktop 接住。只认 Authorization 头里的主人令牌：登录 Cookie 和成员会话都不行。"""
    from src.web.auth import ct_eq, get_api_token
    token = get_api_token()
    header = request.headers.get("authorization", "")
    if not token or not header.startswith("Bearer ") or not ct_eq(header[7:].strip(), token):
        return _error(403, "forbidden", "只给 Desktop 外壳用")
    try:
        rows = await asyncio.to_thread(_store().recent_local)
    except (OSError, ValueError):
        rows = []
    return JSONResponse({"requests": rows}, headers=_NO_STORE)


def _account(request: Request, root: str, who) -> Dict[str, str]:
    found = getattr(request.state, "member_account", None)
    if found is not None:
        return {"name": str(found.get("name") or ""), "email": str(found.get("email") or "")}
    return {"name": views.OWNER_NAME, "email": ""}


@router.get("/api/app/authorize/{request_id}")
async def consent(request_id: str, request: Request):
    from src.workflow.ai_apps.store import ConsentError, Damaged
    root = os.getcwd()
    who, refused = _guard(request, root)
    if refused is not None:
        return refused
    try:
        shown = await asyncio.to_thread(_store().consent, request_id)
    except ConsentError as error:
        return _error(error.status, error.code, str(error))
    except (Damaged, OSError) as error:
        return _error(503, "indisponivel", str(error))
    return {"request": shown, "account": _account(request, root, who),
            "workspaces": [{"id": views.home_id(root), "name": views.overview(root)["name"], "logo": None,
                            "paused": False, "current": True}]}


@router.post("/api/app/authorize/{request_id}")
async def decide_consent(request_id: str, body: Dict[str, Any], request: Request):
    from src.gateway.audit import append_audit
    from src.workflow.ai_apps.store import ConsentError, Damaged
    root = os.getcwd()
    who, refused = _guard(request, root, write=True)
    if refused is not None:
        return refused
    allow = body.get("decision") == "allow"
    if body.get("decision") not in ("allow", "deny"):
        return _error(400, "invalid", "decision 只能是 allow 或 deny")
    if allow and body.get("orgId") != views.home_id(root):
        return _error(403, "not_member", "不是这个空间的成员")
    try:
        target, grant_id = await asyncio.to_thread(_store().decide, request_id, who.id, allow=allow,
                                                   access=str(body.get("access") or "write"),
                                                   issuer=server_url(request))
    except ConsentError as error:
        return _error(error.status, error.code, str(error))
    except (Damaged, OSError) as error:
        return _error(503, "indisponivel", str(error))
    if grant_id is not None:
        append_audit(root, {"category": "ai_app", "operation": "连接 AI 应用", "grant": grant_id,
                            "person": who.id, "access": body.get("access")}, durable=True)
    return {"redirectTo": target}
