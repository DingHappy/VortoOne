"""Calendar uses the Desktop payload with live owner and member sessions."""

import json
import sqlite3
from contextlib import closing
from datetime import datetime
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


def test_calendar_rejects_invites_and_unimplemented_rules(home):
    owner = home["owner"]
    member = _join(home)
    calendar = home["base"] + "/calendar"
    own = _create(owner, calendar)
    path = calendar + "/events/" + own["id"]
    for payload in ({"recurrence": "weekly"}, {"rule": {"freq": "weekly"}},
                    {"rule": _series_rule(freq="monthly", timezone="UTC")},
                    {"rule": _series_rule(count=3, timezone="UTC")},
                    {"rule": _series_rule(times=["09:00", "16:00"], timezone="UTC")},
                    {"participants": own["participants"] + _body(member)["participants"]},
                    {"channelId": "channel-general"}, {"issueId": "issue-test"}):
        assert owner.post(calendar + "/events", json=_body(owner, **payload)).status_code == 409
        assert owner.patch(path, json=payload).status_code == 409
    assert owner.patch(path, json={"scope": "all", "title": "ignored"}).status_code == 409
    assert owner.delete(path, params={"scope": "all", "occurrenceStart": own["start"]}).status_code == 409
    assert owner.post(path + "/rsvp", json={"status": "accepted"}).status_code == 404
    assert _fetch(owner, calendar)["events"] == [own]
    assert _fetch(member, calendar)["events"] == []


def _series_rule(**changes):
    return {
        "freq": "daily", "interval": 1, "times": ["09:00"], "timezone": "America/New_York",
        "start": "2027-03-12", "until": None, "count": None, **changes,
    }


def _series_scope(event, scope):
    return {"scope": scope, "occurrenceStart": event["occurrenceStart"]}


def test_daily_desktop_series_has_stable_identity_and_local_time_across_dst(home):
    owner = home["owner"]
    calendar = home["base"] + "/calendar"
    single = _create(owner, calendar)
    storage = state_dir(home["root"]) / "calendar.sqlite3"
    with closing(sqlite3.connect(storage)) as conn, conn:
        row = json.loads(conn.execute("SELECT content FROM events WHERE id = ?", (single["id"],)).fetchone()[0])
        del row["rule"]
        conn.execute("UPDATE events SET content = ? WHERE id = ?", (json.dumps(row), single["id"]))
        conn.execute("DROP TABLE series_exceptions")
    assert _fetch(owner, calendar)["events"] == [single]
    # EventDialog posts actual browser instants alongside the selected civil schedule.
    event = _create(owner, calendar, title="每天上午", timezone="America/New_York", rule=_series_rule(),
                    start="2027-03-12T09:00:00.000Z", end="2027-03-12T10:00:00.000Z")
    expected = [
        "2027-03-12T14:00:00.000Z", "2027-03-13T14:00:00.000Z", "2027-03-14T13:00:00.000Z",
        "2027-03-15T13:00:00.000Z", "2027-03-16T13:00:00.000Z", "2027-03-17T13:00:00.000Z",
    ]
    all_events = _fetch(owner, calendar)["events"]
    assert all_events[0] == single
    rows = all_events[1:]
    assert [row["start"] for row in rows] == expected
    assert all(row["id"] == row["seriesId"] == event["id"] for row in rows)
    assert [row["occurrenceStart"] for row in rows] == expected
    assert rows[0] == event
    assert rows[2]["rule"]["start"] == "2027-03-14"
    assert rows[2]["end"] == "2027-03-14T14:00:00.000Z"

    path = calendar + "/events/" + event["id"]
    assert owner.patch(path, json={"title": "未指定范围"}).status_code == 409
    assert owner.delete(path).status_code == 409
    assert owner.patch(path, json={"title": "无效日期", "scope": "all",
                                   "occurrenceStart": "2027-03-14T14:00:00Z"}).status_code == 404
    member = _join(home)
    assert _fetch(member, calendar)["events"] == []
    assert member.patch(path, json={"title": "其他成员", **_series_scope(rows[0], "all")}).status_code == 404
    assert member.delete(path, params=_series_scope(rows[0], "all")).status_code == 404
    assert _fetch(owner, calendar)["events"] == all_events


