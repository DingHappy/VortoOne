"""Authenticated, generation-bound computer control and small-file messages."""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from starlette.websockets import WebSocketDisconnect

from src.gateway.computer_file_intents import FileIntentUnavailable, HomeFileIntents
from src.gateway.computer_file_wire import bounded_frame, object_fields, receipt_value
from src.gateway.computer_protocol import (
    FILES_PROTOCOL, PROTOCOL_MAX, PROTOCOL_MIN, binding_fields, decode_frame, identifier, validate_hello, welcome,
)

REVOKE_ACK_SECONDS = 2.0
CHECK_SECONDS = 1.0
PING_SECONDS = 15.0
IDLE_SECONDS = 45.0
HELLO_SECONDS = 10.0
FILE_REQUEST_SECONDS = 12.0


class FileConnectionUnavailable(RuntimeError):
    pass


async def _frame(socket, timeout: float) -> dict:
    message = await asyncio.wait_for(socket.receive(), timeout)
    if message['type'] == 'websocket.disconnect':
        raise WebSocketDisconnect(message.get('code', 1000))
    text = message.get('text')
    if not isinstance(text, str):
        raise ValueError('invalid frame')
    return decode_frame(text)


@dataclass(eq=False)
class Connection:
    root: str
    binding: dict
    socket: Any
    loop: Any
    generation: str = field(default_factory=lambda: uuid.uuid4().hex)
    ack: asyncio.Event = field(default_factory=asyncio.Event)
    revoking: bool = False
    finished: bool = False
    revoke_task: Any = None
    protocol: int = PROTOCOL_MIN
    store: Any = None
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending_file: tuple[str, asyncio.Future] | None = None

    async def send(self, frame: dict) -> None:
        async with self.send_lock:
            await self.socket.send_json(frame)

    async def valid(self) -> bool:
        return (not self.finished and not self.revoking and connections.current(self)
                and await asyncio.to_thread(self.store.check, self.binding)
                and not self.finished and not self.revoking and connections.current(self))

    async def file_request(self, method: str, payload: dict, *, timeout: float = FILE_REQUEST_SECONDS) -> dict:
        if self.protocol < FILES_PROTOCOL or self.loop is not asyncio.get_running_loop() or self.pending_file is not None:
            raise FileConnectionUnavailable("电脑暂不可用或正在处理文件")
        request_id = uuid.uuid4().hex
        reply = self.loop.create_future()
        self.pending_file = (request_id, reply)
        try:
            async def dispatch():
                async with self.send_lock:
                    if not await self.valid():
                        raise FileConnectionUnavailable("配对或连接已变化")
                    frame = {"type": "file_request", "generation": self.generation, "binding": binding_fields(self.binding),
                             "request_id": request_id, "method": method, "payload": payload}
                    await self.socket.send_json(bounded_frame(frame))
                return await reply
            result = await asyncio.wait_for(dispatch(), timeout)
            if result is None or not await self.valid():
                raise FileConnectionUnavailable("文件应答无法核对")
            return result
        except FileConnectionUnavailable:
            raise
        except (OSError, RuntimeError, WebSocketDisconnect, asyncio.TimeoutError) as error:
            raise FileConnectionUnavailable("电脑未确认文件请求的结果") from error
        finally:
            self.pending_file = None
            reply.cancel()

    async def receipt(self, value: dict) -> bool:
        if not await self.valid():
            return False
        intents = HomeFileIntents(self.root)
        if not await asyncio.to_thread(intents.accept, self.binding, value):
            return False
        async def acknowledge():
            async with self.send_lock:
                if await self.valid():
                    await self.socket.send_json({"type": "file_ack", "generation": self.generation,
                                                 "action_id": value["action_id"], "digest": value["digest"]})

        try:
            await asyncio.wait_for(acknowledge(), REVOKE_ACK_SECONDS)
        except (asyncio.TimeoutError, WebSocketDisconnect, RuntimeError, OSError):
            pass  # The home has the receipt; the computer can send it again.
        return True

    async def revoke(self, replaced: bool = False) -> None:
        if self.revoking or self.finished:
            return
        self.revoking = True                # Fence before sending, including during the ACK wait.
        deadline = time.monotonic() + REVOKE_ACK_SECONDS
        try:
            await asyncio.wait_for(self.send({
                'type': 'revoked', 'generation': self.generation,
                'reason': 'connection_replaced' if replaced else 'authorization_revoked',
                'credentialRevoked': not replaced,
            }), REVOKE_ACK_SECONDS)
            await asyncio.wait_for(self.ack.wait(), max(0.001, deadline - time.monotonic()))
        except (asyncio.TimeoutError, WebSocketDisconnect, RuntimeError, OSError):
            pass
        finally:
            try:
                await asyncio.wait_for(self.socket.close(code=1008), REVOKE_ACK_SECONDS)
            except (asyncio.TimeoutError, WebSocketDisconnect, RuntimeError, OSError):
                pass


