"""Drive the real connector and CDP pipe with an isolated, synthetic browser."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
import sys
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

from src.gateway import computer_connector as worker
from src.gateway.computer_browser_source import START, STOP, BrowserPreviewSource
from src.gateway.computer_protocol import decode_frame, welcome
from src.web.member_auth import MEMBER_COOKIE
from tests.unit.test_computer_browser_preview import _image
from tests.unit.test_computer_browser_preview import home, private_preview  # noqa: F401 - pytest fixtures
from tests.unit.test_computer_connector import BINDING, GENERATION, TOKEN, config, handshake, isolated_environment, service


def fake_chromium(root: Path, *, initial_url: str = "about:blank") -> tuple[Path, Path]:
    binary = root / "synthetic-chromium"
    log = root / "devtools.jsonl"
    image = base64.b64encode(_image()).decode("ascii")
    binary.write_text(rf"""#!{sys.executable}
import json, os, pathlib, stat, sys, threading, time

assert '--remote-debugging-pipe' in sys.argv and sys.argv[-1] == 'about:blank'
assert '--disable-sync' in sys.argv
assert not any(arg.startswith('--remote-debugging-port') for arg in sys.argv)
profile = pathlib.Path(next(arg.split('=', 1)[1] for arg in sys.argv if arg.startswith('--user-data-dir=')))
assert stat.S_IMODE(profile.stat().st_mode) == 0o700
responses = {{
    'Target.getTargets': {{'targetInfos': [{{'type': 'page', 'targetId': 'initial-page', 'url': {initial_url!r}}}]}},
    'Target.attachToTarget': {{'sessionId': 'initial-tab-session'}},
    'Page.getFrameTree': {{'frameTree': {{'frame': {{'id': 'initial-page'}}}}}},
    'Page.createIsolatedWorld': {{'executionContextId': 17}},
    'Page.captureScreenshot': {{'data': {image!r}}},
}}

def watch_for_local_tab_close():
    marker = pathlib.Path({str(root / "close-tab")!r})
    while not marker.exists():
        time.sleep(0.01)
    event = {{'method': 'Target.detachedFromTarget', 'params': {{'sessionId': 'initial-tab-session'}}}}
    os.write(4, json.dumps(event).encode() + b'\0')

threading.Thread(target=watch_for_local_tab_close, daemon=True).start()
with open({str(log)!r}, 'a', buffering=1) as log, os.fdopen(3, 'rb', buffering=0) as in_pipe:
    log.write(json.dumps({{'profile': str(profile)}}) + '\n')
    data = b''
    while part := in_pipe.read(4096):
        data += part
        while b'\0' in data:
            raw, data = data.split(b'\0', 1)
            command = json.loads(raw)
            method = command['method']
            reported = {{'method': method}} if method == 'Runtime.callFunctionOn' else command
            log.write(json.dumps(reported) + '\n')
            if method == 'Page.captureScreenshot':
                assert command.get('sessionId') == 'initial-tab-session'
            result = responses.get(method, {{}})
            if method == 'Runtime.callFunctionOn':
                assert 'requestSubmit' in command['params']['functionDeclaration']
                assert command['params']['executionContextId'] == 17
                site, offer, kind, credentials = [arg['value'] for arg in command['params']['arguments']]
                state = pathlib.Path({str(root / "page-state.json")!r})
                value = json.loads(state.read_text())
                assert value['url'] == site + ('/login' if kind == 'password' else '/verify')
                if credentials is None:
                    assert value.get('login') is True
                    login_offer = offer
                    result = {{'result': {{'value': {{'username': kind == 'password', 'document': value['document']}}}}}}
                else:
                    assert offer == login_offer
                    expected = ({{'username': 'sample@example.test', 'password': '合成测试 % +'}} if kind == 'password'
                                else {{'code': '05401726'}})
                    assert credentials == expected
                    target = '/verify' if kind == 'password' and value.get('requires_code') else '/ready'
                    state.write_text(json.dumps({{**value, 'url': site + target, 'document': value['document'] + 1}}))
                    result = {{'result': {{'value': True}}}}
            if method == 'Runtime.evaluate':
                assert 'performance.timeOrigin' in command['params']['expression']
                assert command['params']['contextId'] == 17
                state = pathlib.Path({str(root / "page-state.json")!r})
                value = json.loads(state.read_text()) if state.exists() else {{
                    'url': 'about:blank', 'document': 1000, 'protected': False, 'frames': False,
                }}
                value = {{'width': 960, 'height': 600, 'scrollX': 0, 'scrollY': 0, 'loaded': True, **value}}
                if 'elementFromPoint' in command['params']['expression']:
                    value = {{'allowed': True, 'x': 480, 'y': 300, **value}}
                result = {{'result': {{'type': 'object', 'value': value}}}}
            reply = {{'id': command['id'], 'result': result}}
            os.write(4, json.dumps(reply).encode() + b'\0')
            if method == 'Browser.close':
                sys.exit()
