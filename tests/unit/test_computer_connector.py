"""Native-worker control protocol, using real temporary loopback HTTP/WS peers."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
import ssl
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest
from aiohttp import web

from src.gateway import computer_connector as worker
from src.gateway.computer_connections import connections, serve
from src.gateway.computer_protocol import (
    BINDING_FIELDS, CAPABILITIES, MAX_FRAME_BYTES, ComputerProtocolError,
    decode_frame, hello, origin_url, validate_hello, validate_welcome, welcome,
)
from src.gateway.connected_computers import ConnectedComputers

REPO = Path(__file__).resolve().parents[2]
BINDING = {field: format(index, 'x') * 32 for index, field in enumerate(BINDING_FIELDS, 1)}
TOKEN = 'vcc_' + 'x' * 43
GENERATION = 'f' * 32


def config(origin, operation='connect', **changes):
    value = {'operation': operation, 'origin': origin, 'allow_loopback_http': True}
    if operation == 'connect':
        value.update(BINDING, token=TOKEN)
    elif operation == 'pair':
        value.update(home_instance_id=BINDING['home_instance_id'], connector_id=BINDING['connector_id'],
                     name='Test computer', code='ABCD2345EFGH')
    return {**value, **changes}


def identity(home_id=BINDING['home_instance_id']):
    return {'home_instance_id': home_id, 'protocol_min': 1, 'protocol_max': 1, 'features': ['control-only']}


@asynccontextmanager
async def service(routes, *, ssl_context=None):
    app = web.Application()
    for method, path, handler in routes:
        app.router.add_route(method, path, handler)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0, ssl_context=ssl_context)
    await site.start()
    origin = f'{"https" if ssl_context else "http"}://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
    try:
        yield origin
    finally:
        await runner.cleanup()


async def event_before(event, seconds=2):
    await asyncio.wait_for(event.wait(), seconds)


async def finished(task, seconds=2):
    return await asyncio.wait_for(task, seconds)


async def handshake(request, *, frame=None):
    assert request.headers.get('Authorization') == 'Bearer ' + TOKEN
    socket = web.WebSocketResponse(max_msg_size=MAX_FRAME_BYTES)
    await socket.prepare(request)
    first = decode_frame((await socket.receive()).data)
    validate_hello(first, BINDING)
    await socket.send_json(frame if frame is not None else welcome(BINDING, GENERATION))
    return socket


@pytest.mark.parametrize('compressed', [False, True])
async def test_identity_response_is_bounded_across_fragments_and_rejects_compression(compressed):
    import gzip
    async def response(request):
        assert request.headers['Accept-Encoding'] == 'identity'
        raw = json.dumps(identity()).encode()
        headers = {'Content-Encoding': 'gzip'} if compressed else {}
        if compressed:
            raw = gzip.compress(raw)
        answer = web.StreamResponse(headers=headers)
        await answer.prepare(request)
        await answer.write(raw[:10])
        await asyncio.sleep(0.01)
        await answer.write(raw[10:])
        await answer.write_eof()
        return answer
    async with service([('GET', '/api/computers/identity', response)]) as origin:
        seen = []
        if compressed:
            with pytest.raises(ComputerProtocolError):
                await worker.run(config(origin, 'identity'), seen.append)
            assert seen == []
        else:
            await worker.run(config(origin, 'identity'), seen.append)
            assert seen == [{'type': 'identity', **identity()}]


@pytest.mark.parametrize('address', [
    'http://example.test', 'http://127.0.0.1', 'ftp://example.test', '//example.test',
    'https://user:password@example.test', 'https://example.test/path',
    'https://example.test/?secret=yes', 'https://example.test/#secret',
    'https://example.test:0', 'https://example.test:65536', 'https://example.test:bad',
    'https://example.test\n', 'https://example.test\\evil', 'https://example..test',
    'https://%65xample.test', '', None, 7,
])
def test_home_address_rejects_unsafe_or_ambiguous_origin(address):
    with pytest.raises(ComputerProtocolError):
        origin_url(address)


@pytest.mark.parametrize('address', ['http://localhost', 'http://example.test', 'http://192.168.1.2', 'http://127.0.0.1.evil.test'])
def test_plaintext_opt_in_is_only_for_literal_loopback(address):
    with pytest.raises(ComputerProtocolError):
        origin_url(address, allow_loopback_http=True)


def test_origin_normalization_and_local_http_explicit_consent():
    assert origin_url('https://EXAMPLE.test:443/') == 'https://example.test'
    assert origin_url('https://example.test:8443/') == 'https://example.test:8443'
    assert origin_url('http://127.0.0.1:8080/', allow_loopback_http=True) == 'http://127.0.0.1:8080'
    assert origin_url('http://[::1]:8080', allow_loopback_http=True) == 'http://[::1]:8080'
    with pytest.raises(ComputerProtocolError):
        worker.configuration(config('http://127.0.0.1', allow_loopback_http=False))


@pytest.mark.parametrize('raw', [
    '{"type":"ping","type":"pong"}', '{"nested":{"key":1,"key":2}}',
    '{"number":NaN}', '{"number":Infinity}', '{"number":-Infinity}', '[]', 'null',
    b'{"x":"\xff"}', 'x' * (MAX_FRAME_BYTES + 1),
])
def test_protocol_decoder_refuses_ambiguous_nonfinite_and_oversized_frames(raw):
    with pytest.raises(ComputerProtocolError):
        decode_frame(raw)


@pytest.mark.parametrize('field', BINDING_FIELDS)
def test_protocol_binding_requires_exact_string_identity_and_epoch(field):
    for invalid in (True, 1, 'a' * 31, 'A' * 32, 'g' * 32):
        with pytest.raises(ComputerProtocolError):
            hello({**BINDING, field: invalid})


@pytest.mark.parametrize('change', [
    {'protocol': True}, {'protocol': 1.0}, {'features': []}, {'folders': ['/']},
    {'capabilities': {**CAPABILITIES, 'command': 0}},
    {'capabilities': {**CAPABILITIES, 'command': True}}, {'generation': 'x' * 32},
    {'trust_epoch': '0' * 32}, {'extra': 'execution'},
])
def test_welcome_never_grants_execution_or_changes_identity(change):
    assert validate_welcome(welcome(BINDING, GENERATION), BINDING) == GENERATION
    with pytest.raises(ComputerProtocolError):
        validate_welcome({**welcome(BINDING, GENERATION), **change}, BINDING)


class ServerSocket:
    """Adapt aiohttp transport to the actual production control-only serve loop."""

    def __init__(self, socket, ack_seen):
        self.socket = socket
        self.ack_seen = ack_seen

    async def receive(self):
        message = await self.socket.receive()
        if message.type == aiohttp.WSMsgType.TEXT:
            frame = decode_frame(message.data)
            if frame.get('type') == 'ack':
                self.ack_seen.set()
            return {'type': 'websocket.receive', 'text': message.data}
        if message.type == aiohttp.WSMsgType.BINARY:
            return {'type': 'websocket.receive', 'bytes': message.data}
        return {'type': 'websocket.disconnect', 'code': self.socket.close_code or 1000}

    async def send_json(self, frame):
        await self.socket.send_json(frame)

    async def close(self, code=1000):
        await self.socket.close(code=code)


async def test_real_store_server_and_worker_pair_connect_and_persist_before_revoke_ack(tmp_path, monkeypatch):
    monkeypatch.setenv('VORTOONE_API_TOKEN', 'synthetic-owner')
    store = ConnectedComputers(tmp_path)
    made = store.create_pairing(legacy_confirmed=True)
    ack_seen = asyncio.Event()
    server_done = asyncio.Event()
    events = []
    revoked = asyncio.Event()
    online = asyncio.Event()
    persisted = asyncio.Event()
    stop = asyncio.Event()

    async def home_identity(request):
        return web.json_response(identity(store.identity()['home_instance_id']))

    async def pairing(request):
        body = await request.json()
        assert body['home_instance_id'] == store.identity()['home_instance_id']
        return web.json_response(store.pair(body['code'], body['connector_id'], body['name']))

    async def socket_handler(request):
        binding = store.authenticate(request.headers['Authorization'].removeprefix('Bearer '))
        socket = web.WebSocketResponse(max_msg_size=MAX_FRAME_BYTES)
        await socket.prepare(request)
        try:
            await serve(ServerSocket(socket, ack_seen), str(tmp_path), store, binding)
        finally:
            server_done.set()
        return socket

    def emit(frame):
        events.append(frame)
        if frame.get('state') == 'online':
            online.set()
        if frame.get('state') == 'revoked':
            revoked.set()

    routes = [('GET', '/api/computers/identity', home_identity),
              ('POST', '/api/computers/pair', pairing), ('GET', '/api/computers/ws', socket_handler)]
    async with service(routes) as origin:
        discovered = []
        await worker.run(config(origin, 'identity'), discovered.append)
        assert discovered == [{'type': 'identity', **identity(made['home_instance_id'])}]
        paired = []
        await worker.run(config(origin, 'pair', home_instance_id=made['home_instance_id'], code=made['code']), paired.append)
        grant = paired[0].copy()
        assert grant.pop('type') == 'paired'
        assert all(isinstance(grant[field], str) and len(grant[field]) == 32 for field in BINDING_FIELDS)
        task = asyncio.create_task(worker.run(config(origin, **grant), emit, stop, persisted))
        try:
            await event_before(online)
            assert connections.online(str(tmp_path), grant['connector_id'])
            # Revoke just after the first pong, while the previous implementation slept.
            await asyncio.sleep(0.03)
            store.revoke(grant['connector_id'])
            connections.revoked(str(tmp_path), grant['connector_id'])
            await event_before(revoked, 0.8)
            assert not ack_seen.is_set() and not task.done()
            persisted.set()
            await event_before(ack_seen)
            await finished(task)
            await event_before(server_done)
        finally:
            stop.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert [event['state'] for event in events] == ['connecting', 'online', 'revoked']
    assert not store.check(grant) and not connections.online(str(tmp_path), grant['connector_id'])
    assert grant['token'] not in json.dumps(events)


@pytest.mark.parametrize('operation', ['identity', 'pair', 'connect'])
async def test_redirect_is_refused_before_any_credential_reaches_second_origin(operation):
    leaked = []

    async def trap(request):
        leaked.append((dict(request.headers), await request.read()))
        return web.json_response(identity())

    async with service([('*', '/leak', trap)]) as target:
        async def redirect(request):
            return web.Response(status=307, headers={'Location': target + '/leak'})

        routes = [('GET', '/api/computers/identity', redirect), ('GET', '/api/computers/ws', redirect)]
        async with service(routes) as origin:
            events = []
            if operation == 'connect':
                await worker.run(config(origin), events.append)
                assert [value['state'] for value in events] == ['connecting', 'error']
            else:
                with pytest.raises(worker.ConnectorFailure):
                    await worker.run(config(origin, operation), events.append)
            assert not leaked
            assert TOKEN not in json.dumps(events) and 'ABCD2345EFGH' not in json.dumps(events)


async def test_pair_stops_on_changed_home_before_sending_pair_code():
    attempts = []

    async def changed(request):
        return web.json_response(identity('0' * 32))

    async def pairing(request):
        attempts.append(await request.read())
        return web.json_response({})

    async with service([('GET', '/api/computers/identity', changed), ('POST', '/api/computers/pair', pairing)]) as origin:
        with pytest.raises(worker.ConnectorFailure, match='home identity changed'):
            await worker.run(config(origin, 'pair'), lambda _: None)
    assert attempts == []


async def test_pair_post_redirect_does_not_forward_one_time_code():
    leaked = []

    async def trap(request):
        leaked.append(await request.read())
        return web.json_response({**BINDING, 'token': TOKEN})

    async def home_identity(request):
        return web.json_response(identity())

    async with service([('*', '/leak', trap)]) as target:
        async def redirect(request):
            assert (await request.json())['code'] == 'ABCD2345EFGH'
            return web.Response(status=307, headers={'Location': target + '/leak'})

        async with service([('GET', '/api/computers/identity', home_identity),
                            ('POST', '/api/computers/pair', redirect)]) as origin:
            with pytest.raises(worker.ConnectorFailure):
                await worker.run(config(origin, 'pair'), lambda _: None)
    assert not leaked


@pytest.mark.parametrize('hostile', ['execute', 'command', 'binary', 'duplicate', 'wrong-generation', 'enabled-tool'])
async def test_untrusted_peer_cannot_dispatch_execution_and_invalid_frames_stop_worker(hostile, tmp_path):
    attempts = []
    sent = []
    marker = tmp_path / 'must-not-exist'

    async def socket_handler(request):
        attempts.append(True)
        frame = welcome(BINDING, GENERATION)
        if hostile == 'enabled-tool':
            frame['capabilities']['command'] = True
        socket = await handshake(request, frame=frame)
        if hostile != 'enabled-tool':
            sent.append(decode_frame((await socket.receive()).data))
            if hostile == 'binary':
                await socket.send_bytes(b'{}')
            elif hostile == 'duplicate':
                await socket.send_str('{"type":"pong","generation":"' + GENERATION + '","type":"pong"}')
            elif hostile == 'wrong-generation':
                await socket.send_json({'type': 'pong', 'generation': '0' * 32})
            else:
                await socket.send_json({'type': hostile, 'generation': GENERATION, 'command': f'touch {marker}'})
        async for _ in socket:
            pass
        return socket

    async with service([('GET', '/api/computers/ws', socket_handler)]) as origin:
        events = []
        await finished(asyncio.create_task(worker.run(config(origin), events.append)))
    assert not marker.exists() and len(attempts) == 1
    assert events[-1]['state'] == 'error'
    assert all(frame['type'] == 'ping' for frame in sent)
    assert TOKEN not in json.dumps(events)


@pytest.mark.parametrize('credential_revoked', [True, False])
async def test_revocation_waits_for_native_durable_event_and_replacement_never_reconnects(credential_revoked):
    persisted = asyncio.Event()
    notified = asyncio.Event()
    ack_seen = asyncio.Event()
    attempts = []
    frames = []

    async def socket_handler(request):
        attempts.append(True)
        socket = await handshake(request)
        ping = decode_frame((await socket.receive()).data)
        assert ping == {'type': 'ping', 'generation': GENERATION}
        await socket.send_json({'type': 'pong', 'generation': GENERATION})
        await socket.send_json({'type': 'revoked', 'generation': GENERATION,
                                'reason': 'authorization_revoked' if credential_revoked else 'connection_replaced',
                                'credentialRevoked': credential_revoked})
        message = await socket.receive(timeout=2)
        frames.append(decode_frame(message.data))
        ack_seen.set()
        await socket.close()
        return socket

    def emit(value):
        if value.get('state') in {'revoked', 'replaced'}:
            notified.set()

    async with service([('GET', '/api/computers/ws', socket_handler)]) as origin:
        task = asyncio.create_task(worker.run(config(origin), emit, revocation_persisted=persisted))
        try:
            await event_before(notified, 0.8)
            if credential_revoked:
                assert not ack_seen.is_set() and not task.done()
                # The event models native key-store persistence, not receipt of the WS frame.
                persisted.set()
            await event_before(ack_seen)
            await finished(task)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert frames == [{'type': 'ack', 'generation': GENERATION}] and len(attempts) == 1


@pytest.mark.parametrize('status', [401, 403])
async def test_rejected_credential_ends_worker_without_reconnect(status):
    attempts = []

    async def reject(request):
        attempts.append(True)
        return web.Response(status=status)

    async with service([('GET', '/api/computers/ws', reject)]) as origin:
        events = []
        await worker.run(config(origin), events.append)
    assert len(attempts) == 1 and [event['state'] for event in events] == ['connecting', 'revoked']


@pytest.mark.parametrize('jitter_value', [0.8, 1.0, 1.2])
async def test_real_connection_failures_have_bounded_injectable_backoff(jitter_value):
    attempts = []
    delays = []
    stop = asyncio.Event()

    async def unavailable(request):
        attempts.append(True)
        return web.Response(status=503)

    async def retry_wait(signal, delay):
        assert signal is stop
        delays.append(delay)
        return len(delays) == 8

    async with service([('GET', '/api/computers/ws', unavailable)]) as origin:
        async with aiohttp.ClientSession(trust_env=False) as session:
            events = []
            await worker.connect(session, config(origin), events.append, stop, asyncio.Event(),
                                 retry_wait=retry_wait, jitter=lambda _low, _high: jitter_value)
    assert delays == [min(60, max(1, value * jitter_value)) for value in [1, 2, 4, 8, 16, 32, 60, 60]]
    assert all(1 <= delay <= 60 for delay in delays) and len(attempts) == 8
    assert all(value['state'] in {'connecting', 'disconnected'} for value in events)


async def test_peer_disconnect_cannot_execute_and_only_uses_retry_path():
    received = []
    delays = []

    async def socket_handler(request):
        socket = await handshake(request)
        message = await socket.receive()
        received.append(decode_frame(message.data))
        await socket.close()
        return socket

    async def retry_wait(_stop, delay):
        delays.append(delay)
        return True

    async with service([('GET', '/api/computers/ws', socket_handler)]) as origin:
        async with aiohttp.ClientSession(trust_env=False) as session:
            events = []
            await worker.connect(session, config(origin), events.append, asyncio.Event(), asyncio.Event(),
                                 retry_wait=retry_wait, jitter=lambda _low, _high: 1.0)
    assert received == [{'type': 'ping', 'generation': GENERATION}] and delays == [1]
    assert [value['state'] for value in events] == ['connecting', 'online', 'disconnected']



async def test_control_stops_when_the_final_receive_times_out():
    from types import SimpleNamespace

    stop = asyncio.Event()
    events = []

    class Socket:
        async def send_json(self, _message):
            pass

        async def receive(self, *, timeout):
            if not events or events[-1]["state"] != "online":
                return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(welcome(BINDING, GENERATION)))
            if stop.is_set():
                pytest.fail("the control loop read another frame after its parent stopped")
            # The socket can report its receive deadline at the same time that
            # the parent asks the connector to exit.
            stop.set()
            raise asyncio.TimeoutError

    class Session:
        @asynccontextmanager
        async def ws_connect(self, _url, **_options):
            yield Socket()

    await worker.connect(Session(), config("https://home.test"), events.append, stop, asyncio.Event())
    assert any(event["state"] == "online" for event in events)


def isolated_environment(tmp_path):
    cwd, user_home = tmp_path / 'cwd', tmp_path / 'user-home'
    cwd.mkdir()
    user_home.mkdir()
    (cwd / '.env').write_text('OPENAI_API_KEY=must-never-be-loaded\n')
    # Exercise each real entry's command dispatch, and fail if it bootstraps a home,
    # reads .env or imports an execution stack. No test replacement of worker.main.
    (cwd / 'sitecustomize.py').write_text('''
import importlib
import sys
def forbidden(*args, **kwargs):
    raise RuntimeError("connector attempted home/bootstrap/env access")
bootstrap = importlib.import_module("src.runtime_bootstrap")
bootstrap.prepare_runtime = forbidden
bootstrap.setup_logging = forbidden
importlib.import_module("src.env_compat").load_env_file = forbidden
state = importlib.import_module("src.utils.state_dir")
state.state_dir = forbidden
state.home_state_dir = forbidden
class NoExecution:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(("src.agents", "src.tools", "src.llm", "src.web", "src.core")):
            raise RuntimeError("connector imported execution authority")
sys.meta_path.insert(0, NoExecution())
''')
    environment = {**os.environ, 'HOME': str(user_home), 'PYTHONPATH': os.pathsep.join((str(cwd), str(REPO))),
                   'PYTHONDONTWRITEBYTECODE': '1', 'HTTP_PROXY': 'http://127.0.0.1:1',
                   'HTTPS_PROXY': 'http://127.0.0.1:1', 'ALL_PROXY': 'http://127.0.0.1:1', 'NO_PROXY': ''}
    return cwd, user_home, environment


@pytest.mark.parametrize('entry', ['src.runtime_entry', 'desktop.runtime_entry'])
async def test_actual_worker_entries_skip_bootstrap_env_and_home_state(entry, tmp_path):
    async def home_identity(request):
        assert 'Authorization' not in request.headers
        return web.json_response(identity())

    cwd, user_home, environment = isolated_environment(tmp_path)
    before = sorted(path.name for path in cwd.iterdir())
    async with service([('GET', '/api/computers/identity', home_identity)]) as origin:
        process = await asyncio.create_subprocess_exec(sys.executable, '-m', entry, 'computer-connector',
                                                     cwd=cwd, env=environment, stdin=asyncio.subprocess.PIPE,
                                                     stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            process.stdin.write(json.dumps(config(origin, 'identity')).encode() + b'\n')
            await process.stdin.drain()
            stdout = await asyncio.wait_for(process.stdout.readline(), 8)
            await asyncio.wait_for(process.wait(), 2)
            stderr = await process.stderr.read()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            process.stdin.close()
    assert process.returncode == 0, stderr.decode()
    assert json.loads(stdout) == {'type': 'identity', **identity()}
    assert not stderr and not list(user_home.iterdir())
    assert sorted(path.name for path in cwd.iterdir()) == before


@pytest.mark.parametrize('entry', ['src.runtime_entry', 'desktop.runtime_entry'])
async def test_parent_stdin_eof_terminates_actual_worker_without_home_or_execution(entry, tmp_path):
    received = []
    socket_closed = asyncio.Event()

    async def socket_handler(request):
        socket = await handshake(request)
        async for message in socket:
            frame = decode_frame(message.data)
            received.append(frame)
            assert frame == {'type': 'ping', 'generation': GENERATION}
            await socket.send_json({'type': 'pong', 'generation': GENERATION})
        socket_closed.set()
        return socket

    cwd, user_home, environment = isolated_environment(tmp_path)
    before = sorted(path.name for path in cwd.iterdir())
    async with service([('GET', '/api/computers/ws', socket_handler)]) as origin:
        process = await asyncio.create_subprocess_exec(sys.executable, '-m', entry, 'computer-connector',
                                                     cwd=cwd, env=environment, stdin=asyncio.subprocess.PIPE,
                                                     stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            process.stdin.write(json.dumps(config(origin)).encode() + b'\n')
            await process.stdin.drain()
            states = []
            while 'online' not in states:
                line = await asyncio.wait_for(process.stdout.readline(), 8)
                assert line, 'worker exited before handshake'
                states.append(json.loads(line)['state'])
            process.stdin.close()
            await asyncio.wait_for(process.wait(), 2)
            stderr = await process.stderr.read()
            assert process.returncode == 0 and not stderr
            await event_before(socket_closed)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
    assert states == ['connecting', 'online'] and not list(user_home.iterdir())
    assert sorted(path.name for path in cwd.iterdir()) == before
    assert all(frame['type'] == 'ping' for frame in received)


@pytest.mark.parametrize('entry', ['src.runtime_entry', 'desktop.runtime_entry'])
@pytest.mark.parametrize('operation', ['identity', 'pair'])
async def test_parent_eof_cancels_hanging_identity_and_pair(entry, operation, tmp_path):
    request_started = asyncio.Event()
    release_response = asyncio.Event()

    async def hanging(request):
        request_started.set()
        await release_response.wait()
        return web.json_response(identity())

    async def home_identity(request):
        return web.json_response(identity())

    cwd, user_home, environment = isolated_environment(tmp_path)
    before = sorted(path.name for path in cwd.iterdir())
    routes = [('GET', '/api/computers/identity', hanging if operation == 'identity' else home_identity),
              ('POST', '/api/computers/pair', hanging)]
    async with service(routes) as origin:
        process = await asyncio.create_subprocess_exec(sys.executable, '-m', entry, 'computer-connector',
                                                     cwd=cwd, env=environment, stdin=asyncio.subprocess.PIPE,
                                                     stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            process.stdin.write(json.dumps(config(origin, operation)).encode() + b'\n')
            await process.stdin.drain()
            await event_before(request_started, 8)
            process.stdin.close()
            await asyncio.wait_for(process.wait(), 2)
            assert process.returncode == 0
            assert not await process.stdout.read() and not await process.stderr.read()
        finally:
            release_response.set()
            if process.returncode is None:
                process.kill()
                await process.wait()
    assert not list(user_home.iterdir())
    assert sorted(path.name for path in cwd.iterdir()) == before


def tls_material(directory):
    """A test-only CA and loopback leaf; never uses the host's trusted certificates."""
    x509 = pytest.importorskip('cryptography.x509')
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    directory.mkdir()
    now = datetime.now(timezone.utc)
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'computer-test-ca')])
    ca = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
          .not_valid_after(now + timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .sign(key, hashes.SHA256()))
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, '127.0.0.1')]))
            .issuer_name(subject).public_key(leaf_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
            .sign(key, hashes.SHA256()))
    ca_file = directory / 'ca.pem'
    leaf_file = directory / 'leaf.pem'
    key_file = directory / 'leaf.key'
    ca_file.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    leaf_file.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(leaf_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(leaf_file, key_file)
    return ca_file, ca.public_bytes(serialization.Encoding.DER), context


@pytest.mark.parametrize('trusted', [True, False])
async def test_real_https_validates_injected_ca_and_rejects_untrusted_peer(tmp_path, monkeypatch, trusted):
    ca_file, _, server_context = tls_material(tmp_path / 'server')
    unrelated_ca, _, _ = tls_material(tmp_path / 'unrelated')
    monkeypatch.setattr(worker, '_tls_context', lambda: ssl.create_default_context(cafile=ca_file if trusted else unrelated_ca))
    requests = []

    async def home_identity(request):
        requests.append(True)
        return web.json_response(identity())

    async with service([('GET', '/api/computers/identity', home_identity)], ssl_context=server_context) as origin:
        events = []
        if trusted:
            await worker.run(config(origin, 'identity'), events.append)
            assert events == [{'type': 'identity', **identity()}] and len(requests) == 1
        else:
            with pytest.raises(aiohttp.ClientConnectorCertificateError):
                await worker.run(config(origin, 'identity'), events.append)
            assert not events and not requests


@pytest.mark.parametrize('has_certifi', [True, False])
def test_ca_selection_ignores_ambient_ca_environment(tmp_path, monkeypatch, has_certifi):
    ca_file, expected_der, _ = tls_material(tmp_path / 'trusted')
    injected_ca, _, _ = tls_material(tmp_path / 'ambient')
    monkeypatch.setenv('SSL_CERT_FILE', str(injected_ca))
    monkeypatch.setenv('SSL_CERT_DIR', str(tmp_path / 'ambient'))
    monkeypatch.setenv('REQUESTS_CA_BUNDLE', str(injected_ca))
    monkeypatch.setenv('CURL_CA_BUNDLE', str(injected_ca))
    monkeypatch.setattr(sys, 'frozen', False, raising=False)
    if has_certifi:
        monkeypatch.setitem(sys.modules, 'certifi', SimpleNamespace(where=lambda: str(ca_file)))
    else:
        monkeypatch.setitem(sys.modules, 'certifi', None)
        monkeypatch.setattr(ssl, 'get_default_verify_paths', lambda: SimpleNamespace(
            cafile=str(injected_ca), capath=str(tmp_path / 'ambient'),
            openssl_cafile=str(ca_file), openssl_capath=str(tmp_path / 'not-a-directory')))
    context = worker._tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert context.get_ca_certs(binary_form=True) == [expected_der]


def test_frozen_worker_without_packaged_ca_refuses_startup(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setitem(sys.modules, 'certifi', None)
    with pytest.raises(worker.ConnectorFailure, match='computer trust store unavailable'):
        worker._tls_context()


def test_missing_system_ca_never_falls_back_to_ambient_ca(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'frozen', False, raising=False)
    monkeypatch.setitem(sys.modules, 'certifi', None)
    monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path / 'injected-ca.pem'))
    monkeypatch.setattr(ssl, 'get_default_verify_paths', lambda: SimpleNamespace(
        openssl_cafile=None, openssl_capath=None))
    monkeypatch.delattr(ssl, 'enum_certificates', raising=False)
    with pytest.raises(worker.ConnectorFailure, match='computer trust store unavailable'):
        worker._tls_context()


