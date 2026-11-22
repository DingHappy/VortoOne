"""Read private appointments and due dates already visible on the issue board."""

from datetime import date, datetime, timedelta

from src.agents.principal import Principal
from src.scheduling.routine_schedule import zone
from src.web import app_board
from src.workflow.board.store import Board
from src.workflow.private_calendar import PrivateCalendar
from src.workflow.staff.routines import Routines


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
        first = start.astimezone(tz).date()
        last = (end - timedelta(microseconds=1)).astimezone(tz).date()
        for issue in board.issues():
            due = issue.get("dueDate")
            if not due or not app_board.can_see(issue, actor):
                continue
            try:
                day = date.fromisoformat(due)
            except ValueError:
                continue
            if first <= day <= last:
                deadlines.append({"issueId": issue["id"], "number": issue["number"], "title": issue["title"],
                                  "date": due, "assignee": issue["assignee"]})
    return calendar_range(events=calendar.listing(start, end), deadlines=deadlines)


def calendar_range(*, events=(), deadlines=()) -> dict:
    return {"events": list(events), "routines": [], "reminders": [], "deadlines": list(deadlines), "proposals": []}
