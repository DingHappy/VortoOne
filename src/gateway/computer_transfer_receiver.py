"""Pull one approved snapshot from the paired home and hand it to the file worker."""
from __future__ import annotations

import asyncio
import hashlib
import shutil
import tempfile
import time

import aiohttp

from src.gateway.computer_file_capacity import has_file_capacity
from src.gateway.computer_file_paths import FileDenied
from src.gateway.computer_file_process import run_file_action
from src.gateway.computer_file_wire import action_value
from src.gateway.computer_protocol import TRANSFER_PATH, TRANSFER_SECONDS
from src.gateway.computer_transfer_io import CHUNK_BYTES, MIN_DISK_FREE, write_chunk


async def native_delivery(session, config, store, body, *, stop, revoked, disconnected):
    action = action_value(body["action"], delivery=True)
    lifetime = action.deadline - time.time()
    if not 0 < lifetime <= TRANSFER_SECONDS:
        raise FileDenied("传输已过期")
    clock = asyncio.get_running_loop().time
    deadline = clock() + lifetime

    def active():
        return (not (stop.is_set() or revoked.is_set() or disconnected.is_set())
                and clock() < deadline and time.time() < action.deadline)

    headers = {"Authorization": "Bearer " + config["token"], "X-VortoOne-Transfer-Ticket": body["ticket"]}
    with store.transfer_lease():
        target = await asyncio.to_thread(store.delivery_target, action, active)
        recorded = await asyncio.to_thread(store.existing_delivery, action)
        if recorded is not None:
            return {"action_id": action.action_id, "digest": action.digest, "status": recorded}

        if not await asyncio.to_thread(has_file_capacity, "create"):
            return {"action_id": action.action_id, "digest": action.digest, "status": "busy"}
        if await asyncio.to_thread(target.preview, "create") != action.version:
            raise FileDenied("目标目录在确认后变化")
        if shutil.disk_usage(store.directory).free < MIN_DISK_FREE + action.size:
            raise FileDenied("本机没有足够的接收空间")
        staged = tempfile.TemporaryFile(mode="w+b", buffering=0, dir=store.directory)
        try:
            timeout = aiohttp.ClientTimeout(total=max(0.001, deadline - clock()), sock_read=20)
            digest = hashlib.sha256()
            received = 0
            next_check = 0.0
            async with session.get(config["origin"] + TRANSFER_PATH, headers=headers,
                                   timeout=timeout, allow_redirects=False) as response:
                if (response.status != 200 or response.headers.getall("Content-Length", []) != [str(action.size)]
                        or response.headers.getall("Content-Type", []) != ["application/octet-stream"]
                        or response.headers.getall("Content-Encoding", []) not in ([], ["identity"])):
                    raise FileDenied("家未提供确认的文件")
                async for data in response.content.iter_chunked(CHUNK_BYTES):
                    if not active() or received + len(data) > action.size:
                        raise FileDenied("传输已取消、过期或超限")
                    now = clock()
                    if now >= next_check:
                        await asyncio.to_thread(store.delivery_target, action, active)
                        next_check = now + 1
                    digest.update(data)
                    received += len(data)
                    await write_chunk(staged, data)
            if not active() or received != action.size or digest.hexdigest() != action.sha256:
                raise FileDenied("接收内容与确认的文件不符")
        except BaseException:
            staged.close()
            raise
    with staged:
        # The fixed file worker gets the descriptor and binding, never the bearer or ticket.
        return await run_file_action(store.directory.parent, config, action, stop=stop, revoked=revoked,
                                     disconnected=disconnected, source=staged.fileno())
