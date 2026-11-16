"""把新版界面（frontend/apps/web 的构建产物）作为本服务自己的网站提供出来。

新版 Desktop 外壳的主窗口打开的是「一台服务的地址」，页面从那里加载，再用同源的 /api/app/* 取数据。
设了 VORTOONE_APP_UI_DIR（构建产物目录）才启用；没设时本服务的页面与原来完全一样。

只放行不含数据的东西：目录里实际存在的静态文件，以及没有其它路由认领的页面地址（交给界面自己的
路由，一律回 index.html）。/api、/ws 以及本服务已有的页面（/review、/mobile、/artifact/…）不经过这里，
鉴权照旧。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from starlette.responses import FileResponse
from starlette.routing import Match

_NOT_PAGES = ("/api/", "/ws", "/docs", "/redoc", "/openapi.json", "/static/")


def ui_dir() -> Optional[Path]:
    value = os.getenv("VORTOONE_APP_UI_DIR", "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    return path if (path / "index.html").is_file() else None


def _file(base: Path, path: str) -> Optional[Path]:
    from src.web.auth import resolve_within
    relative = path.lstrip("/")
    if not relative:
        return None
    target = resolve_within(base, relative)
    return target if target is not None and target.is_file() else None


def _claimed(request) -> bool:
    """有没有别的路由完整匹配这个地址（服务首页 / 的说明除外：启用界面后它让给界面）。"""
    if request.url.path == "/":
        return False
    return any(route.matches(request.scope)[0] == Match.FULL for route in request.app.router.routes)


async def app_ui_middleware(request, call_next):
    base = ui_dir()
    path = request.url.path
    if base is None or request.method not in {"GET", "HEAD"} or path == "/api" or path.startswith(_NOT_PAGES):
        return await call_next(request)
    asset = _file(base, path)
    if asset is not None and not _claimed(request):
        # 带内容指纹的构建文件可以长期缓存；其余（index.html、sw.js 等）每次核对
        cache = "public, max-age=31536000, immutable" if path.startswith("/assets/") else "no-cache"
        return FileResponse(asset, headers={"Cache-Control": cache})
    if _claimed(request) or Path(path).suffix:      # 已有页面照旧；带扩展名却不存在的是缺失的文件，不是页面
        return await call_next(request)
    return FileResponse(base / "index.html", media_type="text/html", headers={"Cache-Control": "no-cache"})
