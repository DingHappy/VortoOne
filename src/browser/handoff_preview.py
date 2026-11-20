"""In-memory frames from a task's paused, dedicated browser."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
import math
from typing import Any
from urllib.parse import urlsplit

from src.browser.backend import BrowserDriver
from src.browser.connections import site_origin
from src.browser.handoff_login import BrowserHandoffLogin, login_step_path
from src.browser.page_privacy import HANDOFF_CONTROL_STATE_JS, HANDOFF_PREVIEW_STATE_JS, protected_path
from src.gateway.computer_browser_wire import (
    FRAME_TIMEOUT, INPUT_FRAME_SECONDS, LOGIN_WAITING, MIN_FRAME_INTERVAL, SITE_READY, browser_input, validate_png,
)

_Viewport = tuple[int, int, float, float]
_Stamp = tuple[str, float, _Viewport | None]


class BrowserHandoffFrame:
    def __init__(self, browser: BrowserDriver, origin: str, lock: asyncio.Lock, current: Callable[[], bool],
                 *, evaluate: Callable[[str], Awaitable[Any]] | None = None, login: BrowserHandoffLogin | None = None):
        self._browser = browser
        self._origin = origin
        self._lock = lock
        self.current = current
        self._evaluate = evaluate or browser.evaluate
        self._login = login
        self._last_frame: tuple[_Stamp, float] | None = None
        self._viewer: object | None = None
        self._busy = False
        self._next_request = 0.0

    def begin_viewing(self) -> object | None:
        if self._viewer is not None or self._busy:
            return None
        self._viewer = object()
        return self._viewer

    def end_viewing(self, viewer: object) -> None:
        if self._viewer is viewer:
            self._viewer = None

    def _checked_stamp(self, state: object) -> _Stamp | None:
        if not isinstance(state, dict) or state.get("protected") is not False or state.get("frames") is not False:
            return None
        url, document = state.get("url"), state.get("document")
        if (not isinstance(url, str) or len(url) > 2048 or "?" in url or "#" in url
                or type(document) not in (int, float) or not 0 < document <= 1e15):
            return None
        path = urlsplit(url).path
        if site_origin(url) != self._origin or protected_path(path):
            return None
        if (self._login is not None and self._login.touched_document is not None
                and (state.get("loaded") is not True or login_step_path(path))):
            return None
        width, height = state.get("width"), state.get("height")
        if any(value is not None and (type(value) is not int or not 0 < value <= 4096) for value in (width, height)):
            return None
        offsets = state.get("scrollX"), state.get("scrollY")
        viewport = None
        if width is not None and height is not None and all(
            type(value) in (int, float) and math.isfinite(value) for value in offsets
        ):
            viewport = width, height, *offsets
        return url, document, viewport

    async def _stamp(self) -> _Stamp | None:
        return self._checked_stamp(await self._evaluate(HANDOFF_PREVIEW_STATE_JS))

    async def _capture(self, allowed: Callable[[], bool]) -> bytes | None:
        async with self._lock:
            self._last_frame = None
            login = self._login
            if login is not None:
                login.discard_offer()
            if not self.current() or not allowed():
                return None
            before = await self._stamp()
            if not self.current() or not allowed():
                return None
            # A page seen after secret entry can only produce login status, never a PNG.
            if login is not None and (before is None or login.touched_document is not None):
                if before is not None and before[1] != login.touched_document:
                    return SITE_READY
                offer = await login.offer()
                if not self.current() or not allowed():
                    return None
                return offer if offer is not None else LOGIN_WAITING if login.awaiting_page() else None
            if before is None:
                return None
            image = await self._browser.screenshot()
            if not isinstance(image, bytes) or not self.current() or not allowed() or before != await self._stamp():
                return None
            if not self.current() or not allowed():
                return None
            image = validate_png(image)
            self._last_frame = before, asyncio.get_running_loop().time() + INPUT_FRAME_SECONDS
            return image

    async def interact(self, action: object) -> bool:
        """Apply one supported input to the fresh, still-matching page."""
        try:
            action = browser_input(action)
            async with self._lock:
                last, self._last_frame = self._last_frame, None
                clock = asyncio.get_running_loop().time
                if last is None or not self.current() or clock() >= last[1] or last[0][2] is None:
                    return False
                width, height, _, _ = last[0][2]
                kind = action["kind"]
                args = ", ".join(json.dumps(value) for value in (kind, action.get("x", 0), action.get("y", 0)))
                state = await self._evaluate(f"{HANDOFF_CONTROL_STATE_JS}({args})")
                if (self._checked_stamp(state) != last[0] or state.get("allowed") is not True
                        or not self.current() or clock() >= last[1]):
                    return False
                if kind == "click":
                    x, y = state.get("x"), state.get("y")
                    if (type(x) not in (int, float) or type(y) not in (int, float)
                            or not 0 <= x < width or not 0 <= y < height):
                        return False
                    await self._browser.click_at(x, y)
                elif kind == "scroll":
                    await self._browser.scroll(action["delta"])
                elif kind == "text":
                    await self._browser.insert_text(action["text"])
                else:
                    await self._browser.backspace()
                # A fresh pull can present the login form after a click enters a protected page.
                return self.current() and (self._login is not None or await self._stamp() is not None) and self.current()
        except (OSError, RuntimeError, KeyError, ValueError):
            return False

    async def submit_login(self, message: dict) -> bool:
        async with self._lock:
            self._last_frame = None
            return self._login is not None and self.current() and await self._login.submit(message) and self.current()

    async def site_ready(self) -> bool:
        try:
            async with self._lock:
                if not self.current():
                    return False
                checked = await self._stamp()
                return (checked is not None and self.current()
                        and (self._login is None or checked[1] != self._login.touched_document))
        except (OSError, RuntimeError, ValueError):
            return False

    async def pull(self, allowed: Callable[[], bool], *, viewer: object | None = None) -> bytes | None:
        loop = asyncio.get_running_loop()
        delay = self._next_request - loop.time()
        if self._viewer is not viewer or self._busy or (viewer is None and delay > 0):
            return None
        self._busy = True
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            if self._viewer is not viewer or not allowed():
                return None
            self._next_request = loop.time() + MIN_FRAME_INTERVAL
            return await asyncio.wait_for(self._capture(allowed), FRAME_TIMEOUT)
        except (asyncio.TimeoutError, OSError, RuntimeError, ValueError):
            return None
        finally:
            self._busy = False
