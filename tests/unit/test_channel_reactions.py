"""The Desktop reaction contract follows the target message's channel and caller."""

import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from src.workflow.members.registry import MemberRegistry
from tests.unit.test_member_agents import ORIGIN, _join, home  # noqa: F401 - pytest fixture
from tests.unit.test_team_channels import _create, _override


def _message(home, channel, *, thread=None):
    result = home["owner"].post(f"{channel}/messages", json={"body": "频道消息", "threadId": thread})
    assert result.status_code == 200, result.text
    return result.json()["id"]


def _reactions(home, message):
    return f"{home['base']}/messages/{message}/reactions"


def test_reactions_persist_on_thread_and_pin_and_delete_only_the_callers_row(home):
    owner, member = home["owner"], _join(home)
    channel = _create(home, "reactions", people=[member.member_id])
    root = _message(home, channel)
    reply = _message(home, channel, thread=root)
    url = _reactions(home, reply)
    identity = owner.get("/api/app/me").json()["user"]["id"]

    empty = owner.get(url)
    assert empty.status_code == 200 and empty.json() == {"reactions": []}
    assert empty.headers["cache-control"] == "no-store"
    own = owner.post(url, json={"reaction": "🎉"})
    assert own.status_code == 200
    assert own.json() == {"reactions": [{"reaction": "🎉", "count": 1, "userIds": [identity]}]}
    for _ in range(2):
        added = member.post(url, json={"reaction": "🎉"})
        assert added.status_code == 200, added.text
        [group] = added.json()["reactions"]
        assert group["reaction"] == "🎉" and group["count"] == 2
        assert set(group["userIds"]) == {identity, member.member_id}
    assert member.get(url).json() == added.json()
    messages = owner.get(f"{channel}/messages", params={"threadId": root}).json()["messages"]
    assert messages[-1]["id"] == reply and messages[-1]["reactions"] == [group]
    pinned = owner.post(f"{channel}/pins/{reply}").json()["message"]
    assert pinned["reactions"] == [group]
    assert owner.get(f"{channel}/pins").json()["pins"][0]["message"]["reactions"] == [group]
    for _ in range(2):
        removed = member.delete(url, params={"reaction": "🎉"})
        assert removed.status_code == 200 and removed.json() == own.json()
    assert owner.delete(url, params={"reaction": "🎉"}).json() == {"reactions": []}
    assert owner.get(_reactions(home, root)).json() == {"reactions": []}


def test_reactions_require_visible_active_message_membership_and_post_for_writes(home):
    owner, member = home["owner"], _join(home)
    outsider = _join(home, "bo@example.com", "小博")
    channel = _create(home, "private-reactions", visibility="private", people=[member.member_id])
    url = _reactions(home, _message(home, channel))
    assert member.post(url, json={"reaction": "+1"}).status_code == 200
    for client, path, code in [(outsider, url, 404), (member, _reactions(home, "missing"), 404),
                               (TestClient(home["app"]), url, 401)]:
        assert client.get(path).status_code == code
        assert client.post(path, json={"reaction": "-1"}).status_code == code
        assert client.delete(path, params={"reaction": "+1"}).status_code == code

    assert _override(owner, channel, "person", member.member_id, deny=["post"]).status_code == 200
    assert member.get(url).status_code == 200
    assert member.post(url, json={"reaction": "-1"}).status_code == 403
    assert member.delete(url, params={"reaction": "+1"}).status_code == 403
    assert outsider.post(url, headers={"Origin": "https://elsewhere.example"}, json={"reaction": "+1"}).status_code == 403
    assert owner.delete(channel).status_code == 204
    assert owner.get(url).status_code == 404
    assert owner.post(url, json={"reaction": "🎉"}).status_code == 404
    assert owner.delete(url, params={"reaction": "+1"}).status_code == 404


@pytest.mark.parametrize("value", ["text", "😀\u202e", "😀 ", "😀\n"])
def test_reactions_reject_non_emoji_and_hidden_controls(home, value):
    channel = _create(home, "emoji-validation")
    url = _reactions(home, _message(home, channel))
    owner = home["owner"]
    assert owner.post(url, json={"reaction": value}).status_code == 400
    assert owner.delete(url, params={"reaction": value}).status_code == 400
    assert owner.post(url, json={"reaction": "👍", "userId": "another-person"}).status_code == 422
    assert owner.get(url).json() == {"reactions": []}


def test_reactions_lazily_upgrade_existing_history(home):
    channel = _create(home, "existing-history")
    url = _reactions(home, _message(home, channel))
    storage = MemberRegistry(home["root"]).directory / "channel-messages.sqlite3"
    with closing(sqlite3.connect(storage)) as conn:
        conn.executescript("DROP TABLE message_reaction_changes; DROP TABLE message_reactions;")
    owner = home["owner"]
    assert owner.get(url).json() == {"reactions": []}
    with closing(sqlite3.connect(storage)) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'message_reactions'").fetchone() is None
    emojis = {"👩‍💻", "1️⃣", "☕", "‼️", "⁉️"}
    for emoji in emojis:
        assert owner.post(url, json={"reaction": emoji}).status_code == 200
    assert {group["reaction"] for group in owner.get(url).json()["reactions"]} == emojis


def test_reactions_emit_existing_event_only_to_authorized_subscribers(home, monkeypatch):
    from src.web import app_channels

    subscribe = app_channels.subscribe

    async def fast_subscribe(*args, **kwargs):
        kwargs["interval"] = 0.01
        return await subscribe(*args, **kwargs)

    monkeypatch.setattr(app_channels, "subscribe", fast_subscribe)
    owner, member = home["owner"], _join(home)
    channel = _create(home, "realtime-reactions", visibility="private", people=[member.member_id])
    channel_id = channel.rsplit("/", 1)[1]
    message = _message(home, channel)
    url = _reactions(home, message)

    with member.websocket_connect(f"/api/ws?orgId={home['base'].rsplit('/', 1)[1]}", headers=ORIGIN) as socket:
        assert socket.receive_json()["userId"] == member.member_id
        socket.send_json({"type": "subscribe", "channelId": channel_id})
        assert socket.receive_json() == {"type": "subscribed", "channelId": channel_id}
        added = owner.post(url, json={"reaction": "🎉"}).json()
        assert socket.receive_json() == {
            "type": "event", "channelId": channel_id,
            "event": {"type": "message.reactions", "channelId": channel_id, "messageId": message, **added},
        }
        assert owner.delete(url, params={"reaction": "🎉"}).status_code == 200
        assert socket.receive_json()["event"]["reactions"] == []
        assert owner.delete(f"{channel}/members/people/{member.member_id}").status_code == 204
        assert owner.post(url, json={"reaction": "🎉"}).status_code == 200
        assert socket.receive_json() == {"type": "error", "code": "channel_access_lost", "channelId": channel_id}
