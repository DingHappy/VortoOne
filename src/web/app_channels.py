"""Team channel view assembly and authenticated realtime delivery."""
from __future__ import annotations

import asyncio
from contextlib import suppress

from fastapi import WebSocketDisconnect

from src.gateway import app_protocol as F
from src.web import app_views as views
from src.workflow.members.channels import TeamChannels, is_team_channel
from src.workflow.members.direct_messages import DirectMessages, is_person_dm
from src.workflow.members.registry import Damaged, Missing, Refused


def channels(root: str) -> TeamChannels:
    owner = views.person(root)
    return TeamChannels(root, owner["userId"], owner["name"])


def person_dms(root: str) -> DirectMessages:
    return DirectMessages(root, views.person(root))


def streaming_service(root: str, channel_id: str):
    from src.web.app_issue_chat import IssueChat, is_channel
    if is_person_dm(channel_id):
        return person_dms(root)
    return IssueChat(root) if is_channel(channel_id) else channels(root)


def is_streamed_channel(channel_id: str) -> bool:
    from src.web.app_issue_chat import is_channel
    return is_team_channel(channel_id) or is_person_dm(channel_id) or is_channel(channel_id)


def channel_agents(root: str) -> dict:
    """团队里的 agent（专员 id → 界面资料），归档、删掉的和主助理不在里面。

    joinable：能不能放进频道。第一期只要自有框架、不连登记项目的（和成员能用的 agent 同一条判据）：
    本机 CLI 没有确认门，开发 agent 要在项目副本里跑。主助理连着主要聊天，不进频道。"""
    return {row["handle"]: {"id": row["id"], "name": row["name"], "handle": row["handle"],
                            "duties": row.get("instructions") or row.get("role") or "",
                            "joinable": bool(row.get("supportsMemberPolicy")) and bool(row.get("canTalk"))}
            for row in views.agents(root) if row["id"] != views.main_agent_id(root)}


async def subscribe(socket, service, actor, channel_id, *, session_valid, interval=1.0):
    try:
        cursor, reaction_cursor, thread_cursor = await asyncio.to_thread(service.subscribe, actor, channel_id)
    except (Missing, Refused):
        await socket.send_json(F.make_app_frame(F.ERROR, code="channel_access_lost", channelId=channel_id))
        return None
    except (Damaged, OSError):
        await socket.send_json(F.make_app_frame(F.ERROR, code="unavailable", channelId=channel_id))
        return None
    if not await _acknowledge_subscription(socket, service, actor, channel_id, session_valid):
        return None
    return asyncio.create_task(watch(socket, service, actor, channel_id, cursor, reaction_cursor, thread_cursor,
                                     session_valid=session_valid, interval=interval, read_receipts=is_person_dm(channel_id)))


async def _acknowledge_subscription(socket, service, actor, channel_id, session_valid):
    if not session_valid():
        return False
    try:
        readable = await asyncio.to_thread(service.can_read, actor, channel_id)
    except (Missing, Refused):
        readable = False
    except (Damaged, OSError):
        if session_valid():
            await socket.send_json(F.make_app_frame(F.ERROR, code="unavailable", channelId=channel_id))
        return False
    if not session_valid():
        return False
    if readable:
        await socket.send_json(F.make_app_frame(F.SUBSCRIBED, channelId=channel_id))
    else:
        await socket.send_json(F.make_app_frame(F.ERROR, code="channel_access_lost", channelId=channel_id))
    return readable


async def subscribe_channel(socket, root, actor, channel_id, watches, *, session_valid, interval=1.0):
    service = streaming_service(root, channel_id)
    prior = watches.get(channel_id)
    if prior is not None and not prior.done():
        if not await _acknowledge_subscription(socket, service, actor, channel_id, session_valid):
            prior.cancel()
            watches.pop(channel_id)
        return
    subscription = await subscribe(socket, service, actor, channel_id, session_valid=session_valid, interval=interval)
    if subscription is not None:
        watches[channel_id] = subscription


async def watch(socket, service, actor, channel_id, after, reaction_after, thread_after, *, session_valid, interval=1.0, read_receipts=False):
    """Follow committed messages, reactions, and thread changes. Recheck access before each push."""
    async def send(kind, **fields):
        if not session_valid():
            return False
        if not await asyncio.to_thread(service.can_read, actor, channel_id):
            raise Missing("没有这个频道")
        if not session_valid():
            return False
        event = F.channel_event(channel_id, kind, **fields)
        await socket.send_json(F.make_app_frame(F.EVENT, channelId=channel_id, event=event))
        return True

    last_read = ""
    try:
        while session_valid():
            batch = await asyncio.to_thread(service.messages, actor, channel_id, after=after)
            for message in batch:
                if not await send("message.created", messageId=message["id"], message=message,
                                  authorUserId=message["author"]["id"], threadId=message["threadId"]):
                    return
                after = message["sequence"]
            if read_receipts:
                receipts = await asyncio.to_thread(service.reads, actor, channel_id)
                for receipt in receipts:
                    if receipt["lastReadAt"] > last_read:
                        if not await send("channel.read", **receipt):
                            return
                        last_read = receipt["lastReadAt"]
                await asyncio.sleep(interval)
                continue
            changes = await asyncio.to_thread(service.reaction_changes, actor, channel_id, reaction_after)
            for sequence, message_id, reactions in changes:
                if not await send("message.reactions", messageId=message_id, reactions=reactions):
                    return
                reaction_after = sequence
            changes = await asyncio.to_thread(service.thread_changes, actor, channel_id, thread_after)
            for sequence, thread_id, change in changes:
                if not await send("thread.updated", threadId=thread_id, change=change):
                    return
                thread_after = sequence
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
