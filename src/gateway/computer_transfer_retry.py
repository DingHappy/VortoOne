"""Short reconnect window for a transfer that already has owner approval."""
from __future__ import annotations

import asyncio
import time

from src.agents.tool_runtime import check_tool_execution
from src.gateway.computer_connections import connections

RECONNECT_SECONDS = 20
POLL_SECONDS = 0.2


async def reconnect_transfer(previous, deadline, protocol, authorized):
    if await previous.valid():
        return None  # A refusal on the same live connection is final for this call.
    clock = asyncio.get_running_loop().time
    stop = clock() + min(RECONNECT_SECONDS, max(0, deadline - time.time()))
    while clock() < stop and time.time() < deadline:
        check_tool_execution()
        if not await asyncio.to_thread(previous.store.check, previous.binding):
            return None
        candidate = connections.replacement(previous)
        if candidate is not None:
            if (candidate.generation != previous.generation and candidate.binding == previous.binding
                    and candidate.protocol >= protocol and await authorized(candidate)):
                return candidate
            return None
        remaining = min(stop - clock(), deadline - time.time())
        if remaining > 0:
            await asyncio.sleep(min(POLL_SECONDS, remaining))
    return None
