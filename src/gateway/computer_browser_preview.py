"""Short-lived, read-only browser frames from a paired computer's explicit offer."""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from src.gateway.computer_browser_wire import (
    BrowserPreviewDenied, CONTROL_BUSY, CONTROL_BYTES, CONTROL_READY, FRAME_TIMEOUT, INPUT_APPLIED, INPUT_FRAME_SECONDS,
    INPUT_UNAVAILABLE, LOGIN_FORM, LOGIN_FORM_SECONDS, LOGIN_SENT, LOGIN_WAITING, LoginForm, MAX_PNG_BYTES,
    MIN_FRAME_INTERVAL, OFFER_SECONDS, PROTECTED, SITE_READY, PreviewKind, browser_input, desktop_input, validate_png,
)
from src.gateway.computer_connections import HELLO_SECONDS, Connection, connections
from src.gateway.computer_protocol import decode_frame, identifier
from src.gateway.paired_browser_task import PairedBrowserTask, redeem_browser_invite

CHECK_INTERVAL = 1.0


def _png(packet: bytes, request_id: str) -> bytes:
    tag = request_id.encode("ascii")
    if len(packet) > len(tag) + MAX_PNG_BYTES or not packet.startswith(tag):
        raise BrowserPreviewDenied("invalid browser frame")
    return validate_png(packet[len(tag):])


@dataclass(eq=False)
class BrowserPreview:
    connection: Connection
    publisher: Any
    surface_id: str
    expires_at: float
    pending: tuple[str, asyncio.Future[bytes | LoginForm | None], str] | None = None
    viewing: bool = False
    closed: bool = False
    last_capture: float = float("-inf")
    task: PairedBrowserTask | None = None
    allow_control: bool = False
    allow_login: bool = False
    controlling: bool = False
    kind: PreviewKind = "browser"
    _read_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def active(self) -> bool:
        return (self.connection.loop is asyncio.get_running_loop() and not self.closed
                and (self.task is None or self.task.current())
                and time.monotonic() < self.expires_at and not self.connection.revoking
                and not self.connection.finished and connections.current(self.connection))

    async def live(self) -> bool:
        return self.active() and await self.connection.valid() and self.active()

    def begin_viewing(self) -> bool:
        if self.viewing or self.closed:
            return False
        self.viewing = True
        return True

    def begin_control(self) -> bool:
        if not (self.viewing and self.allow_control and self.active()) or self.controlling:
            return False
        self.controlling = True
        return True

    def end_viewing(self) -> bool:
        was_controlling = self.controlling
        self.controlling = False
        self.viewing = False
        return was_controlling

    def accept_frame(self, packet: bytes) -> None:
        pending = self.pending
        if pending is None or pending[1].done():
            raise BrowserPreviewDenied("no requested browser frame")
        request_id, reply, operation = pending
        if self.kind == "desktop" and operation != "capture":
            if not self.allow_control or not packet.startswith(request_id.encode("ascii")):
                raise BrowserPreviewDenied("invalid desktop acknowledgement")
            accepted = {"take_control": (CONTROL_READY, CONTROL_BUSY), "input": (INPUT_APPLIED, INPUT_UNAVAILABLE)}
            result = packet[len(request_id):]
            if result not in accepted.get(operation, ()):
                raise BrowserPreviewDenied("invalid desktop acknowledgement")
            reply.set_result(result)
            return
        if self.task is not None:
            tag = request_id.encode("ascii")
            if packet == tag + PROTECTED:
                reply.set_result(None)
                return
            if operation != "capture":
                accepted = {"verify_site": SITE_READY, "input": INPUT_APPLIED, "submit_login": LOGIN_SENT}[operation]
                if packet != tag + accepted:
                    raise BrowserPreviewDenied("invalid browser acknowledgement")
                reply.set_result(accepted)
                return
            if self.allow_login and packet.startswith(tag):
                payload = packet[len(tag):]
                if payload in (LOGIN_WAITING, SITE_READY):
                    reply.set_result(payload)
                    return
                if payload.startswith(LOGIN_FORM):
                    reply.set_result(LoginForm.from_packet(payload))
                    return
        reply.set_result(_png(packet, request_id))

    async def capture(self) -> bytes | LoginForm | None:
        now = time.monotonic()
        if not self.viewing or now - self.last_capture < MIN_FRAME_INTERVAL:
            raise BrowserPreviewDenied("browser frame unavailable")
        self.last_capture = now
        return await self._read("capture")

    async def verify_site(self) -> bool:
        if self.task is None or self.controlling:
            return False
        try:
            return (await self.live() and await self._read("verify_site") == SITE_READY
                    and await self.live() and not self.controlling)
        except (asyncio.TimeoutError, OSError, ValueError, RuntimeError):
            return False

    async def interact(self, action: object) -> bool:
        action = browser_input(action)
        return (self.controlling and await self.live()
                and await self._read("input", action=action) == INPUT_APPLIED and await self.live())

    async def take_desktop_control(self) -> str | None:
        if self.kind != "desktop" or not self.begin_control():
            return None
        result = await self._read("take_control")
        if result == CONTROL_BUSY:
            self.controlling = False
            return "control_busy"
        return "control_ready" if result == CONTROL_READY and await self.live() else None

    async def interact_desktop(self, action: object) -> str | None:
        value = desktop_input(action)
        if self.kind != "desktop" or not self.controlling or not await self.live():
            return None
        result = await self._read("input", action=value)
        if not await self.live():
            return None
        return {INPUT_APPLIED: "input_applied", INPUT_UNAVAILABLE: "input_unavailable"}.get(result)

    async def submit_login(self, message: dict) -> bool:
        return (self.allow_login and self.controlling and await self.live()
                and await self._read("submit_login", action=message) == LOGIN_SENT and await self.live())

    async def _read(self, operation: str, *, action: dict | None = None) -> bytes | LoginForm | None:
        requires_control = operation in ("input", "submit_login")
        async with self._read_lock:
            if (not self.active() or (requires_control and not self.controlling)
                    or (operation == "verify_site" and self.controlling)):
                raise BrowserPreviewDenied("browser frame unavailable")
            request_id = uuid.uuid4().hex
            reply = asyncio.get_running_loop().create_future()
            self.pending = request_id, reply, operation
            try:
                async def exchange():
                    if not self.active() or (requires_control and not self.controlling):
                        return None
                    message = {"type": operation, "request_id": request_id, "surface_id": self.surface_id}
                    if action is not None:
                        message["action"] = action
                    await self.publisher.send_json(message)
                    return await reply

                return await asyncio.wait_for(exchange(), timeout=FRAME_TIMEOUT)
            finally:
                self.pending = None
                reply.cancel()

    def end(self) -> None:
        self.closed = True
        if self.pending and not self.pending[1].done():
            self.pending[1].set_result(None)


