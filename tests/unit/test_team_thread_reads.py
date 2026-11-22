"""The Desktop's unread marks are personal and follow current team-channel access."""

from datetime import datetime, timedelta, timezone
import uuid

from fastapi.testclient import TestClient

from src.web.app_channels import channels
from src.workflow.members.channel_history import OWNER_KEY, _stamp
from tests.unit.test_member_agents import _join, home  # noqa: F401 - pytest fixture
from tests.unit.test_team_channels import _create, _override


def _post(client, channel, text, thread=None):
    response = client.post(f"{channel}/messages", json={"body": text, "threadId": thread})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _count(home, client):
    response = client.get(f"{home['base']}/unread-threads")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()["count"]


def test_personal_read_cursor_follows_authors_and_does_not_mark_other_people(home):
    owner, alice = home["owner"], _join(home)
    bob = _join(home, "bo@example.com", "小博")
    channel = _create(home, "discuss", people=[alice.member_id, bob.member_id])
    root = _post(owner, channel, "安排")
    url = f"{home['base']}/threads/{root}"
    assert alice.post(f"{url}/read", json={}).status_code == 204
    _post(alice, channel, "第一条回复", root)
    _post(owner, channel, "所有者回话", root)
    _post(alice, channel, "再补一句", root)
    assert _count(home, owner) == _count(home, alice) == 1
    assert _count(home, bob) == 0
    assert not bob.get(url).json()["following"] and bob.get(url).json()["unread"] == 0
    assert owner.get(url).json()["following"] and owner.get(url).json()["unread"] == 2
    assert alice.get(f"{channel}/threads").json()["threads"][0]["unread"] == 1

    assert bob.post(f"{url}/read", json={}).status_code == 204
    assert not bob.get(url).json()["following"]
    # Read access is enough to acknowledge one's own messages, even after losing posting permission.
    assert _override(owner, channel, "person", alice.member_id, deny=["post"]).status_code == 200
    for _ in range(2):
        assert alice.post(f"{url}/read", json={}).status_code == 204
    assert _count(home, alice) == 0 and _count(home, owner) == 1
    assert alice.get(url).json()["unread"] == 0
    _post(owner, channel, "后来回复", root)
    assert _count(home, alice) == 1 and alice.get(url).json()["unread"] == 1


def test_unread_does_not_include_unjoined_revoked_or_archived_channels(home):
    owner, member = home["owner"], _join(home)
    public = _create(home, "discoverable", people=[member.member_id])
    private = _create(home, "private", visibility="private")
    public_root = _post(member, public, "公开话题")
    reply = _post(owner, public, "公开回复", public_root)
    private_root = _post(owner, private, "私有话题")
    _post(owner, private, "私有内容", private_root)
    base = f"{home['base']}/threads"
    assert _count(home, member) == 1
    assert member.post(f"{base}/{private_root}/read", json={}).status_code == 404
    assert member.post(f"{base}/{reply}/read", json={}).status_code == 404
    assert member.post(f"{base}/{public_root}/read", json={}, headers={"Origin": "https://other.invalid"}).status_code == 403
    assert TestClient(home["app"]).post(f"{base}/{public_root}/read", json={}).status_code == 401

    assert owner.delete(f"{public}/members/people/{member.member_id}").status_code == 204
    assert member.post(f"{base}/{public_root}/read", json={}).status_code == 404
    assert _count(home, member) == 0
    assert member.post(f"{public}/join").status_code == 204
    assert _count(home, member) == 0  # Rejoining starts after the old replies, not before them.
    _post(owner, public, "加入后的回复", public_root)
    assert _count(home, member) == 1
    assert owner.delete(public).status_code == 204
    assert _count(home, member) == 0


