"""Thread metadata and transitions use the channel's authenticated history."""

from fastapi.testclient import TestClient

from src.web.app_channels import channels
from tests.unit.test_app_voice import WEBM, _key, speech  # noqa: F401 - pytest fixture
from tests.unit.test_member_agents import ORIGIN, _join, home  # noqa: F401 - pytest fixture
from tests.unit.test_team_channels import _create, _override


def _thread(home, author, channel):
    root = author.post(f"{channel}/messages", json={"body": "原消息"}).json()
    reply = author.post(f"{channel}/messages", json={
        "body": "原回复", "threadId": root["id"], "clientId": "existing-reply", "openThread": True,
    }).json()
    return f"{home['base']}/threads/{root['id']}", root, reply


def _action(client, url, action):
    response = client.post(f"{url}/{action}", json={})
    assert response.status_code == 200, response.text
    return response.json()


def test_title_tabs_and_archive_preserve_existing_history(home):
    owner, member = home["owner"], _join(home)
    channel = _create(home, "thread-lifecycle", people=[member.member_id])
    workspace = f"{home['base']}/threads"
    url, root, reply = _thread(home, member, channel)
    channel_id = channel.rsplit("/", 1)[1]

    def lists(client, tab, expected):
        assert client.get(f"{channel}/threads", params={"tab": tab}).json()["threads"] == expected
        response = client.get(workspace, params={"channelId": channel_id, "tab": tab})
        assert response.json()["threads"] == expected

    assert member.get(url).json()["canManage"]
    assert member.patch(url, json={"title": "  进度\t讨论  "}).json()["title"] == "进度 讨论"
    closed = _action(owner, url, "close")
    owner_id = owner.get("/api/app/me").json()["user"]["id"]
    assert closed["state"] == "closed" and closed["closedBy"] == owner_id and closed["closedAt"]
    assert not closed["following"]
    member_closed = {**closed, "following": True}
    assert _action(member, url, "close") == member_closed
    previous = {"body": "原回复", "threadId": root["id"], "clientId": "existing-reply", "openThread": True}
    assert member.post(f"{channel}/messages", json=previous).json() == reply
    assert owner.get(url).json() == closed
    lists(owner, "closed", [closed])
    lists(owner, "all", [closed])
    lists(owner, "open", [])
    assert owner.get(workspace, params={"tab": "closed", "q": "进度 讨论"}).json()["threads"] == [closed]

    archived = _action(member, url, "archive")
    assert archived["state"] == "archived" and archived["archivedBy"] == member.member_id and archived["archivedAt"]
    assert archived["closedAt"] == closed["closedAt"]
    lists(member, "all", [])
    lists(member, "closed", [])
    lists(member, "archived", [archived])
    for response in (owner.patch(url, json={"title": "拒绝"}), owner.post(f"{url}/reopen", json={}),
                     member.post(f"{channel}/messages", json={"body": "新回复", "threadId": root["id"]})):
        assert response.status_code == 409 and response.json()["error"]["code"] == "thread.archived"
    replay = member.post(f"{channel}/messages", json=previous)
    assert replay.json() == reply
    assert member.get(url).json() == archived
    assert member.get(f"{channel}/messages", params={"threadId": root["id"]}).json()["messages"] == [root, reply]

    restored = _action(member, url, "unarchive")
    assert restored == member_closed
    lists(member, "closed", [member_closed])
    assert _action(member, url, "reopen")["state"] == "open"
    _action(member, url, "close")
    assert owner.post(f"{channel}/messages", json={"body": "人回复会重开", "threadId": root["id"]}).status_code == 200
    open_again = member.get(url).json()
    assert open_again["state"] == "open" and open_again["closedBy"] is None and open_again["replyCount"] == 2
    assert member.patch(url, json={"title": None}).json()["title"] is None
    assert owner.get(f"{channel}/messages").json()["messages"][0]["body"] == root["body"]
    assert not home["pool"].jobs


