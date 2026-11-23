"""Calendar uses the Desktop payload with live owner and member sessions."""

from datetime import datetime
import json
from stat import S_IMODE

import pytest
from fastapi.testclient import TestClient

from src.utils.state_dir import state_dir
from src.web import app_calendar, app_views
from src.workflow.staff.approvals import StaffApprovals
from src.workflow.staff.routines import Routines
from src.workflow.tasks.tasks import TaskLedger
from tests.unit.test_member_login import ORIGIN, PASSWORD, TOKEN, _join, home  # noqa: F401
from tests.unit.test_staff_routines import registered

RANGE = {"from": "2027-03-11T00:00:00.000Z", "to": "2027-03-18T00:00:00.000Z"}


def _body(client, **changes):
    return {
        "title": "个人日程", "description": "", "location": None,
        "start": "2027-03-12T10:00:00.000Z", "end": "2027-03-12T11:00:00.000Z",
        "participants": [{"type": "person", "id": client.get("/api/app/me").json()["user"]["id"]}],
        "timezone": "UTC", "recurrence": "none", "allDay": False, "rule": None,
        **changes,
    }


def _create(client, calendar, **changes):
    result = client.post(calendar + "/events", json=_body(client, **changes))
    assert result.status_code == 201, result.text
    return result.json()


def _fetch(client, calendar, **query):
    result = client.get(calendar, params=query or RANGE)
    assert result.status_code == 200, result.text
    assert result.headers["Cache-Control"] == "no-store"
    return result.json()


def test_owner_and_members_manage_only_their_own_events(home):
    owner = home["owner"]
    member = _join(home)
    other = _join(home, email="other@example.com")
    calendar = home["base"] + "/calendar"
    db = state_dir(home["root"]) / "calendar.sqlite3"
    assert _fetch(owner, calendar)["events"] == []
    assert not db.exists()

    owner_event = _create(owner, calendar, title="仅所有者")
    my_event = _create(member, calendar, title="仅成员")
    assert S_IMODE(db.stat().st_mode) == 0o600
    assert "calendar.sqlite3" in (db.parent / ".gitignore").read_text()
    assert _fetch(owner, calendar)["events"] == [owner_event]
    assert _fetch(other, calendar)["events"] == []
    assert _fetch(member, calendar)["events"] == [my_event]
    assert my_event["participants"] == [my_event["createdBy"]]
    assert my_event["rsvp"] == []

    for client, event in ((owner, my_event), (member, owner_event), (other, my_event)):
        path = calendar + "/events/" + event["id"]
        assert client.patch(path, json={"title": "无法编辑"}).status_code == 404
        assert client.delete(path).status_code == 404

    # Sign in from a separate client, then apply two partial edits to the
    # persisted row, as the event sheet and grid drag do.
    fresh = TestClient(home["app"], headers=ORIGIN)
    assert fresh.post("/api/auth/sign-in/email", json={
        "email": "ana@example.com", "password": PASSWORD,
    }).status_code == 200
    assert _fetch(fresh, calendar)["events"] == [my_event]
    path = calendar + "/events/" + my_event["id"]
    titled = fresh.patch(path, json={"title": "新标题", "location": "办公室"}).json()
    moved = fresh.patch(path, json={"start": "2027-03-12T20:00:00.000+02:00",
                                    "end": "2027-03-12T21:00:00.000+02:00"})
    assert moved.status_code == 200, moved.text
    assert moved.json() == {**titled, "start": "2027-03-12T18:00:00.000Z", "end": "2027-03-12T19:00:00.000Z"}
    assert _fetch(member, calendar)["events"] == [moved.json()]
    assert fresh.delete(path).status_code == 204
    assert _fetch(fresh, calendar)["events"] == []
    assert _fetch(owner, calendar)["events"] == [owner_event]
    assert owner.delete(calendar + "/events/" + owner_event["id"]).status_code == 204
    assert _fetch(owner, calendar)["events"] == []