class Connections:
    def __init__(self):
        self._lock = threading.RLock()
        self._current: dict[tuple[str, str], Connection] = {}

    @staticmethod
    def _schedule(connection: Connection, replaced: bool = False) -> None:
        if connection.finished or connection.loop.is_closed():
            return
        def start():
            if connection.revoke_task is None:
                connection.revoke_task = connection.loop.create_task(connection.revoke(replaced))
        try:
            connection.loop.call_soon_threadsafe(start)
        except RuntimeError:
            pass

    def add(self, root: str, binding: dict, socket, *, protocol: int = PROTOCOL_MIN, store=None) -> Connection:
        new = Connection(root, dict(binding), socket, asyncio.get_running_loop(), protocol=protocol, store=store)
        key = root, binding['connector_id']
        with self._lock:
            old = self._current.get(key)
            self._current[key] = new
        if old is not None:
            self._schedule(old, replaced=True)
        return new

    def current(self, connection: Connection) -> bool:
        with self._lock:
            return self._current.get((connection.root, connection.binding['connector_id'])) is connection

    def remove(self, connection: Connection) -> None:
        with self._lock:
            key = connection.root, connection.binding['connector_id']
            if self._current.get(key) is connection:
                self._current.pop(key, None)
        connection.finished = True
        pending = connection.pending_file
        if pending is not None and not pending[1].done():
            pending[1].set_result(None)

    def revoked(self, root: str, connector_id: str, *, credential_epoch: str | None = None) -> None:
        with self._lock:
            connection = self._current.get((root, connector_id))
        if connection is not None and (credential_epoch is None or connection.binding["credential_epoch"] == credential_epoch):
            self._schedule(connection)

    def online(self, root: str, connector_id: str) -> bool:
        return self.online_connection(root, connector_id) is not None

    def online_connection(self, root: str, connector_id: str) -> Connection | None:
        with self._lock:
            found = self._current.get((root, connector_id))
            if found is not None and not found.revoking and not found.finished:
                return found
            return None

    def active_binding(self, root: str, binding: dict, generation: str) -> Connection | None:
        with self._lock:
            found = self._current.get((root, binding["connector_id"]))
            if (found is not None and found.binding == binding and found.generation == generation
                    and not found.revoking and not found.finished):
                return found
        return None

    def file_connection(self, root: str, connector_id: str, identity: dict) -> Connection | None:
        with self._lock:
            found = self._current.get((root, connector_id))
            if (found is not None and found.protocol >= FILES_PROTOCOL
                    and not found.revoking and not found.finished
                    and found.binding["paired_principal_id"] == identity["owner_subject"]
                    and found.binding["home_instance_id"] == identity["home_instance_id"]
                    and found.binding["trust_epoch"] == identity["trust_epoch"]):
                return found
        return None


connections = Connections()


async def serve(socket, root: str, store, binding: dict, *, protocol_ceiling: int = PROTOCOL_MAX) -> None:
    """Only liveness and validated v2 file responses are accepted from computers."""
    connection = None
    try:
        hello = await _frame(socket, HELLO_SECONDS)
        selected = validate_hello(hello, binding, ceiling=protocol_ceiling)["protocol"]
        if not await asyncio.to_thread(store.check, binding):
            raise ValueError('invalid authorization')
        connection = connections.add(root, binding, socket, protocol=selected, store=store)
        await connection.send(welcome(binding, connection.generation, selected))
        last_received = last_ping = time.monotonic()
        while True:
            if not connection.revoking:
                valid = await asyncio.to_thread(store.check, binding)
                if not valid:
                    connections._schedule(connection)
                elif not connections.current(connection):
                    connections._schedule(connection, replaced=True)
            try:
                frame = await _frame(socket, CHECK_SECONDS)
            except asyncio.TimeoutError:
                now = time.monotonic()
                if not connection.revoking and now - last_received > IDLE_SECONDS:
                    await socket.close(code=1001)
                    return
                if not connection.revoking and now - last_ping > PING_SECONDS:
                    await connection.send({'type': 'ping', 'generation': connection.generation})
                    last_ping = now
                continue
            if frame.get('generation') != connection.generation:
                raise ValueError('invalid frame')
            if connection.revoking or not connections.current(connection):
                if frame == {'type': 'ack', 'generation': connection.generation}:
                    connection.ack.set()
                continue
            if not await asyncio.to_thread(store.check, binding):
                connections._schedule(connection)
                continue
            last_received = time.monotonic()
            if selected >= FILES_PROTOCOL and frame.get("type") == "file_reply":
                object_fields(frame, ("type", "generation", "request_id", "ok", "result"))
                request_id = identifier(frame["request_id"])
                if (type(frame["ok"]) is not bool or not isinstance(frame["result"], dict)
                        or (not frame["ok"] and frame["result"])):
                    raise ValueError("invalid file response")
                pending = connection.pending_file
                if pending and pending[0] == request_id and not pending[1].done():
                    pending[1].set_result(frame["result"] if frame["ok"] else None)
                continue
            if selected >= FILES_PROTOCOL and frame.get("type") == "file_receipt":
                object_fields(frame, ("type", "generation", "receipt"))
                try:
                    await connection.receipt(receipt_value(frame["receipt"]))
                except FileIntentUnavailable:
                    pass  # Leave the durable receipt pending on the computer.
                continue
            if set(frame) != {'type', 'generation'}:
                raise ValueError('invalid frame')
            if frame['type'] == 'ping':
                await connection.send({'type': 'pong', 'generation': connection.generation})
            elif frame['type'] != 'pong':
                raise ValueError('invalid frame')
    except (ValueError, TypeError, KeyError, UnicodeError, asyncio.TimeoutError, RecursionError):
        try:
            await socket.close(code=1008)
        except (RuntimeError, WebSocketDisconnect, OSError):
            pass
    except (WebSocketDisconnect, OSError, RuntimeError):
        try:
            await socket.close(code=1008)
        except (WebSocketDisconnect, OSError, RuntimeError):
            pass
    finally:
        if connection is not None:
            connections.remove(connection)
            if connection.revoke_task is not None and not connection.revoke_task.done():
                connection.revoke_task.cancel()
