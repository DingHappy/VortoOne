"""Calendar uses the Desktop payload with live owner and member sessions."""

from stat import S_IMODE

import pytest
from fastapi.testclient import TestClient

from src.utils.state_dir import state_dir
from tests.unit.test_member_login import ORIGIN, PASSWORD, TOKEN, _join, home  # noqa: F401

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
