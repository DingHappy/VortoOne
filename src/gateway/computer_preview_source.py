"""Connector-side lifetime and outbound handshake for a private screen offer."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import uuid

import aiohttp

from src.gateway.computer_browser_wire import BrowserPreviewDenied, CONTROL_BYTES, FRAME_TIMEOUT, OFFER_SECONDS
from src.gateway.computer_protocol import decode_frame


async def read_command(socket, timeout: float) -> dict | None:
    message = await socket.receive(timeout=timeout)
    if message.type == aiohttp.WSMsgType.TEXT:
        return decode_frame(message.data)
    if message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
        return None
    raise BrowserPreviewDenied("invalid publisher control message")


@asynccontextmanager
async def preview_link(session, config: dict, generation: str, **options):
    origin = config["origin"].replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    surface = uuid.uuid4().hex
    clock = asyncio.get_running_loop().time
    # aiohttp 3.9 supports a numeric close timeout; newer releases use ClientWSTimeout.
    timeout_type = getattr(aiohttp, "ClientWSTimeout", None)
    timeout = timeout_type(ws_close=0.25) if timeout_type else 0.25
    async with session.ws_connect(origin + "/api/computers/browser-preview",
                                  headers={"Authorization": "Bearer " + config["token"]},
                                  max_msg_size=CONTROL_BYTES, timeout=timeout) as socket:
        started_at = clock()
        offer = {"type": "offer", "generation": generation, "surface_id": surface, **options}
        await asyncio.wait_for(socket.send_json(offer), FRAME_TIMEOUT)
        receipt = await read_command(socket, FRAME_TIMEOUT)
        fields = {"type", "expires_in"}
        if "task_code" in options:
            fields.add("site_origin")
        if receipt is None or set(receipt) != fields or receipt["type"] != "offered":
            raise BrowserPreviewDenied("preview offer unavailable")
        expires = receipt["expires_in"]
        if type(expires) is not int or not 0 < expires <= OFFER_SECONDS:
            raise BrowserPreviewDenied("invalid preview lifetime")
        yield socket, surface, started_at + expires, receipt.get("site_origin")


class PreviewSource:
    def __init__(self, config: dict, emit, share):
        self._config = config
        self._emit = emit
        self._share = share
        self._connection: tuple[aiohttp.ClientSession, str] | None = None
        self._task: asyncio.Task | None = None
        self._stopping = False

    def _status(self, generation: str, state: str) -> None:
        if self._connection is not None and self._connection[1] == generation:
            self._emit({"type": "browser_preview", "state": state, "generation": generation,
                        **{key: self._config[key] for key in ("home_instance_id", "connector_id")}})

    def connected(self, session: aiohttp.ClientSession, generation: str) -> None:
        self._connection = session, generation
        self._status(generation, "idle")

    def command(self, name: str, generation: str, *args, **options) -> None:
        connection = self._connection
        if connection is None or generation != connection[1]:
            return
        if name == "browser_preview_stop":
            if self._task is None:
                self._status(generation, "idle")
            else:
                self._cancel()
        elif name == "browser_preview_start" and self._task is None:
            self._task = asyncio.create_task(self._share(
                connection[0], self._config, generation,
                lambda state="sharing": self._status(generation, state), *args, **options,
            ))
            self._task.add_done_callback(lambda finished: self._finished(finished, generation))

    def _cancel(self) -> None:
        if self._task is not None and not self._stopping:
            self._stopping = True
            self._task.cancel()

    def _finished(self, task: asyncio.Task, generation: str) -> None:
        self._task = None
        self._stopping = False
        state = "idle" if task.cancelled() or task.exception() is None else "unavailable"
        self._status(generation, state)

    async def disconnect(self) -> None:
        self._connection = None
        if self._task is not None:
            self._cancel()
            await asyncio.gather(asyncio.shield(self._task), return_exceptions=True)
