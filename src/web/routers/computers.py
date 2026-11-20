"""Private computer pairing and authenticated control and transfer routes."""
from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import threading
import time
from collections import deque
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.requests import ClientDisconnect, HTTPConnection
from starlette.responses import StreamingResponse

from src.agents.identity_groups import screen_level_allows
from src.agents.principal import MEMBER, OWNER, Principal
from src.gateway.audit import append_audit
from src.gateway.computer_browser_preview import BrowserPreview, browser_previews, serve_publisher, serve_viewer
from src.gateway.computer_browser_wire import PreviewKind
from src.gateway.computer_connections import connections, serve
from src.gateway.computer_native import (
    ComputerNativeDenied, ComputerNativeUnclaimed, ComputerNativeUnavailable, redeem,
)
from src.gateway.computer_protocol import (
    DOWNLOAD_PREFIX, FEATURES, MAX_TRANSFER_BYTES, PROTOCOL_MAX, PROTOCOL_MIN, TRANSFER_PATH, decode_frame,
)
from src.gateway.computer_transfer_io import CHUNK_BYTES, file_io
from src.gateway.computer_transfers import TransferDenied, TransferUnavailable, computer_transfers
from src.gateway.connected_computers import ConnectedComputers, ComputerDenied, ComputerUnavailable
from src.web import app_views as views, member_auth
from src.web.auth import SESSION_COOKIE, ct_eq, get_api_token
from src.workflow.members.registry import MemberRegistry, Missing

router = APIRouter(tags=['computers'])
_ORG = '/api/app/organizations/{org_id}'
_PRIVATE = {'Cache-Control': 'no-store', 'Pragma': 'no-cache'}
_ID = re.compile(r'^[a-f0-9]{32}$')


def _error(status: int, code: str) -> HTTPException:
    return HTTPException(status, {'code': code, 'message': '电脑连接请求无法完成'}, headers=_PRIVATE)


def _secure(connection) -> bool:
    if connection.url.scheme in ('https', 'wss'):
        return True
    try:
        peer = ipaddress.ip_address(connection.client.host)
        loopback = peer.is_loopback or bool(getattr(peer, 'ipv4_mapped', None) and peer.ipv4_mapped.is_loopback)
    except (ValueError, AttributeError):
        return False
    return loopback and connection.url.hostname in ('127.0.0.1', 'localhost', '::1')


def _native_loopback(request: Request) -> bool:
    def literal(value):
        if not isinstance(value, str) or '%' in value:
            return False
        address = ipaddress.ip_address(value)
        return address.is_loopback or bool(getattr(address, 'ipv4_mapped', None) and address.ipv4_mapped.is_loopback)

    try:
        return (request.url.scheme in ('http', 'https') and literal(request.url.hostname)
                and request.client is not None and literal(request.client.host))
    except ValueError:
        return False


def _same_origin(request: Request) -> bool:
    raw = request.headers.get('origin')
    if not raw:
        return getattr(request, 'method', None) in ('GET', 'HEAD')
    try:
        parsed = urlsplit(raw)
        served = urlsplit(str(request.base_url))
        def origin(value):
            scheme = {'ws': 'http', 'wss': 'https'}.get(value.scheme, value.scheme)
            return (scheme, (value.hostname or '').lower(), value.port or (443 if scheme == 'https' else 80))
        return (parsed.scheme in ('http', 'https') and parsed.username is None and parsed.password is None
                and not any((parsed.path, parsed.query, parsed.fragment)) and origin(parsed) == origin(served))
    except ValueError:
        return False


def _require_owner_proof(request: Request) -> None:
    token = get_api_token()
    if not token or not _secure(request):
        raise _error(403, 'owner_required')
    authorization = request.headers.get('authorization', '')
    bearer = authorization.startswith('Bearer ') and ct_eq(authorization[7:].strip(), token)
    cookie = ct_eq(request.cookies.get(SESSION_COOKIE, ''), token)
    if not (bearer or cookie):
        raise _error(401, 'owner_required')
    if (cookie and not bearer or request.headers.get('origin')) and not _same_origin(request):
        raise _error(403, 'same_origin_required')


def has_computer_owner_proof(request: Request) -> bool:
    try:
        _require_owner_proof(request)
    except HTTPException:
        return False
    return True


def has_owner_browser_session(connection: HTTPConnection) -> bool:
    token = get_api_token()
    return (bool(token) and ct_eq(connection.cookies.get(SESSION_COOKIE, ''), token)
            and _secure(connection) and len(connection.headers.getlist('origin')) == 1 and _same_origin(connection))


