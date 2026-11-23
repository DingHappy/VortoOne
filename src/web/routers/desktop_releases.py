"""Public metadata for the published Desktop installer."""
from __future__ import annotations

import asyncio
from datetime import datetime
import json
import re
import time

import aiohttp
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

router = APIRouter(tags=["desktop"])

_REPO = "DingHappy/VortoOne"
_RELEASE_URL = f"https://api.github.com/repos/{_REPO}/releases/latest"
_RESPONSE_LIMIT = 256 * 1024
_SUCCESS_TTL = 600
_FAILURE_TTL = 60
_cache: tuple[float, int, dict[str, str | int] | None] = (0.0, 503, None)
_lock = asyncio.Lock()


class _ReleaseUnavailable(ValueError):
    pass


def _installer(release: object) -> dict[str, str | int] | None:
    if not isinstance(release, dict):
        raise _ReleaseUnavailable()
    tag = release.get("tag_name")
    if (release.get("draft") is not False or release.get("prerelease") is not False
            or not isinstance(tag, str) or not re.fullmatch(r"v[0-9]{1,5}\.[0-9]{1,5}\.[0-9]{1,5}", tag)):
        return None
    version = tag[1:]
    filename = f"VortoOne_{version}_aarch64.dmg"
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise _ReleaseUnavailable()
    matches = [asset for asset in assets if isinstance(asset, dict) and asset.get("name") == filename]
    if not matches:
        return None
    if len(matches) != 1:
        raise _ReleaseUnavailable()

    asset = matches[0]
    url = f"https://github.com/{_REPO}/releases/download/{tag}/{filename}"
    digest, size = asset.get("digest"), asset.get("size")
    published_at = release.get("published_at")
    if (asset.get("state") != "uploaded" or asset.get("browser_download_url") != url
            or not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
            or type(size) is not int or size <= 0 or not isinstance(published_at, str)):
        raise _ReleaseUnavailable()
    datetime.strptime(published_at, "%Y-%m-%dT%H:%M:%SZ")
    return {"version": version, "url": url, "sha256": digest.removeprefix("sha256:"),
            "publishedAt": published_at, "size": size}


async def _fetch_installer() -> dict[str, str | int] | None:
    # One fixed public source. User input, cookies and credentials never leave this service.
    headers = {"Accept": "application/vnd.github+json", "Accept-Encoding": "identity",
               "User-Agent": "VortoOne desktop downloads"}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5), trust_env=False,
                                     auto_decompress=False) as client:
        async with client.get(_RELEASE_URL, headers=headers, allow_redirects=False) as response:
            if response.status == 404:
                return None
            if response.status != 200:
                raise _ReleaseUnavailable()
            try:
                await response.content.readexactly(_RESPONSE_LIMIT + 1)
            except asyncio.IncompleteReadError as incomplete:
                return _installer(json.loads(incomplete.partial))
            raise _ReleaseUnavailable()


async def _latest() -> tuple[int, dict[str, str | int] | None]:
    global _cache
    # Up to one bounded request, even when several people open Downloads together.
    async with _lock:
        expires, status, value = _cache
        if expires > time.monotonic():
            return status, value
        try:
            value = await _fetch_installer()
            status = 200 if value is not None else 404
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, RecursionError):
            status, value = 503, None
        _cache = (time.monotonic() + (_SUCCESS_TTL if status == 200 else _FAILURE_TTL), status, value)
        return status, value


@router.get("/api/desktop/latest")
async def desktop_latest(target: str = Query(max_length=16), arch: str = Query(max_length=16)):
    if target == "darwin" and arch in ("aarch64", "arm64"):
        status, value = await _latest()
        if status == 200:
            return JSONResponse(value, headers={"Cache-Control": "public, max-age=60"})
        if status == 503:
            # The download client tries its official release site only after a local 404.
            return JSONResponse({"error": {"code": "desktop.release_unavailable",
                                           "message": "暂时无法核对 Desktop 正式安装包。"}},
                                status_code=404, headers={"Cache-Control": "no-store"})
    return JSONResponse({"error": {"code": "desktop.release_not_found", "message": "该平台暂无正式安装包。"}},
                        status_code=404)
