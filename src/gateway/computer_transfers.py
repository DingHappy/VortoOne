"""One-use receiving tickets for files sent by a connected computer."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
import hmac
import os
import secrets
import shutil
import stat
import tempfile
import threading
import time
from typing import BinaryIO

from src.gateway.computer_connections import Connection, connections
from src.gateway.computer_file_wire import digest_value
from src.gateway.computer_protocol import FILES_PROTOCOL, binding_fields, identifier
from src.utils.state_dir import state_dir

MAX_TRANSFER_BYTES = 1024 ** 3
MIN_DISK_FREE = 128 * 1024 ** 2
TICKET_SECONDS = 300
READ_TIMEOUT = 10
MAX_HOMES = 16


class TransferDenied(ValueError):
    """The ticket or declared bytes cannot be accepted."""


class TransferUnavailable(RuntimeError):
    """Private staging or disk capacity is unavailable."""


@dataclass(frozen=True)
class TransferFile:
    action_id: str
    folder_id: str
    policy_revision: str
    version: str
    size: int
    sha256: str

    def __post_init__(self):
        for value in (self.action_id, self.folder_id, self.policy_revision):
            identifier(value)
        digest_value(self.version)
        digest_value(self.sha256)
        if type(self.size) is not int or not 0 <= self.size <= MAX_TRANSFER_BYTES:
            raise TransferDenied("invalid transfer size")


@dataclass(eq=False)
class _Transfer:
    connection: Connection
    binding: dict
    file: TransferFile
    ticket_digest: bytes
    transfer_id: str
    deadline: float
    ticket_expiry: float
    claimed: bool = False
    staged: BinaryIO | None = None
    expiry_handle: asyncio.TimerHandle | None = None


def _spool(root: str, size: int) -> BinaryIO:
    try:
        directory = state_dir(root) / "computer-file-intents"
        if os.name != "posix" or directory.parent.is_symlink():
            raise TransferUnavailable("private transfer directory unavailable")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise TransferUnavailable("private transfer directory unavailable")
        if shutil.disk_usage(directory).free < size + MIN_DISK_FREE:
            raise TransferUnavailable("insufficient disk for transfer")
        # The descriptor owns the staging bytes. Closing it or killing the home reclaims them.
        return tempfile.TemporaryFile(mode="w+b", buffering=0, dir=directory)
    except OSError as error:
        raise TransferUnavailable("transfer staging unavailable") from error


async def _write(handle: BinaryIO, chunk: bytes) -> None:
    # Keep ownership of the descriptor while the worker is still writing, even on disconnect.
    task = asyncio.create_task(asyncio.to_thread(handle.write, chunk))
    try:
        written = await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise
    if written != len(chunk):
        raise TransferUnavailable("incomplete transfer write")


class ComputerTransfers:
    def __init__(self):
        self._lock = threading.RLock()
        self._by_home: dict[str, _Transfer] = {}

    async def issue(self, connection: Connection, file: TransferFile, *, action_deadline: float) -> dict:
        """Called by the trusted adapter after the owner approves a specific file."""
        if connection.protocol != FILES_PROTOCOL or not await connection.valid():
            raise TransferDenied("transfer is outside the current action")
        remaining = action_deadline - time.time()
        if not 0 < remaining <= 1800:
            raise TransferDenied("transfer is outside the current action")
        ticket = "vct_" + secrets.token_urlsafe(32)
        loop = asyncio.get_running_loop()
        now = time.monotonic()
        claim_for = min(TICKET_SECONDS, remaining)
        transfer = _Transfer(connection, binding_fields(connection.binding), file,
                             hashlib.sha256(ticket.encode("ascii")).digest(), secrets.token_hex(16),
                             now + remaining, now + claim_for)
        with self._lock:
            self._prune()
            if connection.root in self._by_home or len(self._by_home) >= MAX_HOMES:
                raise TransferUnavailable("transfer capacity unavailable")
            self._by_home[connection.root] = transfer
            transfer.expiry_handle = loop.call_later(claim_for, self._expire, transfer)
        return {"transfer_id": transfer.transfer_id, "ticket": ticket}

    def _expire(self, transfer: _Transfer) -> None:
        with self._lock:
            now = time.monotonic()
            expiry = transfer.deadline if transfer.claimed else transfer.ticket_expiry
            if now < expiry:
                transfer.expiry_handle = asyncio.get_running_loop().call_later(expiry - now, self._expire, transfer)
                return
            # An active reader or handoff still owns the descriptor and releases it itself.
            if not transfer.claimed or transfer.staged is not None:
                self._drop(transfer)

    def _prune(self) -> None:
        now = time.monotonic()
        for transfer in list(self._by_home.values()):
            if transfer.claimed and transfer.staged is None:
                continue
            connection = transfer.connection
            if (now >= (transfer.deadline if transfer.claimed else transfer.ticket_expiry)
                    or connection.revoking or connection.finished or not connections.current(connection)):
                self._drop(transfer)

    def _drop(self, transfer: _Transfer) -> None:
        if self._by_home.get(transfer.connection.root) is transfer:
            self._by_home.pop(transfer.connection.root)
        if transfer.expiry_handle is not None:
            transfer.expiry_handle.cancel()
        if transfer.staged is not None:
            transfer.staged.close()
            transfer.staged = None

    def _claim(self, root: str, binding: dict, ticket: str, size: int) -> _Transfer:
        provided = hashlib.sha256(ticket.encode("ascii")).digest()
        with self._lock:
            self._prune()
            transfer = self._by_home.get(root)
            if (transfer is None or transfer.claimed or transfer.binding != binding_fields(binding)
                    or not hmac.compare_digest(provided, transfer.ticket_digest) or transfer.file.size != size):
                raise TransferDenied("transfer ticket invalid")
            transfer.claimed = True
            return transfer

    async def receive(self, root: str, binding: dict, ticket: str, size: int, chunks) -> dict:
        transfer = self._claim(root, binding, ticket, size)
        handle = None
        retained = False

        async def check():
            if time.monotonic() >= transfer.deadline or not await transfer.connection.valid():
                raise TransferDenied("transfer authorization changed")

        try:
            await check()
            # Opening is synchronous so cancellation cannot orphan the returned descriptor.
            handle = _spool(root, size)
            received = 0
            digest = hashlib.sha256()
            next_check = time.monotonic() + 1
            iterator = aiter(chunks)
            while True:
                remaining = transfer.deadline - time.monotonic()
                if remaining <= 0:
                    raise TransferDenied("transfer expired")
                try:
                    chunk = await asyncio.wait_for(anext(iterator), min(READ_TIMEOUT, remaining))
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError as error:
                    raise TransferDenied("transfer body timed out") from error
                received += len(chunk)
                if received > size:
                    raise TransferDenied("transfer body exceeds approved size")
                if time.monotonic() >= next_check:
                    await check()
                    next_check = time.monotonic() + 1
                if chunk:
                    digest.update(chunk)
                    await _write(handle, chunk)
            sha256 = digest.hexdigest()
            if received != size or not hmac.compare_digest(sha256, transfer.file.sha256):
                raise TransferDenied("transfer body does not match the approved file")
            await check()
            handle.seek(0)
            with self._lock:
                if self._by_home.get(root) is not transfer or time.monotonic() >= transfer.deadline:
                    raise TransferDenied("transfer expired")
                transfer.staged = handle
                retained = True
            return {"transfer_id": transfer.transfer_id, "size": size, "sha256": sha256, "status": "staged"}
        except OSError as error:
            raise TransferUnavailable("transfer staging unavailable") from error
        finally:
            if not retained:
                try:
                    if handle is not None:
                        handle.close()
                finally:
                    with self._lock:
                        self._drop(transfer)

    @asynccontextmanager
    async def take_staged(self, connection: Connection, transfer_id: str, action_id: str):
        """Hand the private bytes back to the approving adapter once, after its final checks."""
        if not await connection.valid():
            raise TransferDenied("transfer authorization changed")
        with self._lock:
            self._prune()
            transfer = self._by_home.get(connection.root)
            if (transfer is None or transfer.connection is not connection or transfer.staged is None
                    or transfer.transfer_id != transfer_id or transfer.file.action_id != action_id):
                raise TransferDenied("staged transfer unavailable")
            handle = transfer.staged
            transfer.staged = None
        try:
            yield transfer.file, handle
        finally:
            try:
                handle.close()
            finally:
                with self._lock:
                    self._drop(transfer)


computer_transfers = ComputerTransfers()
