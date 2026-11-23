"""Local appointments and repeating series scoped to their authenticated owner."""

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
_WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
_RULE_FIELDS = {"freq", "interval", "byWeekday", "times", "timezone", "start", "until", "count"}
_SERIES_TIMES = {"start", "end", "startDate", "endDate", "allDay", "timezone", "rule"}
_MAX_SERIES_SPAN = timedelta(days=31)
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
_EXCEPTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS series_exceptions (
    principal TEXT NOT NULL,
    series_id TEXT NOT NULL,
    original_day TEXT NOT NULL,
    event_id TEXT NOT NULL,
    PRIMARY KEY (principal, series_id, original_day),
    FOREIGN KEY (principal, series_id) REFERENCES events (principal, id) ON DELETE CASCADE,
    FOREIGN KEY (principal, event_id) REFERENCES events (principal, id) ON DELETE CASCADE
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


def _rule(value, start: datetime, zone_name: str, all_day: bool) -> dict | None:
    if value is None:
        return None
    if (not isinstance(value, dict) or value.keys() - _RULE_FIELDS
            or value.get("freq") not in ("daily", "weekly") or value.get("count") is not None):
        raise _unsupported("私人日历仅支持每日或每周重复，不支持按次数结束或高级规则")
    if value.get("timezone") != zone_name:
        raise _unsupported("重复规则和日程必须使用同一个时区")
    interval = value.get("interval", 1)
    if type(interval) is not int or not 1 <= interval <= 100:
        raise CalendarError("重复间隔须为 1 到 100")
    local = start.astimezone(zone(zone_name))
    day = _day(value["start"], "rule.start") if value.get("start") is not None else local.date()
    until = _day(value["until"], "rule.until") if value.get("until") is not None else None
    if until is not None and until < day:
        raise CalendarError("重复结束日期不能早于起始日期")
    times = value.get("times", [local.strftime("%H:%M")])
    if (not isinstance(times, list) or len(times) != 1 or not isinstance(times[0], str)
            or len(times[0]) != 5 or times[0][2] != ":"):
        raise _unsupported("重复规则每天仅支持一个时刻")
    try:
        clock = time.fromisoformat(times[0])
    except ValueError as error:
        raise CalendarError("重复时刻需要 HH:MM") from error
    if all_day and clock != time.min:
        raise CalendarError("全天重复日程须从午夜开始")

    weekdays = value.get("byWeekday", [])
    if not isinstance(weekdays, list) or any(day not in _WEEKDAYS for day in weekdays):
        raise _unsupported("每周重复只支持普通星期")
    if value["freq"] == "daily" and weekdays and interval != 1:
        raise _unsupported("按星期筛选的每日规则须使用一天间隔")
    pattern = {
        "freq": value["freq"], "interval": interval, "times": times, "timezone": zone_name,
        "start": day.isoformat(), "until": until.isoformat() if until else None, "count": None,
    }
    if value["freq"] == "weekly":
        pattern["byWeekday"] = [d for d in _WEEKDAYS if d in weekdays] or [_WEEKDAYS[day.weekday()]]
    elif weekdays:
        pattern["byWeekday"] = [d for d in _WEEKDAYS if d in weekdays]
    return pattern


def _rule_matches(rule: dict, day: date) -> bool:
    anchor = date.fromisoformat(rule["start"])
    gap = (day - anchor).days
    if gap < 0 or (rule["until"] and day.isoformat() > rule["until"]):
        return False
    if rule["freq"] == "daily":
        return gap % rule["interval"] == 0 and (
            not rule.get("byWeekday") or _WEEKDAYS[day.weekday()] in rule["byWeekday"])
    # Anchor to Monday, matching the calendar recurrence picker.
    week = (gap + anchor.weekday()) // 7
    return week % rule["interval"] == 0 and _WEEKDAYS[day.weekday()] in rule["byWeekday"]


def _wall(day: date, clock: time, tz) -> datetime | None:
    local = datetime.combine(day, clock, tz)
    result = local.astimezone(timezone.utc)
    # Skip a wall time in the spring-forward gap. A repeated time uses the first fold.
    return result if result.astimezone(tz).replace(tzinfo=None) == local.replace(tzinfo=None) else None


def _occurrence(data: dict, day: date) -> dict | None:
    rule = data["rule"]
    if day.isoformat() in data.get("exdates", ()) or not _rule_matches(rule, day):
        return None
    tz = zone(rule["timezone"])
    start = _wall(day, time.fromisoformat(rule["times"][0]), tz)
    if start is None:
        return None
    if data["allDay"]:
        length = (date.fromisoformat(data["endDate"]) - date.fromisoformat(data["startDate"])).days
        end_day = day + timedelta(days=length)
        end = _wall(end_day + timedelta(days=1), time.min, tz)
        if end is None or end <= start:
            return None
        dates = {"startDate": day.isoformat(), "endDate": end_day.isoformat()}
    else:
        end = start + (instant(data["end"]) - instant(data["start"]))
        dates = {}
    # An occurrence is the editor's own current day, but keeps the same underlying series ID.
    return {**data, **dates, "start": _iso(start), "end": _iso(end),
            "rule": {**rule, "start": day.isoformat()}}


def _days(first: date, last: date):
    return (date.fromordinal(value) for value in range(first.toordinal(), last.toordinal() + 1))


def _first_occurrence(data: dict) -> dict | None:
    rule = data["rule"]
    first = date.fromisoformat(rule["start"])
    days = rule["interval"] * 14
    # Excluded days can only suppress one slot each, so add at most one interval per exclusion.
    days += len(data.get("exdates", ())) * rule["interval"] * 7
    last = date.fromordinal(min(date.max.toordinal(), first.toordinal() + days))
    if rule["until"]:
        last = min(last, date.fromisoformat(rule["until"]))
    return next((item for day in _days(first, last) if (item := _occurrence(data, day)) is not None), None)


def _calendar_rows(data: dict, start: datetime, end: datetime):
    rule = data.get("rule")
    if not rule:
        yield data
        return
    tz = zone(rule["timezone"])
    earliest = start.astimezone(tz).date().toordinal() - _MAX_SERIES_SPAN.days - 2
    first = max(date.fromisoformat(rule["start"]), date.fromordinal(max(1, earliest)))
    last = end.astimezone(tz).date()
    if rule["until"]:
        last = min(last, date.fromisoformat(rule["until"]))
    for day in _days(first, last):
        item = _occurrence(data, day)
        if item is not None and instant(item["start"]) < end and instant(item["end"]) > start:
            yield item


def _row_range(data: dict) -> tuple[str, str]:
    if not (rule := data.get("rule")):
        return data["start"], data["end"]
    first = _first_occurrence(data)
    if first is None:
        raise CalendarError("重复规则没有可用的日程")
    until = rule["until"]
    if until is None:
        end = "9999-12-31T23:59:59.999Z"
    else:
        # Superset of any last local date plus its duration, including timezone offsets.
        upper = date.fromisoformat(until).toordinal() + _MAX_SERIES_SPAN.days + 2
        end = _iso(datetime.combine(date.fromordinal(min(date.max.toordinal(), upper)), time.max, timezone.utc))
    return first["start"], end


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
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(_SCHEMA)
                conn.execute(_EXCEPTION_SCHEMA)
            yield conn

    def _input(self, body: dict, previous: dict, default_zone: str) -> dict:
        if body.keys() & _SERIES_FIELDS:
            raise _unsupported("请按日历界面的重复日程范围提交")
        if body.keys() - _FIELDS:
            raise CalendarError("不认识的日历字段")
        data = {**previous, **body}
        if data.get("recurrence", "none") != "none":
            raise _unsupported("请使用结构化规则设置重复日程")
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
        rule = _rule(data.get("rule"), start, zone_name, all_day)
        span = end_day - start_day + timedelta(days=1) if all_day else end - start
        if rule and span > _MAX_SERIES_SPAN:
            raise _unsupported("重复日程的单次长度最长 31 天")
        if rule and all_day and rule["start"] != start_day.isoformat():
            raise CalendarError("全天日程的起始日期必须和重复规则一致")
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
            "rule": rule,
        }

    def _event(self, event_id: str, data: dict) -> dict:
        wire = {
            **data, "id": event_id, "recurrence": "none", "participants": [self.person], "rsvp": [],
            "createdBy": self.person, "channelId": None, "issueId": None, "rule": data.get("rule"),
        }
        if data.get("rule"):
            wire.update(seriesId=event_id, occurrenceStart=data["start"])
        else:
            wire.pop("exdates", None)
        return wire

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
            events = [self._event(event_id, item) for event_id, raw in rows
                      for item in _calendar_rows(json.loads(raw), start, end)]
        return sorted(events, key=lambda item: (item["start"], item["id"]))

    def _insert(self, conn, data: dict) -> str:
        event_id = "event-" + uuid.uuid4().hex
        conn.execute(
            "INSERT INTO events (principal, id, start, end, content) VALUES (?, ?, ?, ?, ?)",
            (self.principal, event_id, *_row_range(data), json.dumps(data, ensure_ascii=False)),
        )
        return event_id

    def _replace(self, conn, event_id: str, data: dict) -> None:
        conn.execute(
            "UPDATE events SET start = ?, end = ?, content = ? WHERE principal = ? AND id = ?",
            (*_row_range(data), json.dumps(data, ensure_ascii=False), self.principal, event_id),
        )

    def create(self, body: dict, default_zone: str) -> dict:
        data = self._input(body, {}, default_zone)
        visible = _first_occurrence(data) if data["rule"] else data
        if visible is None:
            raise CalendarError("重复规则没有可用的日程")
        with self._database(write=True) as conn:
            event_id = self._insert(conn, data)
        return self._event(event_id, visible)

    @staticmethod
    def _missing() -> CalendarError:
        return CalendarError("没有这个私人日程", status=404, code="calendar.not_found")

    def _data(self, conn, event_id: str) -> dict:
        row = conn.execute(
            "SELECT content FROM events WHERE principal = ? AND id = ?", (self.principal, event_id),
        ).fetchone()
        if row is None:
            raise self._missing()
        return json.loads(row[0])

    def _selected(self, data: dict, scope, occurrence_start) -> tuple[date, dict]:
        if scope not in ("this", "following", "all") or occurrence_start is None:
            raise _unsupported("重复日程需要选择修改范围和该次日程时间")
        chosen = _parse_instant(occurrence_start, "occurrenceStart")
        day = chosen.astimezone(zone(data["timezone"])).date()
        item = _occurrence(data, day)
        if item is None or _iso(chosen) != item["start"]:
            raise self._missing()
        return day, item

    def _remove(self, conn, event_id: str) -> None:
        conn.execute("DELETE FROM events WHERE principal = ? AND id = ?", (self.principal, event_id))

    def _remove_exceptions(self, conn, event_id: str, first_day: date | None = None) -> None:
        if first_day is None:
            scope, args = "", ()
        else:
            scope, args = " AND original_day >= ?", (first_day.isoformat(),)
        conn.execute(
            "DELETE FROM events WHERE principal = ? AND id IN (SELECT event_id FROM series_exceptions"
            " WHERE principal = ? AND series_id = ?" + scope + ")",
            (self.principal, self.principal, event_id, *args),
        )

    def _save_prefix(self, conn, event_id: str, data: dict, stop: date, *, single: bool) -> None:
        cropped = {**data}
        excluded = data.get("exdates", [])
        day = stop.isoformat()
        if single:
            cropped["exdates"] = sorted([*excluded, day])
        else:
            if stop == date.min:
                self._remove(conn, event_id)
                return
            until = (stop - timedelta(days=1)).isoformat()
            cropped["rule"] = {**data["rule"], "until": until}
            cropped["exdates"] = [item for item in excluded if item < day]
        if _first_occurrence(cropped) is None:
            self._remove(conn, event_id)
        else:
            self._replace(conn, event_id, cropped)

    @staticmethod
    def _move_rule(body: dict, previous: dict) -> dict:
        # The grid sends start/end patches without the recurrence mirror used by the edit dialog.
        if "rule" in body or not body.keys() & {"start", "startDate", "timezone"}:
            return body
        rule = previous["rule"]
        zone_name = body.get("timezone", previous["timezone"])
        local = _parse_instant(body.get("start", previous["start"]), "start").astimezone(zone(zone_name))
        all_day = body.get("allDay", previous["allDay"])
        if all_day:
            day = _day(body.get("startDate", previous["startDate"]), "startDate")
            clock = "00:00"
        else:
            day = local.date()
            clock = local.strftime("%H:%M")
        moved = {**rule, "start": day.isoformat(), "times": [clock], "timezone": zone_name}
        if rule["freq"] == "weekly":
            shift = (day - date.fromisoformat(rule["start"])).days
            moved["byWeekday"] = [_WEEKDAYS[(_WEEKDAYS.index(d) + shift) % 7] for d in rule["byWeekday"]]
        return {**body, "rule": moved}

    def _update_series(self, conn, event_id, data, body, scope, occurrence_start):
        day, item = self._selected(data, scope, occurrence_start)
        proposed = body.get("rule")
        if isinstance(proposed, dict) and not body.keys() & (_SERIES_TIMES - {"rule"}):
            # EventDialog mirrors the start in the browser's clock even for a title-only edit.
            # An actual time edit also supplies the event's changed start or civil date.
            pattern = {key: val for key, val in item["rule"].items() if key != "times"}
            if {key: val for key, val in proposed.items() if key != "times"} == pattern:
                body = {key: val for key, val in body.items() if key != "rule"}
        if not body:
            return self._event(event_id, item)
        zone_name = data["timezone"]
        if scope == "this":
            # The dialog mirrors the selected day/time in its rule while editing. Treat an unchanged
            # pattern as a single detached exception; a new recurrence requires its own series.
            proposed = body.get("rule")
            pattern = {key: value for key, value in item["rule"].items() if key not in ("start", "times")}
            if proposed is not None:
                if not isinstance(proposed, dict) or {key: val for key, val in proposed.items()
                                                       if key not in ("start", "times")} != pattern:
                    raise _unsupported("仅本次不能设置新的重复规则")
            exception = self._input({**body, "rule": None}, item, zone_name)
            exception_id = self._insert(conn, exception)
            conn.execute("INSERT INTO series_exceptions (principal, series_id, original_day, event_id)"
                         " VALUES (?, ?, ?, ?)", (self.principal, event_id, day.isoformat(), exception_id))
            self._save_prefix(conn, event_id, data, day, single=True)
            return self._event(exception_id, exception)

        if scope == "all":
            first = _first_occurrence(data)
            assert first is not None
            reschedule = bool(body.keys() & _SERIES_TIMES)
            if reschedule and (item["start"] != first["start"] or data.get("exdates")):
                raise _unsupported("请从系列的首次日程修改时间或重复规则")
            previous = item if reschedule else data
            saved = self._input(self._move_rule(body, previous), previous, zone_name)
            if data.get("exdates"):
                saved["exdates"] = data["exdates"]
            self._replace(conn, event_id, saved)
            return self._event(event_id, _first_occurrence(saved) if saved["rule"] else saved)

        # A split and its replacement are committed in the same SQLite transaction.
        if data.get("exdates") and body.keys() & _SERIES_TIMES:
            raise _unsupported("有单次例外时，不能改动此后日程的时间或重复规则")
        saved = self._input(self._move_rule(body, item), item, zone_name)
        if saved["rule"] is None:
            raise _unsupported("若只保留一项，请先删除此后日程，再新建单次日程")
        remaining = [entry for entry in data.get("exdates", []) if entry >= day.isoformat()]
        if remaining:
            saved["exdates"] = remaining
        new_id = self._insert(conn, saved)
        conn.execute("UPDATE series_exceptions SET series_id = ? WHERE principal = ?"
                     " AND series_id = ? AND original_day >= ?",
                     (new_id, self.principal, event_id, day.isoformat()))
        self._save_prefix(conn, event_id, data, day, single=False)
        return self._event(new_id, _first_occurrence(saved))

    def update(self, event_id: str, body: dict) -> dict:
        if not self.path.exists():
            raise self._missing()
        with self._database(write=True) as conn:
            previous = self._data(conn, event_id)
            if previous.get("rule"):
                body = dict(body)
                scope = body.pop("scope", None)
                occurrence_start = body.pop("occurrenceStart", None)
                return self._update_series(conn, event_id, previous, body, scope, occurrence_start)
            data = self._input(body, previous, previous["timezone"])
            if data["rule"] and conn.execute(
                "SELECT 1 FROM series_exceptions WHERE principal = ? AND event_id = ?", (self.principal, event_id),
            ).fetchone():
                raise _unsupported("单次例外不能再设为重复日程，请新建独立日程")
            self._replace(conn, event_id, data)
        return self._event(event_id, _first_occurrence(data) if data["rule"] else data)

    def delete(self, event_id: str, *, scope=None, occurrence_start=None) -> None:
        if not self.path.exists():
            raise self._missing()
        with self._database(write=True) as conn:
            data = self._data(conn, event_id)
            if data.get("rule"):
                day, _ = self._selected(data, scope, occurrence_start)
                if scope == "all":
                    self._remove_exceptions(conn, event_id)
                else:
                    if scope == "following":
                        self._remove_exceptions(conn, event_id, day)
                    self._save_prefix(conn, event_id, data, day, single=scope == "this")
                    return
            elif scope is not None or occurrence_start is not None:
                raise _unsupported("单次日程没有重复范围")
            self._remove(conn, event_id)
