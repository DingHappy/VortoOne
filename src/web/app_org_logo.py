"""A workspace's local raster logo, shared only through its authenticated membership."""

from __future__ import annotations

import base64
import binascii
import os
import stat
import struct
import tempfile
import zlib
from pathlib import Path

from src.gateway.computer_browser_wire import MAX_PNG_BYTES, validate_png
from src.utils.home_maintenance import home_maintenance
from src.utils.state_dir import ensure_state_gitignore, state_dir

_PNG_PREFIX = "data:image/png;base64,"
_MAX_SIDE = 512
_MAX_BASE64 = 4 * ((MAX_PNG_BYTES + 2) // 3)
MAX_REQUEST_BYTES = _MAX_BASE64 + 512


class InvalidLogo(ValueError):
    pass


class LogoTooLarge(InvalidLogo):
    pass


def _png(data: bytes) -> bytes:
    """Accept the browser's usual PNG pixels and discard ancillary metadata."""
    try:
        validate_png(data)
    except ValueError as error:
        raise InvalidLogo("图片不是有效的 PNG") from error
    width, height, depth, color, compression, filtering, interlace = struct.unpack_from(">IIBBBBB", data, 16)
    if width > _MAX_SIDE or height > _MAX_SIDE:
        raise LogoTooLarge("工作区 Logo 最大 512 × 512 像素")
    if depth != 8 or color not in (2, 6) or compression or filtering or interlace:
        raise InvalidLogo("请上传 8 位 RGB 或 RGBA 非隔行 PNG")
    compressed = bytearray()
    offset = 33  # PNG signature and complete IHDR
    after_image = False
    while offset < len(data):
        if len(data) - offset < 12:
            raise InvalidLogo("PNG 数据块不完整")
        size, kind = struct.unpack_from(">I4s", data, offset)
        end = offset + 8 + size
        if (not kind.isalpha() or not kind[2:3].isupper() or end + 4 > len(data)
                or zlib.crc32(data[offset + 4:end]) != struct.unpack_from(">I", data, end)[0]):
            raise InvalidLogo("PNG 数据块不完整或校验失败")
        if kind == b"IEND":
            if size or end + 4 != len(data):
                raise InvalidLogo("PNG 结束块无效")
            break
        if kind == b"IDAT":
            if after_image:
                raise InvalidLogo("PNG 图像块须连续")
            compressed.extend(data[offset + 8:end])
        else:
            if (kind[:1].isupper() and kind != b"PLTE") or kind == b"tRNS":
                raise InvalidLogo("PNG 包含不支持的图像块")
            after_image = bool(compressed)
        offset = end + 4

    stride = 1 + width * (4 if color == 6 else 3)
    expected = stride * height
    decoder = zlib.decompressobj()
    try:
        pixels = decoder.decompress(compressed, expected + 1)
    except zlib.error as error:
        raise InvalidLogo("PNG 像素数据无效") from error
    if (len(pixels) != expected or not decoder.eof or decoder.unused_data
            or any(value > 4 for value in pixels[::stride])):
        raise InvalidLogo("PNG 像素数据无效")
    if zlib.crc32(data[12:29]) != struct.unpack_from(">I", data, 29)[0]:
        raise InvalidLogo("PNG 图像头校验失败")

    image = b"IDAT" + compressed
    return data[:33] + struct.pack(">I", len(compressed)) + image + struct.pack(">I", zlib.crc32(image)) + data[-12:]


def _path(root: str) -> Path:
    directory = state_dir(root) / "workspace-branding"
    if directory.is_symlink():
        raise OSError("工作区 Logo 目录无效")
    return directory / "logo.png"


def _url(data: bytes) -> str:
    return _PNG_PREFIX + base64.b64encode(data).decode("ascii")


def current(root: str) -> str | None:
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(_path(root), flags), "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                return None
            return _url(_png(source.read(MAX_PNG_BYTES + 1)))
    except (InvalidLogo, OSError):
        return None


def set_logo(root: str, body: object) -> str:
    if not isinstance(body, dict) or body.keys() != {"imageBase64", "contentType"}:
        raise InvalidLogo("请提供 imageBase64 和 contentType")
    if body["contentType"] != "image/png" or not isinstance(body["imageBase64"], str):
        raise InvalidLogo("工作区 Logo 仅支持 PNG")
    encoded = body["imageBase64"]
    if len(encoded) > _MAX_BASE64:
        raise LogoTooLarge("工作区 Logo 最大 768 KiB")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise InvalidLogo("PNG 的 Base64 编码无效") from error
    if len(data) > MAX_PNG_BYTES:
        raise LogoTooLarge("工作区 Logo 最大 768 KiB")
    logo = _png(data)
    with home_maintenance(root):
        target = _path(root)
        ensure_state_gitignore(root)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".logo-", suffix=".tmp", dir=target.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as result:
                result.write(logo)
                result.flush()
                os.fsync(result.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return _url(logo)


def remove_logo(root: str) -> None:
    with home_maintenance(root):
        _path(root).unlink(missing_ok=True)
