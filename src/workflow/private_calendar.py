"""Single local appointments scoped to their authenticated owner."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import date, datetime, time, timedelta, timezone

from src.agents.principal import Principal
from src.scheduling.routine_schedule import instant, zone
from src.utils.state_dir import ensure_state_gitignore, state_dir

_FIELDS = {
    "title", "description", "start", "end", "allDay", "timezone", "recurrence",
    "participants", "channelId", "issueId", "rule", "location", "startDate", "endDate",
}
_SERIES_FIELDS = {"scope", "seriesId", "occurrenceStart", "exdates"}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    principal TEXT NOT NULL,
    id TEXT NOT NULL,
    start TEXT NOT NULL,
    end TEXT NOT NULL,
    content TEXT NOT NULL,
    PRIMARY KEY (principal, id)
);
"""


class CalendarError(ValueError):
    def __init__(self, message: str, *, status: int = 400, code: str = "calendar.invalid"):
        super().__init__(message)
        self.status = status
        self.code = code


def _unsupported(message: str) -> CalendarError:
    return CalendarError(message, status=409, code="calendar.unsupported")


def _parse_instant(value, field: str) -> datetime:
    try:
        if not isinstance(value, str) or len(value) > 40 or "T" not in value:
            raise ValueError
        return instant(value)
    except (ValueError, OverflowError) as error:
        raise CalendarError(f"{field} 需要带时区偏移的日期时间") from error


def date_range(start: str | None, end: str | None) -> tuple[datetime, datetime]:
    if start is None and end is None:
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        return today, today + timedelta(days=45)
    first, last = _parse_instant(start, "from"), _parse_instant(end, "to")
    if not timedelta(0) < last - first <= timedelta(days=93):
        raise CalendarError("查询时间范围须为正且最多 93 天")
    return first, last


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _day(value, field: str) -> date:
    try:
        if not isinstance(value, str) or len(value) != 10:
            raise ValueError
        day = date.fromisoformat(value)
        if day.isoformat() != value:
            raise ValueError
        return day
    except ValueError as error:
        raise CalendarError(f"{field} 需要 YYYY-MM-DD 日期") from error


