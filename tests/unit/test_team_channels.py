"""Channel policy through authenticated HTTP and realtime, with the personal history kept separate."""
from fastapi.testclient import TestClient

from src.agents.principal import OWNER, Principal
from src.workflow.members.registry import MemberRegistry
from tests.unit.test_member_agents import ORIGIN, OWNER_P, _join, home  # noqa: F401 - pytest fixture


def _create(home, name, *, visibility="public", roles=(), people=()):
    response = home["owner"].post(f"{home['base']}/channels", json={
        "name": name, "visibility": visibility, "audienceRoleIds": list(roles), "userIds": list(people),
    })
    assert response.status_code == 201, response.text
    return f"{home['base']}/channels/{response.json()['id']}"


def _list(home, client):
    response = client.get(f"{home['base']}/channels")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return {row["id"]: row for row in response.json()["channels"]}


def _override(client, url, kind, target, *, allow=(), deny=()):
    return client.put(f"{url}/permissions", json={
        "target": {"type": kind, "id": target}, "allow": list(allow), "deny": list(deny),
    })


def test_public_channel_join_post_retry_pagination_replies_pins_and_archive(home):
    owner, member = home["owner"], _join(home)
    url = _create(home, "general-local")
    channel = url.rsplit("/", 1)[1]
    visible = _list(home, member)[channel]
    assert not visible["isMember"] and not visible["canRead"] and visible.get("lastMessage") is None
    for path in ("messages", "members", "roster", "pins", "threads"):
        assert member.get(f"{url}/{path}").status_code == 403
    assert member.post(f"{url}/messages", json={"body": "未加入"}).status_code == 403
    assert member.post(f"{url}/join").status_code == 204
    assert {row["id"] for row in member.get(f"{url}/members").json()["members"]} == {
        member.member_id, owner.get("/api/app/me").json()["user"]["id"],
    }
    first = member.post(f"{url}/messages", json={"body": "共享讨论", "clientId": "one"})
    assert first.status_code == 200, first.text
    first = first.json()
    assert first["author"] == {"type": "person", "id": member.member_id}
    assert member.post(f"{url}/messages", json={"body": "共享讨论", "clientId": "one"}).json() == first
    assert member.post(f"{url}/messages", json={"body": "不同文字", "clientId": "one"}).status_code == 400
    second = owner.post(f"{url}/messages", json={"body": "第二条", "clientId": "one"}).json()
    assert second["id"] != first["id"]
    assert _list(home, member)[channel]["unreadCount"] == 1
    assert member.post(f"{url}/read").status_code == 204
    assert _list(home, member)[channel]["unreadCount"] == 0
    assert member.get(f"{url}/messages", params={"before": second["createdAt"], "limit": 1}).json()["messages"] == [first]
    reply = owner.post(f"{url}/messages", json={"body": "线程回复", "threadId": first["id"], "openThread": True})
    assert reply.status_code == 200, reply.text
    assert [m["id"] for m in member.get(f"{url}/messages", params={"threadId": first["id"]}).json()["messages"]] == [
        first["id"], reply.json()["id"],
    ]
    assert member.get(f"{url}/threads").json()["threads"][0]["replyCount"] == 1
    assert member.get(f"{home['base']}/threads/{first['id']}").json()["rootText"] == "共享讨论"
    assert member.get(f"{home['base']}/messages/{first['id']}/card").json()["name"] == "general-local"
    assert member.post(f"{url}/pins/{first['id']}").json()["message"]["id"] == first["id"]
    assert owner.get(f"{url}/pins").json()["pins"][0]["messageId"] == first["id"]
    assert owner.delete(url).status_code == 204
    assert channel not in _list(home, member)
    assert member.get(f"{url}/messages").status_code == 404
    assert home["owner"].get(f"{home['base']}/archived-channels").json()["channels"][0]["id"] == channel
    assert owner.post(f"{url}/unarchive").status_code == 204
    history = owner.get(f"{url}/messages").json()["messages"]
    assert [item["id"] for item in history] == [first["id"], second["id"]]
    assert "clientId" not in history[0] and history[1]["clientId"] == "one"


