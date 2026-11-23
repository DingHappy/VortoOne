"""Private HTML and text uploads for Desktop Drive."""
import asyncio
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser

from fastapi import Request

from src.agents.tools.artifacts import max_bytes

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


async def read_form(request: Request) -> tuple[str, str | TextUpload]:
    cap = max_bytes()
    body_cap = cap + 16 * 1024
    oversized = UploadError(413, "artifact.upload_too_large", "上传的文件超过大小限制")
    content_type = request.headers.get("content-type", "")
    if content_type.partition(";")[0].strip().lower() != "multipart/form-data":
        raise _invalid()
    try:
        length = int(request.headers.get("content-length", "0"))
    except ValueError:
        raise _invalid() from None
    if length < 0:
        raise _invalid()
    if length > body_cap:
        raise oversized

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > body_cap:
            raise oversized
        body.extend(chunk)
    return await asyncio.to_thread(_parse_form, content_type, bytes(body), cap, oversized)


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
