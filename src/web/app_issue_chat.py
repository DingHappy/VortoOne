"""Human discussion threads whose access follows the current board card."""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager

from src.agents.principal import MEMBER, Principal
from src.web import app_board, app_views
from src.workflow.board.store import Board, NotFound
from src.workflow.members.channel_history import OWNER_KEY, ChannelHistory
from src.workflow.members.registry import Damaged, MemberRegistry, Missing, Refused
from src.workflow.members.thread_reads import ThreadReader

_ISSUE_ID = r"[a-f0-9]{32}"
_CHANNEL = "tc_"
_THREAD = "task-root-"


def is_channel(channel_id):
    return re.fullmatch(_CHANNEL + _ISSUE_ID, channel_id) is not None


def is_thread(thread_id):
    return re.fullmatch(_THREAD + _ISSUE_ID, thread_id) is not None


class IssueChat:
    def __init__(self, root):
        self.root = root
        self.registry = MemberRegistry(root)
        self.history = ChannelHistory(self.registry.directory, app_views.owner_id(root))
        self.board = Board(root)

    @staticmethod
    def _person(actor):
        return OWNER_KEY if actor.is_owner else actor.id

    @staticmethod
    def _ref(issue_id):
        return {"channelId": _CHANNEL + issue_id, "rootId": _THREAD + issue_id}

    @contextmanager
    def _access(self, actor, issue_id):
        if not re.fullmatch(_ISSUE_ID, issue_id):
            raise Missing("没有这个任务对话")
        with self.registry.while_actor_current(actor) as state:
            try:
                with self.board.locked_issue(issue_id) as issue:
                    if not app_board.can_see(issue, actor):
                        raise Missing("没有这个任务对话")
                    yield issue, state
            except NotFound as error:
                raise Missing("没有这个任务对话") from error
            except sqlite3.Error as error:
                raise Damaged("读写任务对话失败") from error

    def _edit(self, actor):
        if not app_board.can_edit(self.root, actor):
            raise Refused("你没有给这张卡评论的权限")

    def _anchor(self, conn, channel_id):
        return self.history.find(conn, channel_id, _THREAD + channel_id[len(_CHANNEL):])

    @contextmanager
    def _read(self, actor, channel_id):
        with self._access(actor, channel_id[len(_CHANNEL):]) as (issue, state):
            with self.history.database() as conn:
                yield conn, self._anchor(conn, channel_id), issue, state

    def open(self, actor, issue_id):
        with self._access(actor, issue_id):
            ref = self._ref(issue_id)
            channel_id = ref["channelId"]
            with self.history.database() as conn:
                try:
                    self._anchor(conn, channel_id)
                except Missing:
                    self._edit(actor)
                else:
                    return ref
            # This record gives existing thread replies and read cursors a durable root.
            # It never appears as a posted message in the task timeline or its card count.
            self.history.initialize_task_thread(channel_id, self._person(actor), ref["rootId"])
            return ref

    def cards(self, actor, issue_ids):
        if not issue_ids or not self.history.path.exists():
            return {}
        try:
            with self.registry.while_actor_current(actor), self.board.locked_issues(issue_ids) as issues:
                refs = {key: self._ref(key) for key, issue in issues.items() if app_board.can_see(issue, actor)}
                with self.history.database() as conn:
                    summaries = self.history.task_thread_overviews(
                        conn, ((ref["channelId"], ref["rootId"]) for ref in refs.values()))
                return {key: {**ref, **summaries[ref["rootId"]]} for key, ref in refs.items()
                        if ref["rootId"] in summaries}
        except sqlite3.Error as error:
            raise Damaged("读写任务对话失败") from error

    def thread(self, actor, thread_id):
        channel_id = _CHANNEL + thread_id[len(_THREAD):]
        with self._read(actor, channel_id) as (conn, root, issue, _):
            reader = ThreadReader(self._person(actor), root["created"])
            info = self.history.thread_summary(conn, channel_id, root, issue["title"], reader=reader)
            return {**info, "channelKind": "task",
                    "participants": self.history.task_thread_authors(conn, channel_id, thread_id)}

    def members(self, actor, channel_id):
        with self._read(actor, channel_id) as (_, _, issue, state):
            return [self.history.author(OWNER_KEY)] + [
                self.history.author(person) for person in state["members"]
                if app_board.can_see(issue, Principal(MEMBER, person, self.root))]

    def post(self, actor, channel_id, text, *, client_id, thread_id, reply_to):
        with self._access(actor, channel_id[len(_CHANNEL):]):
            self._edit(actor)
            with self.history.database() as conn:
                root = self._anchor(conn, channel_id)
                if thread_id != root["id"]:
                    raise Missing("没有这个任务话题")
                if reply_to and self.history.find(conn, channel_id, reply_to)["thread"] != thread_id:
                    raise Missing("没有这条任务消息")
            return self.history.append(channel_id, self._person(actor), text, client_id, thread_id,
                                       reply_to, False)

    def messages(self, actor, channel_id, *, before="", limit=100, thread=None, after=None):
        with self._access(actor, channel_id[len(_CHANNEL):]):
            with self.history.database() as conn:
                root_id = self._anchor(conn, channel_id)["id"]
            if thread and thread != root_id:
                raise Missing("没有这个任务话题")
            if not thread and after is None:
                return []
            return [row for row in self.history.messages(
                channel_id, self._person(actor), before=before, limit=min(max(limit, 1), 200),
                thread=thread, after=after,
            ) if row["threadId"] == root_id]

    def active_runs(self, actor, channel_id):
        with self._read(actor, channel_id):
            return {"runs": []}

    def mark_read(self, actor, thread_id):
        channel_id = _CHANNEL + thread_id[len(_THREAD):]
        with self._access(actor, channel_id[len(_CHANNEL):]):
            with self.history.database() as conn:
                self._anchor(conn, channel_id)
            self.history.mark_thread_read(channel_id, thread_id, self._person(actor))

    def can_read(self, actor, channel_id):
        try:
            with self._read(actor, channel_id):
                return True
        except (Missing, Refused):
            return False

    def subscribe(self, actor, channel_id):
        with self._read(actor, channel_id) as (conn, _, _, _):
            return (self.history.latest_sequence(conn, channel_id), self.history.reaction_cursor(conn, channel_id),
                    self.history.thread_cursor(conn, channel_id))

    def reaction_changes(self, actor, channel_id, after):
        return []  # Task conversations do not expose channel reactions.

    def thread_changes(self, actor, channel_id, after):
        return []  # The board card owns the title and state.
