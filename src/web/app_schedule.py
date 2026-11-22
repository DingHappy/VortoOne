"""新版界面的排期：界面的规则（ApiRule）↔ 后端排期写法，以及常见说法的解析。

后端的排期（scheduling/routine_schedule.py）认五段式 cron、``at HH:MM``、``every …`` 和
``once <带时区的时间>``。界面的规则比 cron 宽（隔几天、第几个星期几、截止日期、次数），
表达不了的照实回「不支持」，不悄悄换成近似的排期。

解析只认确定的写法（数字时间、每天/工作日/每周几/每月几号/每小时/明天几点 和对应英文），
不调用模型：同一句话总是得到同一个排期，猜不出来就让人用选择器。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from src.scheduling.routine_schedule import RoutineSchedule, zone

_WEEKDAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
_CRON_DOW = {"MO": 1, "TU": 2, "WE": 3, "TH": 4, "FR": 5, "SA": 6, "SU": 0}
_ZH_DAY = {"MO": "一", "TU": "二", "WE": "三", "TH": "四", "FR": "五", "SA": "六", "SU": "日"}
_ZH_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7, "七": 7}
_EN_DAY = {"mon": "MO", "tue": "TU", "wed": "WE", "thu": "TH", "fri": "FR", "sat": "SA", "sun": "SU"}
_TIME = re.compile(r"\A([01]?\d|2[0-3]):([0-5]\d)\Z")


class ScheduleError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# ── 规则 → 排期 ───────────────────────────────────────────────────────────────

def _times(rule: Dict[str, Any]) -> List[Tuple[int, int]]:
    raw = rule.get("times") or ["09:00"]
    if not isinstance(raw, list) or not raw or len(raw) > 24:
        raise ScheduleError("invalid", "时间无效")
    found = []
    for value in raw:
        match = _TIME.match(value) if isinstance(value, str) else None
        if not match:
            raise ScheduleError("invalid", f"时间要写成 HH:MM：{value!r}")
        found.append((int(match[1]), int(match[2])))
    return sorted(set(found))


def _hours_minute(times: List[Tuple[int, int]]) -> Tuple[str, int]:
    minutes = {minute for _hour, minute in times}
    if len(minutes) != 1:
        raise ScheduleError("unsupported", "一天里的几个时间要在同一分钟（比如 9:00 和 18:00）")
    return ",".join(str(hour) for hour, _minute in times), minutes.pop()


def _ints(values: Any, low: int, high: int, what: str) -> List[int]:
    if not isinstance(values, list) or not values or any(type(v) is not int or not low <= v <= high for v in values):
        raise ScheduleError("invalid", f"{what}无效")
    return sorted(set(values))


def _clock(times: List[Tuple[int, int]]) -> str:
    return "、".join(f"{hour:02d}:{minute:02d}" for hour, minute in times)


def rule_to_schedule(rule: Any) -> Tuple[str, str, str]:
    """→ (后端排期写法, 时区, 说明)。"""
    if not isinstance(rule, dict) or rule.get("freq") not in ("once", "hourly", "daily", "weekly", "monthly", "yearly"):
        raise ScheduleError("invalid", "规则无效")
    timezone = rule.get("timezone")
    try:
        tz = zone(timezone)
    except ValueError as error:
        raise ScheduleError("invalid", str(error)) from error
    for key in ("until", "count", "bySetPos"):
        if rule.get(key) not in (None, 0) if key == "count" else rule.get(key) is not None:
            raise ScheduleError("unsupported", "截止日期、次数和「第几个星期几」这里还不支持")
    interval = rule.get("interval") or 1
    if type(interval) is not int or interval < 1:
        raise ScheduleError("invalid", "间隔无效")
    freq = rule["freq"]
    times = _times(rule)
    if freq == "once":
        start = rule.get("start")
        try:
            day = datetime.fromisoformat(str(start)[:10]).date()
        except ValueError as error:
            raise ScheduleError("invalid", "一次性的要给日期") from error
        hour, minute = times[0]
        moment = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
        return f"once {moment.isoformat()}", timezone, f"{day.isoformat()} {hour:02d}:{minute:02d}（一次）"
    if freq == "hourly":
        if interval > 23:
            raise ScheduleError("unsupported", "每隔超过 23 小时请改用每天")
        minute = times[0][1]
        step = f"*/{interval}" if interval > 1 else "*"
        return f"{minute} {step} * * *", timezone, ("每小时" if interval == 1 else f"每 {interval} 小时") + f"的第 {minute} 分"
    if interval != 1:
        raise ScheduleError("unsupported", "隔几天 / 隔几周 / 隔几个月这里还不支持")
    hours, minute = _hours_minute(times)
    if freq == "daily":
        return f"{minute} {hours} * * *", timezone, f"每天 {_clock(times)}"
    if freq == "weekly":
        days = rule.get("byWeekday") or []
        if not isinstance(days, list) or not days or any(day not in _CRON_DOW for day in days):
            raise ScheduleError("invalid", "要选星期几")
        ordered = [day for day in _WEEKDAYS if day in days]
        label = "工作日" if ordered == _WEEKDAYS[:5] else "每周" + "、".join(_ZH_DAY[d] for d in ordered)
        return f"{minute} {hours} * * {','.join(str(_CRON_DOW[d]) for d in ordered)}", timezone, f"{label} {_clock(times)}"
    if freq == "monthly":
        days_of_month = _ints(rule.get("byMonthDay"), 1, 31, "几号")
        label = "每月 " + "、".join(f"{d} 日" for d in days_of_month)
        return f"{minute} {hours} {','.join(map(str, days_of_month))} * *", timezone, f"{label} {_clock(times)}"
    months = _ints(rule.get("byMonth"), 1, 12, "月份")
    days_of_month = _ints(rule.get("byMonthDay"), 1, 31, "几号")
    label = "每年 " + "、".join(f"{m} 月" for m in months) + " " + "、".join(f"{d} 日" for d in days_of_month)
    return (f"{minute} {hours} {','.join(map(str, days_of_month))} {','.join(map(str, months))} *", timezone,
            f"{label} {_clock(times)}")


def next_runs(schedule: str, timezone: str, count: int = 3, *, now: Optional[datetime] = None) -> List[str]:
    from datetime import timezone as tz_utc
    parsed = RoutineSchedule(schedule, timezone)
    moment = now or datetime.now(tz_utc.utc)
    found: List[str] = []
    cursor = moment
    for _ in range(count):
        upcoming = parsed.next(cursor, cursor)
        if upcoming is None:
            break
        found.append(upcoming.isoformat().replace("+00:00", "Z"))
        cursor = upcoming + timedelta(seconds=1)
        if parsed.once is not None:
            break
    return found


def rule_info(rule: Any, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """界面的 RuleInfo。cron 字段给后端的排期写法（一次性的是 once …）。"""
    schedule, timezone, description = rule_to_schedule(rule)
    runs = next_runs(schedule, timezone, now=now)
    if schedule.startswith("once ") and not runs:
        raise ScheduleError("past", "这个时间已经过去了")
    return {"ok": True, "rule": {**rule, "interval": rule.get("interval") or 1}, "description": description,
            "cron": schedule, "nextRuns": runs}


# ── 说法 → 规则 ───────────────────────────────────────────────────────────────

def _phrase_time(text: str) -> Optional[Tuple[int, int]]:
    """「早上9点半」「下午3:15」「18点」「9am」「6:30 pm」→ (时, 分)。"""
    match = re.search(r"(凌晨|早上|上午|中午|下午|傍晚|晚上)?\s*(\d{1,2})\s*(?:[:：]\s*(\d{1,2})|点\s*(半|(\d{1,2})\s*分?)?)"
                      r"\s*(am|pm|a\.m\.|p\.m\.)?", text, re.I)
    if not match:
        match = re.search(r"(\d{1,2})\s*(am|pm)", text, re.I) or re.search(r"\bat\s+(\d{1,2})\b()", text, re.I)
        if not match:
            return None
        hour, minute, ampm, part = int(match[1]), 0, (match[2] or "").lower(), None
    else:
        part, hour = match[1], int(match[2])
        if match[3]:
            minute = int(match[3])
        elif match[4] == "半":
            minute = 30
        else:
            minute = int(match[5]) if match[5] else 0
        ampm = (match[6] or "").lower().replace(".", "")
    if part in ("下午", "傍晚", "晚上") and hour < 12:
        hour += 12
    if part == "中午" and hour < 6:
        hour += 12
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def phrase_to_rule(phrase: str, timezone: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    text = " ".join(str(phrase or "").split()).lower()
    if not text or len(text) > 200:
        raise ScheduleError("unparsed", "说一下什么时候，比如「每天早上9点」")
    tz = zone(timezone)
    found = _phrase_time(text)
    clock = [f"{found[0]:02d}:{found[1]:02d}"] if found else ["09:00"]
    rule: Dict[str, Any] = {"timezone": timezone, "times": clock}
    if re.search(r"每小时|每个小时|every hour|hourly", text):
        return {**rule, "freq": "hourly", "times": [f"00:{found[1]:02d}" if found else "00:00"]}
    if re.search(r"工作日|weekdays?|每个工作日", text) and not re.search(r"每周[一二三四五六日天]", text):
        return {**rule, "freq": "weekly", "byWeekday": _WEEKDAYS[:5]}
    if re.search(r"周末|weekends?", text):
        return {**rule, "freq": "weekly", "byWeekday": ["SA", "SU"]}
    zh_week = re.search(r"(?:每周|每星期|每个星期|每礼拜)([一二三四五六日天、和,，\s]+)", text)
    if zh_week:
        days = [_WEEKDAYS[_ZH_NUM[c] - 1] for c in zh_week[1] if c in _ZH_NUM]
        if days:
            return {**rule, "freq": "weekly", "byWeekday": [d for d in _WEEKDAYS if d in days]}
    en_week = re.findall(r"\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*\b", text)
    if en_week and re.search(r"every|each|on", text):
        days = {_EN_DAY[d] for d in en_week}
        return {**rule, "freq": "weekly", "byWeekday": [d for d in _WEEKDAYS if d in days]}
    month = re.search(r"每月\s*(\d{1,2})\s*[号日]|every month on (?:the )?(\d{1,2})|monthly on (?:the )?(\d{1,2})", text)
    if month:
        day = int(next(group for group in month.groups() if group))
        if 1 <= day <= 31:
            return {**rule, "freq": "monthly", "byMonthDay": [day]}
    if re.search(r"每天|每日|天天|every day|daily|each day", text):
        return {**rule, "freq": "daily"}
    today = (now or datetime.now(tz)).astimezone(tz).date()
    for words, offset in ((r"今天|today", 0), (r"明天|tomorrow", 1), (r"后天", 2)):
        if re.search(words, text) and found:
            return {**rule, "freq": "once", "start": (today + timedelta(days=offset)).isoformat()}
    raise ScheduleError("unparsed", "没看懂这个时间，换个说法或用选择器")


def parse_phrase(phrase: str, timezone: str, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    return rule_info(phrase_to_rule(phrase, timezone, now=now), now=now)


def schedule_view(expression: str) -> Optional[Dict[str, Any]]:
    """后端排期写法 → 界面能显示的规则（只认这里写出来的那几种形状）。"""
    cron = expression.split()
    if len(cron) != 5:
        return None
    minute, hour, dom, month, dow = cron
    if not minute.isdigit():
        return None
    if hour.startswith("*"):
        step = hour[2:] if hour.startswith("*/") else "1"
        return {"freq": "hourly", "interval": int(step) if step.isdigit() else 1, "times": [f"00:{int(minute):02d}"]}
    if not re.fullmatch(r"\d+(,\d+)*", hour):
        return None
    times = [f"{int(h):02d}:{int(minute):02d}" for h in hour.split(",")]
    if (dom, month, dow) == ("*", "*", "*"):
        return {"freq": "daily", "times": times}
    if dom == "*" and month == "*" and re.fullmatch(r"\d(,\d)*", dow):
        names = {v: k for k, v in _CRON_DOW.items()}
        return {"freq": "weekly", "times": times, "byWeekday": [names[int(d) % 7] for d in dow.split(",")]}
    if month == "*" and dow == "*" and re.fullmatch(r"\d+(,\d+)*", dom):
        return {"freq": "monthly", "times": times, "byMonthDay": [int(d) for d in dom.split(",")]}
    if dow == "*" and re.fullmatch(r"\d+(,\d+)*", dom) and re.fullmatch(r"\d+(,\d+)*", month):
        return {"freq": "yearly", "times": times, "byMonthDay": [int(d) for d in dom.split(",")],
                "byMonth": [int(m) for m in month.split(",")]}
    return None
