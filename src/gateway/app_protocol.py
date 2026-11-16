"""新版 Desktop 界面那条连接（/api/ws）的出站帧。

它和 /ws 的会话协议（protocol.py）是两条不同的线：这里每个空间一条连接、按对话订阅，
帧的名字和字段按界面的客户端合同（frontend/packages/client/src/realtime/connection.ts）。
规矩与 protocol.make_event 相同：出站帧只能经 make_app_frame 构造，类型和字段先在 OUTBOUND 登记，
缺必填或出现未登记字段即抛错。
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from src.gateway.protocol import ProtocolError

EVENT = "event"
READY = "ready"
PONG = "pong"
SUBSCRIBED = "subscribed"
UNSUBSCRIBED = "unsubscribed"
ERROR = "error"

# 类型 → (必填字段, 可选字段)
OUTBOUND: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    READY: (("orgId", "userId"), ()),
    PONG: ((), ()),
    SUBSCRIBED: (("channelId",), ()),
    UNSUBSCRIBED: (("channelId",), ()),
    ERROR: (("code",), ("channelId", "agentId")),
    EVENT: (("channelId", "event"), ()),
}

# 对话里的事件：类型 → (必填字段, 可选字段)。channelId 由 make_channel_event 统一带上。
_RUN = ("threadId", "runId", "agentId")
EVENTS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    "message.created": (("messageId", "message"), ("threadId", "agentRunId", "authorUserId")),
    "agent.run": ((*_RUN, "status"), ("error", "triggerMessageId", "startedAt")),
    "agent.typing": (_RUN, ()),
    "agent.tool": ((*_RUN, "tool"), ("toolUseId", "detail", "failed")),
    "run.finished": ((*_RUN, "status"), ("error",)),
    "approval.created": (("approval",), ()),
    "approval.decided": (("approval",), ()),
}


def make_app_frame(kind: str, **fields: Any) -> Dict[str, Any]:
    spec = OUTBOUND.get(kind)
    if spec is None:
        raise ProtocolError(f"未登记的界面连接帧类型: {kind!r}")
    required, optional = spec
    clean = {key: value for key, value in fields.items() if value is not None}
    missing = [key for key in required if key not in clean]
    if missing:
        raise ProtocolError(f"界面连接帧 {kind} 缺必填字段: {missing}")
    unknown = [key for key in clean if key not in set(required) | set(optional)]
    if unknown:
        raise ProtocolError(f"界面连接帧 {kind} 有未登记字段: {unknown}（先在 app_protocol.OUTBOUND 登记）")
    return {"type": kind, **clean}


def channel_event(channel_id: str, kind: str, **fields: Any) -> Dict[str, Any]:
    """一条校验过的对话事件，交给 make_app_frame(EVENT, …) 发出。
    tool=None 表示工具调用结束，是合同的一部分，所以保留。"""
    spec = EVENTS.get(kind)
    if spec is None:
        raise ProtocolError(f"未登记的对话事件类型: {kind!r}")
    required, optional = spec
    clean = {key: value for key, value in fields.items() if value is not None or key == "tool"}
    missing = [key for key in required if key not in clean]
    if missing:
        raise ProtocolError(f"对话事件 {kind} 缺必填字段: {missing}")
    unknown = [key for key in clean if key not in set(required) | set(optional)]
    if unknown:
        raise ProtocolError(f"对话事件 {kind} 有未登记字段: {unknown}（先在 app_protocol.EVENTS 登记）")
    return {"type": kind, "channelId": channel_id, **clean}
