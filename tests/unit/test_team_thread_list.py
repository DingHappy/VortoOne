"""The workspace thread index reads the same durable rows as each team's channel."""

from fastapi.testclient import TestClient

from src.web.auth import SESSION_COOKIE
from tests.unit.test_member_agents import ORIGIN, TOKEN, _join, home  # noqa: F401 - pytest fixture
from tests.unit.test_team_channels import _create


def _message(client, channel, body, root=None):
    response = client.post(f"{channel}/messages", json={"body": body, "threadId": root, "openThread": bool(root)})
    assert response.status_code == 200, response.text
    return response.json()


def _thread(client, channel, body):
    root = _message(client, channel, body)
    reply = _message(client, channel, "一条回复", root["id"])
    return root["id"], reply["createdAt"]


def _list(client, url, **filters):
    response = client.get(url, params=filters)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def test_workspace_index_only_returns_joined_active_channels(home):
    owner, alice = home["owner"], _join(home)
    base = home["base"]
    url = f"{base}/threads"
    team = _create(home, "shared", people=[alice.member_id])
    public = _create(home, "discoverable")
    private = _create(home, "private", visibility="private")
    archived = _create(home, "archived", people=[alice.member_id])

    shared_id, activity = _thread(alice, team, "共享话题")
    public_id, _ = _thread(owner, public, "尚未加入")
    private_id, _ = _thread(owner, private, "私有话题")
    _thread(owner, archived, "归档话题")
    unthreaded = _message(owner, team, "无回复的一般消息")
    assert owner.get(f"{url}/{unthreaded['id']}").status_code == 200
    assert owner.delete(archived).status_code == 204

    (visible,) = _list(alice, url)["threads"]
    assert visible == _list(alice, f"{team}/threads")["threads"][0]
    assert visible == alice.get(f"{url}/{shared_id}").json()
    assert visible["rootText"] == "共享话题" and visible["lastActivityAt"] == activity
    assert [row["id"] for row in _list(owner, url)["threads"]] == [private_id, public_id, shared_id]
    signed_in_owner = TestClient(home["app"], headers=ORIGIN, cookies={SESSION_COOKIE: TOKEN})
    assert _list(signed_in_owner, url) == _list(owner, url)
    assert signed_in_owner.get(url, headers={"Origin": "https://other.example"}).status_code == 403
    for channel in (public, private, archived):
        assert _list(alice, url, channelId=channel.rsplit("/", 1)[-1]) == {"threads": [], "nextCursor": None}
    assert _list(alice, url, q="私有话题")["threads"] == []
    assert alice.get(f"{url}/{private_id}").status_code == 404
    assert TestClient(home["app"]).get(url).status_code == 401
    assert alice.get("/api/app/organizations/not-this-home/threads").status_code == 404

    assert owner.delete(f"{team}/members/people/{alice.member_id}").status_code == 204
    assert _list(alice, url)["threads"] == []
    assert alice.get(f"{url}/{shared_id}").status_code == 404


def test_workspace_index_filters_before_sorting_and_paging(home):
    owner, member = home["owner"], _join(home)
    url = f"{home['base']}/threads"
    first = _create(home, "first", people=[member.member_id])
    second = _create(home, "second", people=[member.member_id])
    first_id, _ = _thread(member, first, "Café first")
    second_id, _ = _thread(owner, second, "Café second")
    third_id, _ = _thread(owner, first, "Unrelated allowed channel")
    activity = _message(member, second, "回到这个话题", second_id)["createdAt"]

    one = _list(member, url, q=" CAFÉ ", limit=1)
    assert [row["id"] for row in one["threads"]] == [second_id]
    assert one["threads"][0]["lastActivityAt"] == activity
    assert one["nextCursor"]
    two = _list(member, url, q=" CAFÉ ", cursor=one["nextCursor"], limit=1)
    assert [row["id"] for row in two["threads"]] == [first_id]
    assert two["nextCursor"] is None
    assert [row["id"] for row in _list(member, url, mine=True)["threads"]] == [second_id, first_id]
    assert [row["id"] for row in _list(owner, url, mine=True)["threads"]] == [second_id, third_id]
    assert [row["id"] for row in _list(member, url, channelId=first.rsplit("/", 1)[-1])["threads"]] == [
        third_id, first_id,
    ]
    for extras in ({"agentId": "not-part-of-the-thread"}, {"needsYou": True}, {"tab": "working"},
                   {"tab": "missions"}, {"tab": "closed"}, {"tab": "archived"}):
        assert _list(member, url, **extras) == {"threads": [], "nextCursor": None}
    assert member.get(url, params={"cursor": "not-a-cursor"}).status_code == 400
    assert member.get(url, params={"tab": "unsupported"}).status_code == 400


def test_workspace_index_does_not_create_history_when_no_channels(home, monkeypatch):
    from src.web.app_channels import channels

    url = f"{home['base']}/threads"
    assert _list(home["owner"], url) == {"threads": [], "nextCursor": None}
    assert not channels(home["root"]).history.path.exists()
    monkeypatch.delenv("VORTOONE_API_TOKEN")
    assert _list(TestClient(home["app"]), url) == {"threads": [], "nextCursor": None}
