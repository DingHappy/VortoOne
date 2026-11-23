"""Desktop person-to-person chat and persistent read receipts."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from src.web.auth import SESSION_COOKIE
from src.workflow.members.registry import MemberRegistry
from tests.unit.test_app_board import _drop_from_everyone
from tests.unit.test_member_agents import ORIGIN, OWNER_P, TOKEN, _join, home  # noqa: F401 - fixture


def _open(home, client, other_id):
    response = client.post(home["base"] + "/dms", json={"userId": other_id})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["dm"]["with"]["id"] == other_id
    return response.json()["channelId"]


def _path(home, channel_id, part):
    return f"{home['base']}/channels/{channel_id}/{part}"


def _send(home, client, channel_id, text, **options):
    response = client.post(_path(home, channel_id, "messages"), json={"body": text, **options})
    assert response.status_code == 200, response.text
    return response.json()


def _person_dms(home, client):
    response = client.get(home["base"] + "/dms")
    assert response.status_code == 200, response.text
    return [dm for dm in response.json()["dms"] if dm["with"]["type"] == "person"]


def test_pair_opens_one_conversation_and_delivers_text_without_dispatch(home, monkeypatch):
    first = _join(home, "first@example.test", "First")
    second = _join(home, "second@example.test", "Second")
    with ThreadPoolExecutor(max_workers=4) as pool:
        targets = [(first, second.member_id), (second, first.member_id)] * 2
        channels = list(pool.map(lambda pair: _open(home, *pair), targets))
    channel = channels[0]
    assert channels == [channel] * 4

    monkeypatch.setattr("src.web.routers.app_api._dispatch_ready",
                        lambda *_: pytest.fail("human DMs must never dispatch an agent"))
    message = _send(home, first, channel, "给同事一条消息 @VortoOne", clientId="desktop-1")
    assert _send(home, first, channel, message["body"], clientId="desktop-1") == message
    assert first.post(_path(home, channel, "messages"),
                      json={"body": "Changed retry", "clientId": "desktop-1"}).status_code == 400
    answer = _send(home, second, channel, "回复", clientId="desktop-1", replyTo=message["id"])
    response = first.get(_path(home, channel, "messages"))
    assert response.headers["cache-control"] == "no-store"
    assert [row["id"] for row in response.json()["messages"]] == [message["id"], answer["id"]]
    assert response.json()["messages"][1]["meta"] == {"replyTo": message["id"]}
    assert "clientId" not in response.json()["messages"][1]
    assert _person_dms(home, first)[0]["lastMessage"]["id"] == answer["id"]
    assert _person_dms(home, first)[0]["unread"] == 1
    assert home["pool"].jobs == {}

    assert first.get(_path(home, channel, "members")).json() == {
        "members": [{"type": "person", "id": first.member_id}, {"type": "person", "id": second.member_id}]}
    assert first.get(_path(home, channel, "active-runs")).json() == {"runs": []}
    assert first.get(_path(home, channel, "pins")).json() == {"pins": []}
    assert first.get(_path(home, channel, "threads")).json() == {"threads": [], "nextCursor": None}


def test_read_receipt_matches_persisted_peer_position_and_realtime_protocol(home):
    first = _join(home, "first@example.test")
    second = _join(home, "second@example.test")
    channel = _open(home, first, second.member_id)
    reads = _path(home, channel, "reads")
    mark = _path(home, channel, "read")
    assert _person_dms(home, second) == []
    assert first.get(reads).json() == {"reads": []}
    assert second.post(mark, json={}).status_code == 204
    assert first.get(reads).json() == {"reads": []}

    org = home["base"].rsplit("/", 1)[1]
    with first.websocket_connect(f"/api/ws?orgId={org}", headers=ORIGIN) as socket:
        assert socket.receive_json()["userId"] == first.member_id
        socket.send_json({"type": "subscribe", "channelId": channel})
        assert socket.receive_json() == {"type": "subscribed", "channelId": channel}
        sent = _send(home, first, channel, "Message with a read receipt")
        assert socket.receive_json()["event"]["messageId"] == sent["id"]
        assert _person_dms(home, second)[0]["unread"] == 1
        assert second.post(mark, json={}).status_code == 204
        expected = {"userId": second.member_id, "lastReadAt": sent["createdAt"]}
        assert socket.receive_json()["event"] == {"type": "channel.read", "channelId": channel, **expected}
        assert second.post(mark, json={}).status_code == 204

    restored = TestClient(home["app"], headers=ORIGIN)
    restored.cookies.update(first.cookies)
    assert restored.get(reads).json() == {"reads": [expected]}
    assert restored.get(reads).headers["cache-control"] == "no-store"
    assert second.get(reads).json() == {"reads": []}
    assert _person_dms(home, second)[0]["unread"] == 0


def test_admin_and_owner_are_not_participants_and_no_cross_origin_access(home):
    first = _join(home, "first@example.test")
    second = _join(home, "second@example.test")
    admin = _join(home, "admin@example.test")
    MemberRegistry(home["root"]).set_member_groups(OWNER_P, admin.member_id, ["admins"])
    owner = home["owner"]
    channel = _open(home, first, second.member_id)
    reads = _path(home, channel, "reads")
    message = _send(home, first, channel, "Private")
    owner_id = owner.get("/api/app/me").json()["user"]["id"]
    direct_with_owner = _open(home, first, owner_id)
    assert owner.post(home["base"] + "/dms", json={"userId": first.member_id}).json()["channelId"] == direct_with_owner
    assert first.post(_path(home, direct_with_owner, "messages"),
                      json={"body": "Cross-conversation reference", "replyTo": message["id"]}).status_code == 404

    for outsider in (owner, admin):
        for part in ("reads", "messages", "members", "active-runs"):
            assert outsider.get(_path(home, channel, part)).status_code == 404
        assert outsider.post(_path(home, channel, "read"), json={}).status_code == 404
        assert outsider.post(_path(home, channel, "messages"), json={"body": "Outside send"}).status_code == 404
        assert channel not in [row["channelId"] for row in _person_dms(home, outsider)]

    org = home["base"].rsplit("/", 1)[1]
    with admin.websocket_connect(f"/api/ws?orgId={org}", headers=ORIGIN) as socket:
        assert socket.receive_json()["type"] == "ready"
        socket.send_json({"type": "subscribe", "channelId": channel})
        assert socket.receive_json() == {"type": "error", "channelId": channel, "code": "channel_access_lost"}

    assert TestClient(home["app"]).get(reads).status_code == 401
    assert first.get(reads, headers={"Origin": "https://unrelated.example.test"}).status_code == 403
    owner_browser = TestClient(home["app"])
    owner_browser.cookies.set(SESSION_COOKIE, TOKEN)
    assert owner_browser.get(_path(home, direct_with_owner, "reads"),
                             headers={"Origin": "https://unrelated.example.test"}).status_code == 403
    assert owner.get(reads.replace(org, "unknown")).status_code == 404


def test_revoked_membership_and_existing_speak_permission_are_rechecked(home):
    first = _join(home, "first@example.test")
    second = _join(home, "second@example.test")
    channel = _open(home, first, second.member_id)
    reads = _path(home, channel, "reads")
    send = _path(home, channel, "messages")
    assert first.post(send, json={"body": "no media", "media": {"kind": "gif"}}).status_code == 422
    assert first.post(send, json={"body": "no threads", "threadId": "elsewhere"}).status_code == 422
    assert first.post(send, json={"body": "no tool", "steer": False}).status_code == 422
    _drop_from_everyone(home, "post")
    assert first.post(send, json={"body": "no permission"}).status_code == 403
    assert first.get(reads).json() == {"reads": []}

    org = home["base"].rsplit("/", 1)[1]
    with first.websocket_connect(f"/api/ws?orgId={org}", headers=ORIGIN) as socket:
        assert socket.receive_json()["type"] == "ready"
        socket.send_json({"type": "subscribe", "channelId": channel})
        assert socket.receive_json()["type"] == "subscribed"
        MemberRegistry(home["root"]).remove_member(OWNER_P, second.member_id)
        assert socket.receive_json() == {"type": "error", "channelId": channel, "code": "channel_access_lost"}
    assert first.get(reads).status_code == 404
    assert first.post(send, json={"body": "now former member"}).status_code == 404
    assert not _person_dms(home, first)
