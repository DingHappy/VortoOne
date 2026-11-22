"""Signed-in feedback stored in the current instance's local inbox."""

from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
from uuid import UUID

from src.utils.state_dir import ensure_state_gitignore, state_dir

MAX_TEXT = 4000
MAX_BODY = MAX_TEXT * 6 + 1024

_SCHEMA = """CREATE TABLE IF NOT EXISTS feedback (
    seq INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    author TEXT NOT NULL CHECK(author IN ('owner', 'member')),
    actor_id TEXT NOT NULL
)"""


class FeedbackConflict(ValueError):
    """A client request ID is already bound to another submission."""


def parse_submission(body):
    if not isinstance(body, dict) or set(body) != {"id", "orgId", "text"}:
        raise ValueError("只接受工作区、请求编号和文字反馈")
    if not all(isinstance(value, str) for value in body.values()):
        raise ValueError("反馈字段必须是文字")
    identifier = UUID(body["id"])
    if identifier.version != 4 or str(identifier) != body["id"]:
        raise ValueError("反馈请求编号无效")
    text = body["text"].strip()
    if not text or len(body["text"]) > MAX_TEXT or "\0" in text:
        raise ValueError("反馈内容不能为空或超过 4000 字符")
    # JSON can carry an unpaired surrogate as an escape. SQLite requires UTF-8.
    text.encode("utf-8")
    return str(identifier), body["orgId"], text


def _db_path(root: str, *, write: bool) -> Path:
    state = state_dir(root)
    directory = state / "feedback"
    path = directory / "inbox.sqlite3"
    if directory.is_symlink() or path.is_symlink():
        raise OSError("反馈记录不能是符号链接")
    if write:
        ensure_state_gitignore(root)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        except FileExistsError:
            if path.is_symlink() or not path.is_file():
                raise
        else:
            os.close(fd)
    return path


def submit(root: str, identifier: str, text: str, actor) -> None:
    path = _db_path(root, write=True)
    with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True)) as db, db:
        db.execute("PRAGMA synchronous = FULL")
        db.execute("BEGIN IMMEDIATE")
        db.execute(_SCHEMA)
        created_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        added = db.execute(
            "INSERT INTO feedback (id, text, created_at, author, actor_id) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO NOTHING",
            (identifier, text, created_at, actor.kind, actor.id),
        )
        if added.rowcount == 0:
            old = db.execute("SELECT text, author, actor_id FROM feedback WHERE id = ?", (identifier,)).fetchone()
            if old != (text, actor.kind, actor.id):
                raise FeedbackConflict("反馈请求编号已经使用")


def listing(root: str, cursor: int | None, limit: int) -> dict:
    path = _db_path(root, write=False)
    empty = {"items": [], "nextCursor": None}
    if not path.exists():
        return empty
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        # A failed or concurrent first write can leave the empty database file in place.
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='feedback'").fetchone() is None:
            return empty
        clause = "" if cursor is None else "WHERE seq < ?"
        arguments = (limit + 1,) if cursor is None else (cursor, limit + 1)
        rows = db.execute(
            f"SELECT seq, id, text, created_at, author FROM feedback {clause} ORDER BY seq DESC LIMIT ?",
            arguments,
        ).fetchall()
    return {
        "items": [
            {"id": identifier, "text": text, "createdAt": created_at, "author": author}
            for _, identifier, text, created_at, author in rows[:limit]
        ],
        "nextCursor": rows[limit - 1][0] if len(rows) > limit else None,
    }
