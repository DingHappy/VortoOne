"""Snapshot an explicitly selected home outbox file for one approved delivery."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import os
import time
import uuid

from src.agents.tool_runtime import check_tool_execution
from src.gateway.computer_connections import FileConnectionUnavailable
from src.gateway.computer_file_intents import FileIntentUnavailable
from src.gateway.computer_file_paths import FileDenied, PinnedFile
from src.gateway.computer_file_store import FileDelivery
from src.gateway.connected_computers import ComputerUnavailable
from src.gateway.computer_file_wire import result_value, version_value
from src.gateway.computer_protocol import DELIVERY_PROTOCOL, MAX_TRANSFER_BYTES
from src.gateway.computer_transfer_retry import reconnect_transfer
from src.gateway.computer_transfers import (
    TransferDenied, TransferFile, TransferUnavailable, computer_transfers, private_spool,
)
from src.gateway.computer_transfer_io import CHUNK_BYTES, file_io, write_chunk
from src.utils.home_maintenance import FileLease
from src.utils.state_dir import state_dir

OUTBOX = "computer-outbox"


def outgoing_source(root: str, path: str) -> PinnedFile:
    return PinnedFile.in_private_directory(str(state_dir(root) / OUTBOX), path)


async def _snapshot(source, info, handle, deadline, still_current):
    digest = hashlib.sha256()
    read = 0
    next_check = 0.0
    with source.open_version(info["version"], MAX_TRANSFER_BYTES) as (stream, size):
        if size != info["size"]:
            raise FileDenied("发件文件在确认后变化")
        while read < size:
            check_tool_execution()
            now = time.monotonic()
            if time.time() >= deadline or (now >= next_check and not await still_current()):
                raise FileDenied("发送授权已变化或超时")
            if now >= next_check:
                next_check = now + 1
            data = await file_io(stream.read, min(CHUNK_BYTES, size - read))
            if not data:
                raise FileDenied("发件文件在读取期间变化")
            digest.update(data)
            read += len(data)
            await write_chunk(handle, data)
    check_tool_execution()
    if not await still_current() or time.time() >= deadline:
        raise FileDenied("发送授权已变化或超时")
    return digest.hexdigest()


async def _deliver(connection, handle, file, action, intents):
    # The approval retains the snapshot. A ticket only owns its streaming descriptor.
    check_tool_execution()
    staged = os.fdopen(os.dup(handle.fileno()), "rb", buffering=0)
    issued = None
    try:
        issued = await computer_transfers.issue(connection, file, action_deadline=action.deadline, source=staged)
        check_tool_execution()
        result = result_value(await connection.file_request(
            "transfer_download", {"action": asdict(action), "ticket": issued["ticket"]},
            timeout=max(0.001, action.deadline - time.time())), action)
        if result["status"] == "busy":
            if await file_io(intents.set_waiting, connection.binding, action, waiting=True):
                return "waiting"
        elif await connection.receipt(result):
            return result["status"]
        raise FileConnectionUnavailable("文件回执未能核对")
    finally:
        if issued is None:
            staged.close()
        else:
            computer_transfers.abort(connection, issued["transfer_id"])


async def _resume_delivery(previous, source, info, target, action, file, handle, intents, authorized):
    current = await reconnect_transfer(previous, action.deadline, DELIVERY_PROTOCOL, authorized)
    if current is None:
        return "unknown"
    account = await file_io(current.store.identity)
    status = await file_io(intents.status, action.action_id, account)
    if status in ("created", "blocked"):
        return status
    await computer_transfers.wait_retired(previous, file, action.deadline)
    # Recheck the destination and grant before giving the new generation a ticket.
    preview = await current.file_request("preview", {**target, "operation": "create"})
    if version_value(preview, "create") != action.version:
        return "unknown"
    if await file_io(source.transfer_info, MAX_TRANSFER_BYTES) != info or not await authorized(current):
        return "unknown"
    return await _deliver(current, handle, file, action, intents)


async def deliver_file(connection, source, info, *, target, destination_version, owner, call_id, intents,
                       deadline, still_current):
    size = info["size"]
    lease_path = state_dir(connection.root) / "computer-file-intents" / "delivery-snapshot.lock"
    with private_spool(connection.root, size) as handle, FileLease(lease_path, private=True):
        sha256 = await _snapshot(source, info, handle, deadline, lambda: still_current(connection))
        action = FileDelivery(uuid.uuid4().hex, target["folder_id"], target["policy_revision"], "create",
                              target["path"], destination_version, deadline, owner.session, owner.rid, call_id,
                              source_version=info["version"], size=size, sha256=sha256)
        file = TransferFile(action.action_id, action.folder_id, action.policy_revision,
                            action.source_version, size, sha256)
        check_tool_execution()
        try:
            # Register the single durable intent before any attempt can publish on the computer.
            await file_io(intents.record, connection.binding, action)
            try:
                status = await _deliver(connection, handle, file, action, intents)
            except (FileConnectionUnavailable, TransferDenied):
                status = await _resume_delivery(
                    connection, source, info, target, action, file, handle, intents, still_current)
        except (ComputerUnavailable, FileConnectionUnavailable, FileIntentUnavailable, TransferUnavailable, ValueError, OSError):
            # A lost reply can follow publication. The durable local receipt can still arrive.
            status = "unknown"
        return action.action_id, status
