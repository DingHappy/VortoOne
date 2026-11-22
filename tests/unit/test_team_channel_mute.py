"""The Desktop mute flag is private, durable, and independent of channel read progress."""
from fastapi.testclient import TestClient

from src.web.app_channels import channels
from src.web.auth import SESSION_COOKIE
from tests.unit.test_member_agents import ORIGIN, PASSWORD, TOKEN, _join, home  # noqa: F401 - pytest fixture
from tests.unit.test_team_channels import _create, _list, _override


def _mute(client, channel, muted):
    response = client.put(f"{channel}/mute", json={}) if muted else client.delete(f"{channel}/mute")
    assert response.status_code == 204, response.text
    assert not response.content and response.headers["cache-control"] == "no-store"


def _row(home, client, channel):
    return _list(home, client)[channel.rsplit("/", 1)[1]]


def test_mute_does_not_clear_unread_and_is_private_per_person(home):
    owner, member = home["owner"], _join(home)
    channel = _create(home, "quiet-channel", people=[member.member_id])
    before = _row(home, member, channel)
    assert before["muted"] is False and before["unreadCount"] == 0
    assert owner.post(f"{channel}/messages", json={"body": "新消息"}).status_code == 200
    assert _row(home, member, channel)["unreadCount"] == 1

    _mute(member, channel, True)
    _mute(member, channel, True)
    muted = _row(home, member, channel)
    assert muted["muted"] is True and muted["unreadCount"] == 1
    assert muted["lastReadAt"] == before["lastReadAt"]
    assert _row(home, owner, channel)["muted"] is False
    member_again = TestClient(home["app"], headers=ORIGIN)
    assert member_again.post("/api/auth/sign-in/email", json={
        "email": "ana@example.com", "password": PASSWORD,
    }).status_code == 200
    assert _row(home, member_again, channel)["muted"] is True

    _mute(owner, channel, True)
    browser_owner = TestClient(home["app"], headers=ORIGIN, cookies={SESSION_COOKIE: TOKEN})
    assert _row(home, browser_owner, channel)["muted"] is True
    _mute(member, channel, False)
    _mute(member, channel, False)
    unmuted = _row(home, member, channel)
    assert unmuted["muted"] is False and unmuted["unreadCount"] == 1
    assert _row(home, owner, channel)["muted"] is True

    _mute(member, channel, True)
    assert member.post(f"{channel}/read", json={}).status_code == 204
    marked = _row(home, member, channel)
    assert marked["muted"] is True and marked["unreadCount"] == 0
    assert marked["lastReadAt"] > before["lastReadAt"]


def test_old_channel_history_defaults_to_unmuted_without_rewriting(home):
    owner = home["owner"]
    channel = _create(home, "old-history")
    history = channels(home["root"]).history
    assert _row(home, owner, channel)["muted"] is False
    _mute(owner, channel, False)
    assert not history.path.exists()

    assert owner.post(f"{channel}/messages", json={"body": "旧消息"}).status_code == 200
    with history.database(write=True) as conn:
        conn.execute("DROP TABLE channel_mutes")
    assert _row(home, owner, channel)["muted"] is False
    _mute(owner, channel, False)
    with history.database() as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'channel_mutes'").fetchone() is None
    _mute(owner, channel, True)
    _mute(owner, channel, True)
    with history.database() as conn:
        assert conn.execute("SELECT count(*) FROM channel_mutes").fetchone()[0] == 1
    assert _row(home, owner, channel)["muted"] is True


def test_mute_requires_joined_active_access_and_never_targets_another_person(home, monkeypatch):
    owner, member = home["owner"], _join(home)
    channel = _create(home, "public-mute")
    url = f"{channel}/mute"
    assert member.put(url, json={}).status_code == 403
    assert member.delete(url).status_code == 403
    assert member.post(f"{channel}/join", json={}).status_code == 204
    assert _override(owner, channel, "person", member.member_id, deny=["post"]).status_code == 200
    assert member.put(url, json={"userId": owner.get("/api/app/me").json()["user"]["id"]}).status_code == 422
    assert member.put(url, json={}, headers={"Origin": "https://elsewhere.invalid"}).status_code == 403
    assert TestClient(home["app"]).put(url, json={}).status_code == 401
    _mute(member, channel, True)

    assert member.post(f"{channel}/leave", json={}).status_code == 204
    assert not _row(home, member, channel).get("muted")
    assert member.put(url, json={}).status_code == 403
    assert member.delete(url).status_code == 403
    assert member.post(f"{channel}/join", json={}).status_code == 204
    assert _row(home, member, channel)["muted"] is True

    assert owner.delete(channel).status_code == 204
    assert owner.put(url, json={}).status_code == 404
    assert member.delete(url).status_code == 404
    assert owner.put(f"{home['base']}/channels/{home['channel']}/mute", json={}).status_code == 404
    monkeypatch.delenv("VORTOONE_API_TOKEN")
    assert TestClient(home["app"]).put(url, json={}).status_code == 403
