"""Team channel view assembly and authenticated realtime delivery."""
from __future__ import annotations

import asyncio
from contextlib import suppress

from fastapi import WebSocketDisconnect

from src.gateway import app_protocol as F
from src.web import app_views as views
from src.workflow.members.channels import TeamChannels, is_team_channel
from src.workflow.members.registry import Damaged, Missing, Refused


def channels(root: str) -> TeamChannels:
    owner = views.person(root)
    return TeamChannels(root, owner["userId"], owner["name"])


def streaming_service(root: str, channel_id: str):
    from src.web.app_issue_chat import IssueChat, is_channel
    return IssueChat(root) if is_channel(channel_id) else channels(root)


def is_streamed_channel(channel_id: str) -> bool:
    from src.web.app_issue_chat import is_channel
    return is_team_channel(channel_id) or is_channel(channel_id)


def channel_agents(root: str) -> dict:
    """团队里的 agent（专员 id → 界面资料），归档、删掉的和主助理不在里面。

    joinable：能不能放进频道。第一期只要自有框架、不连登记项目的（和成员能用的 agent 同一条判据）：
    本机 CLI 没有确认门，开发 agent 要在项目副本里跑。主助理连着主要聊天，不进频道。"""
    return {row["handle"]: {"id": row["id"], "name": row["name"], "handle": row["handle"],
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
    if not session_valid():
        return None
    if not await asyncio.to_thread(service.can_read, actor, channel_id):
        await socket.send_json(F.make_app_frame(F.ERROR, code="channel_access_lost", channelId=channel_id))
        return None
    if not session_valid():
        return None
    await socket.send_json(F.make_app_frame(F.SUBSCRIBED, channelId=channel_id))
    return asyncio.create_task(watch(socket, service, actor, channel_id, cursor, reaction_cursor, thread_cursor,
                                     session_valid=session_valid, interval=interval))


async def watch(socket, service, actor, channel_id, after, reaction_after, thread_after, *, session_valid, interval=1.0):
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

    try:
        while session_valid():
            batch = await asyncio.to_thread(service.messages, actor, channel_id, after=after)
            for message in batch:
                if not await send("message.created", messageId=message["id"], message=message,
                                  authorUserId=message["author"]["id"], threadId=message["threadId"]):
                    return
                after = message["sequence"]
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