def test_all_day_uses_civil_dates_and_exclusive_end_across_dst(home):
    client = home["owner"]
    calendar = home["base"] + "/calendar"
    event = _create(
        client, calendar, allDay=True, timezone="America/New_York",
        startDate="2027-03-14", endDate="2027-03-14",
        start="2027-03-14T00:00:00.000Z", end="2027-03-15T00:00:00.000Z",
    )
    assert (event["start"], event["end"]) == ("2027-03-14T05:00:00.000Z", "2027-03-15T04:00:00.000Z")
    assert event["startDate"] == event["endDate"] == "2027-03-14"
    assert _fetch(client, calendar, **{"from": event["end"], "to": "2027-03-16T00:00:00Z"})["events"] == []
    assert _fetch(client, calendar, **{"from": "2027-03-14T00:00:00Z", "to": event["start"]})["events"] == []
    assert _fetch(client, calendar, **{"from": event["start"], "to": event["end"]})["events"] == [event]
    path = calendar + "/events/" + event["id"]
    # The sheet can send browser-zone timestamp hints while editing a title.
    retitled = client.patch(path, json={"title": "全天", "location": None, "rule": None,
                                       "start": "2027-03-14T00:00:00Z", "end": "2027-03-15T00:00:00Z"})
    assert retitled.status_code == 200, retitled.text
    assert retitled.json() == {**event, "title": "全天"}
    shifted = client.patch(path, json={"startDate": "2027-03-15", "endDate": "2027-03-16"})
    assert shifted.status_code == 200, shifted.text
    assert (shifted.json()["start"], shifted.json()["end"]) == (
        "2027-03-15T04:00:00.000Z", "2027-03-17T04:00:00.000Z",
    )
    timed = client.patch(path, json={"allDay": False, "start": "2027-03-16T10:00:00Z", "end": "2027-03-16T11:00:00Z"})
    assert timed.status_code == 200, timed.text
    assert timed.json()["startDate"] is None and timed.json()["endDate"] is None


def test_calendar_has_no_invites_recurrence_or_series_writes(home):
    owner = home["owner"]
    member = _join(home)
    calendar = home["base"] + "/calendar"
    own = _create(owner, calendar)
    path = calendar + "/events/" + own["id"]
    for payload in ({"recurrence": "weekly"}, {"rule": {"freq": "weekly"}},
                    {"participants": own["participants"] + _body(member)["participants"]},
                    {"channelId": "channel-general"}, {"issueId": "issue-test"}):
        assert owner.post(calendar + "/events", json=_body(owner, **payload)).status_code == 409
        assert owner.patch(path, json=payload).status_code == 409
    assert owner.patch(path, json={"scope": "all", "title": "ignored"}).status_code == 409
    assert owner.delete(path, params={"scope": "all", "occurrenceStart": own["start"]}).status_code == 409
    assert owner.post(path + "/rsvp", json={"status": "accepted"}).status_code == 404
    assert _fetch(owner, calendar)["events"] == [own]
    assert _fetch(member, calendar)["events"] == []


@pytest.mark.parametrize("payload", [
    {"title": ""}, {"start": "2027-03-12T10:00:00"}, {"end": "2027-03-12T09:00:00Z"},
    {"timezone": "Not/AZone"}, {"allDay": True, "startDate": "2027-02-29", "endDate": "2027-03-14"},
    {"rsvp": []}, {"location": False},
])
def test_invalid_event_input_does_not_write(home, payload):
    client = home["owner"]
    calendar = home["base"] + "/calendar"
    assert client.post(calendar + "/events", json=_body(client, **payload)).status_code == 400
    assert not (state_dir(home["root"]) / "calendar.sqlite3").exists()
    assert _fetch(client, calendar)["events"] == []


def test_calendar_rechecks_caller_and_protects_same_origin(home, monkeypatch):
    owner = home["owner"]
    member = _join(home)
    calendar = home["base"] + "/calendar"
    own = _create(owner, calendar)
    unauthenticated = TestClient(home["app"])
    assert unauthenticated.get(calendar, params=RANGE).status_code == 401
    assert member.post(calendar + "/events", json=_body(member), headers={"Origin": "http://other.test"}).status_code == 403
    assert owner.get("/api/app/organizations/bad/calendar", params=RANGE).status_code == 404
    assert owner.get(calendar, params={"from": RANGE["from"]}).status_code == 400
    assert owner.get(calendar, params={**RANGE, "to": "2028-03-18T00:00:00Z"}).status_code == 400

    owner_cookie = TestClient(home["app"])
    assert owner_cookie.post("/api/auth/sign-in/email", json={"email": "", "password": TOKEN}).status_code == 200
    assert _fetch(owner_cookie, calendar)["events"] == [own]
    assert owner_cookie.patch(calendar + "/events/" + own["id"], json={"title": "cross-site"},
                              headers={"Origin": "http://other.test"}).status_code == 403
    assert owner_cookie.patch(calendar + "/events/" + own["id"], json={"title": "same-site"},
                              headers=ORIGIN).status_code == 200

    monkeypatch.delenv("VORTOONE_API_TOKEN")
    assert _fetch(unauthenticated, calendar)["events"] == []
    assert unauthenticated.post(calendar + "/events", json=_body(unauthenticated)).status_code == 403