def has_local_owner_browser_session(request: Request) -> bool:
    return (_native_loopback(request) and has_owner_browser_session(request)
            and not any(name == 'forwarded' or name.startswith('x-forwarded-') for name in request.headers))


def _pairer(request: Request, org_id: str) -> tuple[str, ConnectedComputers, str | None, str]:
    account = getattr(request.state, 'member_account', None)
    if account is None:
        _require_owner_proof(request)
        member_id, name = None, '所有者'
    else:
        member_id = member_auth.member_id(account)
        if not member_id or not get_api_token() or not _secure(request) or not _same_origin(request):
            raise _error(403, 'computer_authorization_denied')
        name = str(account.get('name') or '成员')
    root = str(Path(os.getcwd()).resolve())
    if org_id != views.home_id(root):
        raise _error(404, 'not_found')
    return root, ConnectedComputers(root), member_id, name


class _Body(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class PairingBody(_Body):
    legacy_confirmed: bool = False


class PairBody(_Body):
    home_instance_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    connector_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    code: str = Field(min_length=12, max_length=12)
    name: str = Field(min_length=1, max_length=80)


class NativePairBody(_Body):
    connector_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    name: str = Field(min_length=1, max_length=80)

    @field_validator('name')
    @classmethod
    def valid_name(cls, value: str) -> str:
        if not value.strip() or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ValueError('invalid computer name')
        return value


class RenameBody(_Body):
    name: str = Field(min_length=1, max_length=80)


async def _body(request: Request, kind):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 4096:
            raise _error(413, 'invalid_request')
    try:
        return kind.model_validate(decode_frame(bytes(raw)))
    except (ValueError, UnicodeError, ValidationError, RecursionError):
        raise _error(400, 'invalid_request') from None


class _PairingRates:
    def __init__(self):
        self.lock = threading.RLock()
        self.homes = {}
        self.sources = {}

    def allow(self, root: str, source: str) -> bool:
        now = time.monotonic()
        with self.lock:
            for table in (self.homes, self.sources):
                for key in list(table):
                    queue = table[key]
                    while queue and queue[0] <= now - 60:
                        queue.popleft()
                    if not queue:
                        table.pop(key, None)
            if (root, source) not in self.sources and len(self.sources) >= 4096:
                return False
            home = self.homes.setdefault(root, deque())
            peer = self.sources.setdefault((root, source), deque())
            if len(home) >= 60 or len(peer) >= 10:
                return False
            home.append(now)
            peer.append(now)
            return True


_pairing_rates = _PairingRates()


async def _call(method, *args, **kwargs):
    try:
        return await asyncio.to_thread(method, *args, **kwargs)
    except ComputerDenied:
        raise _error(403, 'computer_authorization_denied') from None
    except KeyError:
        raise _error(404, 'not_found') from None
    except ValueError:
        raise _error(400, 'invalid_request') from None
    except (ComputerUnavailable, OSError):
        raise _error(503, 'computer_storage_unavailable') from None


def _machine(root: str, row: dict, member_id: str | None, name: str) -> dict:
    connector = row['connector_id']
    def stamp(value):
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, timezone.utc).isoformat()
        return value if isinstance(value, str) else None
    return {
        'id': connector, 'name': row.get('name', ''), 'kind': 'connected',
        'online': row.get('active') is True and connections.online(root, connector), 'lastSeenAt': None,
        'hostname': '', 'os': 'other', 'arch': '', 'mode': 'single', 'runnerVersion': '1',
        'execAllowed': False, 'pairedBy': {'id': member_id or row['paired_principal_id'], 'name': name},
        'agents': [], 'workspace': None, 'canManage': True, 'createdAt': stamp(row.get('created_at')),
        'browserProfiles': False, 'accessAllow': False, 'cliInstall': False, 'remotePause': False,
        'access': {'folders': [], 'tools': {key: False for key in (
            'claude', 'codex', 'cursor', 'opencode', 'grok', 'antigravity', 'commands', 'browser', 'desktop', 'terminal')}},
    }


@router.get('/api/computers/identity')
async def computer_identity(request: Request, response: Response):
    response.headers.update(_PRIVATE)
    if not _secure(request):
        raise _error(403, 'secure_transport_required')
    identity = await _call(ConnectedComputers(os.getcwd()).identity)
    # Discovery describes the stable pairing API. Workers negotiate file support after pairing.
    return {'home_instance_id': identity['home_instance_id'], 'protocol_min': PROTOCOL_MIN,
            'protocol_max': PROTOCOL_MIN, 'features': list(FEATURES)}