class BrowserPreviews:
    def __init__(self):
        self._lock = threading.RLock()
        self._offers: dict[tuple[str, str, PreviewKind], BrowserPreview] = {}
        self._owner_epoch = object()

    def owner_epoch(self) -> object:
        with self._lock:
            return self._owner_epoch

    def end_owner_views(self) -> None:
        """The home's owner cookie is shared, so signing out fences all open owner views."""
        with self._lock:
            self._owner_epoch = object()

    def register(self, preview: BrowserPreview) -> bool:
        connection = preview.connection
        key = connection.root, connection.binding["connector_id"], preview.kind
        with self._lock:
            prior = self._offers.get(key)
            if prior is not None and prior.connection is connection:
                return False
            self._offers[key] = preview
            return True

    def find(self, root: str, connector_id: str, kind: PreviewKind = "browser") -> BrowserPreview | None:
        with self._lock:
            return self._offers.get((root, connector_id, kind))

    def discard(self, preview: BrowserPreview) -> None:
        connection = preview.connection
        key = connection.root, connection.binding["connector_id"], preview.kind
        with self._lock:
            if self._offers.get(key) is preview:
                del self._offers[key]
        preview.end()
        if preview.task is not None:
            preview.task.detach(preview)


browser_previews = BrowserPreviews()


async def _command(socket) -> dict:
    message = await socket.receive_text()
    if len(message.encode("utf-8")) > CONTROL_BYTES:
        raise BrowserPreviewDenied("invalid browser preview message")
    return decode_frame(message)


