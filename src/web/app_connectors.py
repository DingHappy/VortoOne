"""新版界面「连接器」的视图：只投影登记，key 不在登记里，也从不回给界面。"""
from __future__ import annotations

from typing import Any, Dict

from src.web.app_views import _iso

UNSUPPORTED = "还没接官方连接器目录和登录授权；请用「自定义连接器」填它的 MCP 地址和 key"


def _tool(tool: Dict[str, Any]) -> Dict[str, Any]:
    reads = tool.get("readOnlyHint") is True
    suggested, access = "read" if reads else "approval", tool.get("access")
    return {"name": tool["name"], "description": tool["description"], "readOnlyHint": tool.get("readOnlyHint"),
            "access": access, "suggestedAccess": suggested, "level": access or suggested,
            "suggestionSource": "annotation" if reads else None,
            # 对方没标只读的只有「每次问」或「关」：界面据此不给只读和免确认写两档
            "sensitive": not reads, "pending": access is None, "off": access == "off",
            "label": tool.get("label") or "", "firstSeenAt": _iso(tool.get("firstSeenAt"))}


def server(item: Dict[str, Any]) -> Dict[str, Any]:
    from src.workflow.workbench.custom_connectors import has_key
    return {"id": item["id"], "serverUrl": item["serverUrl"], "label": item["label"], "accountLabel": None,
            "authType": "api_key", "iconUrl": None, "status": "active" if has_key(item["id"]) else "needs_reconnect",
            "toolsError": item.get("toolsError"), "tools": [_tool(tool) for tool in item["tools"]],
            "reviewedAt": _iso(item["reviewedAt"]) if item.get("reviewedAt") else None,
            "createdAt": _iso(item["createdAt"])}


def connection(item: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": item["id"], "connector": "custom", "label": item["label"], "serverUrl": item["serverUrl"],
            "authType": "api_key", "accountLabel": None, "tokenExpiresAt": None,
            "createdAt": _iso(item["createdAt"]), "revokedAt": None}
