"""Durable human messages, pins, and read markers for team channels."""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.workflow.members.registry import Damaged, Missing

OWNER_KEY = "owner"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    sequence INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    channel TEXT NOT NULL,
    author TEXT NOT NULL,
    body TEXT NOT NULL,
    created TEXT NOT NULL,
    client TEXT,
    thread TEXT,
    reply_to TEXT,
    opened INTEGER NOT NULL DEFAULT 0,
    media TEXT,
    UNIQUE (channel, author, client)
);
CREATE INDEX IF NOT EXISTS messages_in_channel ON messages(channel, sequence);
CREATE INDEX IF NOT EXISTS messages_in_thread ON messages(channel, thread, created);
CREATE TABLE IF NOT EXISTS pins (
    message TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
    author TEXT NOT NULL,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS read_markers (
    channel TEXT NOT NULL,
    person TEXT NOT NULL,
    created TEXT NOT NULL,
    PRIMARY KEY(channel, person)
);
"""


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _before(value: str) -> str:
    moment = _parse_stamp(value)
    if moment.tzinfo is None:
        raise ValueError("消息时间需要时区")
    return _stamp(moment)


def _parse_stamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


_GIF_FIELDS = {"kind", "provider", "id", "title", "width", "height"}


def check_media(media):
    """消息附带的图片（目前只有 GIF）。查找和尺寸由上层向服务商核实后填好，这里只拒绝形状不对的。"""
    if media is None:
        return None
    if (not isinstance(media, dict) or set(media) != _GIF_FIELDS or media["kind"] != "gif"
            or media["provider"] != "giphy" or not isinstance(media["id"], str) or not media["id"]
            or len(media["id"]) > 64 or not isinstance(media["title"], str) or len(media["title"]) > 300
            or any(type(media[key]) is not int or not 0 < media[key] <= 10000 for key in ("width", "height"))):
        raise ValueError("消息附带的图片无效")
    return dict(media)


def _migrate(conn) -> None:
    """建表之前的频道记录没有 media 列：原地补上，老消息的 media 为空。"""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
    if "media" not in columns:
        conn.execute("ALTER TABLE messages ADD COLUMN media TEXT")


class ChannelHistory:
    def __init__(self, directory: Path, owner_id: str):
        self.path = directory / "channel-messages.sqlite3"
        self.owner_id = owner_id

    def person_id(self, key: str) -> str:
        return self.owner_id if key == OWNER_KEY else key

    def message(self, row, viewer) -> dict:
        result = {"id": row["id"], "channelId": row["channel"], "threadId": row["thread"],
                  "author": {"type": "person", "id": self.person_id(row["author"])}, "body": row["body"],
                  "mentionedAgentIds": [], "agentRunId": None,
                  "createdAt": row["created"], "sequence": row["sequence"]}
        meta = {}
        if row["reply_to"]:
            meta["replyTo"] = row["reply_to"]
        media = row["media"] if "media" in row.keys() else None
        if media:
            meta["media"] = json.loads(media)
        if meta:
            result["meta"] = meta
        if viewer == row["author"] and row["client"] is not None:
            result["clientId"] = row["client"]
        if "pinned" in row.keys() and row["pinned"]:
            result["pinned"] = True
        return result

    @contextmanager
    def database(self, *, write=False):
        """Callers hold the member registry lock while checking access and writing."""
        if self.path.is_symlink():
            raise Damaged("频道记录不能是符号链接")
        if not write and not self.path.exists():
            yield None
            return
        if write and not self.path.exists():
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            os.close(fd)
        try:
            mode = "rw" if write else "ro"
            with closing(sqlite3.connect(self.path.resolve().as_uri() + f"?mode={mode}", uri=True)) as conn, conn:
                conn.row_factory = sqlite3.Row
                if write:
                    conn.execute("PRAGMA foreign_keys = ON")
                    conn.executescript("BEGIN;" + _SCHEMA + "COMMIT;")
                    _migrate(conn)
                yield conn
        except sqlite3.Error as error:
            raise Damaged("频道消息记录不可用") from error

    @staticmethod
    def find(conn, channel: str, message_id: str):
        row = conn.execute("SELECT m.*, p.message IS NOT NULL AS pinned FROM messages m "
                           "LEFT JOIN pins p ON p.message = m.id WHERE m.channel = ? AND m.id = ?",
                           (channel, message_id)).fetchone() if conn is not None else None
        if row is None:
            raise Missing("没有这条消息")
        return row

    def append(self, channel, actor, body, client, thread, reply_to, opened, media=None):
        media = check_media(media)
        stored = json.dumps(media, ensure_ascii=False, sort_keys=True) if media else None
        with self.database(write=True) as conn:
            if client is not None:
                existing = conn.execute("SELECT * FROM messages WHERE channel = ? AND author = ? AND client = ?",
                                        (channel, actor, client)).fetchone()
                if existing is not None:
                    if (existing["body"], existing["thread"], existing["reply_to"], existing["opened"],
                            existing["media"]) != (body, thread, reply_to, int(opened), stored):
                        raise ValueError("同一条消息的重试内容发生变化")
                    return self.message(existing, actor)
            if thread is not None and self.find(conn, channel, thread)["thread"] is not None:
                raise ValueError("线程需要使用频道中的原消息")
            if reply_to is not None:
                self.find(conn, channel, reply_to)
            latest = conn.execute("SELECT created FROM messages WHERE channel = ? ORDER BY sequence DESC LIMIT 1",
                                  (channel,)).fetchone()
            now = datetime.now(timezone.utc)
            if latest is not None:
                now = max(now, _parse_stamp(latest[0]) + timedelta(microseconds=1))
            message_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO messages(id, channel, author, body, created, client, thread, reply_to, opened, media) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (message_id, channel, actor, body, _stamp(now), client, thread, reply_to, int(opened), stored))
            return self.message(self.find(conn, channel, message_id), actor)

    def messages(self, channel, viewer, *, before="", limit=100, thread=None, after=None):
        with self.database() as conn:
            if thread:
                root = self.find(conn, channel, thread)
                if root["thread"] is not None:
                    raise Missing("没有这个线程")
            if conn is None:
                return []
            if after is not None:
                # A live watcher follows the committed sequence, including replies.
                rows = conn.execute(
                    "SELECT m.*, p.message IS NOT NULL AS pinned FROM messages m "
                    "LEFT JOIN pins p ON p.message = m.id WHERE channel = ? AND sequence > ? ORDER BY sequence LIMIT ?",
                    (channel, after, limit)).fetchall()
            else:
                cutoff = _before(before) if before else "9999"
                rows = conn.execute(
                    "SELECT m.*, p.message IS NOT NULL AS pinned FROM messages m LEFT JOIN pins p ON p.message = m.id "
                    "WHERE m.channel = ? AND m.thread IS ? AND m.created < ? "
                    "ORDER BY sequence DESC LIMIT ?", (channel, thread, cutoff, limit)
                ).fetchall()[::-1]
                if thread and root["created"] < cutoff:
                    rows.insert(0, root)
            return [self.message(row, viewer) for row in rows]

    def latest_sequence(self, channel):
        with self.database() as conn:
            if conn is None:
                return 0
            return conn.execute("SELECT coalesce(max(sequence), 0) FROM messages WHERE channel = ?",
                                (channel,)).fetchone()[0]

    @staticmethod
    def find_by_id(conn, message_id):
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone() if conn is not None else None
        if row is None:
            raise Missing("没有这条消息")
        return row

    def thread_summary(self, conn, channel, root, title):
        def actor(row):
            return {"type": "person", "id": self.person_id(row["author"])}

        reply_count, opened = conn.execute(
            "SELECT count(*), min(CASE WHEN opened = 1 THEN created END) FROM messages "
            "WHERE channel = ? AND thread = ?", (channel, root["id"])).fetchone()
        latest = conn.execute("SELECT * FROM messages WHERE channel = ? AND thread = ? ORDER BY created DESC LIMIT 1",
                              (channel, root["id"])).fetchone()
        authors = conn.execute("SELECT DISTINCT author FROM messages WHERE channel = ? AND thread = ? LIMIT 32",
                               (channel, root["id"])).fetchall()
        participants = dict.fromkeys([root["author"], *(row["author"] for row in authors)])
        return {"id": root["id"], "channelId": channel, "channelName": title, "channelKind": "channel",
                "title": None, "rootText": root["body"], "rootAuthor": actor(root), "state": "open",
                "createdBy": self.person_id(root["author"]), "closedBy": None, "closedAt": None, "archivedBy": None,
                "archivedAt": None, "context": None, "replyCount": reply_count, "createdAt": root["created"],
                "lastActivityAt": latest["created"] if latest else root["created"],
                "lastMessage": ({"author": actor(latest), "excerpt": latest["body"][:140],
                                 "createdAt": latest["created"]} if latest else None),
                "participants": [{"type": "person", "id": self.person_id(person)} for person in participants],
                "working": False, "needsYou": False, "openedByReplyAt": opened,
                "suggestedResolved": False, "canManage": False}

    def threads(self, channel, title, *, limit):
        with self.database() as conn:
            if conn is None:
                return []
            roots = conn.execute(
                "SELECT r.* FROM (SELECT thread, max(sequence) AS latest FROM messages "
                "WHERE channel = ? AND thread IS NOT NULL GROUP BY thread ORDER BY latest DESC LIMIT ?) t "
                "JOIN messages r ON r.id = t.thread AND r.channel = ? ORDER BY t.latest DESC", (channel, limit, channel))
            # Thread management (moving, closing and archiving threads) is not part of this API.
            return [self.thread_summary(conn, channel, row, title) for row in roots]

    def mark_read(self, channel, actor):
        with self.database(write=True) as conn:
            newest = conn.execute("SELECT max(created) FROM messages WHERE channel = ? AND thread IS NULL",
                                  (channel,)).fetchone()[0]
            if newest:
                conn.execute(
                    "INSERT INTO read_markers(channel, person, created) VALUES (?, ?, ?) "
                    "ON CONFLICT(channel, person) DO UPDATE SET created = max(created, excluded.created)",
                    (channel, actor, newest))

    @staticmethod
    def summary(conn, channel, actor, joined_at):
        if conn is None:
            return None, 0, joined_at
        marker = conn.execute("SELECT created FROM read_markers WHERE channel = ? AND person = ?",
                              (channel, actor)).fetchone()
        read_at = max(joined_at, marker[0]) if marker else joined_at
        latest = conn.execute(
            "SELECT * FROM messages WHERE channel = ? AND thread IS NULL ORDER BY created DESC LIMIT 1",
            (channel,)).fetchone()
        unread = conn.execute(
            "SELECT count(*) FROM (SELECT sequence FROM messages WHERE channel = ? AND thread IS NULL "
            "AND created > ? AND author != ? LIMIT 100)", (channel, read_at, actor)).fetchone()[0]
        return latest, unread, read_at

    def _pin(self, channel, row, actor, stamp, viewer):
        return {"messageId": row["id"], "channelId": channel, "threadId": row["thread"],
                "pinnedByUserId": self.person_id(actor), "pinnedAt": stamp,
                "message": {**self.message(row, viewer), "pinned": True}}

    def pins(self, channel, viewer):
        with self.database() as conn:
            if conn is None:
                return []
            rows = conn.execute(
                "SELECT m.*, p.author AS pinned_by, p.created AS pinned_at FROM pins p "
                "JOIN messages m ON m.id = p.message WHERE m.channel = ? ORDER BY p.created DESC", (channel,))
            return [self._pin(channel, row, row["pinned_by"], row["pinned_at"], viewer) for row in rows]

    def set_pin(self, channel, actor, message_id, pinned):
        with self.database(write=True) as conn:
            row = self.find(conn, channel, message_id)
            if not pinned:
                conn.execute("DELETE FROM pins WHERE message = ?", (message_id,))
                return None
            conn.execute("INSERT OR IGNORE INTO pins(message, author, created) VALUES (?, ?, ?)",
                         (message_id, actor, _stamp(datetime.now(timezone.utc))))
            pin = conn.execute("SELECT author, created FROM pins WHERE message = ?", (message_id,)).fetchone()
            return self._pin(channel, row, pin["author"], pin["created"], actor)
