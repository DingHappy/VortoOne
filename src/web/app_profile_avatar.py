"""Account portraits served only to the authenticated roster of the current home."""

from __future__ import annotations

import base64
import binascii
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile

from src.utils.home_maintenance import home_maintenance
from src.utils.state_dir import ensure_state_gitignore, state_dir
from src.web.app_raster_image import jpeg_dimensions, png_dimensions

_MAX_BYTES = 768 * 1024
_MAX_BASE64 = 4 * ((_MAX_BYTES + 2) // 3)
_MAX_HEADER = 512
_MAX_SIDE = 512
MAX_REQUEST_BYTES = _MAX_BASE64 + 1024
_CONTENT_TYPES = {"image/png", "image/jpeg"}
_PERSON = re.compile(r"[a-z0-9_-]{1,64}")
_VERSION = re.compile(r"[a-f0-9]{24}")
_PORTRAIT = re.compile(r"1(?:\.[a-zA-Z0-9_-]{1,24}){18,24}")


class AvatarTooLarge(ValueError):
    pass


def _path(root: str, person_id: str) -> Path:
    if not _PERSON.fullmatch(person_id):
        raise ValueError("头像身份无效")
    state = state_dir(root)
    directory = state / "profile-avatars"
    if state.is_symlink() or directory.is_symlink():
        raise OSError("头像目录无效")
    return directory / f"{person_id}.image"


def _image(body: object) -> tuple[str, bytes, str]:
    if (not isinstance(body, dict) or not {"imageBase64", "contentType"} <= body.keys()
            or body.keys() - {"imageBase64", "contentType", "portrait"}):
        raise ValueError("请提供 imageBase64 和 contentType，可附头像配方")
    content_type, encoded = body["contentType"], body["imageBase64"]
    portrait = body.get("portrait", "")
    if not isinstance(content_type, str) or content_type not in _CONTENT_TYPES or not isinstance(encoded, str):
        raise ValueError("头像仅支持 PNG 和 JPEG")
    if (not isinstance(portrait, str) or len(portrait) > 384
            or ("portrait" in body and (content_type != "image/png" or not _PORTRAIT.fullmatch(portrait)))):
        raise ValueError("头像配方格式无效")
    if len(encoded) > _MAX_BASE64:
        raise AvatarTooLarge("头像最大 768 KiB")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("头像的 Base64 编码无效") from error
    if len(data) > _MAX_BYTES:
        raise AvatarTooLarge("头像最大 768 KiB")
    width, height = png_dimensions(data) if content_type == "image/png" else jpeg_dimensions(data)
    if not 0 < width == height <= _MAX_SIDE:
        raise ValueError("请上传最大 512 × 512 像素的正方形头像")
    return content_type, data, portrait


@contextmanager
def _record(root: str, person_id: str):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(_path(root, person_id), flags), "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= _MAX_HEADER + _MAX_BYTES:
            raise OSError("头像记录无效")
        header = source.readline(_MAX_HEADER + 1)
        if not header.endswith(b"\n") or len(header) > _MAX_HEADER:
            raise ValueError("头像记录无效")
        content_type, version, portrait = header[:-1].decode("ascii").split(" ", 2)
        if (content_type not in _CONTENT_TYPES or not _VERSION.fullmatch(version)
                or (portrait and (content_type != "image/png" or not _PORTRAIT.fullmatch(portrait)))):
            raise ValueError("头像记录无效")
        yield content_type, version, portrait, source


def _url(org_id: str, person_id: str, version: str, portrait: str) -> str:
    address = f"/api/app/organizations/{org_id}/people/{person_id}/avatar?v={version}"
    return address + (f"#px={portrait}" if portrait else "")


def current(root: str, org_id: str, person_id: str) -> str | None:
    try:
        with _record(root, person_id) as (_, version, portrait, _):
            return _url(org_id, person_id, version, portrait)
    except (OSError, ValueError):
        return None


def _version(content_type: str, data: bytes, portrait: str) -> str:
    stamp = f"{content_type} {portrait}\n".encode("ascii")
    return hashlib.sha256(stamp + data).hexdigest()[:24]


def media(root: str, person_id: str, version: str) -> tuple[str, bytes]:
    with _record(root, person_id) as (content_type, current_version, portrait, source):
        if version != current_version:
            raise FileNotFoundError("头像已更新")
        data = source.read(_MAX_BYTES + 1)
        if not 0 < len(data) <= _MAX_BYTES or _version(content_type, data, portrait) != current_version:
            raise ValueError("头像记录无效")
        return content_type, data


def set_avatar(root: str, org_id: str, person_id: str, body: object) -> str:
    content_type, data, portrait = _image(body)
    version = _version(content_type, data, portrait)
    header = f"{content_type} {version} {portrait}\n".encode("ascii")
    with home_maintenance(root):
        target = _path(root, person_id)
        ensure_state_gitignore(root)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".avatar-", dir=target.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as result:
                result.write(header)
                result.write(data)
                result.flush()
                os.fsync(result.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return _url(org_id, person_id, version, portrait)


def remove_avatar(root: str, person_id: str) -> None:
    with home_maintenance(root):
        _path(root, person_id).unlink(missing_ok=True)
