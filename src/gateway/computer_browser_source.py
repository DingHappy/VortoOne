"""Publish one locally opened Chromium tab for the current connector generation."""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Callable

from src.gateway.computer_browser_wire import (
    BrowserPreviewDenied, FRAME_TIMEOUT, INPUT_APPLIED, LOGIN_SENT, MAX_PNG_BYTES, MIN_FRAME_INTERVAL,
    PROTECTED, SITE_READY, browser_input, validate_png,
)
from src.gateway.computer_preview_source import PreviewSource, preview_link, read_command
from src.gateway.computer_protocol import identifier, origin_url

START = "browser_preview_start"
STOP = "browser_preview_stop"


async def _set_viewport(browser, scale: float) -> None:
    await browser.send("Emulation.setDeviceMetricsOverride", {
        "width": 960, "height": 600, "deviceScaleFactor": scale, "mobile": False,
    })


async def _screenshot(browser) -> bytes:
    image = await browser.screenshot()
    if len(image) > MAX_PNG_BYTES:
        await _set_viewport(browser, 0.5)
        image = await browser.screenshot()
    return validate_png(image)


async def _publish(session, config: dict, generation: str, browser, sharing: Callable[[], None],
                   *, task_code: str | None = None, allow_control: bool = False, allow_login: bool = False) -> None:
    clock = asyncio.get_running_loop().time
    options = {}
    if task_code is not None:
        options["task_code"] = task_code
    if allow_control:
        options["allow_control"] = True
    if allow_login:
        options["allow_login"] = True
    async with preview_link(session, config, generation, **options) as (socket, surface, deadline, site_origin):
        def current() -> bool:
            return not socket.closed and browser.page_attached and clock() < deadline

        frame = None
        viewer = None
        if task_code is not None:
            from src.browser.handoff_login import BrowserHandoffLogin
            from src.browser.handoff_preview import BrowserHandoffFrame

            origin = origin_url(site_origin)
            if origin != site_origin:
                raise BrowserPreviewDenied("invalid paired task site")
            login = BrowserHandoffLogin(browser, origin, current) if allow_login else None
            frame = BrowserHandoffFrame(browser, origin, asyncio.Lock(), current,
                                        evaluate=browser.evaluate_isolated, login=login)
            viewer = frame.begin_viewing()
        sharing()
        next_capture = clock()
        while current():
            try:
                request = await read_command(socket, min(1.0, deadline - clock()))
            except asyncio.TimeoutError:
                continue
            if request is None or not browser.page_attached:
                return
            fields = {"type", "request_id", "surface_id"}
            operation = request.get("type")
            action = None
            if operation == "input" and frame is not None and allow_control and set(request) == fields | {"action"}:
                action = browser_input(request["action"])
            elif operation == "submit_login" and frame is not None and allow_login and set(request) == fields | {"action"}:
                action = request["action"]
                if not isinstance(action, dict):
                    raise BrowserPreviewDenied("invalid login action")
            elif (set(request) != fields
                  or (operation != "capture" and (frame is None or operation != "verify_site"))):
                raise BrowserPreviewDenied("invalid browser request")
            if request["surface_id"] != surface:
                raise BrowserPreviewDenied("wrong browser surface")
            request_id = identifier(request["request_id"])
            await asyncio.sleep(max(0.0, next_capture - clock()))
            remaining = deadline - clock()
            if remaining <= 0 or not browser.page_attached:
                return
            next_capture = clock() + MIN_FRAME_INTERVAL
            bound = min(FRAME_TIMEOUT - 0.3, remaining)
            if frame is None:
                image = await asyncio.wait_for(_screenshot(browser), bound)
            elif operation == "submit_login":
                applied = await asyncio.wait_for(frame.submit_login(action), bound)
                image = LOGIN_SENT if applied else PROTECTED
            elif action is not None:
                applied = await asyncio.wait_for(frame.interact(action), bound)
                image = INPUT_APPLIED if applied else PROTECTED
            elif operation == "verify_site":
                ready = await asyncio.wait_for(frame.site_ready(), bound)
                image = SITE_READY if ready else PROTECTED
            else:
                image = await asyncio.wait_for(frame.pull(current, viewer=viewer), bound) or PROTECTED
            if not current():
                return
            await asyncio.wait_for(socket.send_bytes(request_id.encode("ascii") + image),
                                   min(FRAME_TIMEOUT, deadline - clock()))
            del image


async def _share(session, config: dict, generation: str, sharing: Callable[[], None],
                 task_code: str | None = None, allow_control: bool = False, allow_login: bool = False) -> None:
    from src.browser.cdp import CdpBrowser, find_browser

    executable = find_browser()
    if not executable:
        raise BrowserPreviewDenied("Chromium unavailable")
    with tempfile.TemporaryDirectory(prefix="vortoone-browser-preview-") as profile:
        browser = CdpBrowser(executable, Path(profile), private_preview=True)
        try:
            await asyncio.wait_for(browser.start(), 10)
            if not browser.page_attached:
                return
            await asyncio.wait_for(_set_viewport(browser, 1), 2)
            await _publish(session, config, generation, browser, sharing,
                           task_code=task_code, allow_control=allow_control, allow_login=allow_login)
        finally:
            await browser.close()


class BrowserPreviewSource(PreviewSource):
    def __init__(self, config: dict, emit: Callable[[dict], None]):
        super().__init__(config, emit, _share)

    def connected(self, session, generation: str) -> None:
        if os.name == "posix":
            super().connected(session, generation)
