"""Desktop 制品下载和历史，复用所有者的 HTML 制品库。"""

import hmac
import time
from datetime import datetime, timezone
from hashlib import sha256
from urllib.parse import quote, urlencode

from fastapi.responses import JSONResponse, Response

from src.agents.tools.artifacts import ArtifactStore
from src.web import app_views as views
from src.web.auth import get_api_token

_DOWNLOAD_TTL_SECONDS = 300
_NO_STORE = {"Cache-Control": "no-store"}


class ArtifactReadError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


def versions(root: str, artifact_id: str) -> JSONResponse:
    store = ArtifactStore(root)
    rows = store.versions(artifact_id)
    if not rows:
        raise ArtifactReadError(404, "not_found", "没有这个制品")
    author = {"type": "agent", "id": views.main_agent_id(root)}
    return JSONResponse({
        "versions": [
            {"version": int(row["v"]), "author": author, "note": None, "createdAt": views._iso(row.get("ts"))}
            for row in reversed(rows)
        ],
    }, headers=_NO_STORE)


def _version(store: ArtifactStore, artifact_id: str, supplied: int | None) -> int:
    meta = store.meta(artifact_id)
    if not meta:
        raise ArtifactReadError(404, "not_found", "没有这个制品")
    if supplied is not None:
        if supplied not in {int(row["v"]) for row in store.versions(artifact_id)}:
            raise ArtifactReadError(404, "not_found", "没有这个制品版本")
        return supplied
    return int(meta.get("pinned") or meta.get("version") or 1)


def _signature(org_id: str, artifact_id: str, version: int, expires: int) -> str:
    # 这个签名只绑定下载地址和期限，取文件仍需要所有者会话。
    payload = f"vortoone-artifact-download:v1\n{org_id}\n{artifact_id}\n{version}\n{expires}"
    return hmac.new(get_api_token().encode(), payload.encode(), sha256).hexdigest()


def download(root: str, org_id: str, artifact_id: str, supplied: int | None) -> JSONResponse:
    store = ArtifactStore(root)
    version = _version(store, artifact_id, supplied)
    if store.html(artifact_id, version, strict=True) is None:
        raise ArtifactReadError(404, "not_found", "没有这个制品版本")
    expires = int(time.time()) + _DOWNLOAD_TTL_SECONDS
    signature = _signature(org_id, artifact_id, version, expires)
    query = urlencode({"version": version, "expires": expires, "signature": signature})
    path = f"/api/app/organizations/{quote(org_id, safe='')}/artifacts/{quote(artifact_id, safe='')}/file"
    return JSONResponse({
        "url": f"{path}?{query}",
        "expiresAt": datetime.fromtimestamp(expires, timezone.utc).isoformat().replace("+00:00", "Z"),
    }, headers=_NO_STORE)


def file(root: str, org_id: str, artifact_id: str, supplied: int | None,
         format: str, expires: int | None, signature: str) -> Response:
    if expires is not None or signature:
        if supplied is None or expires is None or expires <= int(time.time()) or not hmac.compare_digest(
            signature.encode(), _signature(org_id, artifact_id, supplied, expires).encode()
        ):
            raise ArtifactReadError(403, "artifact.download_expired", "下载地址已失效，请重新下载")
    if format and format != "html":
        raise ArtifactReadError(415, "artifact.format_unavailable", "此制品只提供原始 HTML")
    store = ArtifactStore(root)
    version = _version(store, artifact_id, supplied)
    content = store.html(artifact_id, version, strict=True)
    if content is None:
        raise ArtifactReadError(404, "not_found", "没有这个制品版本")
    filename = quote(f"{artifact_id.strip()}.html", safe="")
    return Response(content, media_type="application/octet-stream", headers={
        **_NO_STORE,
        "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
        "Content-Security-Policy": "sandbox; default-src 'none'",
        "Cross-Origin-Resource-Policy": "same-origin",
        "X-Content-Type-Options": "nosniff",
    })
