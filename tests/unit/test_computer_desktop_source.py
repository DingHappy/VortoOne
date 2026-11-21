"""Exercise a private desktop share through a real connector and home, using a synthetic picker."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys

import aiohttp
import pytest
import uvicorn

from src.agents.identity_groups import EVERYONE
from src.gateway.computer_desktop_source import HELPER_NAME, START, STOP
from src.gateway.desktop_control_lease import claim_desktop, desktop_lease_file, desktop_revision
from src.utils.state_dir import state_dir
from src.web.member_auth import MEMBER_COOKIE
from src.workflow.members.registry import MemberRegistry
from tests.unit.test_computer_browser_preview import OWNER_ACTOR, _image
from tests.unit.test_computer_browser_preview import home, private_preview  # noqa: F401 - pytest fixtures
from tests.unit.test_computer_connector import config, isolated_environment
from tests.unit.test_computer_web import join_member


@pytest.mark.skipif(os.name != "posix", reason="the shared lease uses POSIX file descriptors")
async def test_capture_child_keeps_the_desktop_lease_after_connector_descriptor_closes(tmp_path):
    with desktop_lease_file(tmp_path) as publisher:
        child = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import os, sys; os.fstat(int(sys.argv[1])); sys.stdin.buffer.read(1)",
            str(publisher), stdin=asyncio.subprocess.PIPE, pass_fds=(publisher,),
        )
        assert claim_desktop(publisher)
    try:
        with desktop_lease_file(tmp_path) as role:
            assert not claim_desktop(role)
    finally:
        child.stdin.close()
        assert await asyncio.wait_for(child.wait(), 3) == 0
    with desktop_lease_file(tmp_path) as role:
        assert claim_desktop(role)


@pytest.mark.skipif(os.name != "posix", reason="the native helper uses executable files and pipes")
async def test_desktop_share_requires_local_selection_and_checks_authority_for_each_frame(private_preview, tmp_path):
    home = private_preview
    picker = tmp_path / "picker"
    selected = tmp_path / "selected"
    captured = tmp_path / "captures"
    inputs = tmp_path / "input-receipts"
    allow_second = tmp_path / "release-second-frame"
    closed = tmp_path / "helper-closed"
    helper = tmp_path / HELPER_NAME
    image = _image()
    helper.write_text(rf"""#!{sys.executable}
import json, pathlib, select, sys, time

assert sys.argv[1:] in (["--share"], ["--share-control"])
control_allowed = sys.argv[1] == "--share-control"
controlling = False
pathlib.Path({str(picker)!r}).touch()
try:
    while not pathlib.Path({str(selected)!r}).exists():
        if select.select([sys.stdin], [], [], 0.01)[0]:
            assert not sys.stdin.buffer.read(1)
            sys.exit()
    sys.stdout.buffer.write(b"C" if control_allowed else b"R")
    sys.stdout.buffer.flush()
    for index, command in enumerate(sys.stdin.buffer):
        if command == b"capture\n":
            with open({str(captured)!r}, "ab") as log:
                log.write(command)
            if not control_allowed and index == 1:
                while not pathlib.Path({str(allow_second)!r}).exists():
                    time.sleep(0.01)
            sys.stdout.buffer.write({(len(image).to_bytes(4, "big") + image)!r})
        elif control_allowed and command == b"control\n":
            assert not controlling
            controlling = True
            with open({str(inputs)!r}, "ab") as log:
                log.write(b"control\n")
            sys.stdout.buffer.write(b"\x06")
        else:
            assert controlling and command.startswith(b"input ")
            action = json.loads(command[6:])
            with open({str(inputs)!r}, "ab") as log:
                log.write(action["kind"].encode("ascii") + b"\n")
            if action["kind"] == "text":
                assert action["text"] in ("合成消息", "焦点改变")
            else:
                assert action == {{"kind": "click", "x": 2600, "y": 5700}}
            sys.stdout.buffer.write(b"\x07" if action.get("text") == "焦点改变" else b"\x02")
        sys.stdout.buffer.flush()
finally:
    pathlib.Path({str(closed)!r}).touch()