@router.get(_ORG + '/machines')
async def machines(org_id: str, request: Request, response: Response):
    response.headers.update(_PRIVATE)
    root, store, member_id, name = _pairer(request, org_id)
    rows = await _call(store.list, member_id=member_id)
    listed = [_machine(root, row, member_id, name) for row in rows if row.get('revoked_at') is None]
    for machine in listed:
        browser = desktop = None
        if machine['online']:
            browser = await _available_preview(request, root, member_id, machine['id'], 'browser')
            desktop = await _available_preview(request, root, member_id, machine['id'], 'desktop')
        machine['browserPreviewAvailable'] = browser is not None
        machine['desktopPreviewAvailable'] = desktop is not None
        if desktop is not None and desktop.allow_control and not desktop.controlling:
            machine['desktopControlAvailable'] = True
    return {'machines': listed}


@router.patch(_ORG + '/machines/{machine_id}')
async def rename(org_id: str, machine_id: str, request: Request, response: Response):
    response.headers.update(_PRIVATE)
    _, store, member_id, _ = _pairer(request, org_id)
    if not _ID.fullmatch(machine_id):
        raise _error(404, 'not_found')
    body = await _body(request, RenameBody)
    await _call(store.rename, machine_id, body.name, member_id=member_id)
    return {'id': machine_id, 'name': body.name}


@router.delete(_ORG + '/machines/{machine_id}', status_code=204)
async def remove(org_id: str, machine_id: str, request: Request):
    root, store, member_id, _ = _pairer(request, org_id)
    if not _ID.fullmatch(machine_id):
        raise _error(404, 'not_found')
    revoked = await _call(store.revoke, machine_id, member_id=member_id)
    # A replacement registration never receives the old credential's revocation.
    connections.revoked(root, machine_id, credential_epoch=revoked['credential_epoch'] if member_id else None)
    return Response(status_code=204, headers=_PRIVATE)


@router.post(_ORG + '/computer-pairings', status_code=201)
async def create_pairing(org_id: str, request: Request, response: Response):
    response.headers.update(_PRIVATE)
    _, store, member_id, _ = _pairer(request, org_id)
    body = await _body(request, PairingBody)
    return await _call(store.create_pairing, legacy_confirmed=body.legacy_confirmed, member_id=member_id)


@router.delete(_ORG + '/computer-pairings/{pairing_id}', status_code=204)
async def cancel_pairing(org_id: str, pairing_id: str, request: Request):
    _, store, member_id, _ = _pairer(request, org_id)
    if not _ID.fullmatch(pairing_id):
        raise _error(404, 'not_found')
    await _call(store.cancel_pairing, pairing_id, member_id=member_id)
    return Response(status_code=204, headers=_PRIVATE)


@router.post('/api/computers/pair')
async def pair(request: Request, response: Response):
    response.headers.update(_PRIVATE)
    if not get_api_token() or not _secure(request):
        raise _error(403, 'computer_authorization_denied')
    if (request.headers.get('authorization') or request.headers.get('cookie')
            or request.headers.get('x-api-token') or request.headers.get('origin')):
        raise _error(403, 'computer_authorization_denied')
    root = str(Path(os.getcwd()).resolve())
    source = request.client.host if request.client else 'unknown'
    if not _pairing_rates.allow(root, source):
        raise _error(429, 'computer_pairing_rate_limited')
    body = await _body(request, PairBody)
    store = ConnectedComputers(root)
    identity = await _call(store.identity)
    if identity['home_instance_id'] != body.home_instance_id:
        raise _error(403, 'computer_authorization_denied')
    return await _call(store.pair, body.code, body.connector_id, body.name)


@router.post('/api/computers/native-pair')
async def native_pair(request: Request, response: Response):
    response.headers.update(_PRIVATE)
    authorization = request.headers.get('authorization', '')
    auth_count = sum(key.lower() == b'authorization' for key, _ in request.scope.get('headers', []))
    forbidden = any(key.lower() in ('origin', 'cookie', 'forwarded', 'x-api-token')
                    or key.lower().startswith('x-forwarded-') for key in request.headers)
    if (not _native_loopback(request) or request.scope.get('query_string') or forbidden or auth_count != 1
            or not re.fullmatch(r'Native vcn_[a-f0-9]{64}', authorization)):
        raise _error(403, 'computer_native_authorization_denied')
    body = await _body(request, NativePairBody)
    root = str(Path(os.getcwd()).resolve())
    try:
        return await asyncio.to_thread(redeem, root, authorization[len('Native '):], body.connector_id, body.name)
    except ComputerNativeUnclaimed:
        raise _error(409, 'computer_owner_claim_required') from None
    except ComputerNativeDenied:
        raise _error(403, 'computer_native_authorization_denied') from None
    except (ComputerNativeUnavailable, OSError):
        raise _error(503, 'computer_native_storage_unavailable') from None
    except ValueError:
        raise _error(400, 'invalid_request') from None


