"""本机 Wiki 页面、可见范围和版本。页面内容只给有权限的人阅读。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import unicodedata
import uuid
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from src.agents.principal import Principal
from src.utils.state_dir import ensure_state_gitignore, state_dir

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    id TEXT PRIMARY KEY,
    snapshot TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS slugs (
    slug TEXT PRIMARY KEY,
    page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS revisions (
    page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    body TEXT NOT NULL,
    at TEXT NOT NULL,
    author TEXT NOT NULL,
    note TEXT,
    PRIMARY KEY (page_id, version)
);
CREATE TABLE IF NOT EXISTS message_sources (
    page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    channel_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    block TEXT NOT NULL,
    excerpt TEXT NOT NULL,
    at TEXT NOT NULL,
    PRIMARY KEY (page_id, channel_id, message_id, content_hash)
);
CREATE TABLE IF NOT EXISTS import_batches (
    actor_kind TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    import_id TEXT NOT NULL,
    digest TEXT NOT NULL,
    receipt TEXT NOT NULL,
    PRIMARY KEY (actor_kind, actor_id, import_id, digest)
);
"""
_TRASH_WINDOW = timedelta(days=30)
_PAGE_LINK = re.compile(r"(?<!!)\[\[([^\[\]\n]+)\]\]")
MAX_BODY = 262144
_IMPORT_NOTE_LIMIT = 200
_IMPORT_BODY_LIMIT = 200_000


