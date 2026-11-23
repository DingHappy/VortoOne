"""Private HTML creation and HTML or text uploads for Desktop Drive."""
import asyncio
import json
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser

from fastapi import Request

from src.agents.tools.artifacts import EDITABLE_ARTIFACT_KINDS, max_bytes

_TEXT_MIME = {
    ".txt": ("text/plain",),
    ".md": ("text/markdown", "text/x-markdown"),
    ".csv": ("text/csv", "application/vnd.ms-excel"),
    ".json": ("application/json", "text/json"),
}


@dataclass(frozen=True)
class TextUpload:
    name: str
    content_type: str
    data: bytes


class UploadError(ValueError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


def _invalid():
    return UploadError(400, "invalid", "上传表单需要一份文件，可附标题")


def _unsupported_file():
    return UploadError(415, "artifact.upload_format", "目前只能上传 UTF-8 编码的 HTML、TXT、Markdown、CSV 或 JSON 文件")


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


async def read_form(request: Request) -> tuple[str, str | TextUpload]:
    cap = max_bytes()
    oversized = UploadError(413, "artifact.upload_too_large", "上传的文件超过大小限制")
    content_type = request.headers.get("content-type", "")
    if content_type.partition(";")[0].strip().lower() != "multipart/form-data":
        raise _invalid()
    body = await _read_body(request, cap, _invalid(), oversized)
    return await asyncio.to_thread(_parse_form, content_type, body, cap, oversized)


async def _read_json_payload(request: Request, invalid: UploadError) -> tuple[dict, int]:
    if request.headers.get("content-type", "").partition(";")[0].strip().lower() != "application/json":
        raise UploadError(415, "artifact.create_format", "制品需要 JSON 正文")
    cap = max_bytes()
    oversized = UploadError(413, "artifact.upload_too_large", "制品超过大小限制")
    body = await _read_body(request, cap, invalid, oversized)
    try:
        payload = await asyncio.to_thread(json.loads, body.decode("utf-8"))
    except (ValueError, RecursionError):
        raise invalid from None
    if not isinstance(payload, dict):
        raise invalid
    return payload, cap


async def read_json(request: Request) -> tuple[str, str | dict]:
    invalid = UploadError(400, "invalid", "创建制品需要标题和有效正文")
    payload, cap = await _read_json_payload(request, invalid)
    if set(payload) - {"title", "channelId", "content"}:
        raise invalid
    if payload.get("channelId") is not None:
        raise UploadError(422, "artifact.upload_location", "创建私有制品，暂不支持附到频道")
    title = payload.get("title")
    if not isinstance(title, str) or not _valid_content(title.strip(), "source"):
        raise invalid
    content = payload.get("content")
    if not isinstance(content, dict) or not isinstance(content.get("kind"), str):
        raise invalid
    if content["kind"] in EDITABLE_ARTIFACT_KINDS:
        await asyncio.to_thread(_check_structured, content, cap, invalid)
        result = content
    elif content["kind"] == "html":
        result = content.get("html")
        if set(content) != {"kind", "html"} or not isinstance(result, str) or not _valid_content(title.strip(), result):
            raise invalid
        _check_size(result, cap, invalid)
    else:
        raise UploadError(415, "artifact.upload_format", "通过 JSON 创建仅支持文档、表格和 HTML")
    _check_size(title.strip(), 800, invalid)
    return title.strip(), result


async def read_version(request: Request) -> tuple[int, dict, str | None]:
    invalid = UploadError(400, "invalid", "保存版本需要原版本编号、有效正文和可选的备注")
    payload, cap = await _read_json_payload(request, invalid)
    version, content, note = payload.get("baseVersion"), payload.get("content"), payload.get("note")
    if set(payload) - {"baseVersion", "content", "note"} or type(version) is not int or version < 1:
        raise invalid
    if note is not None:
        if not isinstance(note, str) or len(note) > 1000 or "\x00" in note:
            raise invalid
        _check_size(note, 4000, invalid)
        note = note.strip() or None
    await asyncio.to_thread(_check_structured, content, cap, invalid)
    return version, content, note


def _check_size(text: str, cap: int, invalid: UploadError) -> None:
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        raise invalid from None
    if size > cap:
        raise UploadError(413, "artifact.upload_too_large", "制品超过大小限制")


def _check_structured(content, cap: int, invalid: UploadError) -> None:
    if not isinstance(content, dict) or not isinstance(content.get("kind"), str):
        raise invalid
    if content["kind"] == "document":
        if set(content) != {"kind", "markdown"} or not _is_text(content.get("markdown")):
            raise invalid
    elif content["kind"] == "table":
        columns, rows = content.get("columns"), content.get("rows")
        if (set(content) != {"kind", "columns", "rows"} or not _is_row(columns) or not isinstance(rows, list)
                or not all(_is_row(row) and len(row) == len(columns) for row in rows)):
            raise invalid
    else:
        raise UploadError(415, "artifact.upload_format", "只支持编辑文档和表格")
    _check_size(json.dumps(content, ensure_ascii=False, separators=(",", ":")), cap, invalid)


def _is_text(value) -> bool:
    return isinstance(value, str) and "\x00" not in value


def _is_row(value) -> bool:
    return isinstance(value, list) and all(_is_text(cell) for cell in value)


def _valid_content(title: str, html: str) -> bool:
    return bool(
        title and len(title) <= 200 and all(ord(char) >= 32 for char in title)
        and html.strip() and "\x00" not in html
    )


def _parse_form(content_type: str, body: bytes, cap: int, oversized: UploadError) -> tuple[str, str | TextUpload]:
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
    extension = "." + filename.rsplit(".", maxsplit=1)[-1].lower() if "." in filename else ""
    mime = file.get_content_type()
    is_html = extension in {".html", ".htm"} and mime == "text/html"
    text_mimes = _TEXT_MIME.get(extension)
    if not is_html and (text_mimes is None or mime not in (*text_mimes, "application/octet-stream", "text/plain")):
        raise _unsupported_file()
    if "/" in filename or "\\" in filename or not filename.strip():
        raise _invalid()
    if not is_html and (len(filename) > 200 or any(ord(char) < 32 for char in filename)):
        raise _invalid()

    data = file.get_payload(decode=True) or b""
    if len(data) > cap:
        raise oversized
    title_part = fields.get("title")
    raw_title = (title_part.get_payload(decode=True) or b"") if title_part else b""
    if len(raw_title) > 800:
        raise _invalid()
    try:
        decoded = data.decode("utf-8")
        title = raw_title.decode("utf-8").strip() or filename
    except UnicodeError:
        raise _unsupported_file() from None
    if "\x00" in decoded or len(title) > 200 or any(ord(char) < 32 for char in title):
        raise _invalid()
    if is_html:
        if not decoded.strip():
            raise _invalid()
        return title, decoded
    assert text_mimes is not None
    return title, TextUpload(filename, text_mimes[0], data)