async def serve_publisher(socket, root: str, binding: dict, *, kind: PreviewKind = "browser") -> None:
    """Accept the separate data socket only for the current control generation."""
    await socket.accept()
    offer = await asyncio.wait_for(_command(socket), timeout=HELLO_SECONDS)
    fields = {"type", "generation", "surface_id"}
    task_fields = fields | {"task_code"}
    controls = task_fields | {"allow_control"}
    shapes = ((fields, task_fields, controls, controls | {"allow_login"}) if kind == "browser"
              else (fields, fields | {"allow_control"}))
    if (set(offer) not in shapes or offer["type"] != "offer"
            or offer.get("allow_control", True) is not True or offer.get("allow_login", True) is not True):
        raise BrowserPreviewDenied("invalid browser offer")
    generation, surface = identifier(offer["generation"]), identifier(offer["surface_id"])
    connection = connections.active_binding(root, binding, generation)
    if connection is None or not await connection.valid():
        return
    prior = browser_previews.find(root, binding["connector_id"], kind)
    if prior is not None and prior.connection is connection:
        return
    task = None
    if "task_code" in offer:
        task = redeem_browser_invite(identifier(offer["task_code"]), connection)
        if task is None:
            return
    preview = BrowserPreview(connection, socket, surface, time.monotonic() + OFFER_SECONDS,
                             task=task, allow_control=offer.get("allow_control", False),
                             allow_login=offer.get("allow_login", False), kind=kind)
    if not preview.active() or not browser_previews.register(preview):
        return
    if task is not None:
        task.attach(preview)
    try:
        receipt = {"type": "offered", "expires_in": int(OFFER_SECONDS)}
        if task is not None:
            receipt["site_origin"] = task.origin
        await asyncio.wait_for(socket.send_json(receipt), timeout=FRAME_TIMEOUT)
        while await preview.live():
            try:
                packet = await asyncio.wait_for(socket.receive_bytes(), timeout=CHECK_INTERVAL)
            except asyncio.TimeoutError:
                continue
            if not await preview.live():
                return
            preview.accept_frame(packet)
            del packet
    finally:
        browser_previews.discard(preview)


async def _capture_while_connected(socket, capture: Callable[[], Awaitable[bytes | LoginForm | None]]) -> bytes | LoginForm | None:
    frame = asyncio.ensure_future(capture())
    incoming = asyncio.create_task(socket.receive())
    try:
        done, _ = await asyncio.wait((frame, incoming), return_when=asyncio.FIRST_COMPLETED)
        if incoming in done:
            raise BrowserPreviewDenied("viewer closed or sent a command before the frame")
        return frame.result()
    finally:
        frame.cancel()
        incoming.cancel()
        await asyncio.gather(frame, incoming, return_exceptions=True)


async def serve_viewer(socket, capture: Callable[[], Awaitable[bytes | LoginForm | None]],
                       allowed: Callable[[], Awaitable[bool]], *, cancel_on_disconnect: bool = False,
                       on_command: Callable[[dict], Awaitable[str | None]] | None = None) -> None:
    """Relay exactly one frame per pull and recheck authority after capture."""
    if not await allowed():
        return
    await socket.accept()
    ready = {"type": "ready", "min_frame_ms": int(MIN_FRAME_INTERVAL * 1000)}
    await asyncio.wait_for(socket.send_json(ready), timeout=FRAME_TIMEOUT)
    next_input_until = 0.0
    login_offer = None
    while await allowed():
        try:
            message = await asyncio.wait_for(_command(socket), timeout=CHECK_INTERVAL)
        except asyncio.TimeoutError:
            continue
        if not await allowed():
            return
        if message != {"type": "next"}:
            if on_command is None or time.monotonic() >= next_input_until:
                return
            if message.get("type") == "submit_login":
                if login_offer is None or message.get("offer") != login_offer:
                    return
            elif login_offer is not None and message != {"type": "take_control"}:
                return
            login_offer = None
            next_input_until = 0.0
            result = await on_command(message)
            if result is None or not await allowed():
                return
            await asyncio.wait_for(socket.send_json({"type": result}), timeout=FRAME_TIMEOUT)
            if result == "paused":
                return
            continue
        # A paired publisher has to finish its outstanding request even if the viewer leaves.
        image = await _capture_while_connected(socket, capture) if cancel_on_disconnect else await capture()
        if not await allowed():
            return
        if image is None:
            await asyncio.wait_for(socket.send_json({"type": "paused"}), timeout=FRAME_TIMEOUT)
            return
        if image in (LOGIN_WAITING, SITE_READY):
            if on_command is None:
                return
            status = "login_ready" if image == SITE_READY else "login_waiting"
            await asyncio.wait_for(socket.send_json({"type": status}), timeout=FRAME_TIMEOUT)
            if image == SITE_READY:
                return
            login_offer = None
            next_input_until = 0.0
            continue
        if isinstance(image, LoginForm):
            if on_command is None:
                return
            await asyncio.wait_for(socket.send_json(image.message()), timeout=FRAME_TIMEOUT)
            login_offer = image.offer
            next_input_until = time.monotonic() + LOGIN_FORM_SECONDS
        else:
            await asyncio.wait_for(socket.send_bytes(image), timeout=FRAME_TIMEOUT)
            login_offer = None
            next_input_until = time.monotonic() + INPUT_FRAME_SECONDS
        del image
