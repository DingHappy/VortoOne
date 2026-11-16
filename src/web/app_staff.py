"""新版界面里与专员的私聊：写入一侧。

主人发一条 = 交给这位专员一件事，走现有的 StaffConversations.submit（同一份审计、同一套岗位与额度核对）。
交付方式固定为「只告知结果」、不带验收标准：这是聊天框的含义；要由主人验收的派活仍走原来的表单。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, Optional

from src.gateway import app_protocol as F
from src.web import app_views as views


def owner_proof(connection) -> bool:
    """与 routers/staff.py 的专员对话路由同一条规矩：读写专员对话要显式携带主人令牌（Bearer 头）。

    登录 Cookie 不算——那条规矩由 test_staff_conversations.py 钉住，这里不另开一条更松的路。
    没配置主人令牌时一律不通过。connection 可以是 HTTP 请求或 WebSocket 握手。"""
    from src.web.auth import ct_eq, get_api_token
    token = get_api_token()
    authorization = connection.headers.get("authorization", "")
    return bool(token) and authorization.startswith("Bearer ") and ct_eq(authorization[7:].strip(), token)


def _conversations(root: str):
    from src.workflow.staff.conversations import StaffConversations
    return StaffConversations(root)


def send(root: str, member_id: str, body: str, client_id: Optional[str]) -> Dict[str, Any]:
    """调用方已核对过主人证明。岗位、实例或额度不符时 StaffConversations 抛 ValueError，原样交给路由。"""
    from src.workflow.staff.owner_confirmation import owner_route
    conversations = _conversations(root)
    current = conversations.read(member_id, limit=1)
    if not current["can_dispatch"]:
        raise ValueError(current["unavailable_reason"] or "这位专员现在不能接活")
    with owner_route(root):
        result = conversations.submit(member_id, {
            "request_id": uuid.uuid4().hex, "prompt": body, "acceptance": [], "delivery": "notify",
            "member_revision": current["member"]["revision"], "role_revision": current["role"]["revision"],
            "instance": current["instance"], "identity_digest": current["identity_digest"]})
    return views.member_owner_message(root, member_id, result["task"], client_id)


def stop(root: str, task_id: str) -> bool:
    """「停止」一件进行中的事；任务不属于任何专员或已不在运行时返回 False。"""
    from src.workflow.staff.owner_confirmation import owner_route
    from src.workflow.tasks.tasks import TaskLedger
    task = TaskLedger(root).load(task_id)
    member_id = str(((task.collaboration if task else {}) or {}).get("dispatch", {}).get("member") or "")
    if not member_id:
        return False
    conversations = _conversations(root)
    shown = next((t for t in conversations.read(member_id, limit=50)["tasks"] if t["id"] == task_id), None)
    if shown is None or shown.get("status") not in views.ACTIVE_TASK:
        return False
    with owner_route(root):
        conversations.act(member_id, task_id, {"verdict": "stop", "note": "主人在对话里停止",
                                               **shown["confirmation"]})
    return True


async def watch(socket, root: str, member_id: str, *, interval: float = 2.0) -> None:
    """订阅了某位专员私聊的界面连接：定时看台账，把新出现的消息和运行状态的变化推过去。

    专员的任务没有会话事件流可附着，所以这里是轮询；连接断开或取消订阅时由调用方取消本任务。"""
    channel = views.dm_channel_id(views.member_agent_id(root, member_id))
    agent = views.member_agent_id(root, member_id)
    seen: Optional[set] = None
    active: Dict[str, Dict[str, Any]] = {}
    while True:
        conversation = await asyncio.to_thread(views.member_conversation, root, member_id)
        messages = views.member_messages(root, member_id, conversation)
        runs = {run["runId"]: run for run in views.member_runs(root, member_id, conversation)}
        if seen is None:
            seen = {message["id"] for message in messages}
        for message in messages:
            if message["id"] in seen:
                continue
            seen.add(message["id"])
            await socket.send_json(F.make_app_frame(F.EVENT, channelId=channel, event=F.channel_event(
                channel, "message.created", messageId=message["id"], message=message,
                agentRunId=message.get("agentRunId"))))
        for run_id, run in runs.items():
            if active.get(run_id, {}).get("status") != run["status"]:
                await socket.send_json(F.make_app_frame(F.EVENT, channelId=channel, event=F.channel_event(
                    channel, "agent.run", threadId=run["threadId"], runId=run_id, agentId=agent,
                    status=run["status"], triggerMessageId=run["triggerMessageId"], startedAt=run["startedAt"])))
        for run_id, run in active.items():
            if run_id not in runs:
                task = next((t for t in conversation["tasks"] if t["id"] == run_id), {})
                status = "done" if task.get("status") == "done" else "failed"
                await socket.send_json(F.make_app_frame(F.EVENT, channelId=channel, event=F.channel_event(
                    channel, "run.finished", threadId=run["threadId"], runId=run_id, agentId=agent,
                    status=status)))
        active = runs
        await asyncio.sleep(interval)
