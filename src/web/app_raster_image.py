"""Structural validation for private static raster images uploaded to Drive."""

import struct
import zlib

_PNG_HEADER = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
_PNG_DEPTHS = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}


def png_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 45 or not data.startswith(_PNG_HEADER):
        raise ValueError("图片不是有效的 PNG")
    width, height, depth, color, compression, filter_method, interlace = struct.unpack_from(">IIBBBBB", data, 16)
    if (not width or not height or depth not in _PNG_DEPTHS.get(color, ())
            or compression or filter_method or interlace not in (0, 1)):
        raise ValueError("PNG 图像头无效")

    view = memoryview(data)
    offset = 8
    has_pixels = False
    while offset + 12 <= len(data):
        length, kind = struct.unpack_from(">I4s", data, offset)
        end = offset + 8 + length
        if end + 4 > len(data) or zlib.crc32(view[offset + 4:end]) != struct.unpack_from(">I", data, end)[0]:
            break
        if kind in {b"acTL", b"fcTL", b"fdAT"}:
            raise ValueError("只支持静态 PNG")
        if kind == b"IDAT" and length:
            has_pixels = True
        if kind == b"IEND":
            if has_pixels and length == 0 and end + 4 == len(data):
                return width, height
            break
        offset = end + 4
    raise ValueError("PNG 图像数据无效")


def _jpeg_end_of_scan(data: bytes, start: int) -> int:
    """Skip entropy data, including byte stuffing and restart markers."""
    offset = start
    while (marker := data.find(b"\xff", offset)) != -1:
        offset = marker + 1
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        if data[offset] != 0 and not 0xD0 <= data[offset] <= 0xD7:
            if marker > start:
                return marker
            break
        offset += 1
    raise ValueError("JPEG 图像扫描数据无效")


def jpeg_dimensions(data: bytes) -> tuple[int, int]:
    if not data.startswith(b"\xff\xd8\xff") or not data.endswith(b"\xff\xd9"):
        raise ValueError("图片不是有效的 JPEG")
    offset = 2
    dimensions = None
    has_scan = False
    while offset + 2 <= len(data) and data[offset] == 0xFF:
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        if marker == 0xD9:
            if has_scan and dimensions is not None and offset + 1 == len(data):
                return dimensions
            break
        if marker in (0, 0xD8) or 0xD0 <= marker <= 0xD7 or offset + 3 > len(data):
            break
        length = struct.unpack_from(">H", data, offset + 1)[0]
        end = offset + 1 + length
        if length < 2 or end > len(data):
            break
        if marker in (0xC0, 0xC1, 0xC2):
            if dimensions is not None or length < 11 or length != 8 + 3 * data[offset + 8]:
                break
            height, width = struct.unpack_from(">HH", data, offset + 4)
            if not width or not height:
                break
            dimensions = width, height
        if marker == 0xDA:
            if dimensions is None or length < 8 or length != 6 + 2 * data[offset + 3]:
                break
            has_scan = True
            offset = _jpeg_end_of_scan(data, end)
        else:
            offset = end
    raise ValueError("JPEG 图像头或扫描数据无效")
