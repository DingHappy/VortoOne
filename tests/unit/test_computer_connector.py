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


async def test_actual_fastapi_routes_and_worker_interoperate_on_real_loopback(tmp_path, monkeypatch):
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
            worker_task = asyncio.create_task(worker.run(config(origin, **grant), emit, stop, persisted))
            await event_before(connected)
            async with owner.get(origin + base + '/machines') as response:
                machines = (await response.json())['machines']
                machine = next(row for row in machines if row['id'] == grant['connector_id'])
                assert machine['online'] and not machine['execAllowed']
                assert machine['access']['folders'] == [] and not any(machine['access']['tools'].values())
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
