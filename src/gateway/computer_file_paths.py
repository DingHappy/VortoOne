"""File access beneath a user-selected, identity-bound local directory."""
from __future__ import annotations

from contextlib import contextmanager
import difflib
import hashlib
import os
from pathlib import Path
import stat
import unicodedata
import uuid

from src.agents.capabilities import is_sensitive_repo_path

MAX_TEXT_BYTES = 32 * 1024
_TEMP_PREFIX = ".vortoone-create-"


class FileDenied(ValueError):
    """The requested path, version, or platform cannot be used safely."""


def text_bytes(text: object) -> bytes:
    if not isinstance(text, str) or "\0" in text or len(text) > MAX_TEXT_BYTES:
        raise FileDenied("需要不超过 32 KiB 的 UTF-8 文本")
    try:
        data = text.encode("utf-8")
    except UnicodeError:
        raise FileDenied("需要有效的 UTF-8 文本") from None
    if len(data) > MAX_TEXT_BYTES:
        raise FileDenied("文本超过 32 KiB")
    return data


def relative_parts(value: object) -> tuple[str, ...]:
    if (not isinstance(value, str) or not value or len(value) > 1024
            or "\\" in value or ":" in value or is_sensitive_repo_path(value)):
        raise FileDenied("路径不在允许的文件范围内")
    parts = tuple(value.split("/"))
    if len(parts) > 16 or any(
        not part or part.startswith(".") or part != part.strip()
        or part.casefold() in {"secrets", "credentials", "keychains", "owner-key", "model.env", "server.env"}
        or any(unicodedata.category(char).startswith("C") for char in part)
        or len(part.encode("utf-8")) > 255
        for part in parts
    ):
        raise FileDenied("路径不在允许的文件范围内")
    return parts


def _directory_flags() -> int:
    if (os.name != "posix" or not all(hasattr(os, flag) for flag in ("O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK"))
            or not {os.open, os.stat, os.link, os.unlink} <= os.supports_dir_fd
            or not {os.stat, os.link} <= os.supports_follow_symlinks):
        raise FileDenied("此系统尚无可验证的本机文件边界")
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _identity(info: os.stat_result) -> tuple[str, str]:
    return str(info.st_dev), str(info.st_ino)


def _version(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_ctime_ns, info.st_mtime_ns)


def _fingerprint(value: tuple) -> str:
    return hashlib.sha256(repr(value).encode("ascii")).hexdigest()


def _private_to_owner(info: os.stat_result) -> bool:
    return info.st_uid == os.geteuid() and not info.st_mode & 0o022


