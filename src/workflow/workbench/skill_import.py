"""Decode one explicitly uploaded personal skill without extracting or running it."""

from __future__ import annotations

import base64
import io
import re
import stat
import zipfile
import zlib

import yaml
from yaml.nodes import MappingNode, ScalarNode

from src.workflow.workbench.personal_skills import MAX_BYTES

MAX_ARCHIVE_BYTES = 128 * 1024
_MAX_BASE64_CHARS = (MAX_ARCHIVE_BYTES + 2) // 3 * 4
MAX_IMPORT_BODY = _MAX_BASE64_CHARS + 1024
_HEADER = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", re.S)
_STRING = "tag:yaml.org,2002:str"


class SkillImportError(ValueError):
    def __init__(self, message: str, *, status: int = 400, code: str = "skill.invalid_upload"):
        super().__init__(message)
        self.status = status
        self.code = code


def _too_large() -> SkillImportError:
    return SkillImportError("ZIP 最大 128 KiB，SKILL.md 最大 64 KiB",
                            status=413, code="skill.local_upload_too_large")


def _unzip(value) -> bytes:
    if not isinstance(value, str) or not value:
        raise SkillImportError("需要上传 ZIP 文件")
    if len(value) > _MAX_BASE64_CHARS:
        raise _too_large()
    try:
        data = base64.b64decode(value, validate=True)
    except ValueError as error:
        raise SkillImportError("ZIP 编码无效") from error
    if len(data) > MAX_ARCHIVE_BYTES:
        raise _too_large()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            files = [entry for entry in entries if not entry.is_dir()]
            if len(files) != 1 or len(entries) > 2:
                raise SkillImportError("ZIP 只能包含一个 SKILL.md")
            item = files[0]
            parts = item.filename.split("/")
            if (len(parts) not in (1, 2) or parts[-1] != "SKILL.md"
                    or any(part in ("", ".", "..") for part in parts)
                    or any(char in item.orig_filename for char in ("\\", ":", "\x00"))
                    or stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG)):
                raise SkillImportError("请将 SKILL.md 放在 ZIP 根目录或一层普通目录内")
            directories = [entry for entry in entries if entry.is_dir()]
            if directories:
                directory = directories[0]
                if (len(parts) != 2 or directory.orig_filename != parts[0] + "/"
                        or stat.S_IFMT(directory.external_attr >> 16) not in (0, stat.S_IFDIR)
                        or directory.file_size):
                    raise SkillImportError("ZIP 只能包含一个 SKILL.md")
            if item.file_size > MAX_BYTES:
                raise _too_large()
            if item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) or item.flag_bits & 1:
                raise SkillImportError("ZIP 需要普通压缩且不能加密")
            with archive.open(item) as handle:
                raw = handle.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise _too_large()
            return raw
    except (OSError, zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError) as error:
        raise SkillImportError("读取 ZIP 失败，请检查压缩文件") from error


def upload_draft(value) -> dict:
    try:
        text = _unzip(value).decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise SkillImportError("SKILL.md 需要 UTF-8 文本") from error
    header = _HEADER.match(text)
    if not header or len(header[1]) > 4096:
        raise SkillImportError("SKILL.md 需要包含 name 的简短 YAML 头部")
    try:
        node = yaml.compose(header[1], Loader=yaml.BaseLoader)
    except (yaml.YAMLError, RecursionError) as error:
        raise SkillImportError("SKILL.md 的 YAML 头部无效") from error
    if not isinstance(node, MappingNode):
        raise SkillImportError("SKILL.md 的 YAML 头部应为键值表")
    fields = {}
    for key, value in node.value:
        if not isinstance(key, ScalarNode) or key.value not in {"name", "description"}:
            raise SkillImportError("本地导入只支持 name 和 description 元数据")
        if not isinstance(value, ScalarNode) or value.tag != _STRING or key.value in fields:
            raise SkillImportError("SKILL.md 的技能名称和说明需为不重复的文本字段")
        fields[key.value] = value.value
    return {"name": fields.get("name", ""), "description": fields.get("description", ""),
            "instructions": text[header.end():], "revision": None}
