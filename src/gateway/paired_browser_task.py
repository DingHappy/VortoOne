"""One pending owner confirmation can accept one explicitly opened browser."""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import TYPE_CHECKING

from src.gateway.computer_connections import connections

if TYPE_CHECKING:
    from src.gateway.computer_browser_preview import BrowserPreview
    from src.gateway.computer_connections import Connection

INVITE_SECONDS = 120
_invites: dict[str, PairedBrowserTask] = {}


class PairedBrowserNotReady(RuntimeError):
    pass


class PairedBrowserTask:
    def __init__(self, root: str, origin: str, owner_epoch: object, owner_identity: tuple[str, str, str]):
        self.root = root
        self.origin = origin
        self.owner_epoch = owner_epoch
        self.owner_identity = owner_identity
        self._future: asyncio.Future | None = None
        self._invite: tuple[str, Connection, float] | None = None
        self._preview: BrowserPreview | None = None

    def start(self, future: asyncio.Future) -> None:
        self._future = future

    def current(self) -> bool:
        from src.gateway.computer_browser_preview import browser_previews

        return (self._future is not None and not self._future.done()
                and self._future.get_loop() is asyncio.get_running_loop()
                and browser_previews.owner_epoch() is self.owner_epoch)

    def preview(self) -> BrowserPreview | None:
        return self._preview if self._preview is not None and self._preview.active() else None

    def invite(self, connection: Connection) -> str | None:
        binding = connection.binding
        if (not self.current() or self.preview() is not None or connection.root != self.root
                or connection.loop is not asyncio.get_running_loop() or connection.revoking or connection.finished
                or (binding["home_instance_id"], binding["paired_principal_id"], binding["trust_epoch"]) != self.owner_identity
                or not connections.current(connection)):
            return None
        self._discard_invite()
        code = uuid.uuid4().hex
        self._invite = code, connection, time.monotonic() + INVITE_SECONDS
        _invites[code] = self
        return code

    def attach(self, preview: BrowserPreview) -> None:
        self._preview = preview

    def detach(self, preview: BrowserPreview) -> None:
        if self._preview is preview:
            self._preview = None

    def _discard_invite(self) -> None:
        if self._invite is not None:
            _invites.pop(self._invite[0], None)
            self._invite = None

    def close(self) -> None:
        self._future = None
        self._discard_invite()
        if self._preview is not None:
            self._preview.end()
            self._preview = None


def redeem_browser_invite(code: str, connection: Connection) -> PairedBrowserTask | None:
    task = _invites.get(code)
    if task is None or task._invite is None:
        return None
    _, invited, deadline = task._invite
    if (invited is not connection or connection.revoking or connection.finished
            or not connections.current(connection) or deadline <= time.monotonic() or not task.current()):
        return None
    task._discard_invite()
    return task
