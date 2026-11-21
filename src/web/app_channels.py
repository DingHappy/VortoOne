"""Team channel view assembly and authenticated realtime delivery."""
from __future__ import annotations

import asyncio
from contextlib import suppress

from fastapi import WebSocketDisconnect

from src.gateway import app_protocol as F
from src.web import app_views as views
from src.workflow.members.channels import TeamChannels
from src.workflow.members.registry import Damaged, Missing, Refused


def channels(root: str) -> TeamChannels:
    owner = views.person(root)
    return TeamChannels(root, owner["userId"], owner["name"])


async def subscribe(socket, service, actor, channel_id, *, session_valid, interval=1.0):
    try:
        cursor = await asyncio.to_thread(service.subscribe, actor, channel_id)
    except (Missing, Refused):
        await socket.send_json(F.make_app_frame(F.ERROR, code="channel_access_lost", channelId=channel_id))
        return None
    except (Damaged, OSError):
        await socket.send_json(F.make_app_frame(F.ERROR, code="unavailable", channelId=channel_id))
        return None
    if not session_valid():
        return None
    if not service.can_read(actor, channel_id):
        await socket.send_json(F.make_app_frame(F.ERROR, code="channel_access_lost", channelId=channel_id))
        return None
    await socket.send_json(F.make_app_frame(F.SUBSCRIBED, channelId=channel_id))
    return asyncio.create_task(watch(socket, service, actor, channel_id, cursor,
                                     session_valid=session_valid, interval=interval))


async def watch(socket, service, actor, channel_id, after, *, session_valid, interval=1.0):
    """Follow committed messages. Recheck channel and session access before each push."""
    try:
        while True:
            batch = await asyncio.to_thread(service.messages, actor, channel_id, after=after)
            for message in batch:
                if not session_valid():
                    return
                if not service.can_read(actor, channel_id):
                    raise Missing("没有这个频道")
                event = F.channel_event(channel_id, "message.created", messageId=message["id"],
                                        message=message, authorUserId=message["author"]["id"],
                                        threadId=message["threadId"])
                await socket.send_json(F.make_app_frame(F.EVENT, channelId=channel_id, event=event))
                after = message["sequence"]
            await asyncio.sleep(interval)
    except (Missing, Refused):
        # No private data follows a lost subscription. The next REST read rechecks access too.
        if session_valid():
            with suppress(WebSocketDisconnect):
                await socket.send_json(F.make_app_frame(F.ERROR, code="channel_access_lost", channelId=channel_id))
    except (Damaged, OSError):
        if session_valid():
            with suppress(WebSocketDisconnect):
                await socket.send_json(F.make_app_frame(F.ERROR, code="unavailable", channelId=channel_id))
    except WebSocketDisconnect:
        return
