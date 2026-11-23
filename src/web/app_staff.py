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


def _same_origin(connection) -> bool:
    """请求是不是从我们自己的页面发出的。浏览器给跨站请求和所有长连接握手都带 Origin，页面脚本改不了它：
    带了就必须与本服务的地址一致。没带 Origin 的只放行普通读取（同源 GET 浏览器可以不带；跨站读取拿不到
    应答），写入和长连接握手没带一律不算。"""
    from urllib.parse import urlsplit
    origin = connection.headers.get("origin", "")
    if not origin:
        return connection.scope.get("type") == "http" and connection.scope.get("method") == "GET"
    served = {connection.headers.get("host", "").lower(), connection.headers.get("x-forwarded-host", "").lower()}
    return urlsplit(origin).netloc.lower() in served - {""}


def owner_proof(connection) -> bool:
    """读写专员对话要有主人证明：显式的主人令牌（Bearer 头），或登录 Cookie 加同源。

    2026-11-16 主人定：新版界面只有登录 Cookie（它本身就是出示主人令牌换来的），专员也都是主人自己的，
    所以这组界面接口认 Cookie；Bearer 原本多挡的「别的网站借用登录状态」改由来源检查挡。
    routers/staff.py 的旧接口仍只认 Bearer（test_staff_conversations.py 钉住），这里不动它。
    没配置主人令牌时一律不通过。connection 可以是 HTTP 请求或 WebSocket 握手。

    「持有登录 Cookie 就是主人」只在家里只有主人一个人时成立。接入成员（HOME_AND_MEMBERS_DESIGN 7.4、8）
    之后登录要分清是谁，这里得改成「是主人，或在这位专员的受众里」，回合权限按发起人封顶——不能沿用本判断。"""
    from src.web.auth import SESSION_COOKIE, ct_eq, get_api_token
    token = get_api_token()
    if not token:
        return False
    authorization = connection.headers.get("authorization", "")
    if authorization.startswith("Bearer ") and ct_eq(authorization[7:].strip(), token):
        return True
    return ct_eq(connection.cookies.get(SESSION_COOKIE, "").strip(), token) and _same_origin(connection)


def _conversations(root: str):
    from src.workflow.staff.conversations import StaffConversations
    return StaffConversations(root)


def send(root: str, member_id: str, body: str, client_id: Optional[str]) -> Dict[str, Any]:
    """调用方已核对过主人证明。岗位、实例或额度不符时 StaffConversations 抛 ValueError，原样交给路由。"""
    return views.member_owner_message(root, member_id, send_task(root, member_id, body), client_id)


def send_task(root: str, member_id: str, body: str, via: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """所有者交给专员一件事，返回任务。via：经哪个 AI 应用发来（/mcp 认出所有者的连接时）。岗位、实例或额度不符时 StaffConversations 抛 ValueError，原样交给路由。"""
    from src.workflow.staff.owner_confirmation import owner_route
    conversations = _conversations(root)
    current = conversations.read(member_id, limit=1)
    if not current["can_dispatch"]:
        raise ValueError(current["unavailable_reason"] or "这位专员现在不能接活")
    with owner_route(root):
        result = conversations.submit(member_id, {
            "request_id": uuid.uuid4().hex, "prompt": body, "acceptance": [], "delivery": "notify",
            "member_revision": current["member"]["revision"], "role_revision": current["role"]["revision"],
            "instance": current["instance"], "identity_digest": current["identity_digest"]}, via=via)
    return result["task"]


def stop(root: str, task_id: str) -> bool:
    """「停止」一件进行中的事；任务不属于任何专员或已不在运行时返回 False。"""
    from src.workflow.staff.owner_confirmation import owner_route
    from src.workflow.tasks.tasks import TaskLedger
    task = TaskLedger(root).load(task_id)
    if ((task.collaboration if task else {}) or {}).get("dispatch", {}).get("channel"):
        with owner_route(root):                         # 频道里的事不在私聊列表里，按任务本身核对后停止
            return _conversations(root).stop_channel_task(task_id)
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


def stop_routine(root: str, routine_id: str) -> bool:
    """调用方已核对所有者证明，停止动作交给原专员执行池。"""
    from src.workflow.staff.owner_confirmation import owner_route

    with owner_route(root):
        return _conversations(root).stop_routine(routine_id)


async def watch(socket, root: str, member_id: str, *, interval: float = 1.0,
                viewer: Optional[str] = None) -> None:
    """订阅了某位专员私聊的界面连接：定时看台账，把新出现的消息和运行状态的变化推过去。

    专员的任务没有会话事件流可附着，所以这里是轮询；连接断开或取消订阅时由调用方取消本任务。"""
    channel = views.dm_channel_id(views.member_agent_id(root, member_id))
    agent = views.member_agent_id(root, member_id)
    seen: set = set()
    active: Dict[str, Dict[str, Any]] = {}
    while True:
        conversation = await asyncio.to_thread(views.member_conversation, root, member_id, viewer)
        messages = views.member_messages(root, member_id, conversation, viewer=viewer)
        runs = {run["runId"]: run for run in views.member_runs(root, member_id, conversation, viewer=viewer)}
        # 首次也发快照：订阅确认与历史读取之间刚完成的回复不能漏掉；客户端按消息 id 合并。
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
        seen.intersection_update(message["id"] for message in messages)
        await asyncio.sleep(interval)
