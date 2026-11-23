"""Desktop 制品下载和历史，复用所有者的制品库。"""

import csv
import hmac
import io
import time
from datetime import datetime, timezone
from hashlib import sha256
from urllib.parse import quote, urlencode

from fastapi.responses import JSONResponse, Response

from src.agents.tools.artifacts import EDITABLE_ARTIFACT_KINDS, ArtifactStore
from src.web import app_views as views
from src.web.auth import get_api_token

_DOWNLOAD_TTL_SECONDS = 300
_NO_STORE = {"Cache-Control": "no-store"}


class ArtifactReadError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


def _store(root: str, artifact_id: str) -> tuple[ArtifactStore, bool]:
    """这个制品在哪个库：所有者上传的私有库优先（与详情、删除同序）。调用处已核对所有者证明。"""
    uploads = ArtifactStore(root, desktop_uploads=True)
    if uploads.exists(artifact_id):
        return uploads, True
    return ArtifactStore(root), False


def versions(root: str, artifact_id: str) -> JSONResponse:
    store, uploaded = _store(root, artifact_id)
    rows = store.versions(artifact_id)
    if not rows:
        raise ArtifactReadError(404, "not_found", "没有这个制品")
    # 旧制品只存版本、时间和大小。空角色 ID 让界面显示未知智能体。
    author = {"type": "person", "id": views.owner_id(root)} if uploaded else {"type": "agent", "id": ""}
    history = []
    for row in reversed(rows):
        version = int(row["v"])
        if store.has_snapshot(artifact_id, version):
            history.append({"version": version, "author": author, "note": row.get("note"),
                            "createdAt": views._iso(row.get("ts"))})
    return JSONResponse({
        "versions": history,
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
    store, _uploaded = _store(root, artifact_id)
    version = _version(store, artifact_id, supplied)
    if not store.has_snapshot(artifact_id, version):
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
    store, uploaded = _store(root, artifact_id)
    version = _version(store, artifact_id, supplied)
    meta = store.meta(artifact_id) if uploaded else None
    kind = meta.get("kind") if meta else "html"
    if kind in EDITABLE_ARTIFACT_KINDS:
        content, extension = _structured_download(store, artifact_id, version, kind, format)
        name = f"{artifact_id.strip()}.{extension}"
    elif kind == "file":
        if format:
            raise ArtifactReadError(415, "artifact.format_unavailable", "此制品只提供原始文件")
        content = store.uploaded_file(artifact_id, version, int(meta["bytes"]))
        name = meta["name"]
    else:
        if format and format != "html":
            raise ArtifactReadError(415, "artifact.format_unavailable", "此制品只提供原始文件")
        content = store.html(artifact_id, version, strict=True)
        name = f"{artifact_id.strip()}.html"
    if content is None:
        raise ArtifactReadError(404, "not_found", "没有这个制品版本")
    filename = quote(name, safe="")
    return Response(content, media_type="application/octet-stream", headers={
        **_NO_STORE,
        "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
        "Content-Security-Policy": "sandbox; default-src 'none'",
        "Cross-Origin-Resource-Policy": "same-origin",
        "X-Content-Type-Options": "nosniff",
    })


def _structured_download(store: ArtifactStore, artifact_id: str, version: int, kind: str,
                         format: str) -> tuple[str, str]:
    extension = "md" if kind == "document" else "csv"
    if format not in ("", extension):
        raise ArtifactReadError(415, "artifact.format_unavailable", "该导出格式不可用")
    source = store.structured_content(artifact_id, version)
    if source is None:
        raise ArtifactReadError(404, "not_found", "没有这个制品版本")
    if source["kind"] != kind:
        raise ValueError("制品版本格式不符")
    if kind == "document":
        return source["markdown"], extension
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(source["columns"])
    writer.writerows(source["rows"])
    return output.getvalue(), extension
