"""Artifacts（制品）—— 把会话产出的自包含 HTML 发布成可分享、可实时更新的网页。

复刻 Claude Code 的 artifact 能力，落到 VortoOne 的本地优先 / 人在关口模型：
- 制品存到 `.vortoone/artifacts/<id>/`（gitignored，纯运行时产物，不进版本库）；
- 由 Web 服务器在 `/artifact/<id>` 渲染（iframe 沙箱 + 限制性 CSP，禁止外联——
  对齐 CC「静态、无外部请求」的隔离：制品脚本可跑但不能联网/SSRF）；
- 页面随会话**实时更新**：重发同一 id 会 version+1，查看页轮询版本号自动刷新；
- 发布是**写操作**：TUI 首次发布弹确认（之后静默更新），Web 端靠 build 模式门控——都落「人在关口」。

本模块只管"存储 + 工具封装"，**零 Web 依赖**；HTTP 路由见 `src/web/routers/artifacts.py`。
"""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import os
import re
import shutil
import stat
import tempfile
from threading import Lock
import uuid
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Optional
from src.utils.state_dir import state_dir


def _slugify(title: str) -> str:
    """标题 → ASCII kebab（id 段恒为 ASCII，避免 URL/路径里出现 CJK）。空则 'artifact'。"""
    s = re.sub(r"[^a-z0-9]+", "-", (title or "").strip().lower())
    return s.strip("-")[:40] or "artifact"


# 制品体积上限：默认 16 MiB（对齐 Claude Code），VORTOONE_ARTIFACT_MAX_BYTES 可覆盖。
DEFAULT_MAX_BYTES = 16 * 1024 * 1024
_WINDOWS_ARTIFACT_LOCK = Lock()


def max_bytes() -> int:
    try:
        v = int(os.getenv("VORTOONE_ARTIFACT_MAX_BYTES") or DEFAULT_MAX_BYTES)
        return v if v > 0 else DEFAULT_MAX_BYTES
    except ValueError:
        return DEFAULT_MAX_BYTES


