"""Bounded source reads and an outbound upload to the paired home."""
from __future__ import annotations

import asyncio
import hashlib
import time

import aiohttp

from src.gateway.computer_file_paths import FileDenied
from src.gateway.computer_file_wire import staged_transfer
from src.gateway.computer_protocol import MAX_TRANSFER_BYTES, TRANSFER_PATH, TRANSFER_SECONDS, decode_frame

CHUNK_BYTES = 256 * 1024


async def _read(stream, length):
    # Closing a descriptor while a background read still owns it can reuse the wrong descriptor.
    task = asyncio.create_task(asyncio.to_thread(stream.read, length))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise


async def native_transfer(session, config, store, method, body, active):
    def source():
        return store.transfer_source(body["folder_id"], body["path"], body["policy_revision"])

    if method == "transfer_preview":
        return await asyncio.to_thread(lambda: source().transfer_info(MAX_TRANSFER_BYTES))

    remaining = body["deadline"] - time.time()
    if not active() or not 0 < remaining <= TRANSFER_SECONDS:
        raise FileDenied("传输已取消或过期")
    clock = asyncio.get_running_loop().time
    deadline = clock() + remaining
    last_check = 0.0

    async def check(*, final=False):
        nonlocal last_check
        now = clock()
        if now >= deadline or time.time() >= body["deadline"] or not active():
            raise FileDenied("传输已取消或过期")
        if final or now - last_check >= 1:
            await asyncio.to_thread(source)
            last_check = now

    digest = hashlib.sha256()
    sent = 0
    with store.transfer_lease(), source().open_version(body["version"], MAX_TRANSFER_BYTES) as (stream, size):
        if size != body["size"]:
            raise FileDenied("文件大小已变化")

        async def chunks():
            nonlocal sent
            while sent < size:
                await check()
                piece = await _read(stream, min(CHUNK_BYTES, size - sent))
                if not piece:
                    raise FileDenied("文件在传输期间变化")
                await check()
                sent += len(piece)
                digest.update(piece)
                yield piece

        if method == "transfer_hash":
            async for _ in chunks():
                pass
            result = {"version": body["version"], "size": size, "sha256": digest.hexdigest()}
        else:
            headers = {"Authorization": "Bearer " + config["token"],
                       "X-VortoOne-Transfer-Ticket": body["ticket"],
                       "Content-Type": "application/octet-stream", "Content-Length": str(size)}
            timeout = aiohttp.ClientTimeout(total=max(0.001, deadline - clock()), sock_read=20)
            async with session.put(config["origin"] + TRANSFER_PATH, data=chunks() if size else b"",
                                   headers=headers, timeout=timeout, allow_redirects=False) as response:
                if response.status != 201:
                    raise FileDenied("家未接受这次传输")
                raw = bytearray()
                async for piece in response.content.iter_chunked(1024):
                    raw.extend(piece)
                    if len(raw) > 4096:
                        raise FileDenied("传输回执超限")
                result = staged_transfer(decode_frame(bytes(raw)), transfer_id=body["transfer_id"],
                                         size=size, sha256=body["sha256"])
            if sent != size or digest.hexdigest() != body["sha256"]:
                raise FileDenied("传输内容已变化")
        await check(final=True)
    return result