def test_system_windows_ca_fallback_uses_only_server_trusted_certificates(tmp_path, monkeypatch):
    _, expected_der, _ = tls_material(tmp_path / 'system')
    _, unrelated_der, _ = tls_material(tmp_path / 'unrelated')
    monkeypatch.setattr(sys, 'frozen', False, raising=False)
    monkeypatch.setitem(sys.modules, 'certifi', None)
    monkeypatch.setattr(ssl, 'get_default_verify_paths', lambda: SimpleNamespace(
        openssl_cafile=None, openssl_capath=None))
    monkeypatch.setattr(ssl, 'enum_certificates', lambda store: [
        (expected_der, 'x509_asn', {ssl.Purpose.SERVER_AUTH.oid}),
        (unrelated_der, 'x509_asn', {'client-auth-only'}),
        (unrelated_der, 'pkcs_7_asn', True),
    ] if store == 'ROOT' else [], raising=False)
    context = worker._tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    assert context.get_ca_certs(binary_form=True) == [expected_der]


@pytest.mark.parametrize('entry', ['src.runtime_entry', 'desktop.runtime_entry'])
async def test_actual_worker_persists_revocation_before_ack_and_exits_with_stdin_still_open(entry, tmp_path):
    ack_seen = asyncio.Event()

    async def socket_handler(request):
        peer = await handshake(request)
        ping = decode_frame((await peer.receive()).data)
        assert ping == {'type': 'ping', 'generation': GENERATION}
        await peer.send_json({'type': 'pong', 'generation': GENERATION})
        await peer.send_json({'type': 'revoked', 'generation': GENERATION,
                              'reason': 'authorization_revoked', 'credentialRevoked': True})
        ack = decode_frame((await peer.receive(timeout=2)).data)
        assert ack == {'type': 'ack', 'generation': GENERATION}
        ack_seen.set()
        await peer.close()
        return peer

    cwd, user_home, environment = isolated_environment(tmp_path)
    async with service([('GET', '/api/computers/ws', socket_handler)]) as origin:
        process = await asyncio.create_subprocess_exec(sys.executable, '-m', entry, 'computer-connector',
                                                     cwd=cwd, env=environment, stdin=asyncio.subprocess.PIPE,
                                                     stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            process.stdin.write(json.dumps(config(origin)).encode() + b'\n')
            await process.stdin.drain()
            states = []
            while 'revoked' not in states:
                line = await asyncio.wait_for(process.stdout.readline(), 8)
                assert line
                states.append(json.loads(line)['state'])
            assert not ack_seen.is_set()
            process.stdin.write(b'{"type":"revocation_persisted"}\n')
            await process.stdin.drain()
            await event_before(ack_seen)
            await asyncio.wait_for(process.wait(), 2)
            assert process.returncode == 0 and not await process.stderr.read()
            assert states == ['connecting', 'online', 'revoked']
        finally:
            process.stdin.close()
            if process.returncode is None:
                process.kill()
                await process.wait()
    assert not list(user_home.iterdir())


@pytest.mark.parametrize("file_access", [False, True], ids=["control-v1", "file-process-v4"])
async def test_actual_fastapi_routes_and_worker_interoperate_on_real_loopback(tmp_path, monkeypatch, file_access):
    """Cover the real mounted auth/routes, not just the transport test adapter."""
    import uvicorn
    from src.web import app_views
    from src.web.server import app

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VORTOONE_API_TOKEN', 'loopback-test-owner')
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', 0))
    origin = f'http://127.0.0.1:{listener.getsockname()[1]}'
    server = uvicorn.Server(uvicorn.Config(app, lifespan='off', log_level='critical', access_log=False,
                                         log_config=None, ws='websockets'))
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    connected = asyncio.Event()
    revoked = asyncio.Event()
    persisted = asyncio.Event()
    stop = asyncio.Event()
    events = []
    worker_task = None

    def emit(frame):
        events.append(frame)
        if frame.get('state') == 'online':
            connected.set()
        if frame.get('state') == 'revoked':
            revoked.set()

    try:
        async def ready():
            while not server.started:
                if server_task.done():
                    await server_task
                await asyncio.sleep(0.005)
        await asyncio.wait_for(ready(), 2)
        base = f'/api/app/organizations/{app_views.home_id(str(tmp_path))}'
        async with aiohttp.ClientSession(trust_env=False, headers={'Authorization': 'Bearer loopback-test-owner'}) as owner:
            async with owner.post(origin + base + '/computer-pairings', json={'legacy_confirmed': True}) as response:
                assert response.status == 201
                pairing = await response.json()
            pairs = []
            await worker.run(config(origin, 'pair', home_instance_id=pairing['home_instance_id'], code=pairing['code']), pairs.append)
            grant = {key: value for key, value in pairs[0].items() if key != 'type'}
            worker_task = asyncio.create_task(worker.run(config(origin, **grant, file_access=file_access), emit, stop, persisted))
            await event_before(connected)
            async with owner.get(origin + base + '/machines') as response:
                machines = (await response.json())['machines']
                machine = next(row for row in machines if row['id'] == grant['connector_id'])
                assert machine['online'] and not machine['execAllowed']
                assert machine['access']['folders'] == [] and not any(machine['access']['tools'].values())
            if file_access:
                from dataclasses import asdict
                import time
                import uuid

                from src.gateway.computer_file_intents import HomeFileIntents
                from src.gateway.computer_file_store import ComputerFiles, FileAction
                from src.gateway.computer_file_wire import result_value, version_value

                root = str(tmp_path.resolve())
                account = ConnectedComputers(root).identity()
                connection = connections.file_connection(root, grant["connector_id"], account)
                assert connection is not None
                node = ComputerFiles(tmp_path / "file-access", grant)
                folder = tmp_path / "selected"
                folder.mkdir()
                selection = node.grant(str(folder.resolve()), "Selected", read=True, write=True)
                target = {"operation": "create", "path": "child.txt", "folder_id": selection["folder_id"],
                          "policy_revision": node.policy()["policy_revision"]}
                version = version_value(await connection.file_request("preview", target), "create")
                action = FileAction(uuid.uuid4().hex, **target, version=version, deadline=int(time.time()) + 60,
                                    session_id="session", turn_id="turn", call_id="call", content="child data")
                intents = HomeFileIntents(root)
                intents.record(grant, action)
                receipt = result_value(await connection.file_request("perform", asdict(action)), action)
                assert receipt["status"] == "created" and await connection.receipt(receipt)
                assert (folder / action.path).read_text() == action.content
                assert intents.status(action.action_id, account) == "created"
            async with owner.delete(origin + base + '/machines/' + grant['connector_id']) as response:
                assert response.status == 204
            await event_before(revoked)
            assert not worker_task.done()
            persisted.set()
            await finished(worker_task)
            async with owner.get(origin + base + '/machines') as response:
                assert (await response.json())['machines'] == []
        assert [frame['state'] for frame in events] == ['connecting', 'online', 'revoked']
    finally:
        stop.set()
        if worker_task is not None:
            worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)
        server.should_exit = True
        await asyncio.wait_for(server_task, 3)
        listener.close()


