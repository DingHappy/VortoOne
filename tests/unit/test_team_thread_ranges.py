"""Desktop can create, adjust and safely undo an in-channel selection."""

from fastapi.testclient import TestClient

from src.web.app_channels import channels
from tests.unit.test_member_agents import ORIGIN, _join, home  # noqa: F401 - pytest fixture
from tests.unit.test_team_channels import _create, _override


def _send(client, channel, text, **extra):
    response = client.post(f"{channel}/messages", json={"body": text, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def _selection(channel, first, last, **extra):
    return {"channelId": channel.rsplit("/", 1)[1], "firstMessageId": first["id"],
            "lastMessageId": last["id"], **extra}


def _create_range(client, home, selection):
    response = client.post(f"{home['base']}/threads", json=selection)
    assert response.status_code == 201, response.text
    thread = response.json()
    return f"{home['base']}/threads/{thread['id']}", thread


def test_member_can_create_adjust_and_undo_with_source_and_live_cursors_preserved(home):
    owner, member = home["owner"], _join(home)
    channel = _create(home, "range-work", people=[member.member_id])
    original = [_send(owner, channel, title) for title in ("起点", "排除", "结尾", "稍后")]
    first, excluded, last, later = original
    selection = _selection(channel, first, last, excludedMessageIds=[excluded["id"]], title="安排")
    cid = selection["channelId"]
    org = home["base"].rsplit("/", 1)[1]
    workspace_threads = f"{home['base']}/threads"

    with owner.websocket_connect(f"/api/ws?orgId={org}", headers=ORIGIN) as socket:
        socket.receive_json()
        socket.send_json({"type": "subscribe", "channelId": cid})
        assert socket.receive_json() == {"type": "subscribed", "channelId": cid}

        url, made = _create_range(member, home, selection)
        assert made["context"] == {"channelId": cid, "firstMessageId": first["id"], "lastMessageId": last["id"],
                                   "excludedMessageIds": [excluded["id"]], "count": 2, "moved": False}
        assert made["rootText"] == "起点" and made["title"] == "安排" and made["replyCount"] == 0
        assert made["createdBy"] == member.member_id and made["canManage"]
        assert _create_range(member, home, selection) == (url, made)
        assert member.get(url).json() == made
        assert member.get(workspace_threads, params={"mine": True}).json()["threads"] == [made]
        assert member.get(f"{channel}/threads").json()["threads"] == [made]

        created = socket.receive_json()["event"]
        assert created["type"] == "message.created" and created["messageId"] == made["id"]
        marker = created["message"]
        assert marker["meta"]["threadStart"] == {
            "title": "安排", "contextChannelId": cid, "firstMessageId": first["id"],
            "lastMessageId": last["id"], "count": 2, "moved": False, "movedCount": 0,
        }
        assert socket.receive_json()["event"] == {
            "type": "thread.updated", "channelId": cid, "threadId": made["id"], "change": "created",
        }
        assert owner.get(f"{channel}/messages").json()["messages"] == [*original, marker]
        assert owner.get(f"{channel}/messages", params={"threadId": made["id"]}).json()["messages"] == [marker]

        changed = member.patch(url, json={"firstMessageId": last["id"], "lastMessageId": later["id"],
                                           "excludedMessageIds": [], "title": "   调整 范围  "})
        assert changed.status_code == 200, changed.text
        revised = changed.json()
        assert revised["id"] == made["id"] and revised["title"] == "调整 范围"
        assert revised["context"]["firstMessageId"] == last["id"] and revised["context"]["count"] == 2
        assert socket.receive_json()["event"]["change"] == "context"
        reopened = TestClient(home["app"], headers=owner.headers)
        assert reopened.get(url).json()["context"] == revised["context"]
        assert reopened.get(f"{channel}/messages").json()["messages"][-1]["meta"]["threadStart"]["title"] == "调整 范围"

        undone = member.delete(url)
        assert undone.status_code == 204, undone.text
        assert socket.receive_json()["event"] == {
            "type": "thread.updated", "channelId": cid, "threadId": made["id"], "change": "deleted",
        }
        assert owner.get(url).status_code == 404
        assert owner.get(workspace_threads).json()["threads"] == []
        assert owner.get(f"{channel}/messages").json()["messages"] == original
        assert member.post(f"{channel}/messages", json={"body": "不再可回复", "threadId": made["id"]}).status_code == 404
        after = _send(owner, channel, "后续消息")
        assert after["sequence"] > marker["sequence"]
        assert socket.receive_json()["event"]["messageId"] == after["id"]
    assert not home["pool"].jobs


def test_range_uses_current_channel_permissions_and_rejects_invalid_ranges(home):
    owner, first_member = home["owner"], _join(home)
    second_member = _join(home, "second@example.test")
    outsider = _join(home, "outsider@example.test")
    channel = _create(home, "range-private", visibility="private",
                      people=[first_member.member_id, second_member.member_id])
    other = _create(home, "other-range-private", visibility="private")
    first, last = [_send(owner, channel, title) for title in ("看得见的起点", "看得见的终点")]
    secret = _send(owner, other, "另一频道")
    selection = _selection(channel, first, last)
    route = f"{home['base']}/threads"
    anonymous = TestClient(home["app"])
    assert anonymous.post(route, json=selection).status_code == 401
    assert outsider.post(route, json=selection).status_code == 404
    assert first_member.post(route, json=selection, headers={"Origin": "https://elsewhere.invalid"}).status_code == 403
    assert _override(owner, channel, "person", second_member.member_id, deny=["post"]).status_code == 200
    assert second_member.post(route, json=selection).status_code == 403
    assert owner.post(route, json={**selection, "contextChannelId": other.rsplit("/", 1)[1]}).status_code == 400
    assert first_member.post(route, json={**selection, "lastMessageId": secret["id"]}).status_code == 404
    assert first_member.post(route, json=_selection(channel, last, first)).status_code == 400
    assert first_member.post(route, json={**selection, "excludedMessageIds": [first["id"]]}).status_code == 400
    assert owner.post(route, json={**selection, "channelId": home["channel"]}).status_code == 404

    url, made = _create_range(first_member, home, selection)
    for client, status in ((anonymous, 401), (outsider, 404), (second_member, 403)):
        assert client.patch(url, json={"lastMessageId": last["id"]}).status_code == status
        assert client.delete(url).status_code == status
    assert first_member.patch(url, json={"firstMessageId": made["id"]}).status_code == 400
    assert first_member.patch(url, json={"firstMessageId": None}).status_code == 422
    assert first_member.get(url).json() == made
    assert _override(owner, channel, "person", first_member.member_id, deny=["post"]).status_code == 200
    assert first_member.delete(url).status_code == 403
    assert owner.delete(url).status_code == 204


def test_undo_refuses_replies_quotes_pins_reactions_and_mission_drafts(home):
    owner = home["owner"]
    channel = _create(home, "keep-existing-threads")
    roots = [_send(owner, channel, str(i)) for i in range(5)]
    cases = (
        (200, lambda marker: owner.post(f"{channel}/messages", json={"body": "话题回复", "threadId": marker["id"]})),
        (200, lambda marker: owner.post(f"{channel}/messages", json={"body": "引用标记", "replyTo": marker["id"]})),
        (200, lambda marker: owner.post(f"{channel}/pins/{marker['id']}")),
        (200, lambda marker: owner.post(f"{home['base']}/messages/{marker['id']}/reactions", json={"reaction": "👍"})),
        (201, lambda marker: owner.post(f"{home['base']}/missions", json={
            "threadId": marker["id"], "agentId": home["agent"]["id"], "title": "草案", "goal": "准备提纲",
        })),
    )
    for i, (status, make_dependency) in enumerate(cases):
        url, marker = _create_range(owner, home, _selection(channel, roots[i], roots[i]))
        response = make_dependency(marker)
        assert response.status_code == status, response.text
        result = owner.delete(url)
        assert result.status_code == 409 and result.json()["error"]["code"] == "thread.in_use"
        assert owner.get(url).json()["id"] == marker["id"]

    original = roots[0]["id"]
    assert owner.delete(f"{home['base']}/threads/{original}").status_code == 409
    assert owner.get(f"{channel}/messages").json()["messages"][0] == roots[0]


def test_old_message_database_reads_first_and_upgrades_on_range_create(home):
    owner = home["owner"]
    channel = _create(home, "legacy-range")
    first = _send(owner, channel, "旧格式消息")
    history = channels(home["root"]).history
    with history.database(write=True) as conn:
        conn.execute("DROP INDEX live_thread_ranges")
        conn.execute("ALTER TABLE messages DROP COLUMN thread_range")
        conn.execute("ALTER TABLE messages DROP COLUMN removed")
    assert owner.get(f"{channel}/messages").json()["messages"] == [first]
    assert owner.get(f"{channel}/threads").json()["threads"] == []
    url, created = _create_range(owner, home, _selection(channel, first, first))
    assert created["context"]["count"] == 1
    assert owner.delete(url).status_code == 204
    assert owner.get(f"{channel}/messages").json()["messages"] == [first]