def _computer_token(socket: WebSocket) -> str | None:
    authorization = socket.headers.getlist('authorization')
    if (not get_api_token() or not _secure(socket) or len(authorization) != 1 or socket.query_params
            or any(name in socket.headers for name in ('cookie', 'origin', 'x-api-token'))
            or not re.fullmatch(r'Bearer vcc_[A-Za-z0-9_-]{43}', authorization[0])):
        return None
    return authorization[0][len('Bearer '):]


@router.websocket('/api/computers/ws')
async def computer_socket(socket: WebSocket):
    token = _computer_token(socket)
    if token is None:
        await socket.close(code=1008)
        return
    root = str(Path(os.getcwd()).resolve())
    store = ConnectedComputers(root)
    try:
        binding = await asyncio.to_thread(store.authenticate, token)
        identity = await asyncio.to_thread(store.identity)
    except (ComputerDenied, ComputerUnavailable, ValueError, OSError):
        await socket.close(code=1008)
        return
    await socket.accept()
    ceiling = PROTOCOL_MAX if binding['paired_principal_id'] == identity['owner_subject'] else PROTOCOL_MIN
    await serve(socket, root, store, binding, protocol_ceiling=ceiling)


@router.websocket('/api/computers/browser-preview')
async def publish_browser_preview(socket: WebSocket):
    await _publish_private_preview(socket, 'browser')


@router.websocket('/api/computers/desktop-preview')
async def publish_desktop_preview(socket: WebSocket):
    await _publish_private_preview(socket, 'desktop')


async def _publish_private_preview(socket: WebSocket, kind: PreviewKind) -> None:
    """A connector opens an outbound data socket for the locally selected screen."""
    try:
        token = _computer_token(socket)
        if token is None:
            return
        root = str(Path(os.getcwd()).resolve())
        store = ConnectedComputers(root)
        binding = await asyncio.to_thread(store.authenticate, token)
        await serve_publisher(socket, root, binding, kind=kind)
    except (WebSocketDisconnect, ComputerDenied, RuntimeError, ValueError, KeyError, OSError, asyncio.TimeoutError):
        pass
    finally:
        with suppress(WebSocketDisconnect, RuntimeError, OSError):
            await socket.close(code=1008)


def _screen_viewer(connection: HTTPConnection, root: str) -> tuple[Principal, object | None] | None:
    token = get_api_token()
    if not token:
        return None
    if ct_eq(connection.cookies.get(SESSION_COOKIE, ''), token):
        return Principal(OWNER, views.owner_id(root), root), browser_previews.owner_epoch()
    member_id = member_auth.member_id(member_auth.account(connection))
    return (Principal(MEMBER, member_id, root), None) if member_id else None


def _private_view_allowed(connection, root, viewer, binding, kind: PreviewKind):
    actor, _ = viewer
    member_id = actor.id if actor.is_member else None
    if not ConnectedComputers(root).check_pairer(binding, member_id):
        return False
    if member_id is not None:
        level = MemberRegistry(root).standing(member_id)["computer"]
        if not screen_level_allows(level, kind, own=True):
            return False
    return _screen_viewer(connection, root) == viewer


async def _live_preview_allowed(connection: HTTPConnection, root: str, viewer: tuple[Principal, object | None],
                                preview: BrowserPreview) -> bool:
    _, owner_epoch = viewer
    return (await preview.live()
            and await asyncio.to_thread(_private_view_allowed, connection, root, viewer, preview.connection.binding, preview.kind)
            and (owner_epoch is None or browser_previews.owner_epoch() is owner_epoch)
            and preview.active())


async def _available_preview(request: Request, root: str, member_id: str | None, machine_id: str,
                             kind: PreviewKind) -> BrowserPreview | None:
    preview = browser_previews.find(root, machine_id, kind)
    if preview is None or preview.task is not None or not preview.active():
        return None
    try:
        viewer = await asyncio.to_thread(_screen_viewer, request, root)
        if viewer is None:
            return None
        actor, _ = viewer
        if (actor.id if actor.is_member else None) != member_id:
            return None
        return preview if await _live_preview_allowed(request, root, viewer, preview) else None
    except (ComputerDenied, ComputerUnavailable, Missing, OSError, ValueError):
        return None