class WikiError(ValueError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class WikiReader:
    actor: Principal
    known_people: frozenset[str]
    can_write: bool
    can_manage: bool
    source_names: dict[str, str]

    @property
    def author(self) -> dict[str, str]:
        return {"type": "person", "id": self.actor.id}

    def sees(self, page: dict) -> bool:
        audience = page["audience"]
        return self.actor.is_owner or audience is None or self.actor.id in audience["userIds"]

    def edits(self, page: dict) -> bool:
        return self.can_write and (not page["locked"] or self.can_manage)


def _fail(code: str, message: str, *, status: int = 400) -> None:
    raise WikiError(status, code, message)


def _missing() -> None:
    _fail("not_found", "没有这个 Wiki 页面", status=404)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _text(value: Any, maximum: int, label: str, *, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or "\0" in value:
        _fail("invalid", f"{label}无效或超过 {maximum} 字")
    if required and not value.strip():
        _fail("invalid", f"{label}不能为空")
    return value


def _title(value: Any) -> str:
    title = _text(value, 200, "标题", required=True).strip()
    if any(ord(char) < 32 for char in title):
        _fail("invalid", "标题不能包含控制字符")
    return title


def _folder(value: Any) -> str:
    folder = _text(value, 500, "文件夹").strip()
    if not folder:
        return ""
    parts = [part.strip() for part in folder.split("/")]
    if any(part in ("", ".", "..") or "\\" in part or any(ord(char) < 32 for char in part) for part in parts):
        _fail("invalid", "文件夹名称无效")
    return "/".join(parts)


def _tags(value: Any) -> list[str]:
    if not isinstance(value, list) or len(value) > 32:
        _fail("invalid", "标签最多 32 个")
    return list(dict.fromkeys(_text(tag, 80, "标签", required=True).strip() for tag in value))


def _fold(title: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFD", title.lower())
                   if unicodedata.category(char) != "Mn").strip()


def _slug(title: str, page_id: str) -> str:
    leaf = title.rsplit("/", 1)[-1].strip() or title
    slug = re.sub(r"[^\w]+", "-", _fold(leaf).replace("_", "-")).strip("-")
    return slug or f"page-{page_id}"


def _page_slug(page: dict) -> str:
    slug = _slug(page["title"], page["id"])
    return slug if page["audience"] is None else f"{slug}-{page['id'][:8]}"


def _recently_deleted(page: dict) -> bool:
    stamp = page.get("deletedAt")
    return stamp is not None and datetime.fromisoformat(stamp.replace("Z", "+00:00")) >= (
        datetime.now(timezone.utc) - _TRASH_WINDOW
    )


def _ids(value: Any) -> list[str]:
    if (not isinstance(value, list) or not 1 <= len(value) <= 500
            or any(not isinstance(item, str) or not item for item in value)):
        _fail("invalid", "请选择 1 到 500 个 Wiki 页面")
    return list(dict.fromkeys(value))


def _audience(choice: Any, reader: WikiReader) -> dict | None:
    if choice is None or choice == {"mode": "everyone"}:
        return None
    if not isinstance(choice, dict) or set(choice) != {"mode", "userIds"} or choice["mode"] != "people":
        _fail("invalid", "可见范围只能是所有人或指定成员")
    people = choice["userIds"]
    if (not isinstance(people, list) or not people or len(people) > 100
            or any(not isinstance(person, str) or person not in reader.known_people for person in people)):
        _fail("invalid", "可见范围必须指定在册成员")
    if not reader.actor.is_owner and reader.actor.id not in people:
        _fail("forbidden", "新建仅限指定成员的页面须包含你自己", status=403)
    return {"roleIds": [], "userIds": list(dict.fromkeys(people))}


def _fields(data: dict, allowed: set[str]) -> None:
    if data.keys() - allowed:
        _fail("invalid", "不认识的 Wiki 字段")


def _import_digest(value: Any) -> str:
    try:
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    except ValueError:
        _fail("invalid", "Markdown 导入包含无效字符")


def _import_note(note: Any, folder: str) -> dict | str:
    if not isinstance(note, dict):
        _fail("invalid", "每条 Markdown 笔记必须是对象")
    _fields(note, {"title", "folder", "body", "tags", "aliases", "attachments"})
    if note.get("aliases", []) != [] or note.get("attachments", {}) != {}:
        _fail("wiki_import_unsupported", "Markdown 导入暂不支持笔记别名或 Drive 附件", status=409)
    if not isinstance(note.get("title"), str) or not isinstance(note.get("body"), str):
        _fail("invalid", "Markdown 笔记必须提供标题和正文")
    destination = _folder("/".join(filter(None, (folder, _folder(note.get("folder", ""))))))
    tags = _tags(note.get("tags", []))
    try:
        title = _title(note["title"])
    except WikiError:
        return "title"
    if len(note["body"]) > _IMPORT_BODY_LIMIT:
        return "too_long"
    return {"title": title, "folder": destination, "tags": tags, "body": _text(note["body"], MAX_BODY, "正文")}


class Wiki:
    def __init__(self, root: str, reader: WikiReader):
        self.root = root
        self.path = state_dir(root) / "wiki" / "pages.sqlite3"
        self.reader = reader

    @contextmanager
    def _database(self, *, write: bool = False):
        if self.path.parent.is_symlink() or self.path.is_symlink():
            raise OSError("Wiki 记录不能是符号链接")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
            ensure_state_gitignore(self.root)
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.executescript(_SCHEMA)
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn

    def _require_writer(self) -> None:
        if not self.reader.can_write:
            _fail("forbidden", "没有编辑 Wiki 的权限", status=403)

    def _page(self, conn, slug: str, *, edit: bool = False) -> dict:
        row = conn.execute(
            "SELECT snapshot FROM slugs JOIN pages ON pages.id = slugs.page_id WHERE slugs.slug = ?", (slug,)
        ).fetchone()
        if row is None:
            _missing()
        page = json.loads(row["snapshot"])
        if page.get("deletedAt") or not self.reader.sees(page):
            _missing()
        if edit and not self.reader.edits(page):
            _fail("forbidden", "没有编辑这张 Wiki 页面的权限", status=403)
        return page

    @staticmethod
    def _all(conn) -> list[dict]:
        return [json.loads(row["snapshot"]) for row in conn.execute("SELECT snapshot FROM pages")]

    @staticmethod
    def _summary(page: dict) -> dict:
        hidden = {"body", "createdBy", "createdAt"}
        return {**{key: item for key, item in page.items() if key not in hidden},
                "excerpt": " ".join(page["body"].split())[:200], "agentContext": False}

    def _detail(self, conn, page: dict) -> dict:
        authors = list(dict.fromkeys(row["author"] for row in conn.execute(
            "SELECT author FROM revisions WHERE page_id = ? ORDER BY version DESC", (page["id"],)
        )))
        pages = self._visible_pages(conn)
        titles = {visible["slug"]: visible["title"] for visible in pages}
        backlinks = [{"kind": "page", "slug": source, "title": titles[source]}
                     for source, target in self._links(conn, pages) if target == page["slug"]]
        for source in conn.execute(
            "SELECT DISTINCT channel_id, message_id, excerpt, at FROM message_sources"
            " WHERE page_id = ? AND instr(?, block) > 0 ORDER BY at DESC, message_id",
            (page["id"], page["body"]),
        ):
            if source["channel_id"] in self.reader.source_names:
                backlinks.append({"kind": "message", "channelId": source["channel_id"],
                                  "messageId": source["message_id"],
                                  "channelName": self.reader.source_names[source["channel_id"]],
                                  "excerpt": source["excerpt"], "at": source["at"]})
        return {**self._summary(page), "body": page["body"], "createdAt": page["createdAt"],
                "createdBy": page["createdBy"], "authors": [json.loads(author) for author in authors],
                "backlinks": backlinks, "canEdit": self.reader.edits(page)}

    @staticmethod
    def _attach_slug(conn, page: dict) -> None:
        existing = conn.execute("SELECT page_id FROM slugs WHERE slug = ?", (page["slug"],)).fetchone()
        if existing is None:
            conn.execute("INSERT INTO slugs VALUES (?, ?)", (page["slug"], page["id"]))
        elif existing["page_id"] != page["id"]:
            _fail("slug_taken", "这个 Wiki 页面标题已在使用", status=409)

    def _save(self, conn, page: dict, note: str | None = None) -> None:
        page["version"] += 1
        page["updatedAt"] = _now()
        page["updatedBy"] = self.reader.author
        conn.execute("UPDATE pages SET snapshot = ? WHERE id = ?", (json.dumps(page), page["id"]))
        self._attach_slug(conn, page)
        conn.execute("INSERT INTO revisions VALUES (?, ?, ?, ?, ?, ?)",
                     (page["id"], page["version"], page["body"], page["updatedAt"],
                      json.dumps(self.reader.author), note))

    def listing(self, *, deleted: bool = False) -> dict:
        with self._database() as conn:
            pages = [self._summary(page) for page in self._all(conn) if self.reader.sees(page)
                     and (_recently_deleted(page) if deleted else not page.get("deletedAt"))]
        sort_key = "deletedAt" if deleted else "updatedAt"
        return {"pages": sorted(pages, key=lambda page: page[sort_key], reverse=True)}

    def _visible_pages(self, conn) -> list[dict]:
        pages = (page for page in self._all(conn) if not page.get("deletedAt") and self.reader.sees(page))
        return sorted(pages, key=lambda page: page["updatedAt"], reverse=True)

    def search(self, query: str) -> dict:
        query = _text(query, 200, "搜索词").strip().lower()
        with self._database() as conn:
            pages = self._visible_pages(conn)
        titles = [(page, page["title"].lower()) for page in pages]
        matches = [(page, title) for page, title in titles if query in title]
        matches.sort(key=lambda match: not match[1].startswith(query))
        return {"pages": [self._summary(page) for page, _ in matches]}

    @staticmethod
    def _links(conn, pages):
        """Resolve page links against the same visible pages for the graph and backlinks."""
        by_id = {page["id"]: page["slug"] for page in pages}
        aliases = {row["slug"]: by_id[row["page_id"]] for row in conn.execute("SELECT slug, page_id FROM slugs")
                   if row["page_id"] in by_id}
        titles, paths = {}, {}
        for page in pages:
            title, folder, slug = page["title"], page["folder"], page["slug"]
            titles.setdefault(_fold(title), slug)
            if folder:
                paths.setdefault(_fold(f"{folder}/{title}"), slug)

        for page in pages:
            source = page["slug"]
            targets = set()
            for match in _PAGE_LINK.finditer(page["body"]):
                title = re.split(r"[|#]", match[1], maxsplit=1)[0].strip()
                key = _fold(title)
                target = titles.get(key) or paths.get(key) or aliases.get(_slug(title, ""))
                if target is not None and target != source and target not in targets:
                    targets.add(target)
                    yield source, target

    def graph(self) -> dict:
        with self._database() as conn:
            pages = self._visible_pages(conn)
            return {
                "nodes": [{key: page[key] for key in ("slug", "title", "folder", "tags")} for page in pages],
                "edges": [{"from": source, "to": target} for source, target in self._links(conn, pages)],
            }

    def get(self, slug: str) -> dict:
        with self._database() as conn:
            return self._detail(conn, self._page(conn, slug))

    def _insert(
        self, conn, *, title: str, folder: str, tags: list[str], body: str,
        audience: dict | None = None, provenance: dict | None = None,
    ) -> dict:
        page_id = uuid.uuid4().hex
        page = {"id": page_id, "title": title, "folder": folder, "tags": tags, "body": body,
                "audience": audience, "locked": False, "pinned": False,
                "version": 0, "createdAt": _now(), "createdBy": self.reader.author, "deletedAt": None}
        page["slug"] = _page_slug(page)
        if provenance is not None:
            page.update(provenance)
            # The returned URL must not depend on slugs held by hidden or trashed pages.
            page["slug"] = f"{page['slug']}-{page_id}"
        conn.execute("INSERT INTO pages VALUES (?, ?)", (page_id, "{}"))
        self._save(conn, page)
        return page

    def create(self, data: dict) -> dict:
        self._require_writer()
        _fields(data, {"title", "folder", "tags", "body", "audience"})
        fields = {"title": _title(data.get("title")), "folder": _folder(data.get("folder", "")),
                  "tags": _tags(data.get("tags", [])), "body": _text(data.get("body"), MAX_BODY, "正文"),
                  "audience": _audience(data.get("audience"), self.reader)}
        with self._database(write=True) as conn:
            return self._detail(conn, self._insert(conn, **fields))

    def import_notes(self, data: dict) -> dict:
        """Import only Markdown values already read by the caller, without fetching resources."""
        self._require_writer()
        _fields(data, {"source", "notes", "folder", "onConflict", "importId"})
        source, notes, conflict = data.get("source"), data.get("notes"), data.get("onConflict")
        if not isinstance(source, dict) or set(source) != {"type", "name"}:
            _fail("invalid", "必须提供 Markdown 文件来源类型和名称")
        source_type = source["type"]
        if source_type not in ("files", "obsidian", "github"):
            _fail("wiki_import_unsupported", "目前支持 Markdown 文件、Obsidian 和 GitHub 笔记", status=409)
        if source_type == "github" and not self.reader.actor.is_owner:
            _fail("forbidden", "GitHub 导入需要工作区所有者", status=403)
        source_name = _text(source["name"], 300, "来源名称", required=True).strip()
        if conflict not in ("keep_both", "skip") or not isinstance(notes, list) or not 1 <= len(notes) <= _IMPORT_NOTE_LIMIT:
            _fail("invalid", "请提供 1 到 200 条笔记和有效的同名处理方式")
        import_id = _text(data["importId"], 128, "导入编号", required=True) if "importId" in data else None
        folder = _folder(data.get("folder", ""))
        prepared = [_import_note(note, folder) for note in notes]
        actor = self.reader.actor
        digest = _import_digest(data)
        batch = (actor.kind, actor.id, import_id, digest)
        group_id = _import_digest([actor.kind, actor.id, import_id]) if import_id is not None else uuid.uuid4().hex
        provenance = {"sourceType": source_type, "sourceName": source_name, "importId": group_id}
        with self._database(write=True) as conn:
            previous = None if import_id is None else conn.execute(
                "SELECT receipt FROM import_batches WHERE actor_kind = ? AND actor_id = ? AND import_id = ? AND digest = ?",
                batch,
            ).fetchone()
            if previous is None:
                receipt = self._import_batch(conn, prepared, conflict, provenance)
                if import_id is not None:
                    conn.execute("INSERT INTO import_batches VALUES (?, ?, ?, ?, ?)", (*batch, json.dumps(receipt)))
            else:
                receipt = json.loads(previous["receipt"])
            return self._import_result(conn, notes, receipt)

    def _import_batch(self, conn, notes, conflict, provenance):
        pages = (json.loads(row["snapshot"]) for row in conn.execute("SELECT snapshot FROM pages"))
        visible = {_fold(page["title"]) for page in pages
                   if not page.get("deletedAt") and self.reader.sees(page)}
        receipt = {"created": [], "skipped": [], "failed": []}
        for index, note in enumerate(notes):
            if isinstance(note, str):
                receipt["failed"].append((index, note))
                continue
            title = note["title"]
            if _fold(title) in visible and conflict == "skip":
                receipt["skipped"].append(index)
                continue
            count = 1
            while _fold(note["title"]) in visible:
                count += 1
                suffix = f" ({count})"
                note["title"] = title[:200 - len(suffix)].rstrip() + suffix
            visible.add(_fold(note["title"]))
            receipt["created"].append(self._insert(conn, **note, provenance=provenance)["id"])
        return receipt

    def _import_result(self, conn, notes, receipt):
        created = []
        for page_id in receipt["created"]:
            row = conn.execute("SELECT snapshot FROM pages WHERE id = ?", (page_id,)).fetchone()
            if row is not None:
                page = json.loads(row["snapshot"])
                if not page.get("deletedAt") and self.reader.sees(page):
                    created.append(self._summary(page))
        return {"created": created, "skipped": [notes[index]["title"] for index in receipt["skipped"]],
                "failed": [{"title": notes[index]["title"], "reason": reason} for index, reason in receipt["failed"]]}

    def update(self, slug: str, data: dict) -> dict:
        _fields(data, {"body", "baseVersion", "title", "folder", "tags", "note"})
        version = data.get("baseVersion")
        if type(version) is not int or version < 1:
            _fail("invalid", "必须提供编辑时的页面版本")
        note = _text(data["note"], 500, "版本说明") if "note" in data else None
        with self._database(write=True) as conn:
            page = self._page(conn, slug, edit=True)
            if page["version"] != version:
                _fail("version_conflict", "页面已经更新，请重新读取后编辑", status=409)
            page["body"] = _text(data.get("body"), MAX_BODY, "正文")
            return self._change(conn, page, data, note)

    def append(self, slug: str, text: str, source: dict) -> dict:
        """Append one attributed selection and its revision in a single page transaction."""
        block = _text(text, MAX_BODY, "摘录", required=True).rstrip("\n") + "\n"
        digest = hashlib.sha256(block.encode("utf-8")).hexdigest()
        with self._database(write=True) as conn:
            page = self._page(conn, slug, edit=True)
            key = (page["id"], source["channelId"], source["messageId"], digest)
            recorded = conn.execute(
                "SELECT 1 FROM message_sources"
                " WHERE page_id = ? AND channel_id = ? AND message_id = ? AND content_hash = ?", key
            ).fetchone()
            body = page["body"]
            if recorded is not None and block in body:
                return self._detail(conn, page)
            if body and not body.endswith("\n\n"):
                body += "\n" if body.endswith("\n") else "\n\n"
            page["body"] = _text(body + block, MAX_BODY, "正文")
            conn.execute(
                "INSERT OR IGNORE INTO message_sources VALUES (?, ?, ?, ?, ?, ?, ?)",
                (*key, block, source["excerpt"], source["at"]),
            )
            self._save(conn, page)
            return self._detail(conn, page)

    def _change(self, conn, page: dict, data: dict, note: str | None = None) -> dict:
        if "title" in data:
            page["title"] = _title(data["title"])
            page["slug"] = _page_slug(page)
        if "folder" in data:
            page["folder"] = _folder(data["folder"])
        if "tags" in data:
            page["tags"] = _tags(data["tags"])
        self._save(conn, page, note)
        return self._detail(conn, page)

    def change(self, slug: str, attribute: str, value: Any) -> dict:
        with self._database(write=True) as conn:
            page = self._page(conn, slug, edit=True)
            if attribute in ("locked", "pinned"):
                if type(value) is not bool:
                    _fail("invalid", "必须指定开启或关闭")
                if attribute == "locked" and not self.reader.can_manage:
                    _fail("forbidden", "只有管理设置的成员能锁定页面", status=403)
                page[attribute] = value
                self._save(conn, page)
                return self._detail(conn, page)
            return self._change(conn, page, {attribute: value})

    def remove(self, slug: str) -> None:
        with self._database(write=True) as conn:
            page = self._page(conn, slug, edit=True)
            page["deletedAt"] = _now()
            self._save(conn, page)

    def remove_many(self, data: dict) -> dict:
        self._require_writer()
        if set(data) == {"ids"}:
            ids = _ids(data["ids"])
        elif set(data) == {"folder"}:
            ids = None
            folder = _folder(data["folder"])
        else:
            _fail("wiki_import_unavailable", "当前只支持按页面或文件夹移入回收站", status=409)
        with self._database(write=True) as conn:
            pages = [page for page in self._all(conn) if not page.get("deletedAt") and self.reader.sees(page)
                     and (page["id"] in ids if ids is not None else
                          not folder or page["folder"] == folder or page["folder"].startswith(folder + "/"))]
            changed = 0
            for page in pages:
                if self.reader.edits(page):
                    page["deletedAt"] = _now()
                    self._save(conn, page)
                    changed += 1
            return {"deleted": changed, "kept": (len(ids) if ids is not None else len(pages)) - changed}

    def restore(self, ids: Any) -> dict:
        self._require_writer()
        selected = _ids(ids)
        with self._database(write=True) as conn:
            restored = 0
            failed = []
            for page in self._all(conn):
                if page["id"] not in selected or not self.reader.sees(page) or not _recently_deleted(page):
                    continue
                if not self.reader.edits(page):
                    failed.append({"id": page["id"], "title": page["title"], "code": "forbidden"})
                    continue
                page["deletedAt"] = None
                self._save(conn, page)
                restored += 1
            return {"restored": restored, "kept": len(selected) - restored, "failed": failed}

    def purge(self, data: dict) -> dict:
        if not self.reader.actor.is_owner:
            _fail("forbidden", "只有所有者能永久删除页面", status=403)
        _fields(data, {"ids"})
        selected = _ids(data["ids"]) if "ids" in data else None
        with self._database(write=True) as conn:
            targets = [page["id"] for page in self._all(conn) if page.get("deletedAt")
                       and (selected is None or page["id"] in selected)]
            conn.executemany("DELETE FROM pages WHERE id = ?", [(page_id,) for page_id in targets])
            return {"purged": len(targets), "kept": 0 if selected is None else len(selected) - len(targets)}

    def history(self, slug: str) -> dict:
        with self._database() as conn:
            page = self._page(conn, slug)
            versions = [{"version": row["version"], "at": row["at"], "author": json.loads(row["author"]),
                         "note": row["note"]} for row in conn.execute(
                "SELECT version, at, author, note FROM revisions WHERE page_id = ? ORDER BY version DESC",
                (page["id"],),
            )]
        return {"versions": versions}

    def version(self, slug: str, version: int) -> dict:
        with self._database() as conn:
            page = self._page(conn, slug)
            if not 1 <= version <= page["version"]:
                _fail("not_found", "没有这个页面版本", status=404)
            row = conn.execute("SELECT body FROM revisions WHERE page_id = ? AND version = ?",
                               (page["id"], version)).fetchone()
            if row is None:
                _fail("not_found", "没有这个页面版本", status=404)
            return {"version": version, "body": row["body"]}

    def context(self, slug: str, on: bool) -> dict:
        with self._database() as conn:
            page = self._page(conn, slug, edit=True)
            if on:
                _fail("wiki_context_unavailable", "当前 Wiki 页面不会进入智能体上下文", status=409)
            return self._detail(conn, page)
