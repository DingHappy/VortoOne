"""Short-lived, read-only browser frames from a paired computer's explicit offer."""
from __future__ import annotations

import asyncio
import struct
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

from src.gateway.computer_connections import HELLO_SECONDS, Connection, connections
from src.gateway.computer_protocol import decode_frame, identifier

OFFER_SECONDS = 300.0
FRAME_TIMEOUT = 3.0
MIN_FRAME_INTERVAL = 0.5
CHECK_INTERVAL = 1.0
MAX_FRAME_BYTES = 768 * 1024
MAX_WIDTH, MAX_HEIGHT = 1600, 1200
_PNG_HEADER = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
_PNG_END = b"\x00\x00\x00\x00IEND\xae\x42\x60\x82"


class BrowserPreviewDenied(ValueError):
    pass


def _png(packet: bytes, request_id: str) -> bytes:
    tag = request_id.encode("ascii")
    if not len(tag) + 45 <= len(packet) <= len(tag) + MAX_FRAME_BYTES or not packet.startswith(tag):
        raise BrowserPreviewDenied("invalid browser frame")
    data = packet[len(tag):]
    if not data.startswith(_PNG_HEADER) or not data.endswith(_PNG_END):
        raise BrowserPreviewDenied("invalid browser frame")
    width, height = struct.unpack_from(">II", data, len(_PNG_HEADER))
    if not 0 < width <= MAX_WIDTH or not 0 < height <= MAX_HEIGHT:
        raise BrowserPreviewDenied("invalid browser frame dimensions")
    return data


@dataclass(eq=False)
class BrowserPreview:
    connection: Connection
    publisher: Any
    surface_id: str
    expires_at: float
    pending: tuple[str, asyncio.Future[bytes | None]] | None = None
    viewing: bool = False
    closed: bool = False
    last_capture: float = float("-inf")

    def active(self) -> bool:
        return (self.connection.loop is asyncio.get_running_loop() and not self.closed
                and time.monotonic() < self.expires_at and not self.connection.revoking
                and not self.connection.finished and connections.current(self.connection))

    async def live(self) -> bool:
        return self.active() and await self.connection.valid() and self.active()

    def begin_viewing(self) -> bool:
        if self.viewing or self.closed:
            return False
        self.viewing = True
        return True

    def accept_frame(self, packet: bytes) -> None:
        pending = self.pending
        if pending is None or pending[1].done():
            raise BrowserPreviewDenied("no requested browser frame")
        pending[1].set_result(_png(packet, pending[0]))

    async def capture(self) -> bytes:
        now = time.monotonic()
        if (not self.viewing or not self.active() or self.pending is not None
                or now - self.last_capture < MIN_FRAME_INTERVAL):
            raise BrowserPreviewDenied("browser frame unavailable")
        self.last_capture = now
        request_id = uuid.uuid4().hex
        reply = asyncio.get_running_loop().create_future()
        self.pending = request_id, reply
        try:
            async def exchange():
                await self.publisher.send_json({"type": "capture", "request_id": request_id, "surface_id": self.surface_id})
                return await reply

            image = await asyncio.wait_for(exchange(), timeout=FRAME_TIMEOUT)
            if image is None:
                raise BrowserPreviewDenied("browser frame unavailable")
            return image
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
        self._offers: dict[tuple[str, str], BrowserPreview] = {}
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
        key = connection.root, connection.binding["connector_id"]
        with self._lock:
            prior = self._offers.get(key)
            if prior is not None and prior.connection is connection:
                return False
            self._offers[key] = preview
            return True

    def find(self, root: str, connector_id: str) -> BrowserPreview | None:
        with self._lock:
            return self._offers.get((root, connector_id))

    def discard(self, preview: BrowserPreview) -> None:
        connection = preview.connection
        key = connection.root, connection.binding["connector_id"]
        with self._lock:
            if self._offers.get(key) is preview:
                del self._offers[key]
        preview.end()


browser_previews = BrowserPreviews()


async def _command(socket) -> dict:
    message = await socket.receive_text()
    if len(message) > 512:
        raise BrowserPreviewDenied("invalid browser preview message")
    return decode_frame(message)


async def serve_publisher(socket, root: str, binding: dict) -> None:
    """Accept the separate data socket only for the current control generation."""
    await socket.accept()
    offer = await asyncio.wait_for(_command(socket), timeout=HELLO_SECONDS)
    if set(offer) != {"type", "generation", "surface_id"} or offer["type"] != "offer":
        raise BrowserPreviewDenied("invalid browser offer")
    generation, surface = identifier(offer["generation"]), identifier(offer["surface_id"])
    connection = connections.active_binding(root, binding, generation)
    if connection is None:
        return
    preview = BrowserPreview(connection, socket, surface, time.monotonic() + OFFER_SECONDS)
    if not await preview.live() or not browser_previews.register(preview):
        return
    try:
        receipt = {"type": "offered", "expires_in": int(OFFER_SECONDS)}
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


async def serve_viewer(socket, preview: BrowserPreview, allowed) -> None:
    """Relay exactly one frame per pull and recheck authority after capture."""
    if not await allowed():
        return
    await socket.accept()
    ready = {"type": "ready", "min_frame_ms": int(MIN_FRAME_INTERVAL * 1000)}
    await asyncio.wait_for(socket.send_json(ready), timeout=FRAME_TIMEOUT)
    while await allowed():
        try:
            message = await asyncio.wait_for(_command(socket), timeout=CHECK_INTERVAL)
        except asyncio.TimeoutError:
            continue
        if message != {"type": "next"} or not await allowed():
            return
        image = await preview.capture()
        if not await allowed():
            return
        await asyncio.wait_for(socket.send_bytes(image), timeout=FRAME_TIMEOUT)
        del image