@contextmanager
def _directory(path: str, components: tuple[str, ...] = (), *, expected=None):
    """Open each component without following links. Yield the pinned parent and chain."""
    flags = _directory_flags()
    absolute = Path(path)
    if not absolute.is_absolute() or len(absolute.parts) < 2 or ".." in absolute.parts:
        raise FileDenied("请在本机明确选择一个文件夹")
    fd = os.open(absolute.anchor, flags)
    chain = []
    root_device = None
    try:
        names = absolute.parts[1:]
        for index, name in enumerate((*names, *components)):
            child = os.open(name, flags, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            chain.append(_identity(info))
            if index >= len(names) - 1:
                if root_device is None:
                    root_device = info.st_dev
                if info.st_dev != root_device or not _private_to_owner(info):
                    raise FileDenied("文件夹权限或文件系统不符合本机约束")
        if expected is not None and tuple(chain[:len(names)]) != expected:
            raise FileDenied("所选文件夹已移动或替换，请在本机重新授权")
        yield fd, tuple(chain)
    finally:
        os.close(fd)


def select_directory(value: str) -> tuple[str, tuple[tuple[str, str], ...]]:
    """Only the native folder picker calls this operation."""
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise FileDenied("请在本机明确选择一个文件夹")
    canonical = str(Path(value).resolve(strict=True))
    if value != canonical:
        raise FileDenied("所选文件夹路径已变化，请重新选择")
    if is_sensitive_repo_path(value) or is_sensitive_repo_path(canonical):
        raise FileDenied("不能开放凭据或应用状态目录")
    if any(part.casefold() in {".git", "keychains", "computer-connections"} for part in Path(canonical).parts):
        raise FileDenied("不能开放凭据或应用状态目录")
    with _directory(canonical) as (_, chain):
        return canonical, chain


class PinnedFile:
    def __init__(self, directory: str, directory_identity, relative_path: str):
        self.directory = directory
        self.identity = directory_identity
        self.components = relative_parts(relative_path)
        self.name = self.components[-1]

    @contextmanager
    def parent(self):
        with _directory(self.directory, self.components[:-1], expected=self.identity) as pinned:
            yield pinned

    def _check_parent(self, chain) -> None:
        with self.parent() as (_, observed):
            if observed != chain:
                raise FileDenied("父目录已变化，请重新发起操作")

    def _observed_file(self, parent: int) -> os.stat_result:
        info = os.stat(self.name, dir_fd=parent, follow_symlinks=False)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_dev != os.fstat(parent).st_dev
                or not _private_to_owner(info) or info.st_size > MAX_TEXT_BYTES):
            raise FileDenied("只支持所属用户拥有的普通小文本文件")
        return info

    def preview(self, operation: str) -> str:
        with self.parent() as (parent, chain):
            if operation == "create":
                try:
                    os.stat(self.name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    self._check_parent(chain)
                    return "absent:" + _fingerprint(chain)
                raise FileDenied("目标已存在，只能生成补丁")
            info = self._observed_file(parent)
            self._check_parent(chain)
            return _fingerprint((chain, _version(info)))

    def read(self, version: str) -> str:
        with self.parent() as (parent, chain):
            before = self._observed_file(parent)
            observed = _fingerprint((chain, _version(before)))
            if observed != version:
                raise FileDenied("文件在确认后变化，请重新发起")
            fd = os.open(self.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(fd, "rb") as stream:
                if _version(os.fstat(stream.fileno())) != _version(before):
                    raise FileDenied("文件在确认后变化，请重新发起")
                data = stream.read(MAX_TEXT_BYTES + 1)
                if not (_version(os.fstat(stream.fileno())) == _version(self._observed_file(parent)) == _version(before)):
                    raise FileDenied("文件在读取期间变化")
            self._check_parent(chain)
        if len(data) > MAX_TEXT_BYTES or b"\0" in data:
            raise FileDenied("文件不在允许的文本范围内")
        try:
            return data.decode("utf-8")
        except UnicodeError:
            raise FileDenied("文件不是 UTF-8 文本") from None

    def patch(self, version: str, content: str) -> str:
        old = self.read(version)
        if old == content:
            return ""
        # Generate a displayable patch without changing the source file.
        parts = difflib.unified_diff(old.splitlines(keepends=True), content.splitlines(keepends=True),
                                     fromfile=self.name, tofile=self.name)
        patch = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                        for line in parts)
        text_bytes(patch)
        return patch

    def create(self, data: bytes, version: str) -> None:
        with self.parent() as (parent, chain):
            if version != "absent:" + _fingerprint(chain):
                raise FileDenied("父目录在确认后变化，请重新发起")
            token = _TEMP_PREFIX + uuid.uuid4().hex
            fd = os.open(token, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                    self._check_parent(chain)
                    if _version(os.stat(token, dir_fd=parent, follow_symlinks=False)) != _version(os.fstat(stream.fileno())):
                        raise FileDenied("新建暂存文件已变化")
                    # A hard link creates the destination only if it does not already exist.
                    os.link(token, self.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
                    try:
                        self._check_parent(chain)
                    except (OSError, FileDenied):
                        # Publication may already have happened in the selected inode.
                        raise OSError("cannot verify directory after publication") from None
            finally:
                os.unlink(token, dir_fd=parent)
                os.fsync(parent)
