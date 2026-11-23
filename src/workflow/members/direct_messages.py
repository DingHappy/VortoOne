"""Private conversations between two current workspace people."""
from __future__ import annotations

import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from src.agents import identity_groups as rules
from src.workflow.members.channel_history import OWNER_KEY, ChannelHistory, _stamp
from src.workflow.members.registry import MemberRegistry, Missing, Refused

_ID = re.compile(r"pd_[a-f0-9]{32}")


def is_person_dm(channel_id: str) -> bool:
    return _ID.fullmatch(channel_id) is not None


class DirectMessages:
    def __init__(self, root: str, owner: dict):
        self.registry = MemberRegistry(root)
        self.history = ChannelHistory(self.registry.directory, owner["userId"])
        self.owner = owner

    @staticmethod
    def _participant(state, key):
        return key == OWNER_KEY or key in state["members"]

    @staticmethod
    def _require_speak(state, actor):
        owner, groups = MemberRegistry._actor(state, actor)
        if not rules.has("chat.speak", groups, MemberRegistry._posts(state), owner=owner):
            raise Refused("没有发送私信的权限")

    @staticmethod
    def _viewer(actor):
        return OWNER_KEY if actor.is_owner else actor.id

    @staticmethod
    def _has_table(conn):
        return conn is not None and conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'person_direct_messages'"
        ).fetchone() is not None

    @staticmethod
    def _peer(row, viewer):
        return row["second"] if row["first"] == viewer else row["first"]

    @contextmanager
    def _access(self, actor, channel_id):
        with self.registry._locked_state() as state:
            MemberRegistry._actor(state, actor)
            viewer = self._viewer(actor)
            with self.history.database() as conn:
                row = conn.execute(
                    "SELECT * FROM person_direct_messages WHERE channel = ? AND (first = ? OR second = ?)",
                    (channel_id, viewer, viewer),
                ).fetchone() if self._has_table(conn) else None
                if row is None or not self._participant(state, self._peer(row, viewer)):
                    raise Missing("没有这个私聊")
                yield conn, state, viewer, row

    def _describe(self, conn, state, row, viewer):
        peer = self._peer(row, viewer)
        person = self.owner if peer == OWNER_KEY else state["members"][peer]
        latest, unread, last_read = self.history.summary(conn, row["channel"], viewer, "")
        last_message = None
        if latest is not None:
            last_message = {"id": latest["id"], "body": latest["body"], "createdAt": latest["created"],
                            "authorType": "person"}
        return {"channelId": row["channel"], "with": {
            "type": "person", "id": self.history.person_id(peer),
            "name": person.get("name") or self.history.person_id(peer),
            "handle": person.get("handle") or self.history.person_id(peer),
        }, "openedAt": row["opened"], "lastMessage": last_message,
            "unread": unread, "lastReadAt": last_read or None}

    def list(self, actor):
        with self.registry._locked_state() as state, self.history.database() as conn:
            MemberRegistry._actor(state, actor)
            if not self._has_table(conn):
                return []
            viewer = self._viewer(actor)
            rows = conn.execute(
                "SELECT d.* FROM person_direct_messages d WHERE (d.first = ? OR d.second = ?) "
                "AND (d.creator = ? OR EXISTS (SELECT 1 FROM messages WHERE channel = d.channel)) "
                "ORDER BY d.opened DESC", (viewer, viewer, viewer),
            )
            return [self._describe(conn, state, row, viewer) for row in rows
                    if self._participant(state, self._peer(row, viewer))]

    def open(self, actor, peer_id):
        with self.registry._locked_state() as state:
            MemberRegistry._actor(state, actor)
            viewer = self._viewer(actor)
            peer = OWNER_KEY if peer_id == self.owner["userId"] else peer_id
            if peer == OWNER_KEY and peer_id != self.owner["userId"]:
                raise Missing("没有这个成员")
            if peer == viewer or not self._participant(state, peer):
                raise Missing("没有这个成员")
            first, second = sorted((viewer, peer))
            with self.history.database() as conn:
                row = conn.execute("SELECT * FROM person_direct_messages WHERE first = ? AND second = ?",
                                   (first, second)).fetchone() if self._has_table(conn) else None
                if row is not None:
                    return self._describe(conn, state, row, viewer)
            self._require_speak(state, actor)
            channel = "pd_" + uuid.uuid4().hex
            with self.history.database(write=True) as conn:
                conn.execute("INSERT INTO person_direct_messages VALUES (?, ?, ?, ?, ?)",
                             (channel, first, second, viewer, _stamp(datetime.now(timezone.utc))))
                row = conn.execute("SELECT * FROM person_direct_messages WHERE channel = ?", (channel,)).fetchone()
                return self._describe(conn, state, row, viewer)

    def roster(self, actor, channel_id):
        with self._access(actor, channel_id) as (_, _, viewer, row):
            return [self.history.author(key) for key in (viewer, self._peer(row, viewer))]

    def can_read(self, actor, channel_id):
        try:
            with self._access(actor, channel_id):
                return True
        except (Missing, Refused):
            return False

    def messages(self, actor, channel_id, *, before="", limit=100, after=None):
        with self._access(actor, channel_id) as (_, _, viewer, _row):
            return self.history.messages(channel_id, viewer, before=before,
                                         limit=max(1, min(200, limit)), after=after)

    def send(self, actor, channel_id, text, *, client_id, reply_to):
        with self._access(actor, channel_id) as (conn, state, viewer, _row):
            self._require_speak(state, actor)
            if reply_to:
                self.history.find(conn, channel_id, reply_to)
            return self.history.append(channel_id, viewer, text, client_id, None, reply_to, False)

    def mark_read(self, actor, channel_id):
        with self._access(actor, channel_id) as (_, _, viewer, _row):
            self.history.mark_read(channel_id, viewer)

    def reads(self, actor, channel_id):
        with self._access(actor, channel_id) as (conn, _, viewer, row):
            peer = self._peer(row, viewer)
            marker = conn.execute("SELECT created FROM read_markers WHERE channel = ? AND person = ?",
                                  (channel_id, peer)).fetchone()
            return [{"userId": self.history.person_id(peer), "lastReadAt": marker[0]}] if marker else []

    def subscribe(self, actor, channel_id):
        with self._access(actor, channel_id) as (conn, _, _, _row):
            return self.history.latest_sequence(conn, channel_id), 0, 0
