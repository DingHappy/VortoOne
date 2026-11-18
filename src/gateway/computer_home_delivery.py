"""Snapshot an explicitly selected home outbox file for one approved delivery."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import time
import uuid

from src.agents.tool_runtime import check_tool_execution
from src.gateway.computer_connections import FileConnectionUnavailable
from src.gateway.computer_file_intents import FileIntentUnavailable
from src.gateway.computer_file_paths import FileDenied, PinnedFile
from src.gateway.computer_file_store import FileDelivery
from src.gateway.connected_computers import ComputerUnavailable
from src.gateway.computer_file_wire import result_value
from src.gateway.computer_protocol import MAX_TRANSFER_BYTES
from src.gateway.computer_transfers import TransferFile, computer_transfers, private_spool
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


async def deliver_file(connection, source, info, *, target, destination_version, owner, call_id, intents,
                       deadline, still_current):
    size = info["size"]
    handle = private_spool(connection.root, size)
    issued = None
    action = None
    try:
        lease = state_dir(connection.root) / "computer-file-intents" / "delivery-snapshot.lock"
        with FileLease(lease, private=True):
            sha256 = await _snapshot(source, info, handle, deadline, still_current)
            action = FileDelivery(uuid.uuid4().hex, target["folder_id"], target["policy_revision"], "create",
                                  target["path"], destination_version, deadline, owner.session, owner.rid, call_id,
                                  source_version=info["version"], size=size, sha256=sha256)
            check_tool_execution()
            file = TransferFile(action.action_id, action.folder_id, action.policy_revision,
                                action.source_version, size, sha256)
            issued = await computer_transfers.issue(connection, file, action_deadline=deadline, source=handle)
            handle = None
            await file_io(intents.record, connection.binding, action)
        check_tool_execution()
        try:
            result = result_value(await connection.file_request(
                "transfer_download", {"action": asdict(action), "ticket": issued["ticket"]},
                timeout=max(0.001, deadline - time.time())), action)
            if result["status"] == "busy":
                if await file_io(intents.set_waiting, connection.binding, action, waiting=True):
                    return action.action_id, "waiting"
            elif await connection.receipt(result):
                return action.action_id, result["status"]
        except (ComputerUnavailable, FileConnectionUnavailable, FileIntentUnavailable, ValueError, OSError):
            # A lost reply can follow a successful publication. The local outbox
            # reports the same action receipt when the connection returns.
            pass
        return action.action_id, "unknown"
    finally:
        if issued is not None:
            computer_transfers.abort(connection, issued["transfer_id"])
        if handle is not None:
            handle.close()
