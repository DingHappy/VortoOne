"""成员的界面实时连接：仅订阅已加入的团队频道和本人发起的专员对话。

不接主人的主会话桥，不接全空间广播。会话和办事级别在每轮以及每次推送前重读，撤销立即收口。
"""
from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Optional, Tuple

from fastapi import WebSocketDisconnect

from src.agents.principal import MEMBER, Principal
from src.gateway import app_protocol as F
from src.web import app_channels, app_members, app_presence, app_staff, app_views as views, member_auth
from src.workflow.members.channels import is_team_channel


def identity(socket, root: str) -> Optional[Tuple[str, str]]:
    if not member_auth.same_origin(socket):
        return None
    try:
        found = member_auth.account(socket)
        member = member_auth.member_id(found)
    except (ValueError, OSError):
        return None
    return (member, str(found["id"])) if member and found else None


async def serve(socket, root: str, bound: Tuple[str, str], *, interval: float = 1.0) -> None:
    actor = Principal(MEMBER, bound[0], root)

    def session_valid():
        return identity(socket, root) == bound

    def permitted(channel: str) -> Optional[str]:
        if not session_valid():
            return None
        try:
            staff = views.member_for_channel(root, channel)
            if staff and app_members.member_staff(root, actor, views.member_agent_id(root, staff)) == staff:
                return staff
        except (ValueError, OSError):
            pass
        return None

    class PrivateSocket:
        async def send_json(self, frame):
            # to_thread 读取后再查一次，不能把读盘期间已撤销的内容推给旧订阅。
            if not permitted(frame["channelId"]):
                await socket.close(code=1008)
                raise WebSocketDisconnect(code=1008)
            await socket.send_json(F.make_app_frame(F.EVENT, channelId=frame["channelId"], event=frame["event"]))

    await socket.accept()
    watches = {}
    pending_receive: Optional[asyncio.Task[str]] = None
    presence = app_presence.connected(root, actor.id)
    try:
        await socket.send_json(F.make_app_frame(F.READY, orgId=views.home_id(root), userId=bound[0]))
        while True:
            if not session_valid() or any(not permitted(channel) for channel in watches if not is_team_channel(channel)):
                await socket.close(code=1008)
                return
            if pending_receive is None:
                pending_receive = asyncio.create_task(socket.receive_text())
            # A permission tick must not cancel a receive that already claimed an incoming frame.
            ready, _ = await asyncio.wait((pending_receive,), timeout=interval)
            if not ready:
                continue
            received = pending_receive
            pending_receive = None
            raw = received.result()
            if not session_valid():
                await socket.close(code=1008)
                return
            try:
                frame = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(frame, dict):
                continue
            kind, channel = frame.get("type"), str(frame.get("channelId") or "")
            if kind == "ping":
                await socket.send_json(F.make_app_frame(F.PONG))
            elif kind == "subscribe":
                if is_team_channel(channel):
                    prior = watches.get(channel)
                    if prior is not None and not prior.done():
                        await socket.send_json(F.make_app_frame(F.SUBSCRIBED, channelId=channel))
                    else:
                        subscription = await app_channels.subscribe(socket, app_channels.channels(root), actor, channel,
                                                                    session_valid=session_valid, interval=interval)
                        if subscription is not None:
                            watches[channel] = subscription
                    continue
                staff = permitted(channel)
                if not staff:
                    await socket.send_json(F.make_app_frame(F.ERROR, code="not_found", channelId=channel))
                    continue
                await socket.send_json(F.make_app_frame(F.SUBSCRIBED, channelId=channel))
                if channel not in watches:
                    watches[channel] = asyncio.create_task(app_staff.watch(
                        PrivateSocket(), root, staff, interval=interval, viewer=bound[0]))
            elif kind == "unsubscribe":
                task = watches.pop(channel, None)
                if task:
                    task.cancel()
                    with suppress(asyncio.CancelledError, WebSocketDisconnect):
                        await task
                await socket.send_json(F.make_app_frame(F.UNSUBSCRIBED, channelId=channel))
    except WebSocketDisconnect:
        pass
    finally:
        app_presence.disconnected(presence)
        pending = list(watches.values())
        if pending_receive is not None:
            pending.append(pending_receive)
        for task in pending:
            task.cancel()
        for task in pending:
            with suppress(asyncio.CancelledError, WebSocketDisconnect):
                await task