def test_private_channel_isolated_from_other_members_and_personal_sessions(home):
    from src.gateway.session_store import create_channel, list_sessions

    owner, alice, bob = home["owner"], _join(home), _join(home, "bo@example.com", "小博")
    legacy = create_channel(home["root"], "owner-only-session")
    url = _create(home, "private-work", visibility="private", people=[alice.member_id])
    channel = url.rsplit("/", 1)[1]
    secret = alice.post(f"{url}/messages", json={"body": "私有频道正文"}).json()
    assert channel in _list(home, alice)
    assert not any(row["name"] == "owner-only-session" for row in _list(home, alice).values())
    assert {row["sid"] for row in list_sessions(home["root"])} == {legacy["sid"]}
    assert channel not in _list(home, bob)
    for path in ("messages", "members", "pins", "threads", "roster", "card", "permissions", "active-runs"):
        assert bob.get(f"{url}/{path}").status_code == 404, path
    for path in ("messages", "join", "read"):
        assert bob.post(f"{url}/{path}", json={"body": "猜测的频道"}).status_code == 404
    for path in (f"messages/{secret['id']}/card", f"threads/{secret['id']}"):
        assert bob.get(f"{home['base']}/{path}").status_code == 404
    assert bob.post(f"{home['base']}/channels", json={"name": "private-work"}).status_code == 201
    unauthenticated = TestClient(home["app"])
    assert unauthenticated.get(f"{url}/messages").status_code == 401
    assert alice.post(f"{url}/messages", json={"body": "跨站"}, headers={"Origin": "https://other.example"}).status_code == 403
    assert owner.delete(f"{url}/members/people/{alice.member_id}").status_code == 204
    assert alice.get(f"{url}/messages").status_code == 404


def test_department_requires_membership_and_rechecks_roles_and_cleans_removed_targets(home):
    owner, alice, bob = home["owner"], _join(home), _join(home, "bo@example.com", "小博")
    registry = MemberRegistry(home["root"])
    role = registry.create_group(OWNER_P, "研究")
    registry.set_member_groups(OWNER_P, alice.member_id, [role])
    url = _create(home, "research", visibility="private", roles=[role])
    channel = url.rsplit("/", 1)[1]
    assert channel in _list(home, alice) and not _list(home, alice)[channel]["isMember"]
    assert channel not in _list(home, bob)
    assert owner.post(f"{url}/members", json={"userId": bob.member_id}).status_code == 403
    assert alice.post(f"{url}/join").status_code == 204
    assert alice.post(f"{url}/messages", json={"body": "部门工作"}).status_code == 200
    assert _override(owner, url, "role", role, deny=["talk_to_agents"]).status_code == 200
    assert not _list(home, alice)[channel]["can"]["callAgents"]
    assert _override(owner, url, "person", alice.member_id, allow=["talk_to_agents"]).status_code == 200
    assert _list(home, alice)[channel]["can"]["callAgents"]
    registry.set_member_groups(OWNER_P, alice.member_id, [])
    assert channel not in _list(home, alice) and alice.get(f"{url}/messages").status_code == 404
    registry.set_member_groups(OWNER_P, alice.member_id, [role])
    assert not _list(home, alice)[channel]["isMember"]  # Regaining the role only lets the person join again.
    assert registry.snapshot()["channels"][channel]["kind"] == "department"
    registry.delete_group(OWNER_P, role)
    registry.remove_member(OWNER_P, alice.member_id)
    state = registry.snapshot()["channels"][channel]
    assert state["kind"] == "department" and state["audience_roles"] == [] and state["overrides"] == []
    assert channel not in _list(home, bob)
    assert owner.get(f"{url}/messages").json()["messages"][0]["body"] == "部门工作"


