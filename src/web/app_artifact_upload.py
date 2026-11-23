"""Private HTML creation and file uploads for Desktop Drive."""
import asyncio
import json
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser

from fastapi import Request

from src.agents.tools.artifacts import EDITABLE_ARTIFACT_KINDS, max_bytes, valid_structured_source
from src.web.app_raster_image import jpeg_dimensions, png_dimensions

_TEXT_MIME = {
    ".txt": ("text/plain",),
    ".md": ("text/markdown", "text/x-markdown"),
    ".csv": ("text/csv", "application/vnd.ms-excel"),
    ".json": ("application/json", "text/json"),
}
TEXT_CONTENT_TYPES = frozenset(mimes[0] for mimes in _TEXT_MIME.values())
_MULTIPART_OVERHEAD_BYTES = 16 * 1024
_IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
_MAX_IMAGE_EDGE = 12_000
_MAX_IMAGE_PIXELS = 40_000_000


@dataclass(frozen=True)
class FileUpload:
    name: str
    content_type: str
    data: bytes


class UploadError(ValueError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


def _invalid(*, for_version: bool = False):
    message = "上传表单需要一份文件，可附版本备注" if for_version else "上传表单需要一份文件，可附标题"
    return UploadError(400, "invalid", message)


def _unsupported_file():
    return UploadError(415, "artifact.upload_format", "仅支持 UTF-8 编码的 HTML、TXT、Markdown、CSV、JSON 以及静态 PNG 和 JPEG")


async def _read_body(request: Request, cap: int, invalid: UploadError, oversized: UploadError) -> bytes:
    body_cap = cap + _MULTIPART_OVERHEAD_BYTES
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


async def read_form(request: Request, *, for_version: bool = False) -> tuple[str, str | FileUpload]:
    cap = max_bytes()
    oversized = UploadError(413, "artifact.upload_too_large", "上传的文件超过大小限制")
    content_type = request.headers.get("content-type", "")
    invalid = _invalid(for_version=for_version)
    if content_type.partition(";")[0].strip().lower() != "multipart/form-data":
        raise invalid
    body = await _read_body(request, cap, invalid, oversized)
    return await asyncio.to_thread(_parse_form, content_type, body, cap, oversized, for_version=for_version)


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
    title = _title(payload.get("title"), invalid)
    content = payload.get("content")
    if not isinstance(content, dict) or not isinstance(content.get("kind"), str):
        raise invalid
    if content["kind"] in EDITABLE_ARTIFACT_KINDS:
        await asyncio.to_thread(_check_structured, content, cap, invalid)
        result = content
    elif content["kind"] == "html":
        result = content.get("html")
        if set(content) != {"kind", "html"} or not isinstance(result, str) or not result.strip() or "\x00" in result:
            raise invalid
        _check_size(result, cap, invalid)
    else:
        raise UploadError(415, "artifact.upload_format", "通过 JSON 创建仅支持文档、表格和 HTML")
    return title, result


def _title(value, invalid: UploadError) -> str:
    if not isinstance(value, str):
        raise invalid
    title = value.strip()
    if not title or len(title) > 200 or any(ord(char) < 32 for char in title):
        raise invalid
    _check_size(title, 800, invalid)
    return title


async def read_version(request: Request) -> tuple[int, dict, str | None, str | None]:
    invalid = UploadError(400, "invalid", "保存版本需要原版本编号、有效正文和可选的备注")
    payload, cap = await _read_json_payload(request, invalid)
    version, content, note = payload.get("baseVersion"), payload.get("content"), payload.get("note")
    if set(payload) - {"baseVersion", "content", "note", "title"} or type(version) is not int or version < 1:
        raise invalid
    title = _title(payload["title"], invalid) if "title" in payload else None
    if note is not None:
        if not isinstance(note, str) or len(note) > 1000 or "\x00" in note:
            raise invalid
        _check_size(note, 4000, invalid)
        note = note.strip() or None
    await asyncio.to_thread(_check_structured, content, cap, invalid)
    return version, content, note, title


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
    if content["kind"] not in EDITABLE_ARTIFACT_KINDS:
        raise UploadError(415, "artifact.upload_format", "只支持编辑文档和表格")
    if not valid_structured_source(content):
        raise invalid
    _check_size(json.dumps(content, ensure_ascii=False, separators=(",", ":")), cap, invalid)


def _parse_form(content_type: str, body: bytes, cap: int, oversized: UploadError, *,
                for_version: bool = False) -> tuple[str, str | FileUpload]:
    invalid = _invalid(for_version=for_version)
    if "\r" in content_type or "\n" in content_type:
        raise invalid
    message = BytesParser(policy=policy.HTTP).parsebytes(
        b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body
    )
    if not message.is_multipart() or message.defects:
        raise invalid
    fields = {}
    label = "note" if for_version else "title"
    for part in message.iter_parts():
        name = part.get_param("name", header="Content-Disposition")
        if name in {"channelId", "threadId"}:
            raise UploadError(422, "artifact.upload_location", "通过 Drive 上传，暂不支持附到频道或话题")
        if (name not in {"file", label} or name in fields or part.is_multipart() or part.defects
                or part.get_content_disposition() != "form-data" or "Content-Transfer-Encoding" in part):
            raise invalid
        fields[name] = part
    file = fields.get("file")
    if file is None:
        raise invalid
    filename = file.get_filename() or ""
    extension = "." + filename.rsplit(".", maxsplit=1)[-1].lower() if "." in filename else ""
    mime = file.get_content_type()
    is_html = extension in {".html", ".htm"} and mime == "text/html"
    text_mimes = _TEXT_MIME.get(extension)
    if for_version and text_mimes is None:
        raise UploadError(415, "artifact.upload_format", "只可追加私有文本制品的版本")
    image_mime = _IMAGE_MIME.get(extension)
    if image_mime:
        compatible_mimes = (image_mime, "application/octet-stream")
    elif text_mimes:
        compatible_mimes = (*text_mimes, "application/octet-stream", "text/plain")
    else:
        compatible_mimes = ()
    if not is_html and mime not in compatible_mimes:
        raise _unsupported_file()
    if "/" in filename or "\\" in filename or not filename.strip():
        raise invalid
    if not is_html and (len(filename) > 200 or any(ord(char) < 32 for char in filename)):
        raise invalid

    data = file.get_payload(decode=True) or b""
    if len(data) > cap:
        raise oversized
    label_part = fields.get(label)
    raw_label = (label_part.get_payload(decode=True) or b"") if label_part else b""
    if len(raw_label) > (_MULTIPART_OVERHEAD_BYTES if for_version else 800):
        raise invalid
    try:
        value = raw_label.decode("utf-8").strip()
        if not for_version:
            value = value or filename
    except UnicodeError:
        raise _unsupported_file() from None
    allowed_whitespace = "\r\n\t" if for_version else ""
    if ((not for_version and len(value) > 200)
            or any(ord(char) < 32 and char not in allowed_whitespace for char in value)):
        raise invalid

    if image_mime:
        try:
            width, height = (png_dimensions(data) if image_mime == "image/png"
                             else jpeg_dimensions(data))
        except ValueError as error:
            raise UploadError(415, "artifact.upload_format", str(error)) from None
        if max(width, height) > _MAX_IMAGE_EDGE or width * height > _MAX_IMAGE_PIXELS:
            raise UploadError(413, "artifact.upload_too_large", "图片最长边不能超过 12000 像素，总像素不能超过 4000 万")
        return value, FileUpload(filename, image_mime, data)

    try:
        decoded = data.decode("utf-8")
    except UnicodeError:
        raise _unsupported_file() from None
    if "\x00" in decoded:
        raise invalid
    if is_html:
        if not decoded.strip():
            raise invalid
        return value, decoded
    assert text_mimes is not None
    return value, FileUpload(filename, text_mimes[0], data)