def test_daily_gap_and_fall_back_are_resolved_as_civil_time(home):
    owner = home["owner"]
    calendar = home["base"] + "/calendar"
    _create(owner, calendar, title="春季", timezone="America/New_York",
            rule=_series_rule(times=["02:30"], start="2027-03-13", until="2027-03-15"))
    _create(owner, calendar, title="秋季", timezone="America/New_York",
            rule=_series_rule(times=["01:30"], start="2027-11-06", until="2027-11-08"))
    spring = _fetch(owner, calendar)["events"]
    assert [row["start"] for row in spring] == ["2027-03-13T07:30:00.000Z", "2027-03-15T06:30:00.000Z"]
    fall = _fetch(owner, calendar, **{"from": "2027-11-06T00:00:00Z", "to": "2027-11-09T00:00:00Z"})["events"]
    assert [row["start"] for row in fall] == [
        "2027-11-06T05:30:00.000Z", "2027-11-07T05:30:00.000Z", "2027-11-08T06:30:00.000Z",
    ]


def test_weekly_all_day_series_anchors_to_monday_and_keeps_civil_dates(home):
    owner = home["owner"]
    calendar = home["base"] + "/calendar"
    event = _create(owner, calendar, allDay=True, timezone="America/New_York",
                    startDate="2027-03-12", endDate="2027-03-13",
                    rule=_series_rule(freq="weekly", interval=2, byWeekday=["SU", "MO"], times=["00:00"]))
    rows = _fetch(owner, calendar, **{"from": "2027-03-12T00:00:00Z", "to": "2027-04-01T00:00:00Z"})["events"]
    assert [row["startDate"] for row in rows] == ["2027-03-14", "2027-03-22", "2027-03-28"]
    assert event == rows[0]
    assert event["start"] == "2027-03-14T05:00:00.000Z"
    assert event["endDate"] == "2027-03-15"
    assert event["end"] == "2027-03-16T04:00:00.000Z"
    assert sum(row["id"] == event["id"] for row in rows) == 3


def test_weekday_picker_preset_skips_weekends(home):
    owner = home["owner"]
    calendar = home["base"] + "/calendar"
    event = _create(owner, calendar, timezone="America/New_York",
                    rule=_series_rule(byWeekday=["MO", "TU", "WE", "TH", "FR"]))
    rows = _fetch(owner, calendar)["events"]
    assert rows[0] == event
    assert [row["start"] for row in rows] == [
        "2027-03-12T14:00:00.000Z", "2027-03-15T13:00:00.000Z",
        "2027-03-16T13:00:00.000Z", "2027-03-17T13:00:00.000Z",
    ]


def test_series_edit_and_delete_scopes_apply_only_to_selected_occurrences(home):
    owner = home["owner"]
    calendar = home["base"] + "/calendar"
    event = _create(owner, calendar, timezone="America/New_York", rule=_series_rule(), title="原件")
    path = calendar + "/events/" + event["id"]
    rows = _fetch(owner, calendar)["events"]
    one = owner.patch(path, json={**_series_scope(rows[1], "this"), "title": "仅本次",
                                 "start": "2027-03-13T16:00:00Z", "end": "2027-03-13T17:00:00Z",
                                 "rule": {**rows[1]["rule"], "times": ["11:00"]}})
    assert one.status_code == 200, one.text
    assert one.json()["rule"] is None and one.json()["id"] != event["id"]
    after_one = _fetch(owner, calendar)["events"]
    assert [row["title"] for row in after_one] == ["原件", "仅本次", "原件", "原件", "原件", "原件"]
    assert after_one[1]["start"] == "2027-03-13T16:00:00.000Z"

    following = owner.patch(path, json={"title": "此后", **_series_scope(rows[3], "following")})
    assert following.status_code == 200, following.text
    new_path = calendar + "/events/" + following.json()["id"]
    later = _fetch(owner, calendar)["events"][-1]
    # Editing text from a browser in another zone can echo a different clock time.
    finalized = owner.patch(new_path, json={"title": "整组", **_series_scope(later, "all"),
                                         "rule": {**later["rule"], "times": ["13:00"]}})
    assert finalized.status_code == 200, finalized.text
    after_edit = _fetch(owner, calendar)["events"]
    assert [row["title"] for row in after_edit] == ["原件", "仅本次", "原件", "整组", "整组", "整组"]
    assert after_edit[-1]["start"] == later["start"]

    assert owner.delete(new_path, params=_series_scope(after_edit[4], "this")).status_code == 204
    assert owner.delete(new_path, params=_series_scope(after_edit[5], "following")).status_code == 204
    after_delete = _fetch(owner, calendar)["events"]
    assert [row["title"] for row in after_delete] == ["原件", "仅本次", "原件", "整组"]
    assert owner.delete(path, params=_series_scope(after_delete[2], "all")).status_code == 204
    assert [row["title"] for row in _fetch(owner, calendar)["events"]] == ["整组"]


