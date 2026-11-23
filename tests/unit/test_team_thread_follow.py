"""Explicit thread choices use the current reader and preserve existing read markers."""

from fastapi.testclient import TestClient

from src.web.app_channels import channels
from tests.unit.test_member_agents import _join, home  # noqa: F401 - pytest fixture
from tests.unit.test_team_channels import _create, _override
from tests.unit.test_team_thread_reads import _count, _post


def test_follow_and_unfollow_update_only_my_thread_badges(home):
    owner, alice = home["owner"], _join(home)
    bob = _join(home, "thread-follower@example.com", "小博")
    channel = _create(home, "follow-cursors", people=[alice.member_id, bob.member_id])
    root = _post(owner, channel, "话题基线")
    _post(alice, channel, "第一条回复", root)
    _post(owner, channel, "下一条回复", root)
    url = f"{home['base']}/threads/{root}"
    follow = url + "/follow"
    assert _override(owner, channel, "person", bob.member_id, deny=["post"]).status_code == 200

    def overview(client, following, unread):
        result = client.get(url)
        assert result.status_code == 200
        row = result.json()
        assert row["following"] is following and row["unread"] == unread
        assert client.get(f"{channel}/threads").json()["threads"] == [row]
        assert client.get(f"{home['base']}/threads").json()["threads"] == [row]
        assert _count(home, client) == int(unread > 0)

    def change(client, following):
        response = client.put(follow, json={}) if following else client.delete(follow)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {"threadId": root, "channelId": channel.rsplit("/", 1)[1],
                                   "following": following}

    overview(bob, False, 0)
    overview(owner, True, 1)
    overview(alice, True, 1)
    change(bob, True)
    change(bob, True)
    overview(bob, True, 2)
    assert bob.post(url + "/read", json={}).status_code == 204
    overview(bob, True, 0)

    change(bob, False)
    change(bob, False)
    change(alice, False)
    _post(owner, channel, "取消关注后发的新回复", root)
    _post(alice, channel, "仍可继续回复", root)
    overview(bob, False, 0)
    overview(alice, False, 0)
    overview(owner, True, 2)
    change(bob, True)
    overview(bob, True, 2)  # Only the two messages after Bob's unchanged read marker.


def test_follow_uses_current_channel_access_and_hides_unsupported_targets(home):
    owner, member = home["owner"], _join(home)
    public = _create(home, "follow-visibility", people=[member.member_id])
    private = _create(home, "invisible-thread", visibility="private")
    root = _post(owner, public, "能看的话题")
    reply = _post(owner, public, "不能作为话题根的回复", root)
    not_thread = _post(owner, public, "没有回复的频道消息")
    hidden = _post(owner, private, "私有话题")
    _post(owner, private, "只对所有者可见", hidden)
    base = f"{home['base']}/threads"
    follow = f"{base}/{root}/follow"
    missing = member.put(f"{base}/missing/follow", json={})
    assert missing.status_code == 404
    for target in (reply, not_thread, hidden):
        result = member.put(f"{base}/{target}/follow", json={})
        assert result.status_code == 404 and result.json() == missing.json()
    assert TestClient(home["app"]).put(follow, json={}).status_code == 401
    assert member.put(follow, json={}, headers={"Origin": "https://external.invalid"}).status_code == 403
    assert member.put(follow, json={"userId": "somebody-else"}).status_code == 422
    assert member.put(follow, json={}).status_code == 200

    assert owner.delete(f"{public}/members/people/{member.member_id}").status_code == 204
    for method in ("PUT", "DELETE"):
        result = member.request(method, follow)
        assert result.status_code == 404 and result.json() == missing.json()
    assert _count(home, member) == 0

    assert member.post(f"{public}/join").status_code == 204
    assert member.get(f"{base}/{root}").json()["following"] is True
    assert _count(home, member) == 0  # Joining again does not surface replies from before admission.
    assert owner.delete(f"{home['base']}/members/{member.member_id}").status_code == 204
    assert member.delete(follow).status_code in (401, 403)


def test_older_database_and_archived_thread_follow_choices(home):
    owner, member = home["owner"], _join(home)
    channel = _create(home, "follow-migration", people=[member.member_id])
    root = _post(owner, channel, "旧数据库里的话题")
    _post(owner, channel, "已有一条回复", root)
    history = channels(home["root"]).history
    with history.database(write=True) as conn:
        conn.execute("DROP TABLE thread_follows")
        conn.execute("DROP TABLE thread_read_markers")
    url = f"{home['base']}/threads/{root}"
    assert not member.get(url).json()["following"] and _count(home, member) == 0
    with history.database() as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='thread_follows'").fetchone() is None

    assert member.put(url + "/follow", json={}).status_code == 200
    assert _count(home, member) == 1
    assert owner.post(url + "/archive", json={}).status_code == 200
    assert member.delete(url + "/follow").json()["following"] is False
    assert member.put(url + "/follow", json={}).json()["following"] is True
    archived = member.get(url).json()
    assert archived["state"] == "archived" and archived["following"] and archived["unread"] == 0
    assert _count(home, member) == 0
    assert owner.post(url + "/unarchive", json={}).status_code == 200
    assert _count(home, member) == 1