@router.websocket(_ORG + '/machines/{machine_id}/browser-preview')
async def watch_browser_preview(socket: WebSocket, org_id: str, machine_id: str):
    await _watch_private_preview(socket, org_id, machine_id, 'browser')


@router.websocket(_ORG + '/machines/{machine_id}/desktop-preview')
async def watch_desktop_preview(socket: WebSocket, org_id: str, machine_id: str):
    await _watch_private_preview(socket, org_id, machine_id, 'desktop')


async def _watch_private_preview(socket: WebSocket, org_id: str, machine_id: str, kind: PreviewKind) -> None:
    """A person can watch their own live offer and claim its locally granted control."""
    watching = False
    try:
        root = str(Path(os.getcwd()).resolve())
        if (org_id != views.home_id(root) or not _ID.fullmatch(machine_id) or socket.query_params
                or not _secure(socket) or len(socket.headers.getlist('origin')) != 1 or not _same_origin(socket)):
            return
        viewer = await asyncio.to_thread(_screen_viewer, socket, root)
        preview = browser_previews.find(root, machine_id, kind)
        if viewer is None or preview is None or preview.task is not None:
            return
        actor, _ = viewer

        async def allowed():
            return await _live_preview_allowed(socket, root, viewer, preview)

        if not await allowed() or not preview.begin_viewing():
            return
        watching = True
        event = {'category': 'decision', 'decision': 'allowed', 'decision_source': 'user',
                 'operation': '观看私有桌面' if kind == 'desktop' else '观看私有浏览器',
                 'operation_kind': 'read', 'actor_id': actor.id,
                 'connector_id': machine_id, 'surface_id': preview.surface_id}
        await asyncio.to_thread(append_audit, root, event)

        async def desktop_control(message: dict) -> str | None:
            if message == {'type': 'take_control'} and not preview.controlling and await allowed():
                result = await preview.take_desktop_control()
                if result != 'control_ready':
                    return result
                recorded = await asyncio.to_thread(append_audit, root, {
                    **event, 'operation': '接管私有桌面', 'operation_kind': 'interact',
                })
                return result if recorded and await allowed() else None
            if (message.get('type') == 'input' and set(message) == {'type', 'action'}
                    and preview.controlling and await allowed()):
                return await preview.interact_desktop(message['action'])
            return None

        try:
            await serve_viewer(socket, preview.capture, allowed,
                               on_command=desktop_control if kind == 'desktop' and preview.allow_control else None)
        finally:
            if preview.controlling:
                preview.end()
                await asyncio.to_thread(append_audit, root, {
                    **event, 'operation': '结束私有桌面接管', 'operation_kind': 'interact',
                })
    except (WebSocketDisconnect, ComputerDenied, Missing, RuntimeError, ValueError, KeyError, OSError, asyncio.TimeoutError):
        pass
    finally:
        if watching:
            preview.end_viewing()
        with suppress(WebSocketDisconnect, RuntimeError, OSError):
            await socket.close(code=1008)


def _transfer_credentials(request: Request) -> tuple[str, str]:
    credentials = request.headers.getlist("authorization")
    tickets = request.headers.getlist("x-vortoone-transfer-ticket")
    if (not get_api_token() or not _secure(request) or request.scope.get("query_string")
            or any(name in request.headers for name in ("origin", "cookie", "x-api-token"))
            or len(credentials) != 1 or not re.fullmatch(r"Bearer vcc_[A-Za-z0-9_-]{43}", credentials[0])
            or len(tickets) != 1 or not re.fullmatch(r"vct_[A-Za-z0-9_-]{43}", tickets[0])):
        raise _error(403, "computer_transfer_denied")
    return credentials[0][len("Bearer "):], tickets[0]