def _text(value, field: str, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise CalendarError(f"{field} 最多 {maximum} 个字符")
    text = value.strip()
    if required and not text:
        raise CalendarError(f"{field} 不能为空")
    return text


class PrivateCalendar:
    def __init__(self, root: str, actor: Principal):
        self.root = root
        self.path = state_dir(root) / "calendar.sqlite3"
        # A change to the home's filesystem path also changes the owner's wire id.
        self.principal = "owner" if actor.is_owner else f"member:{actor.id}"
        self.person = {"type": "person", "id": actor.id}

    @contextmanager
    def _database(self, *, write: bool = False):
        if self.path.parent.is_symlink() or self.path.is_symlink():
            raise OSError("日历记录不能是符号链接")
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            except FileExistsError:
                pass
            else:
                os.close(fd)
                ensure_state_gitignore(self.root)
        mode = "rw" if write else "ro"
        with closing(sqlite3.connect(self.path.absolute().as_uri() + f"?mode={mode}", uri=True)) as conn, conn:
            if write:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(_SCHEMA)
            yield conn

    def _input(self, body: dict, previous: dict, default_zone: str) -> dict:
        if body.keys() & _SERIES_FIELDS:
            raise _unsupported("本地日历暂只支持单次日程")
        if body.keys() - _FIELDS:
            raise CalendarError("不认识的日历字段")
        data = {**previous, **body}
        if data.get("recurrence", "none") != "none" or data.get("rule") is not None:
            raise _unsupported("本地日历暂只支持单次日程")
        if data.get("channelId") is not None or data.get("issueId") is not None:
            raise _unsupported("私人日程暂不支持关联频道或任务")
        if data.get("participants", [self.person]) not in ([], [self.person]):
            raise _unsupported("私人日程只能包含你自己")
        all_day = data.get("allDay", False)
        if not isinstance(all_day, bool):
            raise CalendarError("allDay 需要布尔值")
        zone_name = data.get("timezone", default_zone)
        tz = zone(zone_name)
        start = _parse_instant(data.get("start"), "start")
        end = _parse_instant(data.get("end"), "end")
        start_day = end_day = None
        if all_day:
            start_day = _day(data.get("startDate"), "startDate")
            end_day = _day(data.get("endDate"), "endDate")
            if end_day < start_day:
                raise CalendarError("结束日期不能早于开始日期")
            # The form sends both browser-time instants and civil dates. For
            # all-day events the dates are authoritative, including across DST.
            day_start = datetime.combine(start_day, time.min, tz)
            day_end = datetime.combine(end_day + timedelta(days=1), time.min, tz)
            if body and body.keys() <= {"start", "end"} and (start != day_start or end != day_end):
                raise CalendarError("移动全天日程请更新 startDate 和 endDate")
            start, end = day_start, day_end
        elif any(body.get(field) is not None for field in ("startDate", "endDate")):
            raise CalendarError("只有全天日程可以指定 startDate 或 endDate")
        start_iso, end_iso = _iso(start), _iso(end)
        if end_iso <= start_iso:
            raise CalendarError("结束时间须晚于开始时间")
        location = data.get("location")
        if location is not None:
            location = _text(location, "地点", 1_000) or None
        return {
            "title": _text(data.get("title"), "标题", 500, required=True),
            "description": _text(data.get("description", ""), "说明", 10_000),
            "location": location,
            "start": start_iso, "end": end_iso, "timezone": zone_name, "allDay": all_day,
            "startDate": start_day.isoformat() if start_day else None,
            "endDate": end_day.isoformat() if end_day else None,
        }

    def _event(self, event_id: str, data: dict) -> dict:
        return {
            **data, "id": event_id, "recurrence": "none", "participants": [self.person], "rsvp": [],
            "createdBy": self.person, "channelId": None, "issueId": None, "rule": None,
        }

    def listing(self, start: datetime, end: datetime) -> list[dict]:
        if not self.path.exists():
            return []
        with self._database() as conn:
            # The first write's schema may still be uncommitted.
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone():
                return []
            rows = conn.execute(
                "SELECT id, content FROM events WHERE principal = ? AND start < ? AND end > ? ORDER BY start, id",
                (self.principal, _iso(end), _iso(start)),
            )
            return [self._event(event_id, json.loads(payload)) for event_id, payload in rows]

    def create(self, body: dict, default_zone: str) -> dict:
        data = self._input(body, {}, default_zone)
        event_id = "event-" + uuid.uuid4().hex
        with self._database(write=True) as conn:
            conn.execute(
                "INSERT INTO events (principal, id, start, end, content) VALUES (?, ?, ?, ?, ?)",
                (self.principal, event_id, data["start"], data["end"], json.dumps(data, ensure_ascii=False)),
            )
        return self._event(event_id, data)

    @staticmethod
    def _missing() -> CalendarError:
        return CalendarError("没有这个私人日程", status=404, code="calendar.not_found")

    def update(self, event_id: str, body: dict) -> dict:
        if not self.path.exists():
            raise self._missing()
        with self._database(write=True) as conn:
            row = conn.execute(
                "SELECT content FROM events WHERE principal = ? AND id = ?", (self.principal, event_id),
            ).fetchone()
            if row is None:
                raise self._missing()
            previous = json.loads(row[0])
            data = self._input(body, previous, previous["timezone"])
            conn.execute(
                "UPDATE events SET start = ?, end = ?, content = ? WHERE principal = ? AND id = ?",
                (data["start"], data["end"], json.dumps(data, ensure_ascii=False), self.principal, event_id),
            )
        return self._event(event_id, data)

    def delete(self, event_id: str, *, series: bool) -> None:
        if not self.path.exists():
            raise self._missing()
        with self._database(write=True) as conn:
            removed = conn.execute("DELETE FROM events WHERE principal = ? AND id = ?", (self.principal, event_id))
            if not removed.rowcount:
                raise self._missing()
            if series:
                # Rolling back also keeps the reply indistinguishable for other users' IDs.
                raise _unsupported("本地日历暂只支持单次日程")