def inline_file_actions(monkeypatch):
    """Keep fault injection inside the test process for deterministic network timing."""
    from src.gateway.computer_file_store import ComputerFiles

    async def execute(directory, binding, action, *, stop, revoked):
        store = ComputerFiles(directory / "file-access", binding)
        return await asyncio.to_thread(store.execute, action,
                                        active=lambda: not stop.is_set() and not revoked.is_set())

    monkeypatch.setattr(worker, "run_file_action", execute)


async def test_file_actions_use_main_chat_approval_and_recover_lost_receipts(tmp_path, monkeypatch):
    import re
    import uvicorn

    from src.agents.agent_loop import MainAgent
    from src.gateway import computer_file_capacity, computer_file_tools, protocol as P
    from src.gateway.computer_connections import Connection
    from src.gateway.computer_file_intents import HomeFileIntents
    from src.gateway.computer_file_store import ComputerFiles
    from src.gateway.computer_home_delivery import OUTBOX
    from src.gateway.computer_protocol import TRANSFER_PATH
    from src.gateway.computer_transfers import ComputerTransfers
    from src.utils.state_dir import state_dir
    from src.web import app_views
    from src.web.app_bridge import MainChatBridge, existing_bridge
    from src.web.auth import SESSION_COOKIE
    from src.web.routers import computers, realtime
    from src.web.server import app

    root = str(tmp_path.resolve())
    monkeypatch.chdir(root)
    monkeypatch.setenv("VORTOONE_API_TOKEN", "synthetic-file-owner")
    monkeypatch.setenv("OPENAI_API_KEY", "unused-test-key")
    monkeypatch.setenv("VORTOONE_TURN_DIGEST", "0")
    monkeypatch.setenv("VORTOONE_WORKSPACE_SCOPE", "general")
    inline_file_actions(monkeypatch)
    store = ConnectedComputers(root)
    pairing = store.create_pairing(legacy_confirmed=True)
    grant = store.pair(pairing["code"], BINDING["connector_id"], "测试电脑")
    local = ComputerFiles(tmp_path / "file-access", grant)
    folder = tmp_path / "selected"
    folder.mkdir()
    target = folder / "original.txt"
    target.write_text("本地原文\n", encoding="utf-8")
    permitted = local.grant(str(folder.resolve()), "测试目录", read=True, write=True)
    folder_id = permitted["folder_id"]
    args = {"computer_id": grant["connector_id"], "folder_id": folder_id}
    outcomes = asyncio.Queue()
    operations = {
        "读取": ("connected_computer_read", {**args, "path": "original.txt"}),
        "预览": ("connected_computer_patch_preview", {**args, "path": "original.txt", "text": "提议内容\n"}),
        "撤权后写入": ("connected_computer_create", {**args, "path": "revoked.txt", "text": "不可写入"}),
        "断线后补报": ("connected_computer_create", {**args, "path": "durable.txt", "text": "只写一次\n"}),
        "清单": ("connected_computer_folders", {"computer_id": grant["connector_id"]}),
    }
    binary = folder / "manual.bin"
    binary_bytes = bytes(range(256)) * 4096
    binary.write_bytes(binary_bytes)

    outbox = state_dir(root) / OUTBOX
    outbox.mkdir(parents=True, mode=0o700)
    source = outbox / "package.bin"
    source.write_bytes(binary_bytes)
    operations["接收文件"] = ("connected_computer_deliver", {**args, "path": "copy.bin", "source_path": source.name})
    operations["修改源后接收"] = ("connected_computer_deliver", {**args, "path": "changed.bin", "source_path": source.name})
    operations["发送文件"] = ("connected_computer_transfer", {**args, "path": binary.name})
    memory = [None]
    monkeypatch.setattr(computer_file_capacity, "available_memory", lambda: memory[0])
    monkeypatch.setattr(computer_file_tools, "FILE_QUEUE_POLL_SECONDS", 0.08)
    attempts = []
    tickets = []
    issue = ComputerTransfers.issue

    async def observe_ticket(self, connection, file, **kwargs):
        issued = await issue(self, connection, file, **kwargs)
        tickets.append((connection.generation, file, issued["ticket"]))
        return issued

    monkeypatch.setattr(ComputerTransfers, "issue", observe_ticket)
    disconnect_upload = False
    interrupt_download = None
    request = Connection.file_request

    async def disconnect(connection):
        await connection.socket.close(code=1012)
        await until(lambda: connection.finished)

    download_chunks = computers._DeliveryResponse._chunks

    async def interrupt_download_once(self):
        nonlocal interrupt_download
        async for data in download_chunks(self):
            yield data
            if interrupt_download:
                scenario, interrupt_download = interrupt_download, None
                if scenario == "changed":
                    source.write_bytes(binary_bytes[::-1])
                await disconnect(self.transfer.connection)
                return

    monkeypatch.setattr(computers._DeliveryResponse, "_chunks", interrupt_download_once)

    async def observe_request(self, method, payload, **kwargs):
        nonlocal disconnect_upload
        if method == "perform":
            attempts.append(payload.copy())
        result = await request(self, method, payload, **kwargs)
        if method == "transfer_upload" and disconnect_upload:
            disconnect_upload = False
            await disconnect(self)
        return result

    monkeypatch.setattr(Connection, "file_request", observe_request)
    queued = asyncio.Event()
    bridge_emit = MainChatBridge._emit

    async def observe_app_event(self, kind, **fields):
        if kind == "agent.tool" and "已在家中等待" in (fields.get("detail") or ""):
            queued.set()
        await bridge_emit(self, kind, **fields)

    monkeypatch.setattr(MainChatBridge, "_emit", observe_app_event)

    async def scripted_turn(self, prompt, *, mode, say, emit, owner_text, **_):
        self._tool_turn_state.reset_escalation()
        name, parameters = operations[owner_text]
        result = await self._tool_runtime(say).execute(name, parameters, mode)
        await outcomes.put(result)
        emit(result.text)

    monkeypatch.setattr(MainAgent, "run_turn", scripted_turn)
    sent_json = aiohttp.ClientWebSocketResponse.send_json
    dropped_reply = asyncio.Event()
    dropped_receipt = {}

    async def drop_create_result_once(ws, data, **kwargs):
        if (data.get("type") == "file_reply" and data.get("result", {}).get("status") == "created"
                and attempts[-1]["path"] == "durable.txt"):
            assert not dropped_reply.is_set(), "unknown creates must never be sent twice"
            dropped_receipt.update(data["result"])
            dropped_reply.set()
            await ws.close()
        else:
            await sent_json(ws, data, **kwargs)

    monkeypatch.setattr(aiohttp.ClientWebSocketResponse, "send_json", drop_create_result_once)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="critical", access_log=False,
                                         log_config=None, ws="websockets"))
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    stop = asyncio.Event()
    online = asyncio.Event()
    worker_task = None

    def emit(event):
        if event.get("state") == "online":
            online.set()

    async def until(check, timeout=4):
        async def poll():
            while not (result := check()):
                await asyncio.sleep(0.01)
            return result
        return await asyncio.wait_for(poll(), timeout)

    try:
        await until(lambda: server.started)
        worker_task = asyncio.create_task(worker.run(config(origin, file_access=True, **grant), emit, stop))
        await event_before(online)
        account = store.identity()
        assert connections.file_connection(root, grant["connector_id"], account)
        base = origin + f"/api/app/organizations/{app_views.home_id(root)}"
        channel = app_views.dm_channel_id(app_views.main_agent_id(root))

        async with aiohttp.ClientSession(headers={"Authorization": "Bearer synthetic-file-owner"}) as owner:
            async def send(text):
                async with owner.post(f"{base}/channels/{channel}/messages", json={"body": text}) as response:
                    assert response.status == 200, await response.text()

            async def decide(card):
                async with owner.post(f"{base}/approvals/{card['id']}/approve", json={}) as response:
                    assert response.status == 200, await response.text()

            async def card_for(fragment):
                return await until(lambda: next((card for card in existing_bridge(root).pending_confirmations()
                                                if fragment in card["text"]), None))

            await send("清单")
            listed = await asyncio.wait_for(outcomes.get(), 3)
            assert "测试目录" in listed.text and str(folder) not in listed.text
            await send("读取")
            card = await card_for("original.txt")
            assert card["scope_options"] == ["once"] and "测试电脑" in card["text"] and str(folder) not in card["text"]
            attached = SimpleNamespace(query_params={"sid": existing_bridge(root).key.removeprefix("sid-")})
            await realtime.handle_websocket_message(attached, {"type": P.AGENT_CONFIRM_RESPONSE, "id": card["id"], "ok": True})
            assert existing_bridge(root).pending(card["id"]) is not None
            async with aiohttp.ClientSession() as other:
                async with other.post(f"{base}/approvals/{card['id']}/approve", json={},
                                      headers={"Cookie": f"{SESSION_COOKIE}=synthetic-file-owner",
                                               "Origin": "http://foreign.test"}) as response:
                    assert response.status in (403, 409)
                async with other.post(f"{base}/approvals/{card['id']}/approve", json={},
                                      headers={"Cookie": f"{SESSION_COOKIE}=synthetic-file-owner",
                                               "Origin": origin}) as response:
                    assert response.status == 200, await response.text()
            result = await asyncio.wait_for(outcomes.get(), 3)
            assert result.text == "本地原文\n"

            await send("预览")
            await decide(await card_for("预览文件差异"))
            patch = await asyncio.wait_for(outcomes.get(), 3)
            assert "-本地原文" in patch.text and "+提议内容" in patch.text
            assert target.read_text(encoding="utf-8") == "本地原文\n"

            await send("发送文件")
            card = await card_for("发送文件到家")
            assert "manual.bin" in card["text"] and str(len(binary_bytes)) in card["text"]
            await decide(card)
            transfer = await asyncio.wait_for(outcomes.get(), 6)
            assert transfer.status == "succeeded"
            download_path = re.search(r"/api/computers/downloads/[a-f0-9]{32}", transfer.text).group()
            assert "vct_" not in transfer.text
            async with aiohttp.ClientSession() as other:
                async with other.get(origin + download_path,
                                     headers={"Authorization": "Bearer " + grant["token"]}) as response:
                    assert response.status == 401
            async with aiohttp.ClientSession(headers={"Cookie": f"{SESSION_COOKIE}=synthetic-file-owner"}) as browser:
                async with browser.get(origin + download_path, headers={"Sec-Fetch-Site": "cross-site"}) as response:
                    assert response.status == 403
                async with browser.get(origin + download_path, headers={"Sec-Fetch-Site": "same-origin"}) as response:
                    assert response.status == 200
                    assert response.headers["Content-Disposition"].endswith("manual.bin")
                    assert response.headers["Cache-Control"] == "no-store"
                    assert response.headers["X-Content-Type-Options"] == "nosniff"
                    assert await response.read() == binary_bytes
            async with owner.get(origin + download_path) as response:
                assert response.status == 404

            await send("发送文件")
            card = await card_for("发送文件到家")
            binary.write_bytes(binary_bytes[::-1])
            await decide(card)
            altered = await asyncio.wait_for(outcomes.get(), 3)
            assert altered.status == "blocked" and "downloads/" not in altered.text

            await send("接收文件")
            await decide(await card_for("授权执行本回合"))
            card = await card_for("接收文件并新建")
            assert "computer-outbox/package.bin" in card["text"] and str(len(binary_bytes)) in card["text"]
            assert not (folder / "copy.bin").exists()
            await decide(card)
            delivered = await asyncio.wait_for(outcomes.get(), 8)
            assert delivered.status == "succeeded", delivered.text
            assert "vct_" not in delivered.text and (folder / "copy.bin").read_bytes() == binary_bytes
            delivery_id = re.search(r"[a-f0-9]{32}", delivered.text).group()
            assert HomeFileIntents(root).status(delivery_id, account) == "created"

            await send("接收文件")
            await decide(await card_for("授权执行本回合"))
            blocked = await asyncio.wait_for(outcomes.get(), 3)
            assert blocked.status == "blocked" and (folder / "copy.bin").read_bytes() == binary_bytes

            await send("修改源后接收")
            await decide(await card_for("授权执行本回合"))
            card = await card_for("接收文件并新建")
            source.write_bytes(binary_bytes[::-1])
            await decide(card)
            assert (await asyncio.wait_for(outcomes.get(), 3)).status == "blocked"
            assert not (folder / "changed.bin").exists()

            def assert_reissued(start):
                old, new = tickets[start:]
                assert old[0] != new[0] and old[2] != new[2]
                assert old[1] == new[1], "the second ticket must preserve the exact approved action"
                return old[2]

            ticket_count = len(tickets)
            disconnect_upload = True
            binary.write_bytes(binary_bytes)
            await send("发送文件")
            await decide(await card_for("发送文件到家"))
            resumed = await asyncio.wait_for(outcomes.get(), 8)
            assert resumed.status == "succeeded", resumed.text
            old_ticket = assert_reissued(ticket_count)
            connector_headers = {"Authorization": "Bearer " + grant["token"],
                                 "X-VortoOne-Transfer-Ticket": old_ticket,
                                 "Content-Type": "application/octet-stream"}
            async with aiohttp.ClientSession() as other:
                async with other.put(origin + TRANSFER_PATH, headers=connector_headers, data=binary_bytes) as denied:
                    assert denied.status == 403
            resumed_path = re.search(r"/api/computers/downloads/[a-f0-9]{32}", resumed.text).group()
            async with owner.get(origin + resumed_path) as response:
                assert response.status == 200 and await response.read() == binary_bytes

            for scenario in ("resume", "changed"):
                ticket_count = len(tickets)
                source.write_bytes(binary_bytes)
                filename = "restored.bin" if scenario == "resume" else "changed-midway.bin"
                operations["接收断线"] = ("connected_computer_deliver", {
                    **args, "path": filename, "source_path": source.name,
                })
                interrupt_download = scenario
                await send("接收断线")
                await decide(await card_for("授权执行本回合"))
                await decide(await card_for("接收文件并新建"))
                result = await asyncio.wait_for(outcomes.get(), 8)
                if scenario == "changed":
                    assert result.status == "blocked" and len(tickets) == ticket_count + 1
                    assert not (folder / filename).exists()
                    continue
                assert result.status == "succeeded", result.text
                assert (folder / filename).read_bytes() == binary_bytes
                old_ticket = assert_reissued(ticket_count)
                async with aiohttp.ClientSession() as other:
                    async with other.get(origin + TRANSFER_PATH, headers={
                        **connector_headers, "X-VortoOne-Transfer-Ticket": old_ticket,
                    }) as denied:
                        assert denied.status == 403

            await send("撤权后写入")
            await decide(await card_for("授权执行本回合"))
            card = await card_for("revoked.txt")
            local.revoke(folder_id)
            await decide(card)
            blocked = await asyncio.wait_for(outcomes.get(), 3)
            assert blocked.status == "blocked" and not (folder / "revoked.txt").exists()

            restored = local.grant(str(folder.resolve()), "重新授权目录", read=True, write=True)
            operations["断线后补报"][1]["folder_id"] = restored["folder_id"]
            await send("断线后补报")
            await decide(await card_for("授权执行本回合"))
            card = await card_for("durable.txt")
            assert "SHA-256" in card["text"] and "只写一次" in card["text"]
            old_connection = connections.file_connection(root, grant["connector_id"], account)
            await decide(card)
            result = await asyncio.wait_for(outcomes.get(), 3)
            assert result.status == "blocked" and "结果未知" in result.text and dropped_reply.is_set()
            action_id = re.search(r"[a-f0-9]{32}", result.text).group()
            path = folder / "durable.txt"
            fingerprint = path.stat()
            home_intents = HomeFileIntents(root)
            await until(lambda: home_intents.status(action_id, account) == "created")
            await until(lambda: not local.pending())
            assert dropped_receipt["action_id"] == action_id
            assert home_intents.accept(grant, dropped_receipt)
            assert not home_intents.accept({**grant, "credential_epoch": "e" * 32}, dropped_receipt)
            assert not home_intents.accept(grant, {**dropped_receipt, "digest": "0" * 64})
            assert not home_intents.accept(grant, {**dropped_receipt, "status": "blocked"})
            assert home_intents.status(action_id, {**account, "trust_epoch": "e" * 32}) is None
            assert connections.file_connection(root, grant["connector_id"], account) is not old_connection
            assert path.read_text(encoding="utf-8") == "只写一次\n"
            assert (path.stat().st_ino, path.stat().st_mtime_ns) == (fingerprint.st_ino, fingerprint.st_mtime_ns)
            await realtime.handle_websocket_message(existing_bridge(root), {
                "type": P.AGENT, "text": "清单", "rid": "unscoped-old-client",
            })
            assert (await asyncio.wait_for(outcomes.get(), 3)).status == "blocked"

            for should_create in (True, False):
                filename = "capacity.txt" if should_create else "queued-revoked.txt"
                operations["等待资源"] = ("connected_computer_create", {
                    **args, "folder_id": restored["folder_id"], "path": filename, "text": "原批准的文本",
                })
                queued.clear()
                memory[0] = 0
                await send("等待资源")
                await decide(await card_for("授权执行本回合"))
                await decide(await card_for(filename))
                await event_before(queued)
                action = attempts[-1]
                assert action["path"] == filename and home_intents.status(action["action_id"], account) == "waiting"
                assert not (folder / filename).exists()
                if not should_create:
                    local.revoke(restored["folder_id"])
                memory[0] = None
                outcome = await asyncio.wait_for(outcomes.get(), 3)
                repeated = [attempt for attempt in attempts if attempt["path"] == filename]
                assert len(repeated) >= 2 and all(item == action for item in repeated)
                assert outcome.status == ("succeeded" if should_create else "blocked")
                assert home_intents.status(action["action_id"], account) == ("created" if should_create else "blocked")
                assert (folder / filename).exists() == should_create
    finally:
        stop.set()
        if worker_task is not None:
            await asyncio.gather(worker_task, return_exceptions=True)
        server.should_exit = True
        await asyncio.wait_for(server_task, 3)
        listener.close()


