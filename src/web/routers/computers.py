"""Owner-managed pairing and authenticated computer control and transfer routes."""
from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, HTTPException, Request, Response, WebSocket
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.requests import ClientDisconnect
from starlette.responses import StreamingResponse

from src.gateway.computer_connections import connections, serve
from src.gateway.computer_native import (
    ComputerNativeDenied, ComputerNativeUnclaimed, ComputerNativeUnavailable, redeem,
)
from src.gateway.computer_protocol import DOWNLOAD_PREFIX, FEATURES, MAX_TRANSFER_BYTES, PROTOCOL_MIN, TRANSFER_PATH, decode_frame
from src.gateway.computer_transfers import TransferDenied, TransferUnavailable, computer_transfers
from src.gateway.connected_computers import ConnectedComputers, ComputerDenied, ComputerUnavailable
from src.web import app_views as views
from src.web.auth import SESSION_COOKIE, ct_eq, get_api_token

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
        return request.method in ('GET', 'HEAD')
    try:
        parsed = urlsplit(raw)
        served = urlsplit(str(request.base_url))
        def origin(value):
            return (value.scheme, (value.hostname or '').lower(), value.port or (443 if value.scheme == 'https' else 80))
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


def _owner(request: Request, org_id: str) -> tuple[str, ConnectedComputers]:
    _require_owner_proof(request)
    root = str(Path(os.getcwd()).resolve())
    if org_id != views.home_id(root):
        raise _error(404, 'not_found')
    return root, ConnectedComputers(root)


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


def _machine(root: str, row: dict) -> dict:
    connector = row['connector_id']
    def stamp(value):
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, timezone.utc).isoformat()
        return value if isinstance(value, str) else None
    return {
        'id': connector, 'name': row.get('name', ''), 'kind': 'connected',
        'online': row.get('active') is True and connections.online(root, connector), 'lastSeenAt': None,
        'hostname': '', 'os': 'other', 'arch': '', 'mode': 'single', 'runnerVersion': '1',
        'execAllowed': False, 'pairedBy': {'id': row['paired_principal_id'], 'name': '所有者'},
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
    root, store = _owner(request, org_id)
    rows = await _call(store.list)
    return {'machines': [_machine(root, row) for row in rows if row.get('revoked_at') is None]}


@router.patch(_ORG + '/machines/{machine_id}')
async def rename(org_id: str, machine_id: str, request: Request, response: Response):
    response.headers.update(_PRIVATE)
    _, store = _owner(request, org_id)
    if not _ID.fullmatch(machine_id):
        raise _error(404, 'not_found')
    body = await _body(request, RenameBody)
    await _call(store.rename, machine_id, body.name)
    return {'id': machine_id, 'name': body.name}


@router.delete(_ORG + '/machines/{machine_id}', status_code=204)
async def remove(org_id: str, machine_id: str, request: Request):
    root, store = _owner(request, org_id)
    if not _ID.fullmatch(machine_id):
        raise _error(404, 'not_found')
    await _call(store.revoke, machine_id)  # Persist revocation before notifying its current connection.
    connections.revoked(root, machine_id)
    return Response(status_code=204, headers=_PRIVATE)


@router.post(_ORG + '/computer-pairings', status_code=201)
async def create_pairing(org_id: str, request: Request, response: Response):
    response.headers.update(_PRIVATE)
    _, store = _owner(request, org_id)
    body = await _body(request, PairingBody)
    return await _call(store.create_pairing, legacy_confirmed=body.legacy_confirmed)


@router.delete(_ORG + '/computer-pairings/{pairing_id}', status_code=204)
async def cancel_pairing(org_id: str, pairing_id: str, request: Request):
    _, store = _owner(request, org_id)
    if not _ID.fullmatch(pairing_id):
        raise _error(404, 'not_found')
    await _call(store.cancel_pairing, pairing_id)
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


@router.websocket('/api/computers/ws')
async def computer_socket(socket: WebSocket):
    authorization = socket.headers.get('authorization', '')
    auth_count = sum(key.lower() == b'authorization' for key, _ in socket.scope.get('headers', []))
    if (not get_api_token() or not _secure(socket) or auth_count != 1 or socket.query_params
            or socket.headers.get('cookie') or socket.headers.get('origin')
            or not re.fullmatch(r'Bearer vcc_[A-Za-z0-9_-]{43}', authorization)):
        await socket.close(code=1008)
        return
    root = str(Path(os.getcwd()).resolve())
    store = ConnectedComputers(root)
    try:
        binding = await asyncio.to_thread(store.authenticate, authorization[7:])
    except (ComputerDenied, ComputerUnavailable, ValueError, OSError):
        await socket.close(code=1008)
        return
    await socket.accept()
    await serve(socket, root, store, binding)


@router.put(TRANSFER_PATH, status_code=201)
async def receive_transfer(request: Request, response: Response):
    response.headers.update(_PRIVATE)
    credentials = request.headers.getlist("authorization")
    tickets = request.headers.getlist("x-vortoone-transfer-ticket")
    if (not get_api_token() or not _secure(request) or request.scope.get("query_string")
            or any(name in request.headers for name in ("origin", "cookie", "x-api-token"))
            or len(credentials) != 1 or not re.fullmatch(r"Bearer vcc_[A-Za-z0-9_-]{43}", credentials[0])
            or len(tickets) != 1 or not re.fullmatch(r"vct_[A-Za-z0-9_-]{43}", tickets[0])):
        raise _error(403, "computer_transfer_denied")
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
        binding = await asyncio.to_thread(store.authenticate, credentials[0][len("Bearer "):])
        return await computer_transfers.receive(root, binding, tickets[0], int(lengths[0]), request.stream())
    except (ComputerDenied, TransferDenied, ValueError):
        raise _error(403, "computer_transfer_denied") from None
    except (ComputerUnavailable, TransferUnavailable, OSError):
        raise _error(503, "computer_transfer_unavailable") from None
    except ClientDisconnect:
        raise _error(400, "transfer_disconnected") from None


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
            length = min(256 * 1024, self.transfer.file.size - offset)
            read = asyncio.create_task(asyncio.to_thread(os.pread, self.transfer.staged.fileno(), length, offset))
            try:
                chunk = await asyncio.shield(read)
            except asyncio.CancelledError:
                await asyncio.gather(read, return_exceptions=True)
                raise
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
