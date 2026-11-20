"""Relay frames and the locally approved input for one picker-selected display."""
from __future__ import annotations

import asyncio
from contextlib import nullcontext, suppress
import json
from pathlib import Path

from src.gateway.computer_browser_wire import (
    BrowserPreviewDenied, CONTROL_BUSY, CONTROL_READY, FRAME_TIMEOUT, INPUT_APPLIED, INPUT_FRAME_SECONDS, INPUT_UNAVAILABLE,
    MAX_PNG_BYTES, MIN_FRAME_INTERVAL, desktop_input, validate_png,
)
from src.gateway.computer_preview_source import PreviewSource, preview_link, read_command
from src.gateway.computer_protocol import identifier
from src.gateway.desktop_control_lease import claim_desktop, desktop_lease_file, mark_desktop_takeover

HELPER_NAME = "vortoone-desktop-capture"
START = "desktop_preview_start"
STOP = "desktop_preview_stop"
_PICKER_TIMEOUT = 45.0


async def _capture(process: asyncio.subprocess.Process) -> bytes:
    process.stdin.write(b"capture\n")
    await process.stdin.drain()
    length = int.from_bytes(await process.stdout.readexactly(4), "big")
    if not 45 <= length <= MAX_PNG_BYTES:
        raise BrowserPreviewDenied("invalid desktop frame length")
    return validate_png(await process.stdout.readexactly(length))


async def _close(process: asyncio.subprocess.Process) -> None:
    async def discard_output() -> None:
        while await process.stdout.read(65_536):
            pass

    drain = asyncio.create_task(discard_output())
    process.stdin.close()
    try:
        await asyncio.wait_for(process.wait(), 0.25)
    except asyncio.TimeoutError:
        with suppress(ProcessLookupError):
            process.kill()
        await process.wait()
    finally:
        await drain


async def _share(session, config: dict, generation: str, sharing, allow_control: bool = False,
                 *, stopped: asyncio.Event) -> None:
    # This is the native app's computer-connections directory. The child inherits
    # the descriptor so a connector crash cannot release control before it exits.
    with desktop_lease_file(Path.cwd()) if allow_control else nullcontext() as lease:
        await _share_display(session, config, generation, sharing, lease, stopped)


async def _share_display(session, config: dict, generation: str, sharing, lease: int | None,
                         stopped: asyncio.Event) -> None:
    allow_control = lease is not None
    process = await asyncio.create_subprocess_exec(
        config["desktop_capture"], "--share-control" if allow_control else "--share", stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        env={"PATH": "/usr/bin:/bin"},
        **({"pass_fds": (lease,)} if allow_control else {}),
    )
    try:
        try:
            ready = await asyncio.wait_for(process.stdout.readexactly(1), _PICKER_TIMEOUT)
        except asyncio.IncompleteReadError:
            if await asyncio.wait_for(process.wait(), 0.25) == 0:
                return  # The local user dismissed the picker.
            raise BrowserPreviewDenied("desktop picker unavailable") from None
        if stopped.is_set():
            return
        if ready != (b"C" if allow_control else b"R") or process.returncode is not None:
            raise BrowserPreviewDenied("desktop picker did not accept a display")
        options = {"allow_control": True} if allow_control else {}
        async with preview_link(session, config, generation, "desktop", **options) as (socket, surface, deadline, _):
            sharing("sharing_with_control" if allow_control else "sharing")
            clock = asyncio.get_running_loop().time
            next_frame = clock()
            frame_expires = 0.0
            controlling = False
            while not stopped.is_set() and process.returncode is None and clock() < deadline:
                try:
                    command = await read_command(socket, min(1.0, deadline - clock()))
                except asyncio.TimeoutError:
                    continue
                if stopped.is_set() or command is None or process.returncode is not None:
                    return
                fields = {"type", "request_id", "surface_id"}
                operation = command.get("type")
                if command.get("surface_id") != surface:
                    raise BrowserPreviewDenied("invalid desktop preview request")
                request = identifier(command.get("request_id"))
                if operation == "capture" and set(command) == fields:
                    await asyncio.sleep(max(0, next_frame - clock()))
                    remaining = deadline - clock()
                    if stopped.is_set() or remaining <= 0 or process.returncode is not None:
                        return
                    next_frame = clock() + MIN_FRAME_INTERVAL
                    reply = await asyncio.wait_for(_capture(process), min(FRAME_TIMEOUT - 0.3, remaining))
                    frame_expires = clock() + INPUT_FRAME_SECONDS
                else:
                    if lease is None or clock() >= min(frame_expires, deadline):
                        raise BrowserPreviewDenied("desktop control requires a fresh frame")
                    frame_expires = 0.0
                    if not controlling and operation == "take_control" and set(command) == fields:
                        if not claim_desktop(lease):
                            await asyncio.wait_for(socket.send_bytes(request.encode("ascii") + CONTROL_BUSY),
                                                   min(FRAME_TIMEOUT, deadline - clock()))
                            continue
                        mark_desktop_takeover(lease)
                        native = b"control\n"
                        accepted = (CONTROL_READY,)
                    elif controlling and operation == "input" and set(command) == fields | {"action"}:
                        action = desktop_input(command["action"])
                        native = b"input " + json.dumps(action, ensure_ascii=True).encode("ascii") + b"\n"
                        accepted = (INPUT_APPLIED, INPUT_UNAVAILABLE)
                    else:
                        raise BrowserPreviewDenied("invalid desktop control request")
                    remaining = min(FRAME_TIMEOUT - 0.3, deadline - clock())
                    if stopped.is_set() or remaining <= 0 or process.returncode is not None:
                        return
                    process.stdin.write(native)
                    await asyncio.wait_for(process.stdin.drain(), remaining)
                    reply = await asyncio.wait_for(process.stdout.readexactly(1), remaining)
                    if reply not in accepted:
                        raise BrowserPreviewDenied("invalid desktop helper receipt")
                    if not controlling:
                        controlling = True
                        sharing("controlling")
                if stopped.is_set() or process.returncode is not None or clock() >= deadline:
                    return
                await asyncio.wait_for(socket.send_bytes(request.encode("ascii") + reply),
                                       min(FRAME_TIMEOUT, deadline - clock()))
                del reply
    finally:
        await _close(process)


class DesktopPreviewSource(PreviewSource):
    def __init__(self, config: dict, emit):
        super().__init__(config, emit, "desktop", _share)