def test_weekly_grid_move_updates_initial_rule_without_affecting_other_people(home):
    member = _join(home)
    calendar = home["base"] + "/calendar"
    event = _create(member, calendar, timezone="America/New_York", rule=_series_rule(freq="weekly", byWeekday=["MO"]))
    assert event["start"] == "2027-03-15T13:00:00.000Z"
    path = calendar + "/events/" + event["id"]
    moved = member.patch(path, json={**_series_scope(event, "all"),
                                    "start": "2027-03-16T16:30:00Z", "end": "2027-03-16T17:30:00Z"})
    assert moved.status_code == 200, moved.text
    assert moved.json()["rule"]["byWeekday"] == ["TU"]
    assert moved.json()["rule"]["times"] == ["12:30"]
    checked = moved.json()
    assert _fetch(member, calendar)["events"] == [checked]
    assert _fetch(home["owner"], calendar)["events"] == []
    outside = member.get(calendar, params={"from": "2029-01-01T00:00:00Z", "to": "2029-01-03T00:00:00Z"})
    assert outside.status_code == 200
    assert outside.json()["events"][0]["start"] == "2029-01-02T17:30:00.000Z"


def test_following_split_moves_future_exceptions_and_rollback_keeps_original(home, monkeypatch):
    from src.workflow.private_calendar import PrivateCalendar

    owner = home["owner"]
    calendar = home["base"] + "/calendar"
    item = _create(owner, calendar, timezone="America/New_York", rule=_series_rule(), title="原始")
    path = calendar + "/events/" + item["id"]
    rows = _fetch(owner, calendar)["events"]
    changed = owner.patch(path, json={"title": "例外", **_series_scope(rows[4], "this")})
    assert changed.status_code == 200, changed.text
    exception_path = calendar + "/events/" + changed.json()["id"]
    assert owner.patch(exception_path, json={"rule": _series_rule()}).status_code == 409
    original = _fetch(owner, calendar)["events"]
    scope = {"title": "新系列", **_series_scope(rows[2], "following")}

    def refuse_write(self, conn, event_id, data):
        raise sqlite3.OperationalError("tested write rollback")

    with monkeypatch.context() as fail:
        fail.setattr(PrivateCalendar, "_replace", refuse_write)
        assert owner.patch(path, json=scope).status_code == 409
    assert _fetch(owner, calendar)["events"] == original

    accepted = owner.patch(path, json=scope)
    assert accepted.status_code == 200, accepted.text
    after_split = _fetch(owner, calendar)["events"]
    assert [row["title"] for row in after_split] == ["原始", "原始", "新系列", "新系列", "例外", "新系列"]
    new_path = calendar + "/events/" + accepted.json()["id"]
    assert owner.delete(new_path, params=_series_scope(after_split[3], "following")).status_code == 204
    assert [row["title"] for row in _fetch(owner, calendar)["events"]] == ["原始", "原始", "新系列"]
    assert owner.delete(exception_path).status_code == 404


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
