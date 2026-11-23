"""Read appointments, approved schedules and due dates visible to the caller."""

from datetime import date, datetime, timedelta
from heapq import merge
from itertools import islice

from src.agents.principal import Principal
from src.scheduling.routine_schedule import RoutineSchedule, local_day_ordinal, zone
from src.web import app_board, app_views
from src.workflow.board.store import Board
from src.workflow.private_calendar import PrivateCalendar
from src.workflow.staff.routines import Routines

_MAX_ROUTINE_SLOTS = 4096


def workspace_timezone(root: str) -> str:
    try:
        return Routines(root).read()["timezone"] or "UTC"
    except (OSError, ValueError):
        return "UTC"


def listing(calendar: PrivateCalendar, actor: Principal, start: datetime, end: datetime, timezone_name: str) -> dict:
    deadlines = []
    board = Board(calendar.root)
    if board.path.exists():
        tz = zone(timezone_name)
        first = local_day_ordinal(start, tz)
        last = local_day_ordinal(end - timedelta(microseconds=1), tz)
        for issue in board.issues():
            due = issue.get("dueDate")
            if not due or not app_board.can_see(issue, actor):
                continue
            try:
                day = date.fromisoformat(due).toordinal()
            except ValueError:
                continue
            if first <= day <= last:
                deadlines.append({"issueId": issue["id"], "number": issue["number"], "title": issue["title"],
                                  "date": due, "assignee": issue["assignee"]})
    # Existing routines are private to the owner, even when a member proposed one.
    routines = _upcoming_routine_rows(calendar.root, start, end) if actor.is_owner else []
    payload = calendar_range(events=calendar.listing(start, end), deadlines=deadlines,
                             routines=routines[:_MAX_ROUTINE_SLOTS])
    if len(routines) > _MAX_ROUTINE_SLOTS:
        payload["routinesTruncated"] = True
    return payload


def _upcoming_routine_rows(root: str, start: datetime, end: datetime) -> list[dict]:
    service = Routines(root)
    now = service.clock()
    if end <= now:
        return []
    try:
        registered = service.read()
    except (OSError, ValueError):
        return []
    migrating = {routine_id for entry in registered["migrations"] if entry["status"] == "applying"
                 for routine_id in entry["routines"]}
    streams = []
    for item in registered["routines"]:
        if (not item["enabled"] or item["retired"] or item["id"] in migrating
                or item["state"].get("error") or service.awaiting_takeover_resume(item)
                or service.schedule_held(item)):
            continue
        try:
            member, _ = service.check_binding(item, persist_defaults=False)
            service.check_creator(item, member)
        except (OSError, ValueError):
            continue
        streams.append(_routine_slots(root, item, start, end, now))
    sorted_rows = merge(*streams, key=lambda row: (row["at"], row["routineId"]))
    return list(islice(sorted_rows, _MAX_ROUTINE_SLOTS + 1))


def _routine_slots(root: str, routine: dict, start: datetime, end: datetime, now: datetime):
    agent = app_views.member_agent_id(root, routine["member"])
    target = {"type": "conversation", "channelId": app_views.dm_channel_id(agent), "threadId": None}
    schedule = RoutineSchedule(routine["schedule"], routine["timezone"])
    for slot in schedule.upcoming(start, end, now=now, last=routine["state"]["cursor"]):
        yield {"routineId": routine["id"], "name": routine["name"], "agentId": agent,
               "at": slot.isoformat(timespec="milliseconds").replace("+00:00", "Z"), "target": target}


def calendar_range(*, events=(), deadlines=(), routines=()) -> dict:
    return {"events": list(events), "routines": list(routines), "reminders": [], "deadlines": list(deadlines),
            "proposals": []}
