"""Durable state of conversations rooted in existing team-channel messages."""
from __future__ import annotations

from dataclasses import astuple, dataclass, replace

SCHEMA = """
CREATE TABLE IF NOT EXISTS thread_lifecycle (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    message TEXT NOT NULL UNIQUE REFERENCES messages(id) ON DELETE CASCADE,
    channel TEXT NOT NULL,
    title TEXT,
    closed_by TEXT,
    closed_at TEXT,
    archived_by TEXT,
    archived_at TEXT,
    change TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS thread_lifecycle_channel ON thread_lifecycle(channel, sequence);
"""
ACTIONS = frozenset({"close", "reopen", "archive", "unarchive"})
_TAB_FILTERS = {
    "all": "l.archived_at IS NULL",
    "open": "l.archived_at IS NULL AND l.closed_at IS NULL",
    "closed": "l.archived_at IS NULL AND l.closed_at IS NOT NULL",
    "archived": "l.archived_at IS NOT NULL",
}


class ThreadArchived(ValueError):
    """An archived conversation is read-only until it is unarchived."""

    def __init__(self):
        super().__init__("解除归档后才能修改或回复这个话题")


class ThreadInUse(ValueError):
    """Undo would discard an existing conversation or its references."""


class ThreadRangeUnavailable(ValueError):
    """Only a thread made with a range can edit it."""


@dataclass(frozen=True)
class ThreadState:
    title: str | None = None
    closed_by: str | None = None
    closed_at: str | None = None
    archived_by: str | None = None
    archived_at: str | None = None

    @property
    def state(self):
        if self.archived_at:
            return "archived"
        return "closed" if self.closed_at else "open"

    def allow_reply(self):
        if self.archived_at:
            raise ThreadArchived()


def exists(conn):
    return conn is not None and conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'thread_lifecycle'"
    ).fetchone() is not None


def list_filter(conn, tab):
    """SQL fragments for a query over root messages as r, including pre-migration databases."""
    if exists(conn):
        return " LEFT JOIN thread_lifecycle l ON l.message = r.id AND l.channel = r.channel", _TAB_FILTERS[tab]
    return "", "1" if tab in ("all", "open") else "0"


def read(conn, thread, *, initialized=False):
    if not initialized and not exists(conn):
        return ThreadState()
    row = conn.execute("SELECT title, closed_by, closed_at, archived_by, archived_at "
                       "FROM thread_lifecycle WHERE message = ?", (thread,)).fetchone()
    return ThreadState(*row) if row is not None else ThreadState()


def write(conn, channel, thread, state, change):
    # Each changed thread keeps only its latest event. AUTOINCREMENT preserves the channel subscription cursor.
    conn.execute("INSERT OR REPLACE INTO thread_lifecycle(message, channel, title, closed_by, closed_at, "
                 "archived_by, archived_at, change) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (thread, channel, *astuple(state), change))


def rename(conn, channel, thread, title, *, change="title"):
    state = read(conn, thread, initialized=True)
    state.allow_reply()
    if title != state.title:
        write(conn, channel, thread, replace(state, title=title), change)


def apply(conn, channel, thread, actor, action, now):
    if action not in ACTIONS:
        raise ValueError("这个话题操作不可用")
    state = read(conn, thread, initialized=True)
    if action == "archive" and not state.archived_at:
        write(conn, channel, thread, replace(state, archived_by=actor, archived_at=now), "archived")
    elif action == "unarchive" and state.archived_at:
        write(conn, channel, thread, replace(state, archived_by=None, archived_at=None), "unarchived")
    elif action in ("close", "reopen"):
        state.allow_reply()
        if action == "close" and not state.closed_at:
            write(conn, channel, thread, replace(state, closed_by=actor, closed_at=now), "closed")
        elif action == "reopen" and state.closed_at:
            write(conn, channel, thread, replace(state, closed_by=None, closed_at=None), "reopened")