def test_channel_overrides_order_and_no_management_escalation(home):
    owner, alice, bob = home["owner"], _join(home), _join(home, "bo@example.com", "小博")
    registry = MemberRegistry(home["root"])
    allow = registry.create_group(OWNER_P, "允许")
    deny = registry.create_group(OWNER_P, "禁止")
    registry.set_member_groups(OWNER_P, alice.member_id, [allow, deny])
    url = _create(home, "override-room", people=[alice.member_id, bob.member_id])
    channel = url.rsplit("/", 1)[1]
    root = owner.post(f"{url}/messages", json={"body": "讨论"}).json()
    assert _override(owner, url, "role", allow, allow=["post"]).status_code == 200
    assert _override(owner, url, "role", deny, deny=["post"]).status_code == 200
    assert not _list(home, alice)[channel]["can"]["post"]
    assert alice.post(f"{url}/messages", json={"body": "不允许"}).status_code == 403
    assert alice.post(f"{url}/messages", json={"body": "不能绕过", "threadId": root["id"]}).status_code == 403
    assert alice.post(f"{url}/pins/{root['id']}").status_code == 403
    assert _override(owner, url, "person", alice.member_id, allow=["post"]).status_code == 200
    assert alice.post(f"{url}/messages", json={"body": "个人允许"}).status_code == 200
    assert _override(owner, url, "person", alice.member_id, allow=["manage_channels"], deny=["post"]).status_code == 200
    assert alice.patch(url, json={"topic": "新说明"}).status_code == 200
    assert _override(alice, url, "person", bob.member_id, allow=["post"]).status_code == 403
    assert _override(alice, url, "person", alice.member_id).status_code == 403
    assert _override(owner, url, "role", "everyone", deny=["post", "manage_channels"]).status_code == 200
    assert bob.post(f"{url}/messages", json={"body": "岗位继承拒绝"}).status_code == 403
    registry.set_member_groups(OWNER_P, bob.member_id, ["admins"])
    assert bob.post(f"{url}/messages", json={"body": "管理员通过"}).status_code == 200
    assert owner.patch(url, json={"topic": "所有者通过"}).status_code == 200


def test_realtime_cannot_subscribe_before_join_and_stops_after_revocation(home):
    owner, member = home["owner"], _join(home)
    url = _create(home, "realtime-private", visibility="private")
    channel = url.rsplit("/", 1)[1]
    org = home["base"].rsplit("/", 1)[1]
    with member.websocket_connect(f"/api/ws?orgId={org}", headers=ORIGIN) as socket:
        assert socket.receive_json()["userId"] == member.member_id
        socket.send_json({"type": "subscribe", "channelId": channel})
        assert socket.receive_json()["code"] == "channel_access_lost"
        assert owner.post(f"{url}/members", json={"userId": member.member_id}).status_code == 204
        socket.send_json({"type": "subscribe", "channelId": channel})
        assert socket.receive_json() == {"type": "subscribed", "channelId": channel}
        sent = owner.post(f"{url}/messages", json={"body": "可以实时看到"}).json()
        assert socket.receive_json()["event"]["message"]["id"] == sent["id"]
        assert owner.delete(f"{url}/members/people/{member.member_id}").status_code == 204
        assert owner.post(f"{url}/messages", json={"body": "撤权后不能收到"}).status_code == 200
        assert socket.receive_json() == {"type": "error", "code": "channel_access_lost", "channelId": channel}


def test_a_restored_home_keeps_channel_membership_history_and_the_owner_identity(tmp_path):
    from src.workflow.home_backup import backup, restore
    from src.workflow.members.channels import TeamChannels

    source = TeamChannels(str(tmp_path / "first"), "original-owner")
    original = Principal(OWNER, "original-owner")
    channel = source.create(original, "shared", "", "private", [], [])["id"]
    first = source.post(original, channel, "随家迁移", client_id="local")
    source.set_pin(original, channel, first["id"], True)
    snapshot = backup(str(tmp_path / "first"), str(tmp_path / "backups"))
    restore(str(tmp_path / "restored"), snapshot["path"])

    moved = TeamChannels(str(tmp_path / "restored"), "new-owner")
    owner = Principal(OWNER, "new-owner")
    assert moved.list(owner)[0]["createdByUserId"] == owner.id
    assert moved.roster(owner, channel)[0]["id"] == owner.id
    (message,) = moved.messages(owner, channel)
    assert message["author"]["id"] == owner.id and message["id"] == first["id"] and message["pinned"]
    assert moved.pins(owner, channel)[0]["pinnedByUserId"] == owner.id
    assert moved.post(owner, channel, first["body"], client_id="local")["id"] == first["id"]