def test_member_creation_uses_current_post_permission_and_registration(home):
    owner = home["owner"]
    member = _join(home)
    calendar = home["base"] + "/calendar"
    event = _create(member, calendar)
    everyone = next(r for r in owner.get(home["base"] + "/roles").json()["roles"] if r["id"] == "everyone")
    allowed = [permission for permission in everyone["permissions"] if permission != "post"]
    assert owner.patch(home["base"] + "/roles/everyone", json={"permissions": allowed}).status_code == 204
    assert member.post(calendar + "/events", json=_body(member)).status_code == 403
    assert _fetch(member, calendar)["events"] == [event]
    path = calendar + "/events/" + event["id"]
    assert member.patch(path, json={"location": None, "title": "仍可整理自己的日程"}).status_code == 200
    assert owner.delete(home["base"] + "/members/" + event["createdBy"]["id"]).status_code == 204
    assert member.get(calendar, params=RANGE).status_code in (401, 403)
    assert member.delete(path).status_code in (401, 403)
    assert _fetch(owner, calendar)["events"] == []


def _routine_calendar(home, monkeypatch):
    root = home["root"]
    registry, service, routine = registered(root)
    now = datetime.fromisoformat("2027-03-13T00:00:00+00:00")
    monkeypatch.setattr(app_calendar, "Routines", lambda root: Routines(root, clock=lambda: now))
    return registry, service, routine


def test_calendar_projects_only_owner_visible_active_approved_routines(home, monkeypatch):
    root = home["root"]
    registry, service, routine = _routine_calendar(home, monkeypatch)
    active = {**routine, "schedule": "at 02:30", "timezone": "America/New_York"}
    service.register(active)
    service.register({**routine, "id": "routine-paused", "enabled": False})
    service.register({**routine, "id": "routine-retired"})
    service.control("routine-retired", 1, retire=True)
    StaffApprovals(root).propose("owner", "routine", {**routine, "id": "routine-draft"}, "Wait for the owner")
    path = registry.directory / "routines.json"
    before = path.read_bytes()
    owner, member = home["owner"], _join(home)
    calendar = home["base"] + "/calendar"
    query = {"from": "2027-03-12T00:00:00Z", "to": "2027-03-16T00:00:00Z"}

    agent = app_views.member_agent_id(root, routine["member"])
    result = _fetch(owner, calendar, **query)
    assert result["routines"] == [
        {"routineId": routine["id"], "name": routine["name"], "agentId": agent, "at": at,
         "target": {"type": "conversation", "channelId": app_views.dm_channel_id(agent), "threadId": None}}
        for at in ("2027-03-13T07:30:00.000Z", "2027-03-15T06:30:00.000Z")
    ]
    assert "routinesTruncated" not in result
    assert _fetch(member, calendar, **query)["routines"] == []
    assert TestClient(home["app"]).get(calendar, params=query).status_code == 401
    assert path.read_bytes() == before

    # A stale approval cannot silently attach the same schedule to a new role revision.
    member_binding = registry.member(routine["member"])
    registry.set_member_status(member_binding["id"], "paused", member_binding["revision"])
    assert _fetch(owner, calendar, **query)["routines"] == []


def test_unproven_owner_cannot_read_routine_titles(home, monkeypatch):
    _, service, routine = _routine_calendar(home, monkeypatch)
    name = "Private owner calendar title"
    service.register({**routine, "name": name})
    url = home["base"] + "/calendar"
    owner_cookie = TestClient(home["app"])
    assert owner_cookie.post("/api/auth/sign-in/email", json={"email": "", "password": TOKEN}).status_code == 200
    assert {row["name"] for row in _fetch(owner_cookie, url)["routines"]} == {name}
    denied = owner_cookie.get(url, params=RANGE, headers={"Origin": "http://other.test"})
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "owner_proof_required"
    assert name not in denied.text

    monkeypatch.delenv("VORTOONE_API_TOKEN")
    open_mode = TestClient(home["app"]).get(url, params=RANGE)
    assert open_mode.status_code == 200
    assert name not in open_mode.text
    assert open_mode.json()["routines"] == []


