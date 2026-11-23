"""Workspace badges read the existing per-person counters."""
from fastapi.testclient import TestClient

from src.scheduling.notices import record_notice
from src.utils.state_dir import state_dir
from src.web.auth import SESSION_COOKIE
from tests.unit.test_member_agents import ORIGIN, PASSWORD, TOKEN, _join, home  # noqa: F401 - pytest fixture
from tests.unit.test_member_login import _invite
from tests.unit.test_team_channels import _create
from tests.unit.test_team_thread_reads import _post


PATH = "/api/app/me/unread"


def _expect(home, client, unread):
    response = client.get(PATH)
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json() == {"organizations": [
        {"orgId": home["base"].rsplit("/", 1)[1], "unread": unread, "waiting": 0},
    ]}
    accounts = client.get("/api/app/device-accounts")
    assert accounts.status_code == 200
    assert accounts.headers["Cache-Control"] == "no-store"
    assert accounts.json()["accounts"][0]["unread"] == response.json()["organizations"]


def test_workspace_unread_tracks_channel_thread_and_notice_read_positions(home):
    owner, member = home["owner"], _join(home)
    other = _join(home, email="other@example.com")
    shared = _create(home, "shared", people=[member.member_id])
    private = _create(home, "private", visibility="private", people=[other.member_id])
    root = _post(member, shared, "topic")
    _post(owner, shared, "topic reply", root)
    _post(owner, shared, "new channel message")
    _post(other, private, "private channel message")
    record_notice(home["root"], "private owner notice", source="cron:test")

    _expect(home, owner, 3)  # Shared and private channel messages plus the owner's notice.
    _expect(home, member, 2)  # Channel message and followed thread.
    _expect(home, other, 0)

    assert member.put(f"{shared}/mute", json={}).status_code == 204
    _expect(home, member, 1)
    assert member.post(f"{home['base']}/threads/{root}/read", json={}).status_code == 204
    _expect(home, member, 0)
    assert member.delete(f"{shared}/mute").status_code == 204
    _expect(home, member, 1)
    assert member.post(f"{shared}/read", json={}).status_code == 204
    _expect(home, member, 0)
    _expect(home, owner, 3)

    assert owner.post(f"{home['base']}/notifications/read-all", json={}).status_code == 200
    _expect(home, owner, 2)
    assert owner.delete(private).status_code == 204
    _expect(home, owner, 1)


def test_workspace_unread_requires_current_workspace_access(home, monkeypatch):
    owner = home["owner"]
    guest = TestClient(home["app"], headers=ORIGIN)
    assert guest.get(PATH).status_code == 401

    token = _invite(home)
    assert guest.post("/api/auth/sign-up/email", headers={"x-invitation-token": token}, json={
        "name": "Pending", "email": "ana@example.com", "password": PASSWORD,
    }).status_code == 200
    pending = guest.get(PATH)
    assert pending.status_code == 200 and pending.json() == {"organizations": []}
    assert guest.post(f"/api/app/invitations/{token}/accept", json={}).status_code == 200
    _expect(home, guest, 0)
    assert guest.get(PATH, headers={"Origin": "https://other.example"}).status_code == 403

    owner_session = TestClient(home["app"], cookies={SESSION_COOKIE: TOKEN})
    _expect(home, owner_session, 0)
    assert owner_session.get(PATH, headers={"Origin": "https://other.example"}).status_code == 403

    member_id = guest.get("/api/app/me").json()["user"]["id"]
    assert owner.delete(f"{home['base']}/members/{member_id}").status_code == 204
    assert guest.get(PATH).status_code == 401
    monkeypatch.delenv("VORTOONE_API_TOKEN")
    assert TestClient(home["app"]).get(PATH).status_code == 403


def test_workspace_unread_reports_an_unavailable_inbox_only_to_its_owner(home):
    member = _join(home)
    notices = state_dir(home["root"]) / "logs" / "notice-inbox.json"
    notices.parent.mkdir(parents=True, exist_ok=True)
    notices.write_text("broken", encoding="utf-8")
    response = home["owner"].get(PATH)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "unavailable"
    accounts = home["owner"].get("/api/app/device-accounts")
    assert accounts.status_code == 200
    [owner] = accounts.json()["accounts"]
    assert owner["current"] and owner["organizations"] and owner["unread"] == []
    _expect(home, member, 0)
