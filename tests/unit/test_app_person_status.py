"""Saved human status follows the authenticated person and the Desktop connection."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from src.web.auth import SESSION_COOKIE
from tests.unit.test_member_login import ORIGIN, PASSWORD, TOKEN, _invite, _join
from tests.unit.test_member_login import home  # noqa: F401 — member fixture


def _people(client, base):
    response = client.get(f"{base}/people")
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    return {row["userId"]: row for row in response.json()["people"]}


def test_members_see_a_connected_status_and_hide_it_from_others(home):
    base, owner = home["base"], home["owner"]
    member = _join(home)
    member_id = member.get("/api/app/me").json()["user"]["id"]
    status_path = f"{base}/me/status"
    with member.websocket_connect(f"/api/ws?orgId={home['org']}") as socket:
        assert socket.receive_json()["userId"] == member_id
        changed = member.put(status_path, json={"availability": "busy", "status": {
            "text": "  集中工作 👩‍💻  ", "emoji": "🎧", "until": "2099-01-01T12:00:00Z",
        }})
        assert changed.status_code == 200
        assert changed.headers["Cache-Control"] == "no-store"
        mine = changed.json()
        assert mine["userId"] == member_id
        assert (mine["availability"], mine["presence"], mine["online"]) == ("busy", "busy", True)
        assert mine["status"] == {"text": "集中工作 👩‍💻", "emoji": "🎧", "until": "2099-01-01T12:00:00Z"}
        viewed = _people(owner, base)[member_id]
        assert (viewed["presence"], viewed["status"]) == ("busy", mine["status"])
        assert "availability" not in viewed

        hidden = member.put(status_path, json={"availability": "hidden"}).json()
        assert (hidden["presence"], hidden["status"]) == ("hidden", mine["status"])
        viewed = _people(owner, base)[member_id]
        assert (viewed["presence"], viewed["status"], viewed["lastSeenAt"]) == ("offline", None, None)
        assert "availability" not in viewed

        owner_updated = owner.put(status_path, json={"availability": "away"}).json()
        assert owner_updated["userId"] != member_id
        assert _people(member, base)[member_id] == hidden
        assert member.put(status_path, json={"availability": "auto", "status": None}).json()["presence"] == "online"
        assert _people(owner, base)[member_id]["presence"] == "online"
    assert _people(owner, base)[member_id]["presence"] == "offline"


def test_status_expiry_and_read_after_a_new_session(home, monkeypatch):
    from src.web import app_prefs

    class StatusClock(datetime):
        year_now = 2028

        @classmethod
        def now(cls, tz=None):
            return datetime(cls.year_now, 1, 1, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(app_prefs, "datetime", StatusClock)
    owner, base = home["owner"], home["base"]
    path = f"{base}/me/status"
    status = {"text": "休息", "emoji": None, "until": "2029-01-01T00:00:00+00:00"}
    result = owner.put(path, json={"availability": "out", "status": status})
    assert result.status_code == 200
    assert result.json()["status"] == status
    StatusClock.year_now = 2030
    assert _people(owner, base)[result.json()["userId"]]["status"] is None
    saved = owner.put(path, json={"status": {"text": "假期"}}).json()
    assert saved["availability"] == "out"
    another = TestClient(home["app"], headers={"Authorization": f"Bearer {TOKEN}"})
    assert _people(another, base)[saved["userId"]] == saved
    assert owner.put(path, json={"status": None}).json()["status"] is None


@pytest.mark.parametrize("body", [
    {},
    {"userId": "someone-else", "availability": "away"},
    {"availability": "offline"},
    {"availability": "away", "status": {"text": "line", "until": "2030-01-01"}},
    {"status": {"text": "line", "until": "tomorrow"}},
    {"status": {"text": "x" * 101}},
    {"status": {"text": "two\nlines"}},
    {"status": {"text": "line", "emoji": {}}},
])
def test_invalid_status_keeps_previous_prefs(home, body):
    from src.utils.state_dir import state_dir

    owner, base = home["owner"], home["base"]
    path = f"{base}/me/status"
    saved = owner.put(path, json={"availability": "out", "status": {"text": "例行休息"}})
    assert saved.status_code == 200
    person_id = saved.json()["userId"]
    prefs = state_dir(home["root"]) / "app_prefs.json"
    before = prefs.read_bytes()
    assert owner.put(path, json=body).status_code == 400
    person = _people(owner, base)[person_id]
    assert (person["availability"], person["presence"], person["status"]) == (
        "out", "out", {"text": "例行休息", "emoji": None, "until": None},
    )
    assert prefs.read_bytes() == before


def test_pending_members_and_cross_site_cookies_cannot_set_status(home):
    base = home["base"]
    path = f"{base}/me/status"
    status = {"availability": "away"}
    guest = TestClient(home["app"], headers=ORIGIN)
    assert guest.put(path, json=status).status_code == 401
    token = _invite(home)
    signed = guest.post("/api/auth/sign-up/email", headers={"x-invitation-token": token}, json={
        "name": "待加入成员", "email": "ana@example.com", "password": PASSWORD,
    })
    assert signed.status_code == 200
    assert guest.put(path, json=status).status_code == 403
    assert guest.get(f"{base}/people").status_code == 403

    owner_cookie = TestClient(home["app"], cookies={SESSION_COOKIE: TOKEN})
    assert owner_cookie.put(path, json=status, headers={"Origin": "https://foreign.example"}).status_code == 403
    assert owner_cookie.put(path, json=status, headers=ORIGIN).status_code == 200


@pytest.mark.parametrize("whole_file", [True, False])
def test_damaged_preferences_do_not_disclose_someone_as_active(home, whole_file):
    import json

    from src.utils.state_dir import state_dir

    owner, base = home["owner"], home["base"]
    member = _join(home)
    member_id = member.get("/api/app/me").json()["user"]["id"]
    path = state_dir(home["root"]) / "app_prefs.json"
    assert member.put(f"{base}/me/status", json={"availability": "hidden"}).status_code == 200
    damaged = "{" if whole_file else json.dumps({"version": 1, "people": {member_id: "damaged"}})
    path.write_text(damaged, encoding="utf-8")
    with member.websocket_connect(f"/api/ws?orgId={home['org']}") as socket:
        assert socket.receive_json()["userId"] == member_id
        viewed = _people(owner, base)[member_id]
        assert (viewed["presence"], viewed["status"]) == ("offline", None)
        assert member.put(f"{base}/me/status", json={"availability": "away"}).status_code == 400
        assert path.read_text(encoding="utf-8") == damaged
