"""AI 应用的登记（RFC 7591 动态注册）：收什么、拒什么，回调地址怎么比，确认页上怎么称呼它。

只保留洗干净的名字和回调地址；应用自称的图标、主页、预设权限一概不存，免得它们出现在确认页上。

回调地址只收三种：
- https 地址（claude.ai 这类从自己服务器连过来的应用）；
- 本机回环 http（``localhost`` / ``127.0.0.1`` / ``[::1]``）：Claude Code、VS Code、Gemini CLI 这类本机程序；
  比对时不看端口（RFC 8252 §7.3：本机程序每次换端口）；
- Cursor 固定的 ``cursor://anysphere.cursor-mcp/oauth/callback``。

其它自定义协议一律拒绝：任何本机程序都能注册自定义协议，抢走发回去的授权码。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

NAME_CHARS = 64
REDIRECT_CHARS = 512
MAX_REDIRECTS = 10
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]", "::1"})
CURSOR_REDIRECT = "cursor://anysphere.cursor-mcp/oauth/callback"
# 名字里带这些字、却证明不了自己是那家的应用，确认页提醒一句
_RESERVED = re.compile(r"claude|anthropic|chatgpt|openai|cursor|vortoone|vortocode|copilot|gemini", re.I)


class InvalidClient(ValueError):
    """登记内容不成立；消息给应用看（OAuth 的 error_description）。"""


def clean_name(raw: Any) -> str:
    """去掉控制字符、零宽和方向控制字符与尖括号，空白合成一个，最多 64 个字。"""
    if not isinstance(raw, str):
        return ""
    kept = "".join(ch if unicodedata.category(ch)[0] not in ("C", "Z") or ch == " " else " "
                   for ch in raw if ch not in "<>")
    return " ".join(kept.split())[:NAME_CHARS].strip()


def _loopback(parts) -> bool:
    return parts.scheme == "http" and (parts.hostname or "").lower() in LOOPBACK_HOSTS


def check_redirect(uri: Any) -> str:
    """一个回调地址成立就原样返回，否则抛 InvalidClient。"""
    if not isinstance(uri, str) or not uri or len(uri) > REDIRECT_CHARS:
        raise InvalidClient("redirect_uris: each must be a URL of at most 512 characters")
    if uri == CURSOR_REDIRECT:
        return uri
    try:
        parts = urlsplit(uri)
        parts.port                      # noqa: B018 —— 端口不合法时这里抛 ValueError
    except ValueError:
        raise InvalidClient("redirect_uris: not a valid URL") from None
    if parts.fragment or "#" in uri:
        raise InvalidClient("redirect_uris: no fragment allowed")
    if parts.username is not None or parts.password is not None or not parts.hostname:
        raise InvalidClient("redirect_uris: needs a host and no user info")
    if parts.scheme == "https" or _loopback(parts):
        return uri
    raise InvalidClient("redirect_uris: only https, http on localhost, or Cursor's callback")


def check_registration(body: Any) -> Dict[str, Any]:
    """动态注册的请求体 → 要存的 {name, redirect_uris}。"""
    if not isinstance(body, dict):
        raise InvalidClient("Send a JSON object")
    uris = body.get("redirect_uris")
    if not isinstance(uris, list) or not 1 <= len(uris) <= MAX_REDIRECTS:
        raise InvalidClient("redirect_uris: 1 to 10 addresses")
    checked: List[str] = []
    for uri in uris:
        value = check_redirect(uri)
        if value not in checked:
            checked.append(value)
    # token_endpoint_auth_method 不看：一律登记成公开客户端（none），回答里写明，应用据此不带密钥
    grants = body.get("grant_types")
    if grants is not None and (not isinstance(grants, list)
                               or not set(grants) & {"authorization_code"}):
        raise InvalidClient("grant_types: authorization_code is required")
    return {"name": clean_name(body.get("client_name")) or "AI app", "redirect_uris": checked}


def redirect_matches(registered: List[str], presented: Any) -> bool:
    """这次请求带的回调地址是不是登记过的：完全一样，或同为本机回环且协议和路径一样（端口随意）。"""
    if not isinstance(presented, str) or not presented:
        return False
    if presented in registered:
        return True
    try:
        asked = urlsplit(presented)
        asked.port                      # noqa: B018
    except ValueError:
        return False
    if not _loopback(asked) or asked.fragment or asked.username is not None:
        return False
    for uri in registered:
        known = urlsplit(uri)
        if _loopback(known) and known.path == asked.path and known.query == asked.query:
            return True
    return False


def access_seconds(redirect_uri: str) -> int:
    """访问令牌的寿命：一小时；Cursor 的续期据报不稳，给一个工作日。"""
    host = (urlsplit(redirect_uri).hostname or "").lower() if redirect_uri != CURSOR_REDIRECT else ""
    return 8 * 3600 if redirect_uri == CURSOR_REDIRECT or host in ("cursor.com", "www.cursor.com") else 3600


# 已知应用的品牌，只认它自己的域名：说明文件放在这里（CIMD），或授权码回到这里（https 回调）
BRAND_HOSTS = {"claude.ai": "claude", "claude.com": "claude", "chatgpt.com": "openai",
               "cursor.com": "cursor", "www.cursor.com": "cursor",
               "vscode.dev": "vscode", "insiders.vscode.dev": "vscode"}


def display(redirect_uri: str, name: str, client_id: str = "") -> Dict[str, Any]:
    """确认页和设置里怎么称呼这个应用。由这次请求的回调地址、和说明文件所在的地址决定，不信应用自报的。

    - Cursor 的固定回调：``app_scheme``；
    - 本机回环：``loopback``（「这台电脑上的一个程序」）——不管它拿的是谁的说明文件：任何本机程序都能
      拿着知名应用的 client_id、配上自己的端口来登录；
    - 说明文件在已知应用自己的域名上：``verified``，带品牌标志；
    - 其余 https：``https``，显示域名；授权码回到已知应用的域名时带品牌标志（码只会交到那里）。"""
    if redirect_uri == CURSOR_REDIRECT:
        return {"displayKind": "app_scheme", "host": None, "brand": "cursor",
                "returnsTo": "cursor://anysphere.cursor-mcp", "reservedNameWarning": False}
    parts = urlsplit(redirect_uri)
    host = (parts.hostname or "").lower()
    if _loopback(parts):
        port = f":{parts.port}" if parts.port else ""
        return {"displayKind": "loopback", "host": None, "brand": None,
                "returnsTo": f"{host}{port}", "reservedNameWarning": bool(_RESERVED.search(name))}
    returns = parts.netloc.lower()
    document = cimd_url(client_id)
    if document is not None:
        owner = (urlsplit(document).hostname or "").lower()
        brand = BRAND_HOSTS.get(owner)
        return {"displayKind": "verified" if brand else "https", "host": owner, "brand": brand,
                "returnsTo": returns,
                "reservedNameWarning": bool(_RESERVED.search(name)) and not _vendor_host(name, owner)}
    return {"displayKind": "https", "host": host, "brand": BRAND_HOSTS.get(host), "returnsTo": returns,
            "reservedNameWarning": bool(_RESERVED.search(name)) and not _vendor_host(name, host)}


# ── 按地址登记（Client ID Metadata Document） ──────────────────────────────────
# 应用不来注册，直接拿一个 https 地址当 client_id；那个地址上放着它的说明文件（名字、回调地址）。
# ChatGPT 这样连。文件里必须写着它自己的地址，否则任何网页都能冒充别的应用。

DOCUMENT_BYTES = 64 * 1024


def cimd_url(client_id: Any) -> Optional[str]:
    """这个 client_id 是不是说明文件的地址：https、有路径、不带用户名和片段，主机不是 IP 也不是本机。"""
    if not isinstance(client_id, str) or not client_id.startswith("https://") or len(client_id) > 2048:
        return None
    try:
        parts = urlsplit(client_id)
        parts.port                      # noqa: B018
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if not host or parts.path in ("", "/") or "#" in client_id or parts.username is not None:
        return None
    if host == "localhost" or host.endswith(".localhost") or "." not in host:
        return None
    try:
        import ipaddress
        ipaddress.ip_address(host.strip("[]"))
        return None
    except ValueError:
        return client_id


def parse_document(client_id: str, raw: bytes) -> Dict[str, Any]:
    """取回来的说明文件 → 要存的 {name, redirect_uris}；不成立抛 InvalidClient。"""
    import json
    if len(raw) > DOCUMENT_BYTES:
        raise InvalidClient("client metadata document too large")
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise InvalidClient("client metadata document is not JSON") from None
    if not isinstance(body, dict) or body.get("client_id") != client_id:
        raise InvalidClient("client metadata document must name its own address as client_id")
    method = body.get("token_endpoint_auth_method", "none")
    # 签名请求的客户端（ChatGPT 的 private_key_jwt）照样按公开客户端 + PKCE 连
    if method not in ("none", "private_key_jwt"):
        raise InvalidClient("unsupported token_endpoint_auth_method")
    checked = check_registration({"client_name": body.get("client_name"), "redirect_uris": body.get("redirect_uris"),
                                  "grant_types": body.get("grant_types")})
    host = (urlsplit(client_id).hostname or "").lower()
    return {"name": clean_name(body.get("client_name")) or host, "redirect_uris": checked["redirect_uris"]}


def _vendor_host(name: str, host: str) -> bool:
    """名字里的厂商和回调地址的域名对得上（claude → claude.ai / anthropic.com 等）。"""
    pairs = {"claude": ("claude.ai", "anthropic.com"), "anthropic": ("claude.ai", "anthropic.com"),
             "chatgpt": ("chatgpt.com", "openai.com"), "openai": ("chatgpt.com", "openai.com"),
             "cursor": ("cursor.com", "cursor.sh"), "copilot": ("github.com", "githubcopilot.com"),
             "gemini": ("google.com",)}
    found = {match.lower() for match in _RESERVED.findall(name)}
    for word in found:
        domains: Optional[tuple] = pairs.get(word)
        if not domains or not any(host == d or host.endswith("." + d) for d in domains):
            return False
    return bool(found)
