"""Live control-only computer connections; never an execution or tool transport."""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from starlette.websockets import WebSocketDisconnect

from src.gateway.computer_protocol import decode_frame, validate_hello, welcome

REVOKE_ACK_SECONDS = 2.0
CHECK_SECONDS = 1.0
PING_SECONDS = 15.0
IDLE_SECONDS = 45.0
HELLO_SECONDS = 10.0


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

    async def revoke(self, replaced: bool = False) -> None:
        if self.revoking or self.finished:
            return
        self.revoking = True                # Fence before sending, including during the ACK wait.
        deadline = time.monotonic() + REVOKE_ACK_SECONDS
        try:
            await asyncio.wait_for(self.socket.send_json({
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

    def add(self, root: str, binding: dict, socket) -> Connection:
        new = Connection(root, dict(binding), socket, asyncio.get_running_loop())
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

    def revoked(self, root: str, connector_id: str) -> None:
        with self._lock:
            connection = self._current.get((root, connector_id))
        if connection is not None:
            self._schedule(connection)

    def online(self, root: str, connector_id: str) -> bool:
        with self._lock:
            found = self._current.get((root, connector_id))
            return found is not None and not found.revoking and not found.finished


connections = Connections()


async def serve(socket, root: str, store, binding: dict) -> None:
    """Only protocol/liveness frames are accepted. There are no execution callbacks."""
    connection = None
    try:
        hello = await _frame(socket, HELLO_SECONDS)
        validate_hello(hello, binding)
        if not await asyncio.to_thread(store.check, binding):
            raise ValueError('invalid authorization')
        connection = connections.add(root, binding, socket)
        await socket.send_json(welcome(binding, connection.generation))
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
                    await socket.send_json({'type': 'ping', 'generation': connection.generation})
                    last_ping = now
                continue
            if set(frame) != {'type', 'generation'} or frame['generation'] != connection.generation:
                raise ValueError('invalid frame')
            if connection.revoking or not connections.current(connection):
                if frame['type'] == 'ack':
                    connection.ack.set()
                continue
            if not await asyncio.to_thread(store.check, binding):
                connections._schedule(connection)
                continue
            last_received = time.monotonic()
            if frame['type'] == 'ping':
                await socket.send_json({'type': 'pong', 'generation': connection.generation})
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
