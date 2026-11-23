"""Read public GitHub notes for the Desktop import preview."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from io import BytesIO
import re
import stat
from threading import BoundedSemaphore
from urllib.parse import quote, unquote, urlsplit
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile
from zlib import error as DeflateError

import aiohttp

from src.workflow.wiki import WikiError

_ARCHIVE_BYTES = 16 * 1024 * 1024
_NOTE_BYTES = 800_000
_SELECTED_BYTES = 8 * 1024 * 1024
_MAX_ENTRIES = 10_000
_MAX_NOTES = 1_000
_ACTIVE = BoundedSemaphore(2)
_OWNER = re.compile(r"[A-Za-z0-9-]{1,39}")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}")
_REF_PART = re.compile(r"[A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class GitHubNotes:
    owner: str
    repo: str
    ref: str | None = None
    path: tuple[str, ...] = ()
    file: bool = False

    @property
    def repository(self) -> str:
        return f"{self.owner}/{self.repo}"


def _invalid_link() -> WikiError:
    return WikiError(422, "wiki_github_invalid_link", "请输入公开的 GitHub 仓库、目录或 Markdown 文件链接")


def _clean_parts(parts: tuple[str, ...]) -> bool:
    return all(
        0 < len(part.encode("utf-8")) <= 255
        and part not in (".", "..")
        and not any(char in "/\\:" or ord(char) < 32 or ord(char) == 127 for char in part)
        for part in parts
    )


def _markdown(name: str) -> bool:
    return name.lower().endswith((".md", ".markdown"))


def parse_link(value: str) -> GitHubNotes:
    if any(char in value for char in "\t\r\n"):
        raise _invalid_link()
    try:
        url = urlsplit(value)
        parts = tuple(unquote(part, errors="strict") for part in url.path.strip("/").split("/"))
    except (UnicodeError, ValueError) as error:
        raise _invalid_link() from error
    if (url.scheme != "https" or url.netloc.lower() not in {"github.com", "raw.githubusercontent.com"}
            or url.query or len(parts) < 2):
        raise _invalid_link()
    owner, repo, *extra = parts
    if (not _OWNER.fullmatch(owner) or not _REPOSITORY.fullmatch(repo)
            or repo in (".", "..") or repo.lower().endswith(".git")):
        raise _invalid_link()
    if url.netloc.lower() == "github.com":
        if not extra:
            return GitHubNotes(owner, repo)
        if len(extra) < 2 or extra[0] not in ("blob", "tree"):
            raise _invalid_link()
        kind, ref, *path = extra
        file = kind == "blob"
    else:
        if not extra:
            raise _invalid_link()
        ref, *path = extra
        file = True
    if (not 1 <= len(ref) <= 255 or ".." in ref
            or not all(_REF_PART.fullmatch(part) and not part.startswith(".") for part in ref.split("/"))
            or not _clean_parts(tuple(path))
            or (file and (not path or not _markdown(path[-1])))):
        raise _invalid_link()
    return GitHubNotes(owner, repo, ref, tuple(path), file)


def _too_large() -> WikiError:
    return WikiError(413, "wiki_github_too_large", "GitHub 笔记或仓库快照超过导入上限")


def _missing() -> WikiError:
    return WikiError(404, "wiki_github_not_public", "找不到这个 GitHub 内容。这里只能读取公开仓库")


def _unavailable() -> WikiError:
    return WikiError(502, "wiki_github_unavailable", "GitHub 暂时无法提供这份笔记")


def _check_status(status: int) -> None:
    if status == 404:
        raise _missing()
    if status in (403, 429):
        raise WikiError(503, "wiki_github_unavailable", "GitHub 暂时拒绝匿名读取，请稍后重试")
    if status != 200:
        raise _unavailable()


def _archive_url(location: str, target: GitHubNotes) -> str:
    try:
        url = urlsplit(location)
    except ValueError as error:
        raise _unavailable() from error
    if (url.scheme != "https" or url.netloc.lower() != "codeload.github.com"
            or url.query or url.fragment
            or not url.path.lower().startswith(f"/{target.repository.lower()}/")):
        raise _unavailable()
    return location


async def _read_bytes(response, limit: int) -> bytes:
    if response.content_length is not None and response.content_length > limit:
        raise _too_large()
    body = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        if len(body) + len(chunk) > limit:
            raise _too_large()
        body.extend(chunk)
    return bytes(body)


def _note_text(data: bytes) -> bytes:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise WikiError(415, "wiki_github_not_markdown", "GitHub 笔记须为 UTF-8 Markdown") from error
    if "\0" in text:
        raise WikiError(415, "wiki_github_not_markdown", "GitHub 笔记须为 UTF-8 Markdown")
    return text.encode("utf-8")


def _single_note(name: str, data: bytes) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as out:
        out.writestr(name, _note_text(data))
    return output.getvalue()


def _package_notes(data: bytes, folder: tuple[str, ...]) -> bytes:
    output = BytesIO()
    names: set[str] = set()
    total = 0
    try:
        with ZipFile(BytesIO(data)) as archive, ZipFile(output, "w", compression=ZIP_DEFLATED) as out:
            entries = archive.infolist()
            if len(entries) > _MAX_ENTRIES:
                raise _too_large()
            for item in entries:
                if (item.is_dir() or not _markdown(item.filename)
                        or stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG)):
                    continue
                wrapper, *path = item.orig_filename.split("/")
                relative = tuple(path[len(folder):])
                if not relative or tuple(path[:len(folder)]) != folder:
                    continue
                if not _clean_parts((wrapper, *path)):
                    raise _unavailable()
                name = "/".join(relative)
                if name in names:
                    raise _unavailable()
                if (len(names) >= _MAX_NOTES or item.file_size > _NOTE_BYTES
                        or total + item.file_size > _SELECTED_BYTES):
                    raise _too_large()
                content = _note_text(archive.read(item))
                total += len(content)
                names.add(name)
                out.writestr(name, content)
    except (BadZipFile, DeflateError, EOFError, NotImplementedError, RuntimeError, ValueError) as error:
        if isinstance(error, WikiError):
            raise
        raise _unavailable() from error
    if not names:
        raise WikiError(404, "wiki_github_no_notes", "这个 GitHub 目录里没有 Markdown 笔记")
    return output.getvalue()


async def fetch_notes(target: GitHubNotes) -> bytes:
    if not _ACTIVE.acquire(blocking=False):
        raise WikiError(429, "wiki_github_busy", "正在读取其他 GitHub 笔记，请稍后重试")
    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False, auto_decompress=False,
                                         cookie_jar=aiohttp.DummyCookieJar(),
                                         headers={"Accept-Encoding": "identity", "User-Agent": "VortoOne"}) as session:
            if target.file:
                path = "/".join(quote(part, safe="") for part in target.path)
                url = f"https://api.github.com/repos/{target.repository}/contents/{path}"
                async with session.get(url, params={"ref": target.ref}, allow_redirects=False,
                                       headers={"Accept": "application/vnd.github.raw+json"}) as response:
                    _check_status(response.status)
                    content = await _read_bytes(response, _NOTE_BYTES)
                return await asyncio.to_thread(_single_note, target.path[-1], content)
            ref = "/" + quote(target.ref, safe="") if target.ref else ""
            api = f"https://api.github.com/repos/{target.repository}/zipball{ref}"
            async with session.get(api, headers={"Accept": "application/vnd.github+json"},
                                   allow_redirects=False) as response:
                if response.status != 302:
                    _check_status(response.status)
                    raise _unavailable()
                url = _archive_url(response.headers.get("Location", ""), target)
            async with session.get(url, allow_redirects=False) as response:
                _check_status(response.status)
                content = await _read_bytes(response, _ARCHIVE_BYTES)
        return await asyncio.to_thread(_package_notes, content, target.path)
    except (aiohttp.ClientError, TimeoutError, asyncio.TimeoutError) as error:
        raise _unavailable() from error
    finally:
        _ACTIVE.release()