def test_management_uses_current_channel_scope_and_same_origin(home):
    owner, author = home["owner"], _join(home)
    other = _join(home, "bo@example.com", "小博")
    channel = _create(home, "private-thread", visibility="private", people=[author.member_id, other.member_id])
    url, _, reply = _thread(home, author, channel)
    workspace = f"{home['base']}/threads"

    def can_manage(client, expected):
        detail = client.get(url).json()
        assert detail["canManage"] is expected
        assert client.get(f"{channel}/threads").json()["threads"] == [detail]
        assert client.get(workspace).json()["threads"] == [detail]

    can_manage(author, True)
    can_manage(other, False)
    assert other.patch(url, json={"title": "无权限"}).status_code == 403
    assert other.post(f"{url}/archive", json={}).status_code == 403
    assert author.post(f"{url}/close", json={}, headers={"Origin": "https://elsewhere.invalid"}).status_code == 403
    assert TestClient(home["app"]).patch(url, json={"title": "无证明"}).status_code == 401
    for invalid in ({}, {"title": "有效", "firstMessageId": reply["id"]}, {"title": "x" * 201}):
        assert owner.patch(url, json=invalid).status_code == 422
    assert owner.post(f"{url}/archive", json={"as": author.member_id}).status_code == 422
    assert owner.post(f"{url}/move", json={}).status_code == 400   # 普通话题没有选段可拿出
    assert owner.post(f"{home['base']}/threads/{reply['id']}/close", json={}).status_code == 404
    blank = owner.post(f"{channel}/messages", json={"body": "未讨论"}).json()["id"]
    assert not owner.get(f"{home['base']}/threads/{blank}").json()["canManage"]
    assert owner.post(f"{home['base']}/threads/{blank}/close", json={}).status_code == 404

    assert _override(owner, channel, "person", author.member_id, deny=["post"]).status_code == 200
    can_manage(author, False)
    assert author.post(f"{url}/close", json={}).status_code == 403
    assert _override(owner, channel, "person", other.member_id, allow=["manage_channels"]).status_code == 200
    can_manage(other, True)
    assert _action(other, url, "archive")["archivedBy"] == other.member_id
    assert owner.delete(f"{channel}/members/people/{other.member_id}").status_code == 204
    assert other.post(f"{url}/unarchive", json={}).status_code == 404
    assert other.patch(url, json={"title": "已撤权"}).status_code == 404
    assert other.get(workspace, params={"tab": "archived"}).json()["threads"] == []


def test_pre_lifecycle_channel_db_read_then_upgrade_and_realtime(home):
    owner, member = home["owner"], _join(home)
    path = _create(home, "old-private", visibility="private", people=[member.member_id])
    url, root, _ = _thread(home, owner, path)
    history = channels(home["root"]).history
    with history.database(write=True) as conn:
        conn.execute("DROP TABLE thread_lifecycle")
    assert owner.get(url).json()["state"] == "open"
    assert member.get(f"{path}/threads").json()["threads"][0]["id"] == root["id"]
    assert member.get(f"{home['base']}/threads", params={"q": "原消息"}).json()["threads"][0]["id"] == root["id"]
    with history.database() as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='thread_lifecycle'").fetchone()

    org = home["base"].rsplit("/", 1)[1]
    channel = path.rsplit("/", 1)[1]
    with member.websocket_connect(f"/api/ws?orgId={org}", headers=ORIGIN) as socket:
        assert socket.receive_json()["userId"] == member.member_id
        socket.send_json({"type": "subscribe", "channelId": channel})
        assert socket.receive_json() == {"type": "subscribed", "channelId": channel}
        posted = owner.post(f"{path}/messages", json={"body": "同一个订阅的消息"}).json()
        assert socket.receive_json() == {
            "type": "event", "channelId": channel,
            "event": {"type": "message.created", "channelId": channel, "messageId": posted["id"],
                      "message": posted, "authorUserId": posted["author"]["id"]},
        }
        reaction_url = f"{home['base']}/messages/{posted['id']}/reactions"
        added = owner.post(reaction_url, json={"reaction": "🎉"}).json()
        assert socket.receive_json() == {
            "type": "event", "channelId": channel,
            "event": {"type": "message.reactions", "channelId": channel, "messageId": posted["id"], **added},
        }
        assert owner.patch(url, json={"title": "仅在授权后读取标题"}).status_code == 200
        assert socket.receive_json()["event"] == {
            "type": "thread.updated", "channelId": channel, "threadId": root["id"], "change": "title",
        }
        assert member.get(url).json()["title"] == "仅在授权后读取标题"
        assert owner.delete(reaction_url, params={"reaction": "🎉"}).status_code == 200
        assert socket.receive_json()["event"] == {
            "type": "message.reactions", "channelId": channel, "messageId": posted["id"], "reactions": [],
        }
        assert owner.delete(f"{path}/members/people/{member.member_id}").status_code == 204
        assert _action(owner, url, "archive")["state"] == "archived"
        assert owner.post(reaction_url, json={"reaction": "🎉"}).status_code == 200
        assert socket.receive_json() == {"type": "error", "code": "channel_access_lost", "channelId": channel}
        assert member.get(f"{path}/threads", params={"tab": "archived"}).status_code == 404
        assert member.get(f"{home['base']}/threads", params={"tab": "archived"}).json()["threads"] == []


def test_archived_voice_replies_only_replay_committed_receipts(home, speech):
    _key(home)
    owner = home["owner"]
    channel = _create(home, "voice-thread")
    url, root, _ = _thread(home, owner, channel)

    def voice(client_id):
        return owner.post(f"{channel}/voice", files={"file": ("v.webm", WEBM, "audio/webm")}, data={
            "durationMs": "1500", "peaks": "[]", "clientId": client_id, "threadId": root["id"],
        })

    sent = voice("voice-existing")
    assert sent.status_code == 200, sent.text
    _action(owner, url, "archive")
    calls = len(speech["calls"])
    rejected = voice("voice-new")
    assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "thread.archived"
    assert voice("voice-existing").json() == sent.json()
    assert len(speech["calls"]) == calls
    assert owner.get(url).json()["replyCount"] == 2
