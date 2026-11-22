"""Durable human messages, reactions, pins, and read markers for team channels."""
from __future__ import annotations

import json
import os
import sqlite3
import unicodedata
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
CREATE TABLE IF NOT EXISTS message_reactions (
    message TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    reaction TEXT NOT NULL,
    person TEXT NOT NULL,
    PRIMARY KEY(message, reaction, person)
);
CREATE TABLE IF NOT EXISTS message_reaction_changes (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    message TEXT NOT NULL UNIQUE REFERENCES messages(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS read_markers (
    channel TEXT NOT NULL,
    person TEXT NOT NULL,
    created TEXT NOT NULL,
    PRIMARY KEY(channel, person)
);
"""

REACTION_MAX_LENGTH = 32


def _emoji_symbol(char: str) -> bool:
    # Keep new pictographs working on Python versions with older Unicode data.
    return char in "‼⁉" or unicodedata.category(char) == "So" or 0x1F000 <= ord(char) <= 0x1FAFF


def _check_reaction(reaction: str) -> None:
    if reaction in ("+1", "-1"):
        return
    if not 0 < len(reaction) <= REACTION_MAX_LENGTH:
        raise ValueError("表情无效")
    if reaction[0] in "0123456789#*" and reaction[1:] in ("\u20e3", "\ufe0f\u20e3"):
        return
    if _emoji_symbol(reaction[0]) and all(
        _emoji_symbol(char) or char in "\u200d\ufe0e\ufe0f" or 0xE0020 <= ord(char) <= 0xE007F
        for char in reaction[1:]
    ):
        return
    raise ValueError("表情无效")


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


_VOICE_FIELDS = {"kind", "clipId", "durationMs", "peaks", "expiresAt"}


def _check_voice(media):
    if (not _VOICE_FIELDS <= set(media) <= _VOICE_FIELDS | {"noWords"}
            or not isinstance(media["clipId"], str) or not 0 < len(media["clipId"]) <= 64
            or type(media["durationMs"]) is not int or not 0 < media["durationMs"] <= 10 * 60_000
            or not isinstance(media["peaks"], list) or len(media["peaks"]) > 64
            or any(type(p) not in (int, float) or not 0 <= p <= 1 for p in media["peaks"])
            or not isinstance(media["expiresAt"], str) or media.get("noWords", True) is not True):
        raise ValueError("消息附带的录音无效")
    return dict(media)


def check_media(media):
    """消息附带的 GIF 或录音。GIF 的尺寸、录音的存放由上层核实后填好，这里只拒绝形状不对的。"""
    if media is None:
        return None
    if isinstance(media, dict) and media.get("kind") == "voice":
        return _check_voice(media)
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

    def message(self, row, viewer, *, reactions=None) -> dict:
        result = {"id": row["id"], "channelId": row["channel"], "threadId": row["thread"],
                  "author": {"type": "person", "id": self.person_id(row["author"])}, "body": row["body"],
                  "mentionedAgentIds": [], "agentRunId": None,
                  "createdAt": row["created"], "sequence": row["sequence"]}
        meta = {}
        if row["reply_to"]:
            meta["replyTo"] = row["reply_to"]
        media = row["media"] if "media" in row.keys() else None
        if media:
            media = json.loads(media)
            if media.get("kind") == "voice":
                meta["voice"] = {key: value for key, value in media.items() if key != "kind"}
            else:
                meta["media"] = media
        if meta:
            result["meta"] = meta
        if viewer == row["author"] and row["client"] is not None:
            result["clientId"] = row["client"]
        if "pinned" in row.keys() and row["pinned"]:
            result["pinned"] = True
        if reactions:
            result["reactions"] = reactions
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

    @staticmethod
    def _has_reactions(conn):
        # Old histories stay readable without opening a write transaction to migrate them.
        return conn is not None and conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'message_reactions'"
        ).fetchone() is not None

    def reactions(self, conn, message_ids):
        if not message_ids or not self._has_reactions(conn):
            return {}
        grouped = {}
        for offset in range(0, len(message_ids), 200):
            batch = message_ids[offset:offset + 200]
            marks = ",".join("?" for _ in batch)
            rows = conn.execute(
                f"SELECT message, reaction, person FROM message_reactions WHERE message IN ({marks}) "
                "ORDER BY message, reaction, person", batch)
            for message, reaction, person in rows:
                grouped.setdefault(message, {}).setdefault(reaction, []).append(self.person_id(person))
        return {message: [{"reaction": reaction, "count": len(people), "userIds": people}
                          for reaction, people in reactions.items()]
                for message, reactions in grouped.items()}

    def set_reaction(self, message_id, person, reaction, enabled):
        _check_reaction(reaction)
        with self.database(write=True) as conn:
            if enabled:
                changed = conn.execute(
                    "INSERT INTO message_reactions(message, reaction, person) VALUES (?, ?, ?) "
                    "ON CONFLICT(message, reaction, person) DO NOTHING", (message_id, reaction, person))
            else:
                changed = conn.execute(
                    "DELETE FROM message_reactions WHERE message = ? AND reaction = ? AND person = ?",
                    (message_id, reaction, person))
            if changed.rowcount:
                # One durable cursor per changed message, coalescing changes rather than growing per toggle.
                conn.execute("INSERT OR REPLACE INTO message_reaction_changes(message) VALUES (?)", (message_id,))
            return self.reactions(conn, [message_id]).get(message_id, [])

    def reaction_cursor(self, conn, channel):
        if not self._has_reactions(conn):
            return 0
        return conn.execute(
            "SELECT coalesce(max(r.sequence), 0) FROM message_reaction_changes r "
            "JOIN messages m ON m.id = r.message WHERE m.channel = ?", (channel,)).fetchone()[0]

    def reaction_changes(self, conn, channel, after):
        if not self._has_reactions(conn):
            return []
        rows = conn.execute(
            "SELECT r.sequence, r.message FROM message_reaction_changes r JOIN messages m ON m.id = r.message "
            "WHERE m.channel = ? AND r.sequence > ? ORDER BY r.sequence LIMIT 100", (channel, after)).fetchall()
        reactions = self.reactions(conn, [row["message"] for row in rows])
        return [(row["sequence"], row["message"], reactions.get(row["message"], [])) for row in rows]

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
                    reactions = self.reactions(conn, [existing["id"]]).get(existing["id"])
                    return self.message(existing, actor, reactions=reactions)
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
            reactions = self.reactions(conn, [row["id"] for row in rows])
            return [self.message(row, viewer, reactions=reactions.get(row["id"])) for row in rows]

    @staticmethod
    def latest_sequence(conn, channel):
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

    def _pin(self, channel, row, actor, stamp, viewer, *, reactions=None):
        return {"messageId": row["id"], "channelId": channel, "threadId": row["thread"],
                "pinnedByUserId": self.person_id(actor), "pinnedAt": stamp,
                "message": {**self.message(row, viewer, reactions=reactions), "pinned": True}}

    def pins(self, channel, viewer):
        with self.database() as conn:
            if conn is None:
                return []
            rows = conn.execute(
                "SELECT m.*, p.author AS pinned_by, p.created AS pinned_at FROM pins p "
                "JOIN messages m ON m.id = p.message WHERE m.channel = ? ORDER BY p.created DESC", (channel,)).fetchall()
            reactions = self.reactions(conn, [row["id"] for row in rows])
            return [self._pin(channel, row, row["pinned_by"], row["pinned_at"], viewer,
                              reactions=reactions.get(row["id"])) for row in rows]

    def set_pin(self, channel, actor, message_id, pinned):
        with self.database(write=True) as conn:
            row = self.find(conn, channel, message_id)
            if not pinned:
                conn.execute("DELETE FROM pins WHERE message = ?", (message_id,))
                return None
            conn.execute("INSERT OR IGNORE INTO pins(message, author, created) VALUES (?, ?, ?)",
                         (message_id, actor, _stamp(datetime.now(timezone.utc))))
            pin = conn.execute("SELECT author, created FROM pins WHERE message = ?", (message_id,)).fetchone()
            return self._pin(channel, row, pin["author"], pin["created"], actor,
                             reactions=self.reactions(conn, [message_id]).get(message_id))
