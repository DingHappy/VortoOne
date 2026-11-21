"""One-use directional tickets for transfers between the home and a computer."""
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
from src.gateway.computer_transfer_io import MIN_DISK_FREE, write_chunk
from src.gateway.computer_protocol import (
    DELIVERY_PROTOCOL, FILES_PROTOCOL, MAX_TRANSFER_BYTES, TRANSFER_SECONDS, binding_fields, identifier,
)
from src.utils.state_dir import state_dir

TICKET_SECONDS = 300
DOWNLOAD_SECONDS = 600
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
    outgoing: bool = False
    claimed: bool = False
    staged: BinaryIO | None = None
    filename: str | None = None
    downloading: bool = False
    aborted: bool = False
    expiry_handle: asyncio.TimerHandle | None = None


def private_spool(root: str, size: int) -> BinaryIO:
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


class ComputerTransfers:
    def __init__(self):
        self._lock = threading.RLock()
        self._by_home: dict[str, _Transfer] = {}

    async def issue(self, connection: Connection, file: TransferFile, *, action_deadline: float,
                    source: BinaryIO | None = None) -> dict:
        """Called by the trusted adapter after the owner approves a specific file."""
        protocol = DELIVERY_PROTOCOL if source is not None else FILES_PROTOCOL
        if connection.protocol < protocol or not await connection.valid():
            raise TransferDenied("transfer is outside the current action")
        remaining = action_deadline - time.time()
        if not 0 < remaining <= TRANSFER_SECONDS:
            raise TransferDenied("transfer is outside the current action")
        ticket = "vct_" + secrets.token_urlsafe(32)
        loop = asyncio.get_running_loop()
        now = time.monotonic()
        claim_for = min(TICKET_SECONDS, remaining)
        transfer = _Transfer(connection, binding_fields(connection.binding), file,
                             hashlib.sha256(ticket.encode("ascii")).digest(), secrets.token_hex(16),
                             now + remaining, now + claim_for, outgoing=source is not None, staged=source)
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
            if transfer.outgoing:
                transfer.aborted = True
            if not transfer.downloading and (not transfer.claimed or transfer.staged is not None):
                self._drop(transfer)

    def _prune(self) -> None:
        now = time.monotonic()
        for transfer in list(self._by_home.values()):
            if transfer.downloading or (transfer.claimed and transfer.staged is None):
                continue
            connection = transfer.connection
            stale = (connection.revoking or connection.finished or not connections.current(connection))
            if (now >= (transfer.deadline if transfer.claimed else transfer.ticket_expiry)
                    or (transfer.filename is None and stale)):
                self._drop(transfer)

    def _drop(self, transfer: _Transfer) -> None:
        if self._by_home.get(transfer.connection.root) is transfer:
            self._by_home.pop(transfer.connection.root)
        if transfer.expiry_handle is not None:
            transfer.expiry_handle.cancel()
        if transfer.staged is not None:
            transfer.staged.close()
            transfer.staged = None

    def _claim(self, root: str, binding: dict, ticket: str, size: int | None) -> _Transfer:
        provided = hashlib.sha256(ticket.encode("ascii")).digest()
        with self._lock:
            self._prune()
            transfer = self._by_home.get(root)
            if (transfer is None or transfer.claimed or transfer.aborted or transfer.binding != binding_fields(binding)
                    or transfer.outgoing != (size is None) or (size is not None and transfer.file.size != size)
                    or not hmac.compare_digest(provided, transfer.ticket_digest)):
                raise TransferDenied("transfer ticket invalid")
            transfer.claimed = True
            transfer.downloading = transfer.outgoing
            return transfer

    async def begin_delivery(self, root: str, binding: dict, ticket: str) -> _Transfer:
        transfer = self._claim(root, binding, ticket, None)
        try:
            if not await self.valid_delivery(transfer):
                raise TransferDenied("transfer authorization changed")
            return transfer
        except BaseException:
            self.end_delivery(transfer)
            raise

    async def valid_delivery(self, transfer: _Transfer) -> bool:
        return (not transfer.aborted and time.monotonic() < transfer.deadline
                and await transfer.connection.valid() and not transfer.aborted
                and time.monotonic() < transfer.deadline)

    def end_delivery(self, transfer: _Transfer) -> None:
        with self._lock:
            self._drop(transfer)

    async def receive(self, root: str, binding: dict, ticket: str, size: int, chunks) -> dict:
        transfer = self._claim(root, binding, ticket, size)
        handle = None
        retained = False

        async def check():
            if transfer.aborted or time.monotonic() >= transfer.deadline or not await transfer.connection.valid():
                raise TransferDenied("transfer authorization changed")

        try:
            await check()
            # Opening is synchronous so cancellation cannot orphan the returned descriptor.
            handle = private_spool(root, size)
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
                    await write_chunk(handle, chunk)
            sha256 = digest.hexdigest()
            if received != size or not hmac.compare_digest(sha256, transfer.file.sha256):
                raise TransferDenied("transfer body does not match the approved file")
            await check()
            handle.seek(0)
            with self._lock:
                if transfer.aborted or self._by_home.get(root) is not transfer or time.monotonic() >= transfer.deadline:
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

    def abort(self, connection: Connection, transfer_id: str) -> None:
        with self._lock:
            transfer = self._by_home.get(connection.root)
            if (transfer is not None and transfer.connection is connection
                    and transfer.transfer_id == transfer_id and transfer.filename is None):
                transfer.aborted = True
                if not transfer.downloading and (not transfer.claimed or transfer.staged is not None):
                    self._drop(transfer)

    async def wait_retired(self, connection: Connection, file: TransferFile, deadline: float) -> None:
        """Wait briefly for the old stream to release only its own staging slot."""
        clock = asyncio.get_running_loop().time
        stop = clock() + min(READ_TIMEOUT, max(0, deadline - time.time()))
        while True:
            with self._lock:
                current = self._by_home.get(connection.root)
                if current is None or current.connection is not connection or current.file != file:
                    return
                if not current.aborted:
                    raise TransferDenied("previous ticket is still active")
            remaining = min(stop - clock(), deadline - time.time())
            if remaining <= 0:
                raise TransferUnavailable("previous transfer is still closing")
            await asyncio.sleep(min(0.05, remaining))

    async def _verified_stage(self, connection: Connection, transfer_id: str, action_id: str) -> _Transfer:
        if not await connection.valid():
            raise TransferDenied("transfer authorization changed")
        with self._lock:
            self._prune()
            transfer = self._by_home.get(connection.root)
            if (transfer is None or transfer.outgoing or transfer.connection is not connection or transfer.staged is None
                    or transfer.aborted or transfer.filename is not None or transfer.transfer_id != transfer_id
                    or transfer.file.action_id != action_id):
                raise TransferDenied("staged transfer unavailable")
            return transfer

    @asynccontextmanager
    async def take_staged(self, connection: Connection, transfer_id: str, action_id: str):
        transfer = await self._verified_stage(connection, transfer_id, action_id)
        with self._lock:
            if transfer.staged is None:
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

    async def publish(self, connection: Connection, transfer_id: str, action_id: str, filename: str) -> None:
        transfer = await self._verified_stage(connection, transfer_id, action_id)
        with self._lock:
            if transfer.staged is None or transfer.filename is not None:
                raise TransferDenied("staged transfer unavailable")
            transfer.filename = filename
            transfer.deadline = time.monotonic() + DOWNLOAD_SECONDS
            if transfer.expiry_handle is not None:
                transfer.expiry_handle.cancel()
            transfer.expiry_handle = asyncio.get_running_loop().call_later(DOWNLOAD_SECONDS, self._expire, transfer)

    @staticmethod
    def owned_download(transfer: _Transfer, identity: dict) -> bool:
        return (identity.get("ownership_state") == "claimed"
                and identity["home_instance_id"] == transfer.binding["home_instance_id"]
                and identity["owner_subject"] == transfer.binding["paired_principal_id"]
                and identity["trust_epoch"] == transfer.binding["trust_epoch"]
                and time.monotonic() < transfer.deadline)

    def begin_download(self, root: str, identity: dict, transfer_id: str) -> _Transfer:
        with self._lock:
            self._prune()
            transfer = self._by_home.get(root)
            if (transfer is None or transfer.transfer_id != transfer_id or transfer.filename is None
                    or transfer.staged is None or transfer.downloading or not self.owned_download(transfer, identity)):
                raise TransferDenied("download unavailable")
            transfer.downloading = True
            return transfer

    def end_download(self, transfer: _Transfer, *, completed: bool) -> None:
        with self._lock:
            transfer.downloading = False
            if completed or time.monotonic() >= transfer.deadline:
                self._drop(transfer)


computer_transfers = ComputerTransfers()