""")
    helper.chmod(0o700)
    registry = MemberRegistry(home["root"])
    other, _ = join_member(home, home["owner"], "other-desktop-viewer@example.test")
    cwd, _, environment = isolated_environment(tmp_path)

    async def wait_until(check):
        async def ready():
            while not check():
                await asyncio.sleep(0.01)
        await asyncio.wait_for(ready(), 5)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(home["app"], lifespan="off", log_level="critical",
                                             log_config=None, access_log=False))
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        process = None
        try:
            await wait_until(lambda: server.started or serving.done())
            if serving.done():
                await serving
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "src.runtime_entry", "computer-connector", cwd=cwd, env=environment,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )

            async def send(event):
                process.stdin.write(json.dumps(event).encode() + b"\n")
                await process.stdin.drain()

            async def state(expected):
                async def read():
                    while line := await process.stdout.readline():
                        event = json.loads(line)
                        if event["type"] == "desktop_preview":
                            assert event["state"] == expected
                            return event["generation"]
                    pytest.fail("connector exited before reporting sharing status")
                return await asyncio.wait_for(read(), 5)

            await send(config(origin, **home["grant"], desktop_capture=str(helper)))
            generation = await state("idle")
            await send({"type": START, "generation": generation})
            await wait_until(picker.exists)
            machine_url = origin + home["base"] + "/machines"
            machine_id = home["grant"]["connector_id"]
            desktop_url = f"{machine_url}/{machine_id}/desktop-preview"
            headers = {"Cookie": f"{MEMBER_COOKIE}={home['cookie']}", "Origin": origin}
            async with aiohttp.ClientSession(headers=headers, trust_env=False,
                                             timeout=aiohttp.ClientTimeout(total=5)) as pairer:
                async def machine():
                    async with pairer.get(machine_url) as reply:
                        assert reply.status == 200
                        (item,) = (await reply.json())["machines"]
                        assert item["online"] and item["browserPreviewAvailable"] is False
                        return item

                async def no_view(address, **overrides):
                    with pytest.raises(aiohttp.WSServerHandshakeError) as denied:
                        async with pairer.ws_connect(address, headers=overrides):
                            pytest.fail("unexpected viewing session")
                    assert denied.value.status == 403

                registry.change_group(OWNER_ACTOR, EVERYONE, computer="own_computer")
                assert (await machine())["desktopPreviewAvailable"] is False
                await no_view(desktop_url)
                assert not captured.exists()
                registry.change_group(OWNER_ACTOR, EVERYONE, computer="own_browser")

                selected.touch()
                assert await state("sharing") == generation
                assert (await machine())["desktopPreviewAvailable"] is False
                await no_view(desktop_url)
                registry.change_group(OWNER_ACTOR, EVERYONE, computer="own_computer")
                read_only = await machine()
                assert read_only["desktopPreviewAvailable"] is True and "desktopControlAvailable" not in read_only
                await no_view(desktop_url, Cookie=f"{MEMBER_COOKIE}={other.cookies[MEMBER_COOKIE]}")
                await no_view(desktop_url, Origin="http://other.example")
                await no_view(f"{machine_url}/{machine_id}/browser-preview")

                async with pairer.ws_connect(desktop_url) as viewer:
                    assert await viewer.receive_json(timeout=3) == {"type": "ready", "min_frame_ms": 500}
                    await no_view(desktop_url)
                    assert not captured.exists()
                    await viewer.send_json({"type": "next"})
                    assert await viewer.receive_bytes(timeout=3) == image
                    await viewer.send_json({"type": "take_control"})
                    assert (await viewer.receive(timeout=3)).type == aiohttp.WSMsgType.CLOSE
                    assert captured.read_bytes() == b"capture\n"

                await asyncio.sleep(0.525)
                async with pairer.ws_connect(desktop_url) as viewer:
                    await viewer.receive_json(timeout=3)
                    await viewer.send_json({"type": "next"})
                    await wait_until(lambda: captured.read_bytes() == b"capture\ncapture\n")
                    registry.change_group(OWNER_ACTOR, EVERYONE, computer="own_browser")
                    allow_second.touch()
                    assert (await viewer.receive(timeout=3)).type == aiohttp.WSMsgType.CLOSE

                registry.change_group(OWNER_ACTOR, EVERYONE, computer="own_computer")
                async with pairer.ws_connect(desktop_url) as viewer:
                    await viewer.receive_json(timeout=3)
                    await send({"type": STOP, "generation": generation})
                    assert await state("idle") == generation
                    assert (await viewer.receive(timeout=3)).type == aiohttp.WSMsgType.CLOSE
                assert closed.exists()
                assert (await machine())["desktopPreviewAvailable"] is False

                picker.unlink()
                selected.unlink()
                closed.unlink()
                await send({"type": START, "generation": generation, "allow_control": True})
                await wait_until(picker.exists)
                assert "desktopControlAvailable" not in await machine()
                await no_view(desktop_url)
                selected.touch()
                assert await state("sharing_with_control") == generation
                assert (await machine())["desktopControlAvailable"] is True
                await no_view(desktop_url, Cookie=f"{MEMBER_COOKIE}={other.cookies[MEMBER_COOKIE]}")

                click = {"type": "input", "action": {"kind": "click", "x": 2600, "y": 5700}}

                async def pull(viewer):
                    await viewer.send_json({"type": "next"})
                    assert await viewer.receive_bytes(timeout=3) == image

                async with pairer.ws_connect(desktop_url) as viewer:
                    await viewer.receive_json(timeout=3)
                    await pull(viewer)
                    await viewer.send_json(click)
                    assert (await viewer.receive(timeout=3)).type == aiohttp.WSMsgType.CLOSE
                assert not inputs.exists()

                await asyncio.sleep(0.525)
                async with pairer.ws_connect(desktop_url) as viewer:
                    await viewer.receive_json(timeout=3)
                    with desktop_lease_file(cwd) as role:
                        assert claim_desktop(role) and desktop_revision(role) == b""
                        await pull(viewer)
                        await viewer.send_json({"type": "take_control"})
                        assert await viewer.receive_json(timeout=3) == {"type": "control_busy"}
                        assert not inputs.exists()
                        assert (await machine())["desktopControlAvailable"] is True
                    await asyncio.sleep(0.525)
                    await pull(viewer)
                    await viewer.send_json({"type": "take_control"})
                    assert await viewer.receive_json(timeout=3) == {"type": "control_ready"}
                    assert await state("controlling") == generation
                    assert "desktopControlAvailable" not in await machine()
                    with desktop_lease_file(cwd) as role:
                        assert not claim_desktop(role)

                    await asyncio.sleep(0.525)
                    await pull(viewer)
                    await viewer.send_json(click)
                    assert await viewer.receive_json(timeout=3) == {"type": "input_applied"}
                    for text, receipt in (("焦点改变", "input_unavailable"), ("合成消息", "input_applied")):
                        await asyncio.sleep(0.525)
                        await pull(viewer)
                        await viewer.send_json({"type": "input", "action": {"kind": "text", "text": text}})
                        assert await viewer.receive_json(timeout=3) == {"type": receipt}

                    await asyncio.sleep(0.525)
                    await pull(viewer)
                    registry.change_group(OWNER_ACTOR, EVERYONE, computer="own_browser")
                    await viewer.send_json(click)
                    assert (await viewer.receive(timeout=3)).type == aiohttp.WSMsgType.CLOSE
                assert inputs.read_bytes() == b"control\nclick\ntext\ntext\n"
                assert await state("idle") == generation
                assert closed.exists()
                with desktop_lease_file(cwd) as role:
                    assert claim_desktop(role) and len(desktop_revision(role)) == 32
                registry.change_group(OWNER_ACTOR, EVERYONE, computer="own_computer")
                await no_view(desktop_url)

                for without_frame in (True, False):
                    await send({"type": START, "generation": generation, "allow_control": True})
                    assert await state("sharing_with_control") == generation
                    async with pairer.ws_connect(desktop_url) as viewer:
                        await viewer.receive_json(timeout=3)
                        await pull(viewer)
                        await viewer.send_json({"type": "take_control"})
                        assert await viewer.receive_json(timeout=3) == {"type": "control_ready"}
                        assert await state("controlling") == generation
                        if without_frame:
                            await viewer.send_json(click)
                            assert (await viewer.receive(timeout=3)).type == aiohttp.WSMsgType.CLOSE
                    assert await state("idle") == generation
                    await no_view(desktop_url)
                assert inputs.read_bytes() == b"control\nclick\ntext\ntext\ncontrol\ncontrol\n"

                audit = (state_dir(home["root"]) / "audit.log").read_text()
                events = [event for line in audit.splitlines()
                          if (event := json.loads(line)).get("operation_kind") == "interact"]
                assert [event["operation"] for event in events] == ["接管私有桌面", "结束私有桌面接管"] * 3
                assert all(set(event) == {"id", "ts", "category", "decision", "decision_source", "operation",
                                          "operation_kind", "actor_id", "connector_id", "surface_id"} for event in events)
        finally:
            allow_second.touch()
            try:
                if process is not None:
                    process.stdin.close()
                    await asyncio.wait_for(process.wait(), 5)
                    assert process.returncode == 0 and not await process.stderr.read()
            finally:
                server.should_exit = True
                await asyncio.wait_for(serving, 5)
