"""Outbound-only C1 worker. Native parent owns configuration and secrets.

The first private stdin frame is configuration, subsequent EOF stops the worker.
No home bootstrap, model, agent, tool registry, local listener or .env is loaded.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import random
import re
import ssl
import sys
from typing import Awaitable, Callable

import aiohttp

from src.gateway.computer_protocol import (
    BINDING_FIELDS, DELIVERY_PROTOCOL, FEATURES, FILES_PROTOCOL, MAX_FRAME_BYTES, PROTOCOL_MAX, PROTOCOL_MIN, TRANSFER_PROTOCOL,
    ComputerProtocolError, binding_fields, control_frame, decode_frame, hello,
    identifier, origin_url, validate_welcome,
)
from src.gateway.computer_file_process import FileProcessUnavailable, run_file_action
from src.gateway.computer_file_paths import FileDenied
from src.gateway.computer_file_store import ComputerFiles, FileAction, FileStateUnavailable
from src.gateway.computer_file_wire import digest_value, file_reply, native_file_preflight, native_file_request, object_fields
from src.gateway.computer_transfer_receiver import native_delivery
from src.gateway.computer_transfer_sender import native_transfer
from src.utils.home_maintenance import HomeMaintenanceError

_TOKEN = re.compile(r"vcc_[A-Za-z0-9_-]{43}\Z")
_CONFIG_BYTES = 8192
_HEARTBEAT_INTERVAL = 10


class ConnectorFailure(RuntimeError):
    pass


def _tls_context() -> ssl.SSLContext:
    """Use a bundled CA set, without ambient CA or proxy environment settings."""
    try:
        import certifi
    except ImportError:
        if getattr(sys, 'frozen', False):
            raise ConnectorFailure('computer trust store unavailable') from None
        paths = ssl.get_default_verify_paths()
        # The resolved cafile/capath honor SSL_CERT_FILE/DIR; use compiled defaults.
        cafile = paths.openssl_cafile if paths.openssl_cafile and os.path.isfile(paths.openssl_cafile) else None
        capath = paths.openssl_capath if paths.openssl_capath and os.path.isdir(paths.openssl_capath) else None
        if cafile is not None or capath is not None:
            return ssl.create_default_context(cafile=cafile, capath=capath)
        # On Windows the platform trust store can exist without OpenSSL CA paths.
        # Read it directly rather than load_default_certs(), which also consults env.
        if hasattr(ssl, 'enum_certificates'):
            certificates = []
            for store in ('CA', 'ROOT'):
                try:
                    for certificate, encoding, trust in ssl.enum_certificates(store):
                        if encoding == 'x509_asn' and (trust is True or ssl.Purpose.SERVER_AUTH.oid in trust):
                            certificates.append(ssl.DER_cert_to_PEM_cert(certificate))
                except PermissionError:
                    continue
            if certificates:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.load_verify_locations(cadata=''.join(certificates))
                return context
        raise ConnectorFailure('computer trust store unavailable')
    return ssl.create_default_context(cafile=certifi.where())


def configuration(value: dict) -> dict:
    common = {"operation", "origin", "allow_loopback_http"}
    operation = value.get("operation")
    if operation == "identity":
        allowed, required = common, {"operation", "origin"}
    elif operation == "pair":
        allowed, required = common | {"home_instance_id", "connector_id", "name", "code"}, common - {"allow_loopback_http"} | {"home_instance_id", "connector_id", "name", "code"}
        identifier(value.get("home_instance_id"))
        identifier(value.get("connector_id"))
        name = value.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 80 or any(ord(c) < 32 for c in name):
            raise ConnectorFailure("invalid computer name")
        if not isinstance(value.get("code"), str) or not re.fullmatch(r"[A-Z2-7]{12}", value["code"]):
            raise ConnectorFailure("invalid pairing code")
    elif operation == "connect":
        allowed = common | set(BINDING_FIELDS) | {"token", "file_access"}
        required = allowed - {"allow_loopback_http", "file_access"}
        binding_fields(value)
        if type(value.get("file_access", False)) is not bool:
            raise ConnectorFailure("invalid file access configuration")
        if not isinstance(value.get("token"), str) or not _TOKEN.fullmatch(value["token"]):
            raise ConnectorFailure("invalid computer credential")
    else:
        raise ConnectorFailure("unsupported connector operation")
    if not required <= set(value) <= allowed or type(value.get("allow_loopback_http", False)) is not bool:
        raise ConnectorFailure("invalid connector configuration")
    return {**value, "origin": origin_url(value["origin"], allow_loopback_http=value.get("allow_loopback_http", False))}


async def _json_response(response) -> dict:
    if response.status != 200:
        raise ConnectorFailure("computer request refused")
    raw = bytearray()
    async for chunk in response.content.iter_chunked(4096):
        raw.extend(chunk)
        if len(raw) > 16384:
            raise ConnectorFailure("computer response too large")
    return decode_frame(bytes(raw))


async def discover(session, config: dict) -> dict:
    async with session.get(config["origin"] + "/api/computers/identity", allow_redirects=False) as response:
        value = await _json_response(response)
    if set(value) != {"home_instance_id", "protocol_min", "protocol_max", "features"}:
        raise ComputerProtocolError("invalid home identity")
    identifier(value["home_instance_id"])
    low, high = value["protocol_min"], value["protocol_max"]
    if type(low) is not int or type(high) is not int or not 1 <= low <= high or low > PROTOCOL_MAX or high < PROTOCOL_MIN or value["features"] != list(FEATURES):
        raise ComputerProtocolError("incompatible computer protocol")
    return {"type": "identity", **value}


async def pair(session, config: dict) -> dict:
    seen = await discover(session, config)
    if seen["home_instance_id"] != config["home_instance_id"]:
        raise ConnectorFailure("home identity changed")
    body = {key: config[key] for key in ("code", "connector_id", "name", "home_instance_id")}
    async with session.post(config["origin"] + "/api/computers/pair", json=body, allow_redirects=False) as response:
        grant = await _json_response(response)
    binding_fields(grant)
    if set(grant) != set(BINDING_FIELDS) | {"token"} or grant["home_instance_id"] != config["home_instance_id"] or grant["connector_id"] != config["connector_id"] or not isinstance(grant["token"], str) or not _TOKEN.fullmatch(grant["token"]):
        raise ComputerProtocolError("invalid computer credential")
    return {"type": "paired", **grant}


def _status(emit: Callable[[dict], None], config: dict, state: str) -> None:
    emit({"type": "status", "state": state, **{key: config[key] for key in ("home_instance_id", "connector_id")}})


class _FileWork:
    """One worker slot follows the local process across its control reconnects."""

    def __init__(self, stop: asyncio.Event, config: dict, session):
        self.stop = stop
        self.config = config
        self.session = session
        self.binding = binding_fields(config)
        self.revoked = asyncio.Event()
        self.task: asyncio.Task | None = None

    async def close(self):
        self.revoked.set()
        if self.task is not None:
            await asyncio.gather(self.task, return_exceptions=True)

    async def dispatch(self, ws, store, request, generation, disconnected):
        if self.task is not None and not self.task.done():
            request_id, _, body = request
            busy = ({"action_id": body.action_id, "digest": body.digest, "status": "busy"}
                    if isinstance(body, FileAction) else None)
            await ws.send_json(file_reply(request_id, generation, busy))
            return
        self.task = asyncio.create_task(self._run(ws, store, request, generation, disconnected))

    async def _run(self, ws, store, request, generation, disconnected):
        try:
            request_id, method, body = request
            if isinstance(body, FileAction):
                try:
                    data = await run_file_action(store.directory.parent, self.binding, body,
                                                 stop=self.stop, revoked=self.revoked)
                except FileProcessUnavailable:
                    data = None  # The durable local journal may still contain a terminal receipt.
                reply = file_reply(request_id, generation, data)
            elif method == "transfer_download":
                try:
                    data = await native_delivery(self.session, self.config, store, body, stop=self.stop,
                                                 revoked=self.revoked, disconnected=disconnected)
                except (FileDenied, ValueError, FileStateUnavailable, HomeMaintenanceError,
                        FileProcessUnavailable, OSError, aiohttp.ClientError, asyncio.TimeoutError):
                    data = None
                reply = file_reply(request_id, generation, data)
            elif method in ("transfer_preview", "transfer_hash", "transfer_upload"):
                def active():
                    return not (ws.closed or self.stop.is_set() or self.revoked.is_set())
                try:
                    data = await native_transfer(self.session, self.config, store, method, body, active)
                except (FileDenied, ValueError, FileStateUnavailable, HomeMaintenanceError,
                        OSError, aiohttp.ClientError, asyncio.TimeoutError):
                    data = None
                reply = file_reply(request_id, generation, data)
            else:
                reply = await asyncio.to_thread(native_file_preflight, store, request, generation)
        except Exception:  # The dispatch outcome is unknown; let the next connection drain durable receipts.
            try:
                if not ws.closed:
                    await ws.close(code=1011)
            except (aiohttp.ClientError, OSError, RuntimeError):
                pass
            return
        try:
            if not ws.closed:
                await ws.send_json(reply)
        except (aiohttp.ClientError, OSError, RuntimeError):
            pass  # The result is bound to this generation; the outbox survives the socket.


async def _control(ws, config: dict, emit: Callable[[dict], None], revocation_persisted: asyncio.Event,
                   file_work: _FileWork, disconnected: asyncio.Event) -> bool:
    files = config.get("file_access", False)
    await ws.send_json(hello(config, deliveries=files))
    message = await ws.receive(timeout=15)
    if message.type != aiohttp.WSMsgType.TEXT:
        raise ComputerProtocolError("computer handshake refused")
    frame = decode_frame(message.data)
    generation = validate_welcome(frame, config, deliveries=files)
    selected = frame["protocol"]
    store = ComputerFiles(Path.cwd() / "file-access", config) if selected >= FILES_PROTOCOL else None
    _status(emit, config, "online")
    clock = asyncio.get_running_loop().time
    next_ping = clock()
    pong_deadline = None
    while True:
        now = clock()
        if pong_deadline is not None and now >= pong_deadline:
            raise asyncio.TimeoutError
        if pong_deadline is None and now >= next_ping:
            if store is not None:
                try:
                    receipts = await asyncio.to_thread(store.pending)
                except (FileStateUnavailable, HomeMaintenanceError, OSError):
                    receipts = []
                for receipt in receipts:
                    await ws.send_json({"type": "file_receipt", "generation": generation, "receipt": receipt})
            await ws.send_json({"type": "ping", "generation": generation})
            pong_deadline = clock() + 20
        # Keep receiving between heartbeats: revocation must not wait for a sleep.
        deadline = pong_deadline if pong_deadline is not None else next_ping
        try:
            message = await ws.receive(timeout=max(0.001, deadline - clock()))
        except asyncio.TimeoutError:
            continue
        if message.type in {aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR}:
            return False
        if message.type != aiohttp.WSMsgType.TEXT:
            raise ComputerProtocolError("invalid computer control frame")
        frame = decode_frame(message.data)
        if store is not None and frame.get("type") == "file_request":
            request = native_file_request(frame, generation, config, transfers=selected >= TRANSFER_PROTOCOL,
                                          deliveries=selected >= DELIVERY_PROTOCOL)
            await file_work.dispatch(ws, store, request, generation, disconnected)
            continue
        if store is not None and frame.get("type") == "file_ack":
            object_fields(frame, ("type", "generation", "action_id", "digest"))
            if frame["generation"] != generation:
                raise ComputerProtocolError("stale file acknowledgement")
            action_id, digest = identifier(frame["action_id"]), digest_value(frame["digest"])
            try:
                await asyncio.to_thread(store.acknowledge, action_id, digest)
            except (FileStateUnavailable, OSError):
                pass
            continue
        if frame.get("type") == "revoked":
            if set(frame) != {"type", "generation", "reason", "credentialRevoked"} or frame["generation"] != generation or type(frame["credentialRevoked"]) is not bool:
                raise ComputerProtocolError("invalid computer revocation")
            reason = frame["reason"]
            if (reason, frame["credentialRevoked"]) not in {("authorization_revoked", True), ("connection_replaced", False)}:
                raise ComputerProtocolError("invalid computer revocation")
            disconnected.set()
            if frame["credentialRevoked"]:
                file_work.revoked.set()
            _status(emit, config, "revoked" if frame["credentialRevoked"] else "replaced")
            if frame["credentialRevoked"]:
                # ACK means the trusted parent has durably disabled this pairing.
                try:
                    await asyncio.wait_for(revocation_persisted.wait(), timeout=2)
                except asyncio.TimeoutError:
                    return True
            await ws.send_json({"type": "ack", "generation": generation})
            return True
        kind = control_frame(frame, generation)
        if kind == "ping":
            await ws.send_json({"type": "pong", "generation": generation})
        elif kind == "pong" and pong_deadline is not None:
            pong_deadline = None
            next_ping = clock() + _HEARTBEAT_INTERVAL
        else:
            raise ComputerProtocolError("unexpected computer acknowledgement")


async def _retry_wait(stop: asyncio.Event, delay: float) -> bool:
    try:
        await asyncio.wait_for(stop.wait(), timeout=delay)
        return True
    except asyncio.TimeoutError:
        return False


async def connect(session, config: dict, emit: Callable[[dict], None], stop: asyncio.Event,
                  revocation_persisted: asyncio.Event, *,
                  retry_wait: Callable[[asyncio.Event, float], Awaitable[bool]] = _retry_wait,
                  jitter: Callable[[float, float], float] = random.uniform) -> None:
    delay = 1.0
    file_work = _FileWork(stop, config, session)
    try:
        while not stop.is_set():
            _status(emit, config, "connecting")
            try:
                url = config["origin"].replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/api/computers/ws"
                # aiohttp follows WS redirects internally. Trace veto below rejects them before sending a second request.
                async with session.ws_connect(url, headers={"Authorization": "Bearer " + config["token"]},
                                              max_msg_size=MAX_FRAME_BYTES, autoping=True) as ws:
                    disconnected = asyncio.Event()
                    try:
                        if await _control(ws, config, emit, revocation_persisted, file_work, disconnected):
                            return
                        delay = 1.0
                    finally:
                        disconnected.set()
            except aiohttp.WSServerHandshakeError as error:
                if error.status in {401, 403}:
                    file_work.revoked.set()
                    _status(emit, config, "revoked")
                    return
            except (ValueError, TypeError, KeyError, ConnectorFailure):
                _status(emit, config, "error")
                return
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
                pass
            _status(emit, config, "disconnected")
            if await retry_wait(stop, min(60.0, max(1.0, delay * jitter(0.8, 1.2)))):
                return
            delay = min(60.0, delay * 2)
    finally:
        await file_work.close()


async def run(config: dict, emit: Callable[[dict], None], stop: asyncio.Event | None = None,
              revocation_persisted: asyncio.Event | None = None) -> None:
    config = configuration(config)
    trace = aiohttp.TraceConfig()
    async def no_redirect(_session, _context, _params):
        raise ConnectorFailure("computer redirects refused")
    trace.on_request_redirect.append(no_redirect)
    timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_read=20)
    connector = aiohttp.TCPConnector(ssl=_tls_context())
    async with aiohttp.ClientSession(connector=connector, trust_env=False, timeout=timeout,
                                     auto_decompress=False, headers={"Accept-Encoding": "identity"},
                                     trace_configs=[trace]) as session:
        if config["operation"] == "identity":
            emit(await discover(session, config))
        elif config["operation"] == "pair":
            emit(await pair(session, config))
        else:
            signal = stop or asyncio.Event()
            work = asyncio.create_task(connect(session, config, emit, signal, revocation_persisted or asyncio.Event()))
            ended = asyncio.create_task(signal.wait())
            try:
                await asyncio.wait({work, ended}, return_when=asyncio.FIRST_COMPLETED)
                if work.done():
                    await work
            finally:
                work.cancel()
                ended.cancel()
                await asyncio.gather(work, ended, return_exceptions=True)


def main() -> int:
    def emit(value):
        print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)
    try:
        descriptor = sys.stdin.fileno()
        # Never leave a daemon blocked holding BufferedReader's interpreter-shutdown
        # lock, and do not prefetch the parent's next acknowledgement into a buffer.
        raw = bytearray()
        while len(raw) <= _CONFIG_BYTES:
            part = os.read(descriptor, 1)
            if not part:
                break
            raw.extend(part)
            if part == b'\n':
                break
        if len(raw) > _CONFIG_BYTES or not raw.endswith(b"\n"):
            raise ConnectorFailure("invalid connector configuration")
        config = configuration(decode_frame(bytes(raw)))
        async def start():
            stop = asyncio.Event()
            persisted = asyncio.Event()
            # An OS thread observes only parent EOF; it never parses additional commands.
            # A daemon avoids blocking interpreter shutdown if the connection is revoked first.
            import threading
            loop = asyncio.get_running_loop()
            def parent_lifetime():
                pending = b''
                try:
                    while True:
                        chunk = os.read(descriptor, 128)
                        if not chunk:
                            break
                        pending += chunk
                        while b'\n' in pending:
                            line, pending = pending.split(b'\n', 1)
                            if len(line) > 127 or decode_frame(line) != {"type": "revocation_persisted"}:
                                return
                            loop.call_soon_threadsafe(persisted.set)
                        if len(pending) > 127:
                            break
                except (OSError, ComputerProtocolError, RuntimeError):
                    pass
                finally:
                    try:
                        loop.call_soon_threadsafe(stop.set)
                    except RuntimeError:
                        pass
            threading.Thread(target=parent_lifetime, daemon=True).start()
            work = asyncio.create_task(run(config, emit, stop, persisted))
            ended = asyncio.create_task(stop.wait())
            try:
                await asyncio.wait({work, ended}, return_when=asyncio.FIRST_COMPLETED)
                if work.done():
                    await work
            finally:
                work.cancel()
                ended.cancel()
                await asyncio.gather(work, ended, return_exceptions=True)
        asyncio.run(start())
        return 0
    except (ConnectorFailure, ComputerProtocolError, aiohttp.ClientError, OSError, ValueError, asyncio.TimeoutError):
        emit({"type": "error", "error": "computer_connector_unavailable"})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
