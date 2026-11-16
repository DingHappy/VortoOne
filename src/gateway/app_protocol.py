"""新版 Desktop 界面那条连接（/api/ws）的出站帧。

它和 /ws 的会话协议（protocol.py）是两条不同的线：这里每个空间一条连接、按对话订阅，
帧的名字和字段按界面的客户端合同（frontend/packages/client/src/realtime/connection.ts）。
规矩与 protocol.make_event 相同：出站帧只能经 make_app_frame 构造，类型和字段先在 OUTBOUND 登记，
缺必填或出现未登记字段即抛错。
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from src.gateway.protocol import ProtocolError

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