def test_existing_database_migrates_on_first_read_marker_write(home, monkeypatch):
    owner, member = home["owner"], _join(home)
    channel = _create(home, "version-before-receipts", people=[member.member_id])
    root = _post(owner, channel, "之前的记录")
    _post(member, channel, "尚未读的回复", root)
    history = channels(home["root"]).history
    with history.database(write=True) as conn:
        conn.execute("DROP TABLE thread_read_markers")
    assert _count(home, owner) == 1
    with history.database() as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='thread_read_markers'").fetchone() is None
    assert owner.post(f"{home['base']}/threads/{root}/read", json={}).status_code == 204
    assert _count(home, owner) == 0
    monkeypatch.delenv("VORTOONE_API_TOKEN")
    assert _count(home, TestClient(home["app"])) == 0


def test_archived_topics_do_not_add_badges_but_can_be_read(home):
    owner, member = home["owner"], _join(home)
    channel = _create(home, "archive-compat", visibility="private", people=[member.member_id])
    root = _post(owner, channel, "将归档的话题")
    _post(member, channel, "档案中仍可读", root)
    workspace = f"{home['base']}/threads"
    url = f"{workspace}/{root}"

    def overview(client, tab, state, unread_count):
        detail = client.get(url)
        assert detail.status_code == 200
        row = detail.json()
        assert row["following"] is True and row["unread"] == unread_count
        assert row["state"] == state and row["title"] == "保留个人未读"
        assert client.get(workspace, params={"tab": tab}).json()["threads"] == [row]
        assert client.get(f"{channel}/threads", params={"tab": tab}).json()["threads"] == [row]
        assert _count(home, client) == int(unread_count > 0)
        return row

    renamed = owner.patch(url, json={"title": "保留个人未读"})
    assert renamed.status_code == 200 and renamed.json()["unread"] == 1
    assert overview(owner, "all", "open", 1)["canManage"]
    assert not overview(member, "all", "open", 0)["canManage"]
    assert owner.post(f"{url}/close", json={}).status_code == 200
    overview(owner, "closed", "closed", 1)

    assert owner.post(f"{url}/archive", json={}).status_code == 200
    overview(owner, "archived", "archived", 0)
    assert owner.get(workspace).json()["threads"] == []
    assert owner.get(f"{channel}/threads").json()["threads"] == []
    assert owner.post(f"{url}/unarchive", json={}).status_code == 200
    overview(owner, "closed", "closed", 1)

    assert owner.post(f"{url}/archive", json={}).status_code == 200
    assert owner.post(f"{url}/read", json={}).status_code == 204
    assert owner.post(f"{url}/unarchive", json={}).status_code == 200
    overview(owner, "closed", "closed", 0)
    assert owner.post(f"{url}/reopen", json={}).status_code == 200
    _post(member, channel, "重开后的新回复", root)
    overview(owner, "all", "open", 1)
    _post(owner, channel, "对方仍有未读", root)
    overview(member, "all", "open", 1)

    assert owner.delete(f"{channel}/members/people/{member.member_id}").status_code == 204
    assert member.get(workspace).json()["threads"] == []
    assert member.get(f"{channel}/threads").status_code == 404
    assert member.post(f"{url}/read", json={}).status_code == 404
    assert _count(home, member) == 0


def test_server_counts_threads_once_and_caps_count_and_badges(home):
    owner, member = home["owner"], _join(home)
    channel = _create(home, "unread-cap", people=[member.member_id]).rsplit("/", 1)[1]
    history = channels(home["root"]).history
    stamp = _stamp(datetime.now(timezone.utc) + timedelta(seconds=1))
    roots = [str(uuid.uuid4()) for _ in range(101)]
    with history.database(write=True) as conn:
        conn.executemany("INSERT INTO messages(id, channel, author, body, created, thread) VALUES (?, ?, ?, '', ?, ?)",
                         [(root, channel, OWNER_KEY, stamp, None) for root in roots] +
                         [(str(uuid.uuid4()), channel, member.member_id, stamp, root) for root in roots] +
                         [(str(uuid.uuid4()), channel, member.member_id, stamp, roots[0]) for _ in range(101)])
    assert _count(home, owner) == 100
    url = f"{home['base']}/threads"
    assert owner.get(f"{url}/{roots[0]}").json()["unread"] == 100
    for root in roots[:2]:
        assert owner.post(f"{url}/{root}/read", json={}).status_code == 204
    assert _count(home, owner) == 99