@pytest.mark.parametrize("disconnect", [True, False], ids=["reconnect", "revoke"])
async def test_staged_file_action_keeps_control_live_and_obeys_revocation(tmp_path, monkeypatch, disconnect):
    from dataclasses import asdict
    import time
    import uuid

    from src.gateway.computer_connections import FileConnectionUnavailable
    from src.gateway.computer_file_intents import HomeFileIntents
    from src.gateway.computer_file_store import ComputerFiles, FileAction
    from src.utils.home_maintenance import HomeMaintenanceError
    from tests.unit.test_computer_file_process import park_file_child

    root = str(tmp_path.resolve())
    monkeypatch.chdir(root)
    monkeypatch.setenv("VORTOONE_API_TOKEN", "synthetic-owner")
    _, spawned = park_file_child(monkeypatch)
    monkeypatch.setattr(worker, "_HEARTBEAT_INTERVAL", 0.1)
    store = ConnectedComputers(root)
    pairing = store.create_pairing(legacy_confirmed=True)
    grant = store.pair(pairing["code"], BINDING["connector_id"], "Test computer")
    account = store.identity()
    local = ComputerFiles(tmp_path / "file-access", grant)
    folder = tmp_path / "selected"
    folder.mkdir()
    folder_id = local.grant(str(folder.resolve()), "Selected", read=True, write=True)["folder_id"]
    revision = local.policy()["policy_revision"]
    action = FileAction(uuid.uuid4().hex, folder_id, revision, "create", "staged.txt",
                        local.preview("create", folder_id, "staged.txt", revision),
                        int(time.time()) + 60, "session", "turn", "call", "approved text")
    intents = HomeFileIntents(root)
    intents.record(grant, action)
    staged = tmp_path / "staged.pid"
    resume = tmp_path / "continue"
    pongs = asyncio.Queue()
    acknowledged = asyncio.Event()
    persisted = asyncio.Event()
    stop = asyncio.Event()
    send_json = aiohttp.ClientWebSocketResponse.send_json

    async def observe_pong(ws, frame, **kwargs):
        await send_json(ws, frame, **kwargs)
        if frame.get("type") == "pong":
            pongs.put_nowait(frame["generation"])

    monkeypatch.setattr(aiohttp.ClientWebSocketResponse, "send_json", observe_pong)

    async def handler(request):
        binding = store.authenticate(request.headers["Authorization"].removeprefix("Bearer "))
        socket = web.WebSocketResponse(max_msg_size=MAX_FRAME_BYTES)
        await socket.prepare(request)
        await serve(ServerSocket(socket, acknowledged), root, store, binding)
        return socket

    async def current(*, except_connection=None):
        while True:
            connection = connections.file_connection(root, grant["connector_id"], account)
            if connection is not None and connection is not except_connection:
                return connection
            await asyncio.sleep(0.01)

    async def until(predicate):
        while not predicate():
            await asyncio.sleep(0.01)

    async def terminal():
        while True:
            if disconnect:
                if intents.status(action.action_id, account) == "created":
                    return
            else:
                try:
                    if local.pending() == [{"action_id": action.action_id, "digest": action.digest, "status": "blocked"}]:
                        return
                except HomeMaintenanceError:
                    pass
            await asyncio.sleep(0.02)

    async with service([("GET", "/api/computers/ws", handler)]) as origin:
        running = asyncio.create_task(worker.run(config(origin, **grant, file_access=True), lambda _: None,
                                                 stop, persisted))
        dispatch = None
        try:
            first = await asyncio.wait_for(current(), 2)
            dispatch = asyncio.create_task(first.file_request("perform", asdict(action)))
            process = await asyncio.wait_for(spawned.get(), 3)
            await asyncio.wait_for(until(lambda: staged.exists() and staged.stat().st_size > 0), 3)
            assert int(staged.read_text()) == process.pid
            await first.send({"type": "ping", "generation": first.generation})
            assert await asyncio.wait_for(pongs.get(), 2) == first.generation
            assert not (folder / "staged.txt").exists()
            if disconnect:
                await asyncio.wait_for(first.socket.close(), 2)
                second = await asyncio.wait_for(current(except_connection=first), 3)
                assert second.generation != first.generation
                assert process.returncode is None and not process.stdin.is_closing()
            else:
                store.revoke(grant["connector_id"])
                persisted.set()
                connections.revoked(root, grant["connector_id"])
                await event_before(acknowledged)
                await asyncio.wait_for(until(process.stdin.is_closing), 2)
            with pytest.raises(FileConnectionUnavailable):
                await asyncio.wait_for(dispatch, 3)
            resume.touch()
            await asyncio.wait_for(terminal(), 4)
            if disconnect:
                assert (folder / "staged.txt").read_text() == "approved text"
                assert spawned.empty()
            else:
                assert not (folder / "staged.txt").exists()
        finally:
            resume.touch()
            stop.set()
            await asyncio.gather(running, *([dispatch] if dispatch is not None else []), return_exceptions=True)
