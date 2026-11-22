"""新版界面的 GIF：GIPHY 密钥、搜索、按 id 查找，以及经本服务转发图片。

- 密钥：环境变量 ``GIPHY_API_KEY`` 优先（界面里只读）；否则是所有者在设置里填的，存在
  ``.vortoone/provider_keys.json``（0600，整个 ``.vortoone`` 对 agent 的文件工具是敏感路径）。
  密钥本身从不返回给界面，只给末 4 位。
- 图片：界面按合同从本服务的 ``…/gifs/{id}/media`` 取，不直连服务商（看的人的地址不外泄）。
  转发只去 GIPHY 返回的 ``https://*.giphy.com`` 地址，并限制大小。
- 发消息时界面只给 id；尺寸和标题由这里重新向 GIPHY 查（合同：服务端不信任界面给的尺寸与标题）。

GIF 的标题是外部内容，只当纯文本展示；GIF 消息不进任何 agent 的上下文。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from src.utils.state_dir import state_dir

API = "https://api.giphy.com/v1/gifs"
ENV_KEY = "GIPHY_API_KEY"
_FILE = "provider_keys.json"
_LOCK = threading.Lock()
_ID = re.compile(r"[A-Za-z0-9]{1,64}")
_KEY = re.compile(r"[A-Za-z0-9]{16,64}")
MAX_MEDIA_BYTES = 15 * 1024 * 1024
RATINGS = ("g", "pg")
# 界面的分类（types.ts 的 GifCategory）换成搜索词
CATEGORIES = {"celebrate": "celebrate", "thanks": "thank you", "ok": "ok", "applause": "applause", "wow": "wow",
              "coffee": "coffee", "good_morning": "good morning", "bye": "bye", "friday": "friday",
              "oops": "oops", "thinking": "thinking"}
_SIZES = {"preview": ("fixed_width", "fixed_width_still"), "full": ("downsized", "original"),
          "still": ("original_still", "fixed_width_still")}


class GifError(Exception):
    """GIPHY 拒绝或连不上。status 是给界面的状态码。"""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


# ── 密钥 ──────────────────────────────────────────────────────────────────────

def _path(root: str) -> Path:
    return state_dir(root) / _FILE


def _saved(root: str) -> Tuple[Optional[str], bool]:
    """存下的密钥，以及记录是否能读。"""
    try:
        data = json.loads(_path(root).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, True
    except (OSError, ValueError):
        return None, False
    key = data.get("giphy") if isinstance(data, dict) else None
    if key is None:
        return None, True
    return (key, True) if isinstance(key, str) and _KEY.fullmatch(key) else (None, False)


def api_key(root: str) -> Optional[str]:
    env = os.getenv(ENV_KEY, "").strip()
    if env:
        return env if _KEY.fullmatch(env) else None
    return _saved(root)[0]


def key_view(root: str) -> Dict[str, Any]:
    """界面的 GifKey：来源、末 4 位、状态。"""
    env = os.getenv(ENV_KEY, "").strip()
    if env:
        ok = bool(_KEY.fullmatch(env))
        return {"source": "env", "hint": env[-4:] if ok else None, "state": "ok" if ok else "invalid"}
    key, readable = _saved(root)
    if not readable:
        return {"source": "settings", "hint": None, "state": "invalid"}
    if key is None:
        return {"source": None, "hint": None, "state": "off"}
    return {"source": "settings", "hint": key[-4:], "state": "ok"}


def _write_keys(root: str, change) -> None:
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except FileNotFoundError:
            data = {}
        except (OSError, ValueError):
            data = {}                      # 读不出来的旧记录：这次写入整个换掉（里面没有能用的密钥）
        change(data)
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)


def env_managed() -> bool:
    return bool(os.getenv(ENV_KEY, "").strip())


def save_key(root: str, key: str) -> None:
    _write_keys(root, lambda data: data.__setitem__("giphy", key))


def remove_key(root: str) -> None:
    _write_keys(root, lambda data: data.pop("giphy", None))


def available(root: str) -> bool:
    return key_view(root)["state"] == "ok"


def check_key_shape(key: Any) -> str:
    if not isinstance(key, str) or not _KEY.fullmatch(key.strip()):
        raise ValueError("这不像 GIPHY 的 API Key")
    return key.strip()


# ── 调 GIPHY ─────────────────────────────────────────────────────────────────

async def _get(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    import aiohttp
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, params=params) as response:
                if response.status in (401, 403):
                    raise GifError(400, "gif.key_invalid", "GIPHY 不认这个 API Key")
                if response.status == 404:
                    raise GifError(404, "gif.not_found", "GIPHY 上没有这个 GIF")
                if response.status == 429:
                    raise GifError(429, "gif.rate_limited", "GIPHY 的调用次数用完了，稍后再试")
                if response.status >= 400:
                    raise GifError(502, "gif.unavailable", f"GIPHY 返回了 {response.status}")
                return await response.json(content_type=None)
    except GifError:
        raise
    except Exception as error:  # noqa: BLE001 —— 网络、超时、坏 JSON 一律说连不上
        raise GifError(502, "gif.unavailable", "连不上 GIPHY") from error


def _picture(item: Dict[str, Any], names: Tuple[str, ...]) -> Optional[Dict[str, Any]]:
    images = item.get("images") if isinstance(item.get("images"), dict) else {}
    for name in names:
        found = images.get(name)
        if isinstance(found, dict) and isinstance(found.get("url"), str):
            return found
    return None


def _size(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 1
    return number if 0 < number <= 10000 else 1


def _gif(item: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not _ID.fullmatch(item["id"]):
        return None
    full = _picture(item, _SIZES["full"]) or {}
    title = item.get("title") if isinstance(item.get("title"), str) else ""
    return {"id": item["id"], "title": title[:300], "width": _size(full.get("width")),
            "height": _size(full.get("height"))}


def _rating(rating: str) -> str:
    return rating if rating in RATINGS else "g"


async def verify_key(key: str) -> None:
    await _get(API + "/trending", {"api_key": key, "limit": 1, "rating": "g"})


async def search(root: str, *, q: str = "", category: str = "", offset: int = 0, limit: int = 24,
                 lang: str = "", rating: str = "g") -> Dict[str, Any]:
    key = api_key(root)
    if key is None:
        raise GifError(422, "gif.off", "这台服务还没有设置 GIPHY 的 API Key")
    limit = min(max(int(limit or 24), 1), 50)
    offset = min(max(int(offset or 0), 0), 4999)
    params: Dict[str, Any] = {"api_key": key, "limit": limit, "offset": offset, "rating": _rating(rating)}
    if category and category not in CATEGORIES:
        raise GifError(400, "invalid", "不认识的 GIF 分类")
    words = q.strip()[:50] or CATEGORIES.get(category, "")
    if words:
        params["q"] = words
        if re.fullmatch(r"[a-z]{2}", lang or ""):
            params["lang"] = lang
    data = await _get(API + ("/search" if words else "/trending"), params)
    gifs: List[Dict[str, Any]] = [gif for gif in map(_gif, data.get("data") or []) if gif]
    page = data.get("pagination") if isinstance(data.get("pagination"), dict) else {}
    total = page.get("total_count")
    more = len(data.get("data") or []) >= limit and (not isinstance(total, int) or offset + limit < total)
    return {"gifs": gifs, "next": offset + limit if more else None}


_FOUND: Dict[str, Tuple[float, Dict[str, Any]]] = {}     # id → (时间, GIPHY 原始条目)；只为少查几次
_FOUND_TTL = 3600


async def _lookup(root: str, gif_id: str) -> Dict[str, Any]:
    if not _ID.fullmatch(gif_id or ""):
        raise GifError(404, "gif.not_found", "没有这个 GIF")
    key = api_key(root)
    if key is None:
        raise GifError(422, "gif.off", "这台服务还没有设置 GIPHY 的 API Key")
    cached = _FOUND.get(gif_id)
    if cached and time.monotonic() - cached[0] < _FOUND_TTL:
        return cached[1]
    data = await _get(f"{API}/{gif_id}", {"api_key": key})
    item = data.get("data")
    if not isinstance(item, dict) or item.get("id") != gif_id:
        raise GifError(404, "gif.not_found", "GIPHY 上没有这个 GIF")
    if len(_FOUND) > 2000:
        _FOUND.clear()
    _FOUND[gif_id] = (time.monotonic(), item)
    return item


async def message_media(root: str, gif_id: str) -> Dict[str, Any]:
    """发消息用的 meta.media：重新向 GIPHY 查标题和尺寸。"""
    gif = _gif(await _lookup(root, gif_id))
    if gif is None:
        raise GifError(404, "gif.not_found", "GIPHY 上没有这个 GIF")
    return {"kind": "gif", "provider": "giphy", **gif}


def _safe_media_url(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and (host == "giphy.com" or host.endswith(".giphy.com")) and not parts.username


async def media(root: str, gif_id: str, size: str) -> Tuple[bytes, str]:
    """GIF 的文件（字节与类型）。只去 GIPHY 自己的图片地址。"""
    if size not in _SIZES:
        raise GifError(400, "invalid", "size 只能是 preview、full 或 still")
    picture = _picture(await _lookup(root, gif_id), _SIZES[size])
    if picture is None or not _safe_media_url(picture["url"]):
        raise GifError(404, "gif.not_found", "这个 GIF 没有可用的图片")
    return await _download(picture["url"])


async def _download(url: str) -> Tuple[bytes, str]:
    import aiohttp
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.get(url, allow_redirects=False) as response:
                if response.status != 200:
                    raise GifError(502, "gif.unavailable", f"GIPHY 的图片返回了 {response.status}")
                kind = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
                if kind not in ("image/gif", "image/webp", "image/png", "image/jpeg"):
                    raise GifError(502, "gif.unavailable", "GIPHY 返回的不是图片")
                body = b""
                async for chunk in response.content.iter_chunked(64 * 1024):
                    body += chunk
                    if len(body) > MAX_MEDIA_BYTES:
                        raise GifError(502, "gif.too_large", "这个 GIF 太大了")
                return body, kind
    except GifError:
        raise
    except Exception as error:  # noqa: BLE001
        raise GifError(502, "gif.unavailable", "连不上 GIPHY") from error