def test_calendar_hides_schedules_held_for_work_review_or_recovery(home, monkeypatch):
    registry, service, routine = _routine_calendar(home, monkeypatch)
    ledger = TaskLedger(home["root"])
    running = ledger.create("delegation", "Still working")
    awaiting = ledger.create("delegation", "Awaiting review")
    awaiting.status = "done"
    awaiting.collaboration = {"review": "pending"}
    assert ledger.save(awaiting)
    path = registry.directory / "routines.json"
    for suffix, delivery in [
        ("running", "notify"), ("review", "review"), ("missing", "notify"), ("takeover", "notify"),
        ("parked", "notify"), ("creator-left", "notify"), ("migration", "notify"), ("active", "notify"),
    ]:
        service.register({**routine, "id": f"routine-{suffix}", "delivery": delivery})
    snapshot = json.loads(path.read_bytes())
    rows = {row["id"]: row for row in snapshot["routines"]}
    rows["routine-running"]["state"]["task"] = running.id
    rows["routine-review"]["state"]["task"] = awaiting.id
    rows["routine-missing"]["state"]["task"] = "task-missing"
    rows["routine-takeover"]["state"]["takeover"] = {"awaiting_enable": True}
    rows["routine-parked"]["state"]["error"] = "Review interrupted execution"
    rows["routine-creator-left"]["initiator"] = "departed-member"
    snapshot["migrations"] = [{"id": "fixture", "status": "applying", "names": {},
                               "routines": ["routine-migration"], "cursors": {}}]
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    original = path.read_bytes()
    calendar, owner = home["base"] + "/calendar", home["owner"]

    assert {row["routineId"] for row in _fetch(owner, calendar)["routines"]} == {"routine-active"}
    running.status = "cancelled"
    assert ledger.save(running)
    awaiting.collaboration["review"] = "accepted"
    assert ledger.save(awaiting)
    assert {row["routineId"] for row in _fetch(owner, calendar)["routines"]} == {
        "routine-active", "routine-running", "routine-review",
    }
    assert path.read_bytes() == original


def test_calendar_handles_max_year_utc_and_rejects_unrepresentable_input(home, monkeypatch):
    _, service, routine = _routine_calendar(home, monkeypatch)
    service.configure_timezone("Pacific/Kiritimati", service.read()["revision"])
    service.register({**routine, "id": "routine-east", "schedule": "at 08:00", "timezone": "Pacific/Kiritimati"})
    service.register({**routine, "id": "routine-west", "schedule": "at 23:30", "timezone": "Etc/GMT+12"})
    url, owner = home["base"] + "/calendar", home["owner"]
    due = owner.post(home["base"] + "/issues", json={"title": "Year boundary", "dueDate": "9999-12-31"})
    assert due.status_code == 200, due.text
    result = _fetch(owner, url, **{"from": "9999-12-30T08:00:00Z", "to": "9999-12-31T23:30:00Z"})
    assert [(row["routineId"], row["at"]) for row in result["routines"]] == [
        ("routine-west", "9999-12-30T11:30:00.000Z"), ("routine-east", "9999-12-30T18:00:00.000Z"),
        ("routine-west", "9999-12-31T11:30:00.000Z"),
    ]
    assert [row["issueId"] for row in result["deadlines"]] == [due.json()["id"]]
    beyond_local = _fetch(owner, url, **{"from": "9999-12-31T23:00:00Z", "to": "9999-12-31T23:30:00Z"})
    assert beyond_local["deadlines"] == []
    assert beyond_local["routines"] == []
    invalid = owner.get(url, params={"from": "9999-12-31T23:00:00Z", "to": "9999-12-31T23:30:00-12:00"})
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "calendar.invalid"


def test_calendar_reports_the_routine_projection_limit_without_losing_appointments(home, monkeypatch):
    _, service, routine = _routine_calendar(home, monkeypatch)
    service.register({**routine, "id": "routine-b", "schedule": "every 1m"})
    service.register({**routine, "id": "routine-a", "schedule": "every 1m"})
    monkeypatch.setattr(app_calendar, "_MAX_ROUTINE_SLOTS", 2)
    calendar, owner = home["base"] + "/calendar", home["owner"]
    event = _create(owner, calendar)
    rows = _fetch(owner, calendar)
    assert rows["routinesTruncated"] is True
    assert [(slot["routineId"], slot["at"]) for slot in rows["routines"]] == [
        ("routine-a", "2027-03-13T00:00:00.000Z"), ("routine-b", "2027-03-13T00:00:00.000Z"),
    ]
    assert rows["events"] == [event]


def test_deadlines_apply_original_board_audience_and_range(home):
    owner = home["owner"]
    member = _join(home)
    calendar = home["base"] + "/calendar"
    issues = home["base"] + "/issues"

    def issue(title, due_date, audience):
        result = owner.post(issues, json={"title": title, "dueDate": due_date, "audience": audience})
        assert result.status_code == 200, result.text
        row = result.json()
        return {"issueId": row["id"], "number": row["number"], "title": title, "date": due_date, "assignee": None}

    public = issue("可见任务", "2027-03-15", {"mode": "everyone"})
    hidden = issue("私有任务", "2027-03-15", {
        "mode": "people", "userIds": [owner.get("/api/app/me").json()["user"]["id"]],
    })
    issue("范围之外", "2027-03-18", {"mode": "everyone"})
    assert _fetch(member, calendar)["deadlines"] == [public]
    assert _fetch(owner, calendar)["deadlines"] == [public, hidden]
