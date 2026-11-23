"""Owner's mission drafts bound to roots in the team channel history."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from src.workflow.members import channel_threads
from src.workflow.members.channel_history import OWNER_KEY, _stamp
from src.workflow.members.channels import TeamChannels
from src.workflow.members.registry import Missing, Refused
from src.workflow.tasks.tasks import TASK_STATE_LOCK


_ROWS = (
    " FROM mission_drafts AS draft"
    " JOIN messages AS root ON root.id = draft.thread_id AND root.thread IS NULL"
)


class DraftConflict(ValueError):
    """The thread already has a draft with different content."""


class DraftClosed(ValueError):
    """A repeated request does not match the recorded draft closure."""


class AgentUnavailable(ValueError):
    """The selected staff member is no longer available for a new draft."""


def _has_table(conn, name):
    # Older homes can open their existing history read-only before its first migration.
    return conn is not None and conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,),
    ).fetchone() is not None


def _draft_rows(conn):
    # Keep the original draft check constraint and record cancelation separately.
    if _has_table(conn, "mission_draft_closures"):
        return (
            "WITH drafts AS (SELECT draft.*, root.channel AS channel_id, closure.closed_at, closure.note,"
            " CASE WHEN closure.mission_id IS NULL THEN draft.status ELSE 'canceled' END AS current_status"
            + _ROWS + " LEFT JOIN mission_draft_closures AS closure ON closure.mission_id = draft.id) "
        )
    return (
        "WITH drafts AS (SELECT draft.*, root.channel AS channel_id, NULL AS closed_at, NULL AS note,"
        " draft.status AS current_status" + _ROWS + ") "
    )


class MissionDrafts:
    def __init__(self, channels: TeamChannels):
        self.channels = channels

    def _owner(self, actor):
        if not actor.is_owner or actor.id != self.channels.owner_id:
            raise Refused("只有所有者可以访问这家的任务草案")

    def _can_read(self, state, actor, channel_id):
        try:
            self.channels._find(state, actor, channel_id, archived=False, joined=True)
        except (Missing, Refused):
            return False
        return True

    def _load(self, conn, identifier, *, by_thread=False):
        if not _has_table(conn, "mission_drafts"):
            return None
        key = "thread_id" if by_thread else "id"
        return conn.execute(
            _draft_rows(conn) + f"SELECT * FROM drafts WHERE {key} = ?", (identifier,)
        ).fetchone()

    def create(self, actor, thread_id, staff_id, title, goal):
        """Commit a draft while the current channel ACL and staff revisions are stable."""
        from src.scheduling.staff_runner import get_staff_service

        self._owner(actor)
        registry = self.channels.registry
        history = self.channels.history
        with TASK_STATE_LOCK, registry._locked_state() as state:
            with history.database() as conn:
                root_message = history.find_by_id(conn, thread_id)
                channel_id = root_message["channel"]
                if root_message["thread"] is not None or not self._can_read(state, actor, channel_id):
                    raise Missing("没有可用的团队频道线程")
                previous = self._load(conn, thread_id, by_thread=True)
                if previous is not None:
                    if (previous["staff_id"], previous["title"], previous["goal"]) != (staff_id, title, goal):
                        raise DraftConflict("这个线程已经有另一份任务草案")
                    return dict(previous)
                # Thread archive and draft insertion share the outer registry lock.
                channel_threads.read(conn, thread_id).allow_reply()

            try:
                service = get_staff_service(registry.root)
                staff, role = service.assignment({"member": staff_id, "prompt": goal})
                if staff["engine"] != "vortocode":
                    raise ValueError("当前草案仅支持 VortoOne 原生专员")
                service.executable(role)
            except ValueError as error:
                raise AgentUnavailable(str(error)) from error

            draft_id = "mission-" + uuid.uuid4().hex
            with history.database(write=True) as conn:
                conn.execute(
                    "INSERT INTO mission_drafts"
                    " (id, thread_id, owner, staff_id, staff_revision, role_id, role_revision,"
                    " title, goal, status, revision, created)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', 1, ?)",
                    (draft_id, thread_id, OWNER_KEY, staff["id"], staff["revision"], role["id"],
                     role["revision"], title, goal, _stamp(datetime.now(timezone.utc))),
                )
                return dict(self._load(conn, draft_id))

    def _get_visible(self, conn, state, actor, identifier, *, by_thread=False):
        draft = self._load(conn, identifier, by_thread=by_thread)
        if draft is None or not self._can_read(state, actor, draft["channel_id"]):
            raise Missing("没有这个任务草案")
        return draft

    def get(self, actor, identifier, *, by_thread=False):
        self._owner(actor)
        with self.channels.registry._locked_state() as state, self.channels.history.database() as conn:
            return dict(self._get_visible(conn, state, actor, identifier, by_thread=by_thread))

    def cancel(self, actor, identifier, note):
        """Cancel a draft once while the source access remains stable."""
        self._owner(actor)
        history = self.channels.history
        with self.channels.registry._locked_state() as state:
            with history.database() as conn:
                draft = self._get_visible(conn, state, actor, identifier)
                if draft["closed_at"] is not None:
                    if draft["note"] != note:
                        raise DraftClosed("这个任务草案已取消")
                    return dict(draft)
            with history.database(write=True) as conn:
                closed_at = _stamp(datetime.now(timezone.utc))
                conn.execute(
                    "INSERT INTO mission_draft_closures (mission_id, closed_at, note) VALUES (?, ?, ?)",
                    (identifier, closed_at, note),
                )
                return dict(self._load(conn, identifier))

    def assigned_model(self, actor, identifier):
        """Read only the model from the staff revision recorded by the draft."""
        from src.workflow.staff.registry import StaffRegistry

        with TASK_STATE_LOCK:
            draft = self.get(actor, identifier)
            try:
                staff = StaffRegistry(self.channels.registry.root).member(draft["staff_id"])
            except ValueError as error:
                raise AgentUnavailable("草案绑定的专员登记不可用") from error
            if (staff.get("revision"), staff.get("role"), staff.get("engine"), staff.get("status")) != (
                    draft["staff_revision"], draft["role_id"], "vortocode", "active"):
                raise AgentUnavailable("草案绑定的专员已变更，无法读取原配置")
            model = staff.get("model", "")
            if not isinstance(model, str) or len(model) > 160:
                raise AgentUnavailable("草案绑定的专员模型配置无效")
            return model

    def listing(self, actor, *, channel_id=None, staff_id=None, query="", limit=50, status="open"):
        self._owner(actor)
        counts = {"active": 0, "waitingYou": 0, "closed": 0}
        with self.channels.registry._locked_state() as state, self.channels.history.database() as conn:
            if not _has_table(conn, "mission_drafts"):
                return [], counts
            allowed = {channel_id for channel_id in state["channels"] if self._can_read(state, actor, channel_id)}
            conn.create_function("mission_channel_readable", 1, lambda key: key in allowed)
            filters = ["mission_channel_readable(channel_id)"]
            arguments = []
            if channel_id is not None:
                filters.append("channel_id = ?")
                arguments.append(channel_id)
            if staff_id is not None:
                filters.append("staff_id = ?")
                arguments.append(staff_id)
            if query:
                conn.create_function("mission_casefold", 1, str.casefold)
                filters.append("(instr(mission_casefold(title), ?) OR instr(mission_casefold(goal), ?))")
                arguments.extend([query.casefold()] * 2)
            projection = _draft_rows(conn)
            matching = " FROM drafts WHERE " + " AND ".join(filters)
            by_status = dict(conn.execute(
                projection + "SELECT current_status, count(*)" + matching + " GROUP BY current_status", arguments,
            ))
            counts["active"] = by_status.get("proposed", 0)
            counts["closed"] = by_status.get("canceled", 0)
            target = {"open": "proposed", "closed": "canceled"}.get(status, status)
            if target not in ("proposed", "canceled"):
                return [], counts
            rows = conn.execute(
                projection + "SELECT *" + matching
                + " AND current_status = ? ORDER BY created DESC, id DESC LIMIT ?", [*arguments, target, limit]
            )
            return [dict(row) for row in rows], counts
