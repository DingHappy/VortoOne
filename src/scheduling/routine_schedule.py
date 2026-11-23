"""Timezone-aware schedules; UTC storage and one catch-up per dispatch."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.scheduling.cron import parse_schedule, _cron_date_matches, _cron_matches

UTC = timezone.utc
HORIZON = timedelta(days=62)


def zone(name):
    if not isinstance(name, str) or not name or len(name) > 100:
        raise ValueError('先核对例行工作的默认时区，或明确指定 IANA 时区')
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError('时区无效，请使用 IANA 名称，例如 Asia/Shanghai') from error


def instant(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    if not isinstance(parsed, datetime) or parsed.tzinfo is None:
        raise ValueError('时间必须带明确时区偏移')
    return parsed.astimezone(UTC)


def local_day_ordinal(utc: datetime, tz: ZoneInfo) -> int:
    """Civil day of a UTC instant, permitting an ordinal just outside years 1–9999."""
    try:
        return utc.astimezone(tz).date().toordinal()
    except OverflowError:
        # A UTC offset of less than 24 hours crosses only the outermost representable date.
        return 0 if utc.year == 1 else date.max.toordinal() + 1


class RoutineSchedule:
    def __init__(self, expression, timezone_name):
        self.zone = zone(timezone_name)
        if not isinstance(expression, str) or len(expression) > 200:
            raise ValueError('排期无效')
        self.once = instant(expression[5:].strip()) if expression.startswith('once ') else None
        self.schedule = None if self.once else parse_schedule(expression)

    def _matches(self, at):
        local = at.astimezone(self.zone)
        if local.fold:
            return False  # A repeated wall-clock minute runs once, at its first occurrence.
        if self.schedule.kind == 'at':
            return (local.hour, local.minute) == self.schedule.at
        return _cron_matches(self.schedule.cron, local)

    def due(self, now, last):
        now, last = instant(now), instant(last)
        if self.once:
            return self.once if last < self.once <= now else None
        if self.schedule.kind == 'every':
            target = last + self.schedule.interval
            return now if target <= now else None
        probe, lower = now.replace(second=0, microsecond=0), max(last, now - HORIZON)
        while probe > lower:
            if self._matches(probe):
                return probe
            probe -= timedelta(minutes=1)
        return None

    def next(self, now, last):
        now, last = instant(now), instant(last)
        overdue = self.due(now, last)
        if overdue:
            return overdue
        if self.once:
            return self.once if self.once > max(now, last) else None
        if self.schedule.kind == 'every':
            return last + self.schedule.interval
        probe = max(now, last).replace(second=0, microsecond=0) + timedelta(minutes=1)
        end = probe + HORIZON
        while probe <= end:
            if self._matches(probe):
                return probe
            probe += timedelta(minutes=1)
        return None

    def upcoming(self, start, end, *, now, last):
        """Project future slots in [start, end), without mutating the dispatch cursor."""
        now, last = instant(now), instant(last)
        start, end = max(instant(start), now), instant(end)
        if max(start, last) >= end:
            return
        if self.once is not None:
            if start <= self.once < end and last < self.once:
                yield self.once
            return
        if self.schedule.kind == 'every':
            yield from self._upcoming_intervals(start, end, now, last)
            return
        yield from self._upcoming_clock_slots(start, end, last)

    def _upcoming_intervals(self, start, end, now, last):
        interval = self.schedule.interval
        if interval >= end - last:
            return
        slot = max(now, last + interval)
        if slot < start:
            whole, remainder = divmod(start - slot, interval)
            steps = whole + bool(remainder)
            if steps > (end - slot) // interval:
                return
            slot += steps * interval
        while slot < end:
            yield slot
            if interval >= end - slot:
                break
            slot += interval

    def _upcoming_clock_slots(self, start, end, last):
        cron = self.schedule.cron if self.schedule.kind == 'cron' else None
        if cron is not None:
            hours = sorted(cron[1]) if cron[1] is not None else range(24)
            minutes = sorted(cron[0]) if cron[0] is not None else range(60)
        else:
            hour, minute = self.schedule.at
            hours, minutes = [hour], [minute]
        first_day = max(date.min.toordinal(), local_day_ordinal(max(start, last), self.zone))
        last_day = min(date.max.toordinal(), local_day_ordinal(end - timedelta(microseconds=1), self.zone))
        for ordinal in range(first_day, last_day + 1):
            day = date.fromordinal(ordinal)
            if cron is not None and not _cron_date_matches(cron, day):
                continue
            for hour in hours:
                for minute in minutes:
                    local = datetime.combine(day, time(hour, minute), self.zone)
                    try:
                        slot = local.astimezone(UTC)
                        if slot.second:
                            # Old offsets can have seconds. Dispatch probes whole UTC minutes.
                            slot = slot.replace(second=0) + timedelta(minutes=1)
                        actual = slot.astimezone(self.zone)
                    except OverflowError:
                        continue  # This wall time is outside the representable UTC interval.
                    # The first fold is eligible. A round trip rejects nonexistent wall times.
                    if (not actual.fold and actual.replace(second=0) == local
                            and start <= slot < end and last < slot):
                        yield slot