""")
    binary.chmod(0o700)
    return binary, log


@pytest.mark.skipif(os.name != "posix", reason="Chromium DevTools uses POSIX file descriptors")
@pytest.mark.parametrize("ending", [
    "local-stop", "expiry", "tab-close", "invalid-request", "reconnect", "parent-exit", "unexpected-initial-tab",
])
async def test_pull_only_source_obeys_affirmative_local_generation_and_closes(ending, tmp_path):
    controls = asyncio.Queue()
    publishers = asyncio.Queue()
    frames = asyncio.Queue()
    restarted = "e" * 32
    generations = iter((GENERATION, restarted))

    async def control(request):
        generation = next(generations)
        socket = await handshake(request, frame=welcome(BINDING, generation))
        await controls.put(socket)
        async for message in socket:
            assert decode_frame(message.data) == {"type": "ping", "generation": generation}
            await socket.send_json({"type": "pong", "generation": generation})
        return socket

    async def publisher(request):
        assert request.headers["Authorization"] == "Bearer " + TOKEN
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        offer = await socket.receive_json(timeout=2)
        await socket.send_json({"type": "offered", "expires_in": 1 if ending == "expiry" else 300})
        await publishers.put((socket, offer))
        async for message in socket:
            assert message.type == aiohttp.WSMsgType.BINARY
            await frames.put(message.data)
        return socket

    initial_url = "chrome://settings" if ending == "unexpected-initial-tab" else "about:blank"
    binary, log = fake_chromium(tmp_path, initial_url=initial_url)
    cwd, _, environment = isolated_environment(tmp_path)
    environment.update(VORTOONE_BROWSER_PATH=str(binary), TMPDIR=str(tmp_path))
    routes = [("GET", "/api/computers/ws", control), ("GET", "/api/computers/browser-preview", publisher)]
    async with service(routes) as origin:
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "src.runtime_entry", "computer-connector", cwd=cwd, env=environment,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )

        async def send(frame):
            process.stdin.write(json.dumps(frame).encode() + b"\n")
            await process.stdin.drain()

        async def preview_status(generation, state):
            async def read_until():
                while line := await process.stdout.readline():
                    frame = json.loads(line)
                    if frame["type"] == "browser_preview":
                        assert frame == {
                            "type": "browser_preview", "state": state, "generation": generation,
                            "home_instance_id": BINDING["home_instance_id"], "connector_id": BINDING["connector_id"],
                        }
                        return
                pytest.fail("connector closed before reporting preview status")

            await asyncio.wait_for(read_until(), timeout=5)

        def browser_commands():
            return [json.loads(line) for line in log.read_text().splitlines()]

        try:
            await send(config(origin, browser_preview=True))
            await preview_status(GENERATION, "idle")
            control_socket = await asyncio.wait_for(controls.get(), timeout=2)
            await send({"type": START, "generation": restarted})
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(publishers.get(), timeout=0.1)
            assert not log.exists()
            await send({"type": START, "generation": GENERATION})
            if ending == "unexpected-initial-tab":
                await preview_status(GENERATION, "unavailable")
                assert publishers.empty()
                commands = browser_commands()
                assert not Path(commands[0]["profile"]).exists()
                assert [item.get("method") for item in commands[1:]] == ["Target.getTargets", "Browser.close"]
                return
            await preview_status(GENERATION, "sharing")
            socket, offer = await asyncio.wait_for(publishers.get(), timeout=2)
            assert set(offer) == {"type", "generation", "surface_id"}
            assert offer["type"] == "offer" and offer["generation"] == GENERATION
            assert all(command.get("method") != "Page.captureScreenshot" for command in browser_commands())
            request_id = "7" * 32
            await socket.send_json({"type": "capture", "request_id": request_id, "surface_id": offer["surface_id"]})
            assert await asyncio.wait_for(frames.get(), timeout=2) == request_id.encode() + _image()

            if ending == "local-stop":
                await send({"type": STOP, "generation": GENERATION})
                await preview_status(GENERATION, "idle")
            elif ending == "invalid-request":
                await socket.send_json({"type": "capture", "request_id": "8" * 32, "surface_id": "6" * 32})
                await preview_status(GENERATION, "unavailable")
            elif ending == "tab-close":
                (tmp_path / "close-tab").touch()
                await preview_status(GENERATION, "idle")
            elif ending == "reconnect":
                await control_socket.close()
                await preview_status(restarted, "idle")
                await send({"type": START, "generation": GENERATION})
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(publishers.get(), timeout=0.1)
            elif ending == "expiry":
                await preview_status(GENERATION, "idle")
            else:
                process.stdin.close()
                await asyncio.wait_for(process.wait(), timeout=2)
                assert process.returncode == 0
            commands = browser_commands()
            assert not Path(commands[0]["profile"]).exists()
            assert sum(item.get("method") == "Page.captureScreenshot" for item in commands) == 1
            assert commands[-1]["method"] == "Browser.close"
        finally:
            process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), timeout=4)
            except asyncio.TimeoutError as error:
                if process.returncode is None:
                    process.kill()
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=2)
                raise AssertionError(f"connector did not close: {stdout[-1024:]!r} {stderr[-1024:]!r}") from error
            assert process.returncode == 0 and not await process.stderr.read()


@pytest.mark.skipif(os.name != "posix", reason="Private preview is available on POSIX")
async def test_stop_fences_the_request_that_completes_while_cancelling(monkeypatch):
    from contextlib import asynccontextmanager

    from src.browser import cdp
    from src.gateway import computer_browser_source

    receiving = asyncio.Event()
    closed = asyncio.Event()
    idle_after_share = asyncio.Event()
    captures = []
    states = []
    surface = "a" * 32

    class Browser:
        page_attached = True

        def __init__(self, *_args, **_kwargs):
            pass

        async def start(self):
            pass

        async def send(self, *_args):
            pass

        async def screenshot(self):
            captures.append(True)
            return _image()

        async def close(self):
            closed.set()

    class Socket:
        closed = False

        async def send_bytes(self, _data):
            pytest.fail("a frame was sent after local stop")

    @asynccontextmanager
    async def link(*_args, **_options):
        yield Socket(), surface, asyncio.get_running_loop().time() + 30, None

    async def read_command(*_args):
        assert not receiving.is_set()
        receiving.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            # A read and its timeout can finish while cancellation is being delivered.
            return {"type": "capture", "request_id": "b" * 32, "surface_id": surface}

    monkeypatch.setattr(cdp, "CdpBrowser", Browser)
    monkeypatch.setattr(cdp, "find_browser", lambda: "synthetic-browser")
    monkeypatch.setattr(computer_browser_source, "preview_link", link)
    monkeypatch.setattr(computer_browser_source, "read_command", read_command)

    def emit(frame):
        state = frame["state"]
        states.append(state)
        if state == "idle" and "sharing" in states:
            idle_after_share.set()

    source = BrowserPreviewSource(BINDING, emit)
    source.connected(None, GENERATION)
    source.command(START, GENERATION)
    await asyncio.wait_for(receiving.wait(), 2)
    source.command(STOP, GENERATION)
    await asyncio.wait_for(idle_after_share.wait(), 2)
    await source.disconnect()
    assert closed.is_set() and not captures
    assert states == ["idle", "sharing", "idle"]


@pytest.mark.skipif(os.name != "posix", reason="Chromium DevTools uses POSIX file descriptors")
async def test_real_home_lists_and_relays_only_a_locally_started_browser(private_preview, tmp_path, monkeypatch):
    import uvicorn

    home = private_preview
    executable, capture_log = fake_chromium(tmp_path)
    monkeypatch.setenv("VORTOONE_BROWSER_PATH", str(executable))
    stop = asyncio.Event()
    states = asyncio.Queue()

    def emit(event):
        if event["type"] == "browser_preview":
            states.put_nowait(event)

    async def state(expected):
        event = await asyncio.wait_for(states.get(), 4)
        assert event["state"] == expected
        return event["generation"]

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(home["app"], lifespan="off", log_level="critical",
                                             log_config=None, access_log=False))
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        connection = None
        try:
            async def ready():
                while not server.started:
                    if serving.done():
                        await serving
                    await asyncio.sleep(0.005)

            await asyncio.wait_for(ready(), 2)
            settings = config(origin, **home["grant"], browser_preview=True)
            source = BrowserPreviewSource(settings, emit)
            connection = asyncio.create_task(worker.run(settings, emit, stop, browser_preview=source))
            generation = await state("idle")
            machines = origin + home["base"] + "/machines"
            headers = {"Cookie": f"{MEMBER_COOKIE}={home['cookie']}", "Origin": origin}
            async with aiohttp.ClientSession(headers=headers, trust_env=False,
                                             timeout=aiohttp.ClientTimeout(total=3)) as pairer:
                async with pairer.get(machines) as reply:
                    assert reply.status == 200
                    machine, = (await reply.json())["machines"]
                    assert machine["online"] and machine["browserPreviewAvailable"] is False
                assert not capture_log.exists()
                source.command(START, generation)
                assert await state("sharing") == generation
                async with pairer.get(machines) as reply:
                    available, = (await reply.json())["machines"]
                    assert available == {**machine, "browserPreviewAvailable": True}
                assert "Page.captureScreenshot" not in capture_log.read_text()
                async with pairer.ws_connect(f"{machines}/{machine['id']}/browser-preview") as viewer:
                    assert await viewer.receive_json(timeout=2) == {"type": "ready", "min_frame_ms": 500}
                    await viewer.send_json({"type": "next"})
                    assert await viewer.receive_bytes(timeout=2) == _image()
                    source.command(STOP, generation)
                    assert await state("idle") == generation
                    assert (await viewer.receive(timeout=3)).type == aiohttp.WSMsgType.CLOSE
                async with pairer.get(machines) as reply:
                    assert (await reply.json())["machines"] == [machine]
                assert capture_log.read_text().count("Page.captureScreenshot") == 1
        finally:
            stop.set()
            try:
                if connection is not None:
                    await asyncio.wait_for(connection, 3)
            finally:
                server.should_exit = True
                await asyncio.wait_for(serving, 3)