# 渲染 markdown 制品用的整页外壳（GitHub 风排版，内联 CSS，无外联）。
_MD_DOC = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>__TITLE__</title>
<style>
  body { max-width: 860px; margin: 0 auto; padding: 40px 24px; line-height: 1.7;
         font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
         color: #1f2328; background: #fff; }
  h1, h2, h3 { line-height: 1.25; margin: 1.4em 0 .6em; }
  h1, h2 { border-bottom: 1px solid #d8dee4; padding-bottom: .3em; }
  code { background: #eff1f3; padding: .15em .4em; border-radius: 5px; font-size: 92%; }
  pre { background: #f6f8fa; padding: 14px; border-radius: 8px; overflow: auto; }
  pre code { background: none; padding: 0; }
  blockquote { margin: 0; padding: 0 1em; color: #57606a; border-left: .25em solid #d0d7de; }
  table { border-collapse: collapse; } th, td { border: 1px solid #d0d7de; padding: 6px 13px; }
  a { color: #0969da; } img { max-width: 100%; }
</style></head><body>
__BODY__
</body></html>"""


def render_markdown_doc(title: str, md_text: str) -> str:
    """markdown → 自包含 HTML 整页。无 markdown 库时优雅降级为 <pre>。"""
    import html as _h
    try:
        import markdown as _md
        body = _md.markdown(md_text or "", extensions=["fenced_code", "tables", "sane_lists"])
    except Exception:  # noqa: BLE001
        body = "<pre>" + _h.escape(md_text or "") + "</pre>"
    return _MD_DOC.replace("__TITLE__", _h.escape(title or "制品")).replace("__BODY__", body)


EDITABLE_ARTIFACT_KINDS = frozenset({"document", "table"})


_INVALID_SOURCE_TEXT = re.compile(r"[\x00\ud800-\udfff]")


def _source_text(value) -> bool:
    return isinstance(value, str) and _INVALID_SOURCE_TEXT.search(value) is None


def _source_row(value) -> bool:
    return isinstance(value, list) and all(_source_text(cell) for cell in value)


def valid_structured_source(content) -> bool:
    if not isinstance(content, dict):
        return False
    if content.get("kind") == "document":
        return set(content) == {"kind", "markdown"} and _source_text(content.get("markdown"))
    columns, rows = content.get("columns"), content.get("rows")
    return (content.get("kind") == "table" and set(content) == {"kind", "columns", "rows"}
            and _source_row(columns) and isinstance(rows, list)
            and all(_source_row(row) and len(row) == len(columns) for row in rows))


class ArtifactVersionConflict(ValueError):
    pass


class ArtifactStore:
    """文件系统制品库：`.vortoone/artifacts/<id>/{index.html, meta.json}`。"""

    _ID_RE = re.compile(r"[A-Za-z0-9_-]{1,80}")

    def __init__(self, repo_root: str, *, desktop_uploads: bool = False) -> None:
        self._desktop_uploads = desktop_uploads
        self.root = state_dir(repo_root) / ("desktop-artifacts" if desktop_uploads else "artifacts")
        if desktop_uploads and self.root.is_symlink():
            raise ValueError("桌面制品目录不可用")

    @classmethod
    def _safe_id(cls, artifact_id: str) -> Optional[str]:
        """制品 id 必须是单段 [A-Za-z0-9_-]，挡掉 ../、/ 等路径穿越。非法返回 None。"""
        aid = (artifact_id or "").strip()
        return aid if cls._ID_RE.fullmatch(aid) else None

    def _dir(self, artifact_id: str) -> Path:
        return self.root / artifact_id

    @contextmanager
    def _mutation(self):
        """Serialize publishing, pinning and moves of a whole artifact directory."""
        if self.root.is_symlink():
            raise OSError("制品目录不能是符号链接")
        self.root.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.root / ".lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "r+b") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise OSError("制品锁不可用")
            if os.name != "nt":
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                yield
                return
            with _WINDOWS_ARTIFACT_LOCK:
                import msvcrt
                if not os.fstat(handle.fileno()).st_size:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

    def exists(self, artifact_id: str) -> bool:
        aid = self._safe_id(artifact_id)
        return bool(aid and (self._dir(aid) / "meta.json").is_file())

    def has_uploaded_file(self, artifact_id: str, version: int) -> bool:
        """Check a private upload reference without reading its contents or following metadata links."""
        if version != 1:
            return False
        path = self._desktop_file_path(artifact_id, version)
        if path is None or (path.parent / "meta.json").is_symlink():
            return False
        meta = self.meta(artifact_id)
        return bool(meta and meta.get("id") == artifact_id and meta.get("kind") == "file")

    def publish(self, title: str, html: str, artifact_id: Optional[str] = None,
                kind: str = "html") -> dict:
        """写入/更新一个制品并返回 meta。传已存在的 id 即原地更新（version+1，保留 created_at）。

        超过体积上限（见 max_bytes，默认 16 MiB）抛 ValueError —— 对齐 CC 的大小约束。
        """
        aid = self._safe_id(artifact_id) if artifact_id else None
        if not aid:
            aid = f"{_slugify(title)}-{uuid.uuid4().hex[:6]}"
        with self._mutation():
            return self._publish_at(self._dir(aid), aid, title, html, kind)

    @contextmanager
    def _stage_upload(self):
        """Commit the complete private upload with a single directory rename."""
        if not self._desktop_uploads:
            raise ValueError("文件上传只能写入桌面制品库")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        aid = f"upload-{uuid.uuid4().hex}"
        with tempfile.TemporaryDirectory(prefix=".upload-", dir=self.root) as temporary:
            destination = Path(temporary) / aid
            yield destination, aid
            with self._mutation():
                destination.rename(self._dir(aid))

    def publish_new(self, title: str, html: str) -> dict:
        """Publish a new private HTML artifact only once all initial files are present."""
        with self._stage_upload() as (destination, aid):
            return self._publish_at(destination, aid, title, html, "html")

    def publish_file(self, title: str, name: str, content_type: str, data: bytes) -> dict:
        """Keep the owner's original file bytes separate from renderable HTML."""
        size = len(data)
        if size > max_bytes():
            raise ValueError("制品体积超过上限")
        now = datetime.now().isoformat(timespec="seconds")
        with self._stage_upload() as (destination, aid):
            meta = {
                "id": aid, "title": title, "kind": "file", "version": 1, "pinned": None,
                "created_at": now, "updated_at": now, "bytes": size,
                "name": name, "content_type": content_type, "sha256": sha256(data).hexdigest(),
                "versions": [{"v": 1, "ts": now, "bytes": size}],
            }
            destination.mkdir(mode=0o700)
            (destination / "upload.bin").write_bytes(data)
            (destination / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            return meta

    def publish_structured(self, title: str, content: dict) -> dict:
        """Create an owner-private document or table with its editable source intact."""
        if content.get("kind") not in EDITABLE_ARTIFACT_KINDS:
            raise ValueError("该类型的制品不能保存为可编辑源文件")
        with self._stage_upload() as (destination, aid):
            destination.mkdir(mode=0o700)
            return self._write_structured_version(destination, {
                "id": aid, "kind": content["kind"], "title": title, "version": 0, "pinned": None,
                "created_at": datetime.now().isoformat(timespec="seconds"), "versions": [],
            }, content, None)

    def append_structured(self, artifact_id: str, base_version: int, content: dict, note: str | None,
                          title: str | None = None) -> dict:
        """Append to the current source version without publishing a partial edit."""
        if not self._desktop_uploads:
            raise ValueError("只能编辑私有桌面制品")
        aid = self._safe_id(artifact_id)
        if aid is None:
            raise FileNotFoundError("没有这个制品")
        with self._mutation():
            directory = self._dir(aid)
            if directory.is_symlink() or (directory / "meta.json").is_symlink():
                raise OSError("制品目录不可用")
            current = self._read_meta(directory)
            if current is None:
                raise FileNotFoundError("没有这个制品")
            if current.get("kind") not in EDITABLE_ARTIFACT_KINDS or current["kind"] != content.get("kind"):
                raise ValueError("只能编辑同一类型的文档或表格")
            if current["version"] != base_version:
                raise ArtifactVersionConflict("已有更新的制品版本，请重新打开后编辑")
            if self._structured_path(aid, base_version) is None:
                raise OSError("制品当前版本不可用")
            if title is not None:
                current = {**current, "title": title}
            return self._write_structured_version(directory, current, content, note)

    @staticmethod
    def _write_structured_version(directory: Path, meta: dict, content: dict, note: str | None) -> dict:
        if not valid_structured_source(content):
            raise ValueError("制品版本格式不符")
        data = json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(data) > max_bytes():
            raise ValueError("制品体积超过上限")
        version = meta["version"] + 1
        now = datetime.now().isoformat(timespec="seconds")
        versions = [*meta["versions"], {"v": version, "ts": now, "bytes": len(data), "note": note}]
        updated = {**meta, "version": version, "updated_at": now, "bytes": len(data), "versions": versions}
        snapshot = directory / f"v{version}.json"
        with tempfile.TemporaryDirectory(prefix=".version-", dir=directory.parent) as temporary:
            staged = Path(temporary)
            (staged / "data").write_bytes(data)
            (staged / "meta").write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(staged / "data", snapshot)
            try:
                os.replace(staged / "meta", directory / "meta.json")  # commit the visible version last
            except OSError:
                snapshot.unlink()
                raise
        return updated

    def _structured_path(self, artifact_id: str, version: int) -> Path | None:
        aid = self._safe_id(artifact_id)
        if not self._desktop_uploads or aid is None:
            return None
        directory = self._dir(aid)
        path = directory / f"v{version}.json"
        return path if not directory.is_symlink() and not path.is_symlink() and path.is_file() else None

    def structured_content(self, artifact_id: str, version: int) -> dict | None:
        path = self._structured_path(artifact_id, version)
        if path is None:
            return None
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes():
                raise ValueError("制品版本不可用")
            data = handle.read(info.st_size + 1)
            if len(data) != info.st_size:
                raise ValueError("制品版本大小不符")
            content = json.loads(data)
        if not valid_structured_source(content):
            raise ValueError("制品版本格式不符")
        return content

    @staticmethod
    def _replace_upload_bytes(path: Path, data: bytes) -> None:
        fd, temporary = tempfile.mkstemp(prefix=".upload-write-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def append_uploaded_file(self, artifact_id: str, name: str, content_type: str,
                             data: bytes, note: Optional[str]) -> dict:
        """Publish a file revision only when the new immutable bytes and the manifest are ready."""
        aid = self._safe_id(artifact_id)
        if not self._desktop_uploads or aid is None:
            raise FileNotFoundError("没有这个私有制品")
        if len(data) > max_bytes():
            raise ValueError("制品体积超过上限")
        with self._mutation():
            directory = self._dir(aid)
            manifest = directory / "meta.json"
            if directory.is_symlink() or manifest.is_symlink():
                raise OSError("桌面制品目录不可用")
            meta = self._read_meta(directory)
            if meta is None:
                raise FileNotFoundError("没有这个私有制品")
            if meta.get("kind") != "file" or meta.get("content_type") != content_type:
                raise ValueError("这个制品只接受同一类型的文件")
            versions = meta["versions"]
            current = int(meta["version"])
            if not versions or int(versions[-1]["v"]) != current or self._desktop_file_path(aid, current) is None:
                raise OSError("制品版本记录无法读取")
            if current == 1:
                # The first format records the original's details only in the manifest header.
                versions = [{**versions[0], **{key: meta[key] for key in ("name", "content_type", "sha256")}}]
            version = current + 1
            now = datetime.now().isoformat(timespec="seconds")
            fields = {"name": name, "content_type": content_type, "bytes": len(data),
                      "sha256": sha256(data).hexdigest()}
            versions = [*versions, {"v": version, "ts": now, "note": note, **fields}]
            saved = {**meta, **fields, "version": version, "versions": versions, "updated_at": now}
            target = directory / self._uploaded_filename(version)
            self._replace_upload_bytes(target, data)
            try:
                self._replace_upload_bytes(manifest, json.dumps(saved, ensure_ascii=False, indent=2).encode("utf-8"))
            except (OSError, ValueError):
                target.unlink(missing_ok=True)
                raise
            return saved

    @staticmethod
    def uploaded_version(meta: dict, version: int) -> Optional[dict]:
        if meta.get("kind") != "file":
            return None
        if version == 1 and int(meta["version"]) == 1:
            # Compatibility with the first private uploads and single-version files.
            return meta
        return next((row for row in meta["versions"] if int(row["v"]) == version), None)

    @staticmethod
    def _publish_at(d: Path, aid: str, title: str, html: str, kind: str) -> dict:
        data = (html or "").encode("utf-8")
        cap = max_bytes()
        if len(data) > cap:
            raise ValueError(f"制品体积 {len(data)} 字节超过上限 {cap} 字节"
                             f"（{cap // (1024 * 1024)} MiB）")
        now = datetime.now().isoformat(timespec="seconds")
        d.mkdir(parents=True, exist_ok=True)
        meta_path = d / "meta.json"
        version, created, versions = 1, now, []
        if meta_path.is_file():
            try:
                old = json.loads(meta_path.read_text(encoding="utf-8"))
                version = int(old.get("version", 0)) + 1
                created = old.get("created_at", now)
                versions = list(old.get("versions") or [])
            except Exception:  # noqa: BLE001
                pass
        (d / f"v{version}.html").write_text(html or "", encoding="utf-8")   # 版本快照（可回看）
        (d / "index.html").write_text(html or "", encoding="utf-8")          # 当前=最新
        versions.append({"v": version, "ts": now, "bytes": len(data)})
        meta = {
            "id": aid,
            "title": (title or aid).strip(),
            "kind": kind,
            "version": version,
            "pinned": None,                  # 发布即展示最新（清除 pin）—— 对齐 CC「选给查看者看哪版」
            "created_at": created,
            "updated_at": now,
            "bytes": len(data),
            "versions": versions,
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta

    def delete(self, artifact_id: str, *, strict: bool = False) -> bool:
        """删除一个制品（整目录）。成功 True；id 非法或不存在 False。"""
        aid = self._safe_id(artifact_id)
        if not aid or not self.root.is_dir():
            return False
        with self._mutation():
            d = self._dir(aid)
            if not d.is_dir():
                return False
            shutil.rmtree(d, ignore_errors=not strict)
            return True

    def meta(self, artifact_id: str) -> Optional[dict]:
        aid = self._safe_id(artifact_id)
        if not aid:
            return None
        return self._read_meta(self._dir(aid))

    @staticmethod
    def _read_meta(directory: Path) -> Optional[dict]:
        p = directory / "meta.json"
        if not p.is_file():
            return None
        try:
            meta = json.loads(p.read_text(encoding="utf-8"))
            return meta if isinstance(meta, dict) else None
        except Exception:  # noqa: BLE001
            return None

    def _html_path(self, artifact_id: str, version: Optional[int], *, strict: bool) -> Optional[Path]:
        aid = self._safe_id(artifact_id)
        if not aid:
            return None
        d = self._dir(aid)
        if version is not None:
            try:
                vp = d / f"v{int(version)}.html"
            except (TypeError, ValueError):
                vp = None
            if vp and vp.is_file():
                return vp
            if strict:
                # 只有老格式的单版本制品没有快照；名称或版本不符不能替换成最新。
                meta = self.meta(aid)
                if not meta or meta.get("versions") or version != int(meta.get("version", 1)):
                    return None
        p = d / "index.html"
        return p if p.is_file() else None

    def html(self, artifact_id: str, version: Optional[int] = None, *,
             strict: bool = False) -> Optional[str]:
        """取制品 HTML。指定版本缺失时，旧调用回退当前，strict 调用只接受对应快照。"""
        path = self._html_path(artifact_id, version, strict=strict)
        return path.read_text(encoding="utf-8") if path is not None else None

    def has_snapshot(self, artifact_id: str, version: int) -> bool:
        """不用读取正文即可确认这个版本有对应文件。"""
        return (self._structured_path(artifact_id, version) is not None
                or self._desktop_file_path(artifact_id, version) is not None
                or self._html_path(artifact_id, version, strict=True) is not None)

    @staticmethod
    def _uploaded_filename(version: int) -> str:
        return "upload.bin" if version == 1 else f"upload-v{version}.bin"

    def _desktop_file_path(self, artifact_id: str, version: int) -> Optional[Path]:
        aid = self._safe_id(artifact_id)
        if not self._desktop_uploads or not aid or version < 1:
            return None
        directory = self._dir(aid)
        path = directory / self._uploaded_filename(version)
        return path if not directory.is_symlink() and not path.is_symlink() and path.is_file() else None

    def uploaded_file(self, artifact_id: str, version: int, size: int, digest: Optional[str]) -> Optional[bytes]:
        path = self._desktop_file_path(artifact_id, version)
        if path is None:
            return None
        if not digest:
            raise ValueError("制品文件缺少登记摘要")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size != size:
                raise ValueError("制品文件与登记大小不符")
            data = handle.read(size + 1)
        if len(data) != size:
            raise ValueError("制品文件与登记大小不符")
        if sha256(data).hexdigest() != digest:
            raise ValueError("制品文件与登记摘要不符")
        return data

    def versions(self, artifact_id: str) -> list[dict]:
        """版本清单 [{v, ts, bytes}]，升序。老 meta 无记录则按当前版本合成一条。"""
        m = self.meta(artifact_id)
        if not m:
            return []
        vs = m.get("versions")
        if vs:
            return sorted(vs, key=lambda x: int(x.get("v", 0)))
        return [{"v": int(m.get("version", 1)), "ts": m.get("updated_at", ""), "bytes": m.get("bytes", 0)}]

    def pin(self, artifact_id: str, version: Optional[int]) -> Optional[dict]:
        """把"当前"指向某个历史版本（CC：选给查看者看哪一版）。version=None 取消 pin、回到最新。

        返回更新后的 meta；id/版本非法返回 None。
        """
        aid = self._safe_id(artifact_id)
        if not aid or not self.root.is_dir():
            return None
        with self._mutation():
            return self._pin(aid, version)

    def _pin(self, aid: str, version: Optional[int]) -> Optional[dict]:
        m = self.meta(aid)
        if not m:
            return None
        d = self._dir(aid)
        if version is None:                                   # 取消 pin → 当前 = 最新
            target = int(m.get("version", 1))
            m["pinned"] = None
        else:
            try:
                target = int(version)
            except (TypeError, ValueError):
                return None
            if target not in {int(x["v"]) for x in self.versions(aid)}:
                return None
            m["pinned"] = target
        content = self.html(aid, target)
        if content is None:
            return None
        (d / "index.html").write_text(content, encoding="utf-8")   # 当前内容指向目标版本
        (d / "meta.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
        return m

    def list(self) -> list[dict]:
        """所有制品 meta，按 updated_at 倒序。"""
        if not self.root.is_dir():
            return []
        out: list[dict] = []
        for d in sorted(self.root.iterdir()):
            if d.is_dir():
                m = self.meta(d.name)
                if m:
                    out.append(m)
        out.sort(key=lambda m: m.get("updated_at", ""), reverse=True)
        return out


def artifact_url(base_url: Optional[str], artifact_id: str) -> str:
    """拼可点链接。base_url 缺省读 VORTOONE_WEB_BASE，再缺省 http://127.0.0.1:8080。"""
    base = (base_url or os.getenv("VORTOONE_WEB_BASE") or "http://127.0.0.1:8080").rstrip("/")
    return f"{base}/artifact/{artifact_id}"


def build_artifact_tools(
    repo_root: str,
    confirm: Optional[Callable[..., Awaitable[bool]]] = None,
    base_url: Optional[str] = None,
    on_published: Optional[Callable[[dict, str], None]] = None,
    confirm_delete: Optional[Callable[..., Awaitable[bool]]] = None,
) -> list:
    """构建 publish_artifact / list_artifacts / delete_artifact 工具（TUI 与 Web 共用）。

    - confirm(message, kind) -> bool：运行层使用的确认门，发布及每次更新都需要授权。
    - confirm_delete(message, kind) -> bool：可选的删除确认门，默认复用 confirm。
    - base_url：拼链接用（见 artifact_url）。
    - on_published(meta, url)：发布成功回调，供 UI 提示链接。
    """
    from src.agents.tool import PreparedToolCall, Tool

    store = ArtifactStore(repo_root)

    async def _publish(args: dict) -> object:
        title = str(args.get("title", "")).strip()
        html = str(args.get("html", "") or "")
        md = str(args.get("markdown") or args.get("md") or "")
        artifact_id = str(args.get("id") or args.get("artifact_id") or "").strip() or None
        kind = "html"
        if not html.strip() and md.strip():     # 给了 markdown → 服务端渲染成自包含整页
            html = render_markdown_doc(title, md)
            kind = "markdown"
        if not html.strip():
            return ("publish_artifact 需要 html 或 markdown（二选一；自包含、内联 CSS/JS、"
                    "不要引用外部资源，否则会被 CSP 拦截）。")
        if not title and not artifact_id:
            return "publish_artifact 需要 title（页面标题）。"
        is_update = bool(artifact_id and store.exists(artifact_id))
        # 发布和更新都会改变对外可访问的内容，每次都过确认门。
        async def apply():
            try:
                meta = store.publish(title or (artifact_id or ""), html, artifact_id, kind=kind)
            except ValueError as e:
                return f"发布失败：{e}。请精简内容，或调大环境变量 VORTOONE_ARTIFACT_MAX_BYTES。"
            url = artifact_url(base_url, meta["id"])
            if on_published is not None:
                try:
                    on_published(meta, url)
                except Exception:  # noqa: BLE001
                    pass
            verb = "更新" if meta["version"] > 1 else "发布"
            return (f"已{verb}制品「{meta['title']}」(id={meta['id']}, v{meta['version']}, {kind})。\n"
                    f"查看：{url}\n"
                    f"（要更新同一页面，下次带 id=\"{meta['id']}\" 重新调用即可，已打开的页面会自动刷新。）")
        return PreparedToolCall(apply, f"{'更新' if is_update else '发布'}制品「{title or artifact_id}」？它会被写盘并经 /artifact/ 对外提供访问。", "deliver", denied_result='用户取消了发布制品。')

    async def _list(_args: dict) -> str:
        items = store.list()
        if not items:
            return "（还没有制品。用 publish_artifact 发布一个自包含 HTML 页面。）"
        lines = [
            f"- {m['title']} (id={m['id']}, v{m['version']}, {m.get('kind', 'html')}, "
            f"{m['updated_at']}) → {artifact_url(base_url, m['id'])}"
            for m in items
        ]
        return "制品列表:\n" + "\n".join(lines)

    async def _delete(args: dict) -> object:
        aid = str(args.get("id") or args.get("artifact_id") or "").strip()
        if not aid:
            return "delete_artifact 需要 id（先 list_artifacts 看 id）。"
        if not store.exists(aid):
            return f"没有 id={aid} 的制品。"
        async def apply():
            return f"已删除制品 {aid}。" if store.delete(aid) else f"删除 {aid} 失败。"
        return PreparedToolCall(apply, f"删除制品「{aid}」？此操作不可逆。", "irreversible", denied_result=f'用户取消了删除 {aid}。')

    return [
        Tool.prepared(
            "publish_artifact",
            "把自包含网页内容发布成可分享、随会话实时更新的网页（制品）；写操作，仅 build。"
            "传 html 或 markdown（二选一，markdown 会渲染成整页）。传相同 id 则原地更新、已打开的页面会自动刷新。"
            "适合：带注释的 PR 走查、数据看板、方案对比、交互控件（滑块/开关）、迁移/排查进度清单、报告。",
            {"title": "页面标题", "html": "完整自包含 HTML（内联 CSS/JS，勿引用外部资源）",
             "markdown": "或：markdown 文本（与 html 二选一）", "id": "可选；更新已有制品时传它的 id"},
            _publish, read_only=False,
         operation_kind='deliver', confirm=confirm),
        Tool(
            "list_artifacts", "列出已发布的制品（标题/id/版本/类型/链接）",
            {}, _list, read_only=True,
         operation_kind='read', confirm=confirm),
        Tool.prepared(
            "delete_artifact", "删除一个制品；写操作，需确认，仅 build",
            {"id": "要删除的制品 id"}, _delete, read_only=False,
         operation_kind='irreversible', confirm=confirm_delete or confirm),
    ]