@router.put(TRANSFER_PATH, status_code=201)
async def receive_transfer(request: Request, response: Response):
    response.headers.update(_PRIVATE)
    token, ticket = _transfer_credentials(request)
    lengths = request.headers.getlist("content-length")
    encodings = request.headers.getlist("content-encoding")
    if (len(lengths) != 1 or not re.fullmatch(r"0|[1-9][0-9]{0,9}", lengths[0])
            or int(lengths[0]) > MAX_TRANSFER_BYTES or encodings not in ([], ["identity"])
            or "transfer-encoding" in request.headers
            or request.headers.getlist("content-type") != ["application/octet-stream"]):
        raise _error(400, "invalid_transfer_body")
    root = str(Path(os.getcwd()).resolve())
    store = ConnectedComputers(root)
    try:
        binding = await asyncio.to_thread(store.authenticate, token, owner_only=True)
        return await computer_transfers.receive(root, binding, ticket, int(lengths[0]), request.stream())
    except (ComputerDenied, TransferDenied, ValueError):
        raise _error(403, "computer_transfer_denied") from None
    except (ComputerUnavailable, TransferUnavailable, OSError):
        raise _error(503, "computer_transfer_unavailable") from None
    except ClientDisconnect:
        raise _error(400, "transfer_disconnected") from None


class _DeliveryResponse(StreamingResponse):
    def __init__(self, transfer):
        self.transfer = transfer
        super().__init__(self._chunks(), media_type="application/octet-stream",
                         headers={**_PRIVATE, "Content-Length": str(transfer.file.size),
                                  "X-Content-Type-Options": "nosniff"})

    async def _chunks(self):
        transfer = self.transfer
        offset = 0
        next_check = 0.0
        while offset < transfer.file.size:
            now = time.monotonic()
            if transfer.aborted or now >= transfer.deadline:
                return
            if now >= next_check:
                if not await computer_transfers.valid_delivery(transfer):
                    return
                next_check = now + 1
            length = min(CHUNK_BYTES, transfer.file.size - offset)
            chunk = await file_io(os.pread, transfer.staged.fileno(), length, offset)
            if len(chunk) != length or transfer.aborted:
                return
            if offset + length == transfer.file.size and not await computer_transfers.valid_delivery(transfer):
                return
            offset += length
            yield chunk

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            computer_transfers.end_delivery(self.transfer)


@router.get(TRANSFER_PATH)
async def pull_transfer(request: Request):
    token, ticket = _transfer_credentials(request)
    if ("range" in request.headers or "transfer-encoding" in request.headers
            or request.headers.getlist("content-length") not in ([], ["0"])):
        raise _error(400, "invalid_transfer_body")
    root = str(Path(os.getcwd()).resolve())
    store = ConnectedComputers(root)
    try:
        binding = await asyncio.to_thread(store.authenticate, token, owner_only=True)
        transfer = await computer_transfers.begin_delivery(root, binding, ticket)
    except (ComputerDenied, TransferDenied, ValueError):
        raise _error(403, "computer_transfer_denied") from None
    except (ComputerUnavailable, TransferUnavailable, OSError):
        raise _error(503, "computer_transfer_unavailable") from None
    return _DeliveryResponse(transfer)


class _DownloadResponse(StreamingResponse):
    def __init__(self, transfer, store):
        self.transfer = transfer
        self.store = store
        self.completed = False
        headers = {**_PRIVATE, "Content-Length": str(transfer.file.size), "X-Content-Type-Options": "nosniff",
                   "Content-Disposition": "attachment; filename*=UTF-8''" + quote(transfer.filename, safe="")}
        super().__init__(self._chunks(), headers=headers, media_type="application/octet-stream")

    async def _chunks(self):
        offset = 0
        next_check = 0.0
        while offset < self.transfer.file.size:
            now = time.monotonic()
            if now >= self.transfer.deadline:
                return
            if now >= next_check:
                account = await asyncio.to_thread(self.store.identity)
                if not computer_transfers.owned_download(self.transfer, account):
                    return
                next_check = now + 1
            length = min(CHUNK_BYTES, self.transfer.file.size - offset)
            chunk = await file_io(os.pread, self.transfer.staged.fileno(), length, offset)
            if not chunk:
                return
            yield chunk
            offset += len(chunk)
        self.completed = True

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            computer_transfers.end_download(self.transfer, completed=self.completed)


@router.get(DOWNLOAD_PREFIX + "{transfer_id}")
async def download_transfer(transfer_id: str, request: Request):
    _require_owner_proof(request)
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise _error(403, "same_origin_required")
    if not _ID.fullmatch(transfer_id):
        raise _error(404, "not_found")
    root = str(Path(os.getcwd()).resolve())
    store = ConnectedComputers(root)
    account = await _call(store.identity)
    try:
        transfer = computer_transfers.begin_download(root, account, transfer_id)
    except TransferDenied:
        raise _error(404, "not_found") from None
    return _DownloadResponse(transfer, store)
