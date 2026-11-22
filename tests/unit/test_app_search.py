"""搜索只找调用人读得到的团队频道消息与看得到的卡片；找不到的种类如实不返回。"""
from fastapi.testclient import TestClient

from src.web.app_search import snippet
from tests.unit.test_member_agents import _join, home  # noqa: F401 - pytest fixture
from tests.unit.test_team_channels import _create


def _search(client, home, **params):
    response = client.get(f"{home['base']}/search", params=params)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()["hits"]


def _post(client, url, body, **extra):
    response = client.post(f"{url}/messages", json={"body": body, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def test_messages_are_found_only_where_the_reader_has_joined(home):
    owner, member = home["owner"], _join(home)
    public = _create(home, "general-search")
    private = _create(home, "owners-only", visibility="private")
    assert member.post(f"{public}/join").status_code == 204
    shared = _post(member, public, "发票 Invoice 已经寄出")
    _post(owner, private, "发票的内部备注")
    _post(owner, public, "无关的话")

    hits = _search(member, home, q="invoice")
    assert [hit["sourceId"] for hit in hits] == [shared["id"]]
    hit = hits[0]
    assert hit["kind"] == "message" and hit["channelIds"] == [public.rsplit("/", 1)[1]]
    assert hit["title"] == "general-search" and hit["snippet"] == "发票 Invoice 已经寄出"
    assert hit["author"] == {"type": "person", "id": member.member_id, "name": hit["author"]["name"]}
    assert hit["author"]["name"] and hit["messageId"] == shared["id"] and hit["threadId"] is None
    assert hit["threadTitle"] is None

    owner_hits = _search(owner, home, q="发票")
    assert {h["title"] for h in owner_hits} == {"general-search", "owners-only"}
    assert [h["createdAt"] for h in owner_hits] == sorted((h["createdAt"] for h in owner_hits), reverse=True)
    assert [h["createdAt"] for h in _search(owner, home, q="发票", sort="oldest")] == \
        sorted(h["createdAt"] for h in owner_hits)
    # 筛选：频道、发送人、置顶、链接；搜 agent 发的、提到谁、文件都没有可返回的
    assert [h["title"] for h in _search(owner, home, q="发票", **{"in": private.rsplit("/", 1)[1]})] == ["owners-only"]
    assert [h["sourceId"] for h in _search(owner, home, q="发票", fromUsers=member.member_id)] == [shared["id"]]
    for empty in ({"pinned": "true"}, {"has": "link"}, {"author": "agent"}, {"mentionsUsers": member.member_id},
                  {"has": "file"}, {"kinds": "wiki,file"}):
        assert _search(owner, home, q="发票", **empty) == [], empty
    # 查询里的通配符按字面找
    assert _search(owner, home, q="%") == [] and _search(owner, home, q="_") == []


def test_thread_replies_carry_their_thread_title(home):
    owner = home["owner"]
    url = _create(home, "threads-search")
    root = _post(owner, url, "季度复盘\n第二行")
    reply = _post(owner, url, "复盘结论已写好", threadId=root["id"])
    hit, = [h for h in _search(owner, home, q="结论") if h["sourceId"] == reply["id"]]
    assert hit["threadId"] == root["id"] and hit["threadTitle"] == "季度复盘"


def test_cards_follow_the_board_audience(home):
    owner, member = home["owner"], _join(home)
    base = home["base"]
    owner_id = owner.get("/api/app/me").json()["user"]["id"]
    mine = owner.post(f"{base}/issues", json={"title": "整理报销单", "description": "附上发票",
                                             "audience": {"mode": "people", "userIds": [owner_id]}})
    assert mine.status_code == 200, mine.text
    issue = mine.json()
    hits = _search(owner, home, q="发票", kinds="issue")
    assert [(h["kind"], h["sourceId"], h["title"]) for h in hits] == [
        ("issue", issue["id"], f"#{issue['number']} 整理报销单")]
    assert hits[0]["snippet"] == "附上发票" and hits[0]["channelIds"] == []
    assert _search(member, home, q="发票", kinds="issue") == []
    # 消息筛选对卡片不成立，带上就不返回卡片
    assert _search(owner, home, q="发票", kinds="issue", fromUsers="anyone") == []
    assert _search(owner, home, q="发票", kinds="issue", after="2999-01-01T00:00:00Z") == []
    assert len(_search(owner, home, q="发票", kinds="issue", before="2999-01-01T00:00:00Z")) == 1


def test_bad_queries_and_callers_are_refused(home):
    owner = home["owner"]
    route = f"{home['base']}/search"
    for params in ({"q": ""}, {"q": " "}, {"q": "x" * 201}, {"q": "a", "kinds": "everything"},
                   {"q": "a", "limit": 0}, {"q": "a", "limit": 51}, {"q": "a", "has": "video"},
                   {"q": "a", "author": "bot"}, {"q": "a", "sort": "random"}, {"q": "a", "after": "yesterday"},
                   {"q": "a", "before": "2026-11-22T00:00:00"}):
        assert owner.get(route, params=params).status_code == 400, params
    assert TestClient(home["app"]).get(route, params={"q": "a"}).status_code == 401


def test_limit_and_snippet_window():
    text = "开头" + "很长的内容" * 60 + "关键词" + "后面" * 60
    cut = snippet(text, "关键词")
    assert "关键词" in cut and cut.startswith("…") and cut.endswith("…") and len(cut) <= 162
    assert snippet("短句", "短") == "短句"


def test_limit_caps_the_hits(home):
    owner = home["owner"]
    url = _create(home, "many-search")
    for index in range(5):
        _post(owner, url, f"重复内容 {index}")
    assert len(_search(owner, home, q="重复", limit=3)) == 3
