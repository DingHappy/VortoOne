"""Personal read cursors and follow choices for team-channel threads."""
from __future__ import annotations

from dataclasses import dataclass

MAX_UNREAD = 100
SCHEMA = """
CREATE TABLE IF NOT EXISTS thread_read_markers (
    person TEXT NOT NULL,
    thread TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    PRIMARY KEY (person, thread)
);
CREATE TABLE IF NOT EXISTS thread_follows (
    person TEXT NOT NULL,
    thread TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    following INTEGER NOT NULL CHECK (following IN (0, 1)),
    PRIMARY KEY (person, thread)
);
"""


@dataclass(frozen=True)
class ThreadReader:
    person: str
    joined_at: str


def _has_table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def mark(conn, channel, thread, person):
    last = conn.execute("SELECT max(sequence) FROM messages WHERE channel = ? AND thread = ?",
                        (channel, thread)).fetchone()[0]
    if last is not None:
        conn.execute("INSERT INTO thread_read_markers(person, thread, sequence) VALUES (?, ?, ?) "
                     "ON CONFLICT(person, thread) DO UPDATE SET sequence = excluded.sequence "
                     "WHERE excluded.sequence > thread_read_markers.sequence",
                     (person, thread, last))


def set_follow(conn, thread, person, following):
    conn.execute("INSERT INTO thread_follows(person, thread, following) VALUES (?, ?, ?) "
                 "ON CONFLICT(person, thread) DO UPDATE SET following = excluded.following "
                 "WHERE thread_follows.following <> excluded.following", (person, thread, following))


def info(conn, channel, root, reader: ThreadReader, *, archived):
    thread = root["id"]
    explicit = conn.execute("SELECT following FROM thread_follows WHERE person = ? AND thread = ?",
                            (reader.person, thread)).fetchone() if _has_table(conn, "thread_follows") else None
    following = bool(explicit[0]) if explicit is not None else (
        root["author"] == reader.person or conn.execute(
            "SELECT 1 FROM messages WHERE channel = ? AND thread = ? AND author = ? LIMIT 1",
            (channel, thread, reader.person),
        ).fetchone() is not None
    )
    if not following:
        return {"following": False, "unread": 0}
    if archived:
        return {"following": True, "unread": 0}

    marker = conn.execute("SELECT sequence FROM thread_read_markers WHERE person = ? AND thread = ?",
                          (reader.person, thread)).fetchone() if _has_table(conn, "thread_read_markers") else None
    unread = conn.execute("SELECT count(*) FROM (SELECT 1 FROM messages WHERE channel = ? AND thread = ? "
                          "AND author <> ? AND created > ? AND sequence > ? LIMIT ?)",
                          (channel, thread, reader.person, reader.joined_at, marker[0] if marker else 0,
                           MAX_UNREAD)).fetchone()[0]
    return {"following": True, "unread": unread}


def count(conn, readers: dict[str, ThreadReader]):
    if conn is None:
        return 0
    marker_join = ""
    marker_filter = "1"
    has_markers = _has_table(conn, "thread_read_markers")
    if has_markers:
        marker_join = " LEFT JOIN thread_read_markers k ON k.person = ? AND k.thread = r.id"
        marker_filter = "m.sequence > coalesce(k.sequence, 0)"
    follow_join = ""
    follow_filter = ("(r.author = ? OR EXISTS (SELECT 1 FROM messages p "
                     "WHERE p.channel = r.channel AND p.thread = r.id AND p.author = ?))")
    has_follows = _has_table(conn, "thread_follows")
    if has_follows:
        follow_join = " LEFT JOIN thread_follows f ON f.person = ? AND f.thread = r.id"
        follow_filter = "coalesce(f.following, " + follow_filter + ")"
    archive_filter = "1"
    if _has_table(conn, "thread_lifecycle"):
        archive_filter = ("NOT EXISTS (SELECT 1 FROM thread_lifecycle l WHERE l.channel = r.channel "
                          "AND l.message = r.id AND l.archived_at IS NOT NULL)")

    result = 0
    for channel, reader in readers.items():
        parameters = [reader.person] if has_markers else []
        if has_follows:
            parameters.append(reader.person)
        result += conn.execute(
            "SELECT count(*) FROM (SELECT DISTINCT r.id FROM messages m JOIN messages r "
            "ON r.id = m.thread AND r.channel = m.channel" + marker_join + follow_join +
            " WHERE m.channel = ? AND r.thread IS NULL AND m.created > ? AND m.author <> ? AND " +
            marker_filter + " AND " + archive_filter + " AND " + follow_filter + " LIMIT ?)",
            (*parameters, channel, reader.joined_at, reader.person, reader.person, reader.person, MAX_UNREAD - result),
        ).fetchone()[0]
        if result >= MAX_UNREAD:
            break
    return result
