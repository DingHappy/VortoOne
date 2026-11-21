"""Bounded transfer I/O which keeps descriptors pinned until cancellation settles."""
from __future__ import annotations

import asyncio

CHUNK_BYTES = 256 * 1024
MIN_DISK_FREE = 128 * 1024 ** 2


async def file_io(method, *args):
    task = asyncio.create_task(asyncio.to_thread(method, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.gather(task, return_exceptions=True)
        raise


async def write_chunk(handle, chunk: bytes) -> None:
    if await file_io(handle.write, chunk) != len(chunk):
        raise OSError("incomplete transfer write")
