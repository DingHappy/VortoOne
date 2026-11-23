"""Private HTML creation for Desktop Drive."""
import asyncio
import json
from email import policy
from email.parser import BytesParser

from fastapi import Request

from src.agents.tools.artifacts import max_bytes


class UploadError(ValueError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


def _invalid():
    return UploadError(400, "invalid", "上传表单需要一份 HTML 文件，可附标题")


def _unsupported_file():
    return UploadError(415, "artifact.upload_format", "目前只能上传 UTF-8 编码的 HTML 文件")


async def _read_body(request: Request, cap: int, invalid: UploadError, oversized: UploadError) -> bytes:
    body_cap = cap + 16 * 1024
    try:
        length = int(request.headers.get("content-length", "0"))
    except ValueError:
        raise invalid from None
    if length < 0:
        raise invalid
    if length > body_cap:
        raise oversized
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > body_cap:
            raise oversized
        body.extend(chunk)
    return bytes(body)


async def read_form(request: Request) -> tuple[str, str]:
    cap = max_bytes()
    oversized = UploadError(413, "artifact.upload_too_large", "上传的 HTML 文件超过大小限制")
    content_type = request.headers.get("content-type", "")
    if content_type.partition(";")[0].strip().lower() != "multipart/form-data":
        raise _invalid()
    body = await _read_body(request, cap, _invalid(), oversized)
    return await asyncio.to_thread(_parse_form, content_type, body, cap, oversized)


async def read_json(request: Request) -> tuple[str, str]:
    if request.headers.get("content-type", "").partition(";")[0].strip().lower() != "application/json":
        raise UploadError(415, "artifact.create_format", "创建制品需要 JSON 正文")
    invalid = UploadError(400, "invalid", "创建制品需要标题和 HTML 正文")
    oversized = UploadError(413, "artifact.upload_too_large", "制品的 HTML 超过大小限制")
    cap = max_bytes()
    body = await _read_body(request, cap, invalid, oversized)
    return await asyncio.to_thread(_parse_json, body, cap, invalid, oversized)


def _parse_json(body: bytes, cap: int, invalid: UploadError, oversized: UploadError) -> tuple[str, str]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, RecursionError):
        raise invalid from None
    if not isinstance(payload, dict) or set(payload) - {"title", "channelId", "content"}:
        raise invalid
    if payload.get("channelId") is not None:
        raise UploadError(422, "artifact.upload_location", "创建私有制品，暂不支持附到频道")
    title, content = payload.get("title"), payload.get("content")
    if not isinstance(title, str) or not isinstance(content, dict) or not isinstance(content.get("kind"), str):
        raise invalid
    if content["kind"] != "html":
        raise _unsupported_file()
    html = content.get("html")
    if set(content) != {"kind", "html"} or not isinstance(html, str):
        raise invalid
    title = title.strip()
    if not _valid_content(title, html):
        raise invalid
    try:
        title.encode("utf-8")
        length = len(html.encode("utf-8"))
    except UnicodeEncodeError:
        raise invalid from None
    if length > cap:
        raise oversized
    return title, html


def _valid_content(title: str, html: str) -> bool:
    return bool(
        title and len(title) <= 200 and all(ord(char) >= 32 for char in title)
        and html.strip() and "\x00" not in html
    )


def _parse_form(content_type: str, body: bytes, cap: int, oversized: UploadError) -> tuple[str, str]:
    if "\r" in content_type or "\n" in content_type:
        raise _invalid()
    message = BytesParser(policy=policy.HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body
    )
    if not message.is_multipart() or message.defects:
        raise _invalid()
    fields = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="Content-Disposition")
        if name in {"channelId", "threadId"}:
            raise UploadError(422, "artifact.upload_location", "通过 Drive 上传，暂不支持附到频道或话题")
        if (name not in {"file", "title"} or name in fields or part.is_multipart() or part.defects
                or part.get_content_disposition() != "form-data" or "Content-Transfer-Encoding" in part):
            raise _invalid()
        fields[name] = part
    file = fields.get("file")
    if file is None:
        raise _invalid()
    filename = file.get_filename() or ""
    if (not filename.lower().endswith((".html", ".htm")) or file.get_content_type() != "text/html"):
        raise _unsupported_file()
    if "/" in filename or "\\" in filename or not filename.strip():
        raise _invalid()

    raw_html = file.get_payload(decode=True) or b""
    if len(raw_html) > cap:
        raise oversized
    title_part = fields.get("title")
    raw_title = (title_part.get_payload(decode=True) or b"") if title_part else b""
    if len(raw_title) > 800:
        raise _invalid()
    try:
        html = raw_html.decode("utf-8")
        title = raw_title.decode("utf-8").strip() or filename
    except UnicodeError:
        raise _unsupported_file() from None
    if not _valid_content(title, html):
        raise _invalid()
    return title, html
