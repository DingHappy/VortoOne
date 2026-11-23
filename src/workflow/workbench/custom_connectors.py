"""所有者在新版界面里接的自定义连接器：HTTPS 的 MCP 服务 + 一把 key。

- 登记在 ``.vortoone/custom_connectors.json``（0600），只有凭据名字；key 在
  ``~/.vortoone/connectors/custom-<id>.token``（0600），和其它连接器同一个位置、同一套读取规则。
- 只有所有者分类并保存过的工具会给会话（``exposed``）。对方后来改了说明、参数或只读标记的，回到待审、不再给。
- 「只读」只接受对方也标了 ``readOnlyHint: true`` 的（同现有只读连接器的规矩）；其余要么每次问、要么关。
- 不留长连接：读目录、每次调用都现连现断。调用前后各核对一次登记，断开和改分类立刻生效。
- 不做 OAuth：没给 key 就如实说需要 key。对方返回的内容里出现这把 key 会被抹掉，错误只给固定的说明。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.utils.async_ops import timeout_scope
from src.utils.state_dir import state_dir

logger = logging.getLogger(__name__)

_FILE = "custom_connectors.json"
_LOCK = threading.RLock()
_ID = re.compile(r"mcp-[0-9a-f]{12}")
MAX_SERVERS, MAX_TOOLS, MAX_EXPOSED = 16, 128, 64      # 模型一次能带的工具有限：开着的合计不超过 MAX_EXPOSED
MAX_LABEL, MAX_KEY, MAX_DESCRIPTION, MAX_SCHEMA = 80, 2048, 4000, 16_000
LIST_TIMEOUT, CALL_TIMEOUT = 20, 60
ACCESS = frozenset({"read", "write", "approval", "off"})


class ConnectorError(ValueError):
    """界面上要说明的拒绝：code 给界面分辨，消息给人看，不含 key，也不含对方返回的原文。"""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _missing() -> ConnectorError:
    return ConnectorError("not_found", "没有这个连接器", 404)


def _read(root: str) -> Dict[str, Any]:
    try:
        data = json.loads((state_dir(root) / _FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"servers": []}
    except (OSError, ValueError) as error:
        raise ConnectorError("connectors.unreadable", "连接器记录读不出来，没有改动", 409) from error
    if (not isinstance(data, dict) or not isinstance(data.get("servers"), list)
            or any(not isinstance(x, dict) or not _ID.fullmatch(str(x.get("id"))) for x in data["servers"])):
        raise ConnectorError("connectors.unreadable", "连接器记录格式不对，没有改动", 409)
    return data


def _write(root: str, data: Dict[str, Any]) -> None:
    target = state_dir(root) / _FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def _find(data: Dict[str, Any], server_id: str) -> Dict[str, Any]:
    for item in data["servers"]:
        if item["id"] == server_id:
            return item
    raise _missing()


def servers(root: str) -> List[Dict[str, Any]]:
    with _LOCK:
        return _read(root)["servers"]


def get(root: str, server_id: str) -> Dict[str, Any]:
    with _LOCK:
        return _find(_read(root), server_id)


# ── 校验 ─────────────────────────────────────────────────────────────────────

def check_url(value: Any) -> str:
    from src.agents.mcp_tools import _credential_free_http_url
    url = value.strip() if isinstance(value, str) else ""
    if len(url) > 500 or not url.lower().startswith("https://") or not _credential_free_http_url(url):
        raise ConnectorError("invalid", "地址要是 https:// 开头的 MCP 服务，里面不能带账号、key 或令牌")
    return url


def _check_key(value: Any) -> str:
    key = value.strip() if isinstance(value, str) else ""
    if not 8 <= len(key) <= MAX_KEY or any(not 33 <= ord(ch) <= 126 for ch in key):
        raise ConnectorError("invalid", f"key 要是 8～{MAX_KEY} 个可打印字符，不能有空格或换行")
    return key


def _check_label(value: Any, what: str = "名字") -> str:
    label = " ".join(value.split()) if isinstance(value, str) else ""
    if not label or len(label) > MAX_LABEL:
        raise ConnectorError("invalid", f"{what}要填，且不超过 {MAX_LABEL} 个字")
    return label


def _credential(server_id: str) -> str:
    """凭据名只由 id 算出来，不从登记文件里读：改登记也指不到别的连接器的令牌。"""
    if not _ID.fullmatch(server_id):
        raise _missing()
    return "custom-" + server_id[4:]


# ── 和对方说话 ───────────────────────────────────────────────────────────────

def _client(url: str, key: str, timeout: float):
    from src.tools.mcp_http import StreamableHTTPMCPClient
    return StreamableHTTPMCPClient("custom", url, headers={"Authorization": f"Bearer {key}"}, timeout=timeout,
                                   protocol_version="2025-06-18")


def redact(value: Any, key: str) -> Any:
    if isinstance(value, str):
        return value.replace(key, "[已隐去密钥]")
    if isinstance(value, list):
        return [redact(item, key) for item in value]
    if isinstance(value, dict):
        return {redact(name, key): redact(item, key) for name, item in value.items()}
    return value


def _failure(error: BaseException) -> ConnectorError:
    text = str(error)
    if "MCP HTTP 401" in text or "MCP HTTP 403" in text:
        return ConnectorError("connector.key_refused", "对方没接受这把 key")
    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return ConnectorError("connector.unreachable", "对方太久没回应")
    found = re.search(r"MCP HTTP (\d{3})", text)
    detail = f"对方回了 {found.group(1)}" if found else "连不上，或者它不是 Streamable HTTP 的 MCP 服务"
    return ConnectorError("connector.unreachable", f"读不到这个服务：{detail}")


def _digest(tool: Dict[str, Any]) -> str:
    shown = [tool["name"], tool["description"], tool["schema"], tool["readOnlyHint"]]
    return hashlib.sha256(json.dumps(shown, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _catalog(client: Any, listed: List[Any], key: str) -> Tuple[List[Dict[str, Any]], str]:
    """对方的工具目录里能用的那些：（工具, 略过了多少的说明）。资源和提示那几个虚拟工具不算。"""
    rows, skipped = [], 0
    for item in listed:
        if item.name in getattr(client, "_virtual_methods", {}):
            continue
        schema = redact(item.input_schema, key)
        if (not 1 <= len(item.name) <= 128 or not item.name.isprintable() or key in item.name
                or len(json.dumps(schema)) > MAX_SCHEMA or len(rows) >= MAX_TOOLS):
            skipped += 1
            continue
        hint = (item.annotations or {}).get("readOnlyHint")
        row = {"name": item.name, "description": redact(str(item.description or ""), key)[:MAX_DESCRIPTION],
               "schema": schema, "readOnlyHint": hint if isinstance(hint, bool) else None}
        rows.append({**row, "digest": _digest(row)})
    return rows, f"有 {skipped} 个工具没有列出（名字或参数不合要求，或数量超限）" if skipped else ""


async def _discover(url: str, key: str) -> Tuple[List[Dict[str, Any]], str]:
    client = _client(url, key, LIST_TIMEOUT)
    try:
        async with timeout_scope(LIST_TIMEOUT):
            await client.connect()
            listed = await client.list_tools()
        return _catalog(client, listed, key)
    except Exception as error:  # noqa: BLE001 —— 对方的原话可能带着 key，只给固定说明
        raise _failure(error) from None
    finally:
        await client.disconnect()


def _merge(known: List[Dict[str, Any]], found: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """新目录并进登记：一模一样的沿用决定，新出现或变了的回到待审；关掉的一直关着。"""
    before, now, rows = {tool["name"]: tool for tool in known}, _now(), []
    for tool in found:
        old = before.get(tool["name"]) or {}
        keep = old.get("digest") == tool["digest"] or old.get("access") == "off"
        rows.append({**tool, "access": old.get("access") if keep else None, "label": old.get("label") or "",
                     "firstSeenAt": old.get("firstSeenAt") or now})
    return rows


# ── 改动 ─────────────────────────────────────────────────────────────────────

def _save_key(credential: str, key: str) -> None:
    from src.workflow.workbench.connectors import _connectors_dir, _token_path
    directory = _connectors_dir()
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    tmp = directory / f".{credential}.{uuid.uuid4().hex}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(key + "\n")
        os.replace(tmp, _token_path(credential))
    finally:
        tmp.unlink(missing_ok=True)


def _load_key(server_id: str) -> str:
    from src.agents.mcp_tools import load_connector_secret
    try:
        return load_connector_secret(_credential(server_id))
    except (OSError, ValueError):
        raise ConnectorError("connector.needs_reconnect", "这个连接器的 key 读不到，请重新连接", 409) from None


def has_key(server_id: str) -> bool:
    try:
        _load_key(server_id)
    except ConnectorError:
        return False
    return True


def _unique(data: Dict[str, Any], label: str) -> str:
    taken, candidate, number = {x["label"] for x in data["servers"]}, label, 1
    while candidate in taken:
        number += 1
        candidate = f"{label[:MAX_LABEL - 4]} {number}"
    return candidate


async def connect(root: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """接一个新的，或带 connectionId 给原来那个换 key。能读到工具目录才存。"""
    if not isinstance(body, dict) or set(body) - {"serverUrl", "apiKey", "label", "returnTo", "connectionId"}:
        raise ConnectorError("invalid", "只能填地址、key 和名字")
    url = check_url(body.get("serverUrl"))
    if not body.get("apiKey"):
        return {"status": "needs_key"}              # 不做 OAuth：如实说需要 key，不假装在走登录
    key, existing = _check_key(body["apiKey"]), body.get("connectionId")
    label = _check_label(body["label"]) if body.get("label") else ""
    if existing is not None:
        if get(root, str(existing))["serverUrl"].rstrip("/") != url.rstrip("/"):
            raise ConnectorError("connector.other_server", "重新连接只能换 key，不能换地址；换地址请新接一个", 409)
    found, note = await _discover(url, key)
    if not found:
        raise ConnectorError("connector.no_tools", "连上了，但它没有提供能用的工具")
    with _LOCK:
        data = _read(root)
        if existing is not None:
            item = _find(data, str(existing))
            item.update(tools=_merge(item["tools"], found), toolsError=note or None)
            outcome = "reconnected"
        else:
            if len(data["servers"]) >= MAX_SERVERS:
                raise ConnectorError("connector.limit", f"最多接 {MAX_SERVERS} 个连接器", 409)
            from urllib.parse import urlsplit
            item = {"id": "mcp-" + uuid.uuid4().hex[:12], "serverUrl": url,
                    "label": _unique(data, label or str(urlsplit(url).hostname)[:MAX_LABEL]),
                    "tools": _merge([], found), "toolsError": note or None, "reviewedAt": None, "createdAt": _now()}
            data["servers"].append(item)
            outcome = "connected"
        _save_key(_credential(item["id"]), key)
        _write(root, data)
    return {"status": "connected", "server": item, "outcome": outcome}


async def refresh(root: str, server_id: str) -> Dict[str, Any]:
    url, key = get(root, server_id)["serverUrl"], _load_key(server_id)
    try:
        found, note = await _discover(url, key)
    except ConnectorError as error:
        found, note = None, str(error)
    with _LOCK:
        data = _read(root)
        item = _find(data, server_id)
        if found is not None:
            item["tools"] = _merge(item["tools"], found)
        item["toolsError"] = note or None
        _write(root, data)
    return item


def save_tools(root: str, server_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(body, dict) or set(body) - {"label", "tools"} or not isinstance(body.get("tools"), list):
        raise ConnectorError("invalid", "要带上每个工具的决定")
    with _LOCK:
        data = _read(root)
        item = _find(data, server_id)
        tools = {tool["name"]: tool for tool in item["tools"]}
        for row in body["tools"]:
            if (not isinstance(row, dict) or set(row) - {"name", "access", "label", "confirmed"}
                    or not isinstance(row.get("name"), str) or not isinstance(row.get("access"), str)
                    or row["access"] not in ACCESS):
                raise ConnectorError("invalid", "每个工具只能是只读、每次问或关")
            tool = tools.get(row["name"])
            if tool is None:
                raise ConnectorError("connector.tools_changed", "工具列表已经变了，请刷新后再保存", 409)
            if row["access"] == "write":
                raise ConnectorError("connector.write_always_asks",
                                     f"「{row['name']}」会改东西或对外发送：在这里只能每次问你，或者关掉", 409)
            if row["access"] == "read" and tool["readOnlyHint"] is not True:
                raise ConnectorError("connector.read_needs_hint",
                                     f"「{row['name']}」对方没有标成只读，不能当只读放行：请选每次问，或者关掉", 409)
            tool["access"] = row["access"]
            if row.get("label"):
                tool["label"] = _check_label(str(row["label"])[:MAX_LABEL], "工具名字")
        if body.get("label"):
            label = _check_label(body["label"])
            if any(x["label"] == label and x["id"] != server_id for x in data["servers"]):
                raise ConnectorError("connector.label_taken", "已经有连接器叫这个名字", 409)
            item["label"] = label
        item["reviewedAt"] = _now()
        if sum(len(_open(server)) for server in data["servers"]) > MAX_EXPOSED:
            raise ConnectorError("connector.tools_limit",
                                 f"所有连接器开着的工具合计不能超过 {MAX_EXPOSED} 个，请关掉一些", 409)
        _write(root, data)
    return item


def rename(root: str, server_id: str, value: Any) -> Dict[str, Any]:
    label = _check_label(value)
    with _LOCK:
        data = _read(root)
        item = _find(data, server_id)
        if any(x["label"] == label and x["id"] != server_id for x in data["servers"]):
            raise ConnectorError("connector.label_taken", "已经有连接器叫这个名字", 409)
        item["label"] = label
        _write(root, data)
    return item


def disconnect(root: str, server_id: str) -> None:
    from src.workflow.workbench.connectors import _token_path
    with _LOCK:
        data = _read(root)
        item = _find(data, server_id)
        data["servers"].remove(item)
        _write(root, data)                      # 先除名：从这一刻起任何调用都过不了核对
        _token_path(_credential(server_id)).unlink(missing_ok=True)


# ── 给会话用 ─────────────────────────────────────────────────────────────────

def _open(server: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not server.get("reviewedAt"):
        return []
    return [tool for tool in server["tools"]
            if tool.get("access") == "approval" or (tool.get("access") == "read" and tool.get("readOnlyHint") is True)]


def exposed(root: str) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """现在给会话的：（连接器, 工具）。登记读不出来就一个都不给。"""
    try:
        return [(server, tool) for server in servers(root) for tool in _open(server)][:MAX_EXPOSED]
    except (ConnectorError, OSError, KeyError, TypeError) as error:
        logger.warning("自定义连接器登记不可用，本次不装配：%s", type(error).__name__)
        return []


def authorize(root: str, server_id: str, name: str, digest: str, access: str) -> Optional[Dict[str, Any]]:
    """这一次调用还作不作数：连接器还在、工具还开着、内容和档位还是装配时那一份。"""
    for server, tool in exposed(root):
        if (server["id"], tool["name"]) == (server_id, name):
            return server if (tool["digest"], tool["access"]) == (digest, access) else None
    return None


async def call(root: str, server_id: str, tool: Dict[str, Any], args: Dict[str, Any],
               before_send: Callable[[], None] = lambda: None) -> Any:
    """调一次。返回 MCPToolResult（已抹掉 key）；不该发、发不出去都抛 ConnectorError。"""
    def allowed() -> Dict[str, Any]:
        server = authorize(root, server_id, tool["name"], tool["digest"], tool["access"])
        if server is None:
            raise ConnectorError("connector.changed", "这个连接器或工具已被停用或改过", 409)
        return server

    url, key = allowed()["serverUrl"], _load_key(server_id)
    client = _client(url, key, CALL_TIMEOUT)
    try:
        async with timeout_scope(CALL_TIMEOUT):
            await client.connect()
            current, _note = _catalog(client, await client.list_tools(), key)
            if not any(row["name"] == tool["name"] and row["digest"] == tool["digest"] for row in current):
                raise ConnectorError("connector.tool_changed",
                                     "对方改了这个工具，没有调用；请所有者在连接器里刷新并重新确认", 409)
            allowed()
            before_send()
            from src.tools.mcp_client import MCPToolCall
            result = await client.call_tool(MCPToolCall(tool_name=tool["name"], arguments=args,
                                                        request_id=uuid.uuid4().hex))
            allowed()                           # 期间被断开或改过：结果不交出去
        result.output = redact(result.output, key)
        result.structured_content = redact(result.structured_content, key)
        result.error = None if result.success else "对方报告这次调用失败"
        return result
    except ConnectorError:
        raise
    except Exception as error:  # noqa: BLE001 —— 同上：不把对方的原话带出来
        raise _failure(error) from None
    finally:
        await client.disconnect()
