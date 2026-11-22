"""团队频道里的 GIF：GIPHY 密钥、搜索、取图、带 GIF 的消息。GIPHY 一律打桩，不联网。"""

import os
import sqlite3
import stat

import pytest

from src.web import app_gifs
from tests.unit.test_member_agents import ORIGIN, _join, home  # noqa: F401 - pytest fixture
from tests.unit.test_team_channels import _create

KEY = "a" * 32


def _item(gif_id, title="cat", width=480, height=270):
    return {"id": gif_id, "title": title, "images": {
        "fixed_width": {"url": f"https://media1.giphy.com/media/{gif_id}/200w.gif", "width": "200", "height": "113"},
        "downsized": {"url": f"https://media1.giphy.com/media/{gif_id}/giphy-downsized.gif",
                      "width": str(width), "height": str(height)},
        "original_still": {"url": f"https://media1.giphy.com/media/{gif_id}/giphy_s.gif"},
    }}


@pytest.fixture
def giphy(monkeypatch):
    """假的 GIPHY：记下每次调用，按路径回答。"""
    calls = []
    state = {"key_ok": True, "items": {"abc123": _item("abc123", "dancing cat")}}

    async def fake_get(url, params):
        calls.append((url, dict(params)))
        if not state["key_ok"]:
            raise app_gifs.GifError(400, "gif.key_invalid", "GIPHY 不认这个 API Key")
        if url.endswith("/search") or url.endswith("/trending"):
            items = list(state["items"].values())
            return {"data": items, "pagination": {"total_count": 100, "count": len(items),
                                                  "offset": params.get("offset", 0)}}
        gif_id = url.rsplit("/", 1)[1]
        if gif_id not in state["items"]:
            raise app_gifs.GifError(404, "gif.not_found", "没有")
        return {"data": state["items"][gif_id]}

    downloads = []

    async def fake_download(url):
        downloads.append(url)
        return b"GIF89a-bytes", "image/gif"

    monkeypatch.setattr(app_gifs, "_get", fake_get)
    monkeypatch.setattr(app_gifs, "_download", fake_download)
    monkeypatch.delenv(app_gifs.ENV_KEY, raising=False)
    app_gifs._FOUND.clear()
    yield {"calls": calls, "state": state, "downloads": downloads}
    app_gifs._FOUND.clear()


def _with_key(home):
    answer = home["owner"].put("/api/app/gif-key", json={"apiKey": KEY})
    assert answer.status_code == 200, answer.text
    return answer.json()


# ── 密钥 ──────────────────────────────────────────────────────────────────────

def test_the_owner_saves_a_key_that_giphy_accepts(home, giphy):
    owner = home["owner"]
    assert owner.get("/api/app/gif-key").json() == {"source": None, "hint": None, "state": "off"}
    assert owner.get(f"{home['base']}/settings").json()["gifsAvailable"] is False
    assert _with_key(home) == {"source": "settings", "hint": KEY[-4:], "state": "ok"}
    assert giphy["calls"][0][0].endswith("/trending") and giphy["calls"][0][1]["api_key"] == KEY
    settings = owner.get(f"{home['base']}/settings").json()
    assert settings["gifsAvailable"] is True and settings["gifs"] is True
    path = app_gifs._path(home["root"])
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert KEY not in owner.get("/api/app/gif-key").text                    # 密钥本身从不回给界面
    assert owner.delete("/api/app/gif-key").json()["state"] == "off"
    assert owner.get(f"{home['base']}/settings").json()["gifsAvailable"] is False


def test_a_key_giphy_refuses_is_not_kept(home, giphy):
    giphy["state"]["key_ok"] = False
    answer = home["owner"].put("/api/app/gif-key", json={"apiKey": KEY})
    assert answer.status_code == 400 and answer.json()["error"]["code"] == "gif.key_invalid"
    assert home["owner"].get("/api/app/gif-key").json()["state"] == "off"
    assert home["owner"].put("/api/app/gif-key", json={"apiKey": "short"}).status_code == 400


def test_an_environment_key_is_read_only(home, giphy, monkeypatch):
    monkeypatch.setenv(app_gifs.ENV_KEY, "b" * 32)
    assert home["owner"].get("/api/app/gif-key").json() == {"source": "env", "hint": "bbbb", "state": "ok"}
    assert home["owner"].put("/api/app/gif-key", json={"apiKey": KEY}).status_code == 409
    assert home["owner"].delete("/api/app/gif-key").status_code == 409


def test_members_cannot_see_or_change_the_key(home, giphy):
    member = _join(home)
    assert member.get("/api/app/gif-key").status_code == 403
    assert member.put("/api/app/gif-key", json={"apiKey": KEY}).status_code == 403
    assert member.delete("/api/app/gif-key").status_code == 403


# ── 搜索与取图 ────────────────────────────────────────────────────────────────

def test_search_trending_categories_and_paging(home, giphy):
    _with_key(home)
    owner, base = home["owner"], home["base"]
    page = owner.get(f"{base}/gifs", params={"q": " cats ", "limit": 1, "lang": "zh"}).json()
    assert page == {"gifs": [{"id": "abc123", "title": "dancing cat", "width": 480, "height": 270}], "next": 1}
    url, params = giphy["calls"][-1]
    assert url.endswith("/search") and params["q"] == "cats" and params["rating"] == "g" and params["lang"] == "zh"
    owner.get(f"{base}/gifs")
    assert giphy["calls"][-1][0].endswith("/trending")
    owner.get(f"{base}/gifs", params={"category": "good_morning"})
    assert giphy["calls"][-1][1]["q"] == "good morning"
    assert owner.get(f"{base}/gifs", params={"category": "nsfw"}).status_code == 400


def test_the_homes_rating_and_switch_are_used(home, giphy):
    _with_key(home)
    owner, base = home["owner"], home["base"]
    assert owner.patch(f"{base}/settings", json={"gifRating": "pg"}).json()["gifRating"] == "pg"
    owner.get(f"{base}/gifs", params={"q": "x"})
    assert giphy["calls"][-1][1]["rating"] == "pg"
    assert owner.patch(f"{base}/settings", json={"gifRating": "r"}).status_code == 400
    assert owner.patch(f"{base}/settings", json={"gifs": False}).json()["gifs"] is False
    assert owner.get(f"{base}/gifs").status_code == 422


def test_without_a_key_there_are_no_gifs(home, giphy):
    assert home["owner"].get(f"{home['base']}/gifs").status_code == 422
    assert home["owner"].get(f"{home['base']}/gifs/abc123/media").status_code == 422
    assert giphy["calls"] == []


def test_pictures_come_through_this_server_only_from_giphy(home, giphy):
    _with_key(home)
    owner, base = home["owner"], home["base"]
    answer = owner.get(f"{base}/gifs/abc123/media", params={"size": "preview"})
    assert answer.status_code == 200 and answer.content == b"GIF89a-bytes"
    assert answer.headers["content-type"] == "image/gif"
    assert giphy["downloads"] == ["https://media1.giphy.com/media/abc123/200w.gif"]
    assert owner.get(f"{base}/gifs/abc123/media", params={"size": "huge"}).status_code == 400
    assert owner.get(f"{base}/gifs/nope404/media").status_code == 404
    # GIPHY 的条目里混进别的主机：不去取
    giphy["state"]["items"]["evil1"] = {"id": "evil1", "title": "", "images": {
        "downsized": {"url": "https://attacker.example/x.gif", "width": "1", "height": "1"}}}
    assert owner.get(f"{base}/gifs/evil1/media").status_code == 404
    assert giphy["downloads"] == ["https://media1.giphy.com/media/abc123/200w.gif"]


@pytest.mark.parametrize("url, safe", [
    ("https://media1.giphy.com/a.gif", True), ("https://giphy.com/a.gif", True),
    ("http://media1.giphy.com/a.gif", False), ("https://giphy.com.evil.example/a.gif", False),
    ("https://user@media.giphy.com/a.gif", False), ("https://evilgiphy.com/a.gif", False),
])
def test_only_giphys_own_https_hosts_are_fetched(url, safe):
    assert app_gifs._safe_media_url(url) is safe


# ── 带 GIF 的消息 ─────────────────────────────────────────────────────────────

def test_a_gif_message_in_a_team_channel(home, giphy):
    _with_key(home)
    owner, member = home["owner"], _join(home)
    url = _create(home, "fun")
    assert member.post(f"{url}/join").status_code == 204
    sent = member.post(f"{url}/messages", json={"body": "", "clientId": "g1", "media": {"kind": "gif", "id": "abc123"}})
    assert sent.status_code == 200, sent.text
    media = {"kind": "gif", "provider": "giphy", "id": "abc123", "title": "dancing cat", "width": 480, "height": 270}
    assert sent.json()["meta"]["media"] == media and sent.json()["body"] == ""
    # 重试同一条：同一个回答
    again = member.post(f"{url}/messages", json={"body": "", "clientId": "g1", "media": {"kind": "gif", "id": "abc123"}})
    assert again.json()["id"] == sent.json()["id"]
    listed = owner.get(f"{url}/messages").json()["messages"]
    assert listed[-1]["meta"]["media"] == media
    # 成员也能取图
    assert member.get(f"{home['base']}/gifs/abc123/media").status_code == 200


def test_gif_messages_are_refused_where_they_do_not_belong(home, giphy):
    _with_key(home)
    owner, base = home["owner"], home["base"]
    url = _create(home, "fun")
    gif = {"kind": "gif", "id": "abc123"}
    # 和主助理、和专员的私聊：GIF 不进模型上下文
    main = owner.post(f"{base}/dms", json={"agentId": home["main"]["id"]}).json()["channelId"]
    for channel in (main, home["channel"]):
        answer = owner.post(f"{base}/channels/{channel}/messages", json={"body": "", "media": gif})
        assert answer.status_code == 422 and answer.json()["error"]["code"] == "message.media_off"
    # 没有这个 GIF
    answer = owner.post(f"{url}/messages", json={"body": "", "media": {"kind": "gif", "id": "missing9"}})
    assert answer.status_code == 422 and answer.json()["error"]["code"] == "message.media_not_found"
    # 不认识的媒体、界面自己带标题和尺寸
    for media in ({"kind": "sticker", "agentId": "a", "mood": "happy"}, {**gif, "title": "fake", "width": 9}):
        assert owner.post(f"{url}/messages", json={"body": "", "media": media}).status_code == 422
    # 关掉 GIF
    owner.patch(f"{base}/settings", json={"gifs": False})
    answer = owner.post(f"{url}/messages", json={"body": "", "media": gif})
    assert answer.status_code == 422 and answer.json()["error"]["code"] == "message.media_off"
    assert owner.post(f"{url}/messages", json={"body": ""}).status_code == 400          # 没字也没图


def test_an_old_channel_record_gains_the_media_column(home, giphy):
    """有 GIF 之前建的频道库没有 media 列：读照常，第一次写入时补上。"""
    _with_key(home)
    owner = home["owner"]
    url = _create(home, "old")
    assert owner.post(f"{url}/messages", json={"body": "老消息"}).status_code == 200
    from src.workflow.members.registry import MemberRegistry
    path = MemberRegistry(home["root"]).directory / "channel-messages.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE messages DROP COLUMN media")
    assert owner.get(f"{url}/messages").json()["messages"][0]["body"] == "老消息"
    assert owner.post(f"{url}/messages", json={"body": "", "media": {"kind": "gif", "id": "abc123"}}).status_code == 200
    bodies = [(m["body"], (m.get("meta") or {}).get("media", {}).get("id")) for m in
              owner.get(f"{url}/messages").json()["messages"]]
    assert bodies == [("老消息", None), ("", "abc123")]


def test_stored_media_is_checked():
    from src.workflow.members.channel_history import check_media
    good = {"kind": "gif", "provider": "giphy", "id": "abc", "title": "t", "width": 1, "height": 1}
    assert check_media(good) == good
    for bad in ({**good, "url": "https://x"}, {**good, "width": 0}, {**good, "provider": "other"}, "gif"):
        with pytest.raises(ValueError):
            check_media(bad)


def test_origin_is_checked_for_member_gif_posts(home, giphy):
    _with_key(home)
    member = _join(home)
    url = _create(home, "fun")
    member.post(f"{url}/join")
    answer = member.post(f"{url}/messages", json={"body": "", "media": {"kind": "gif", "id": "abc123"}},
                         headers={"Origin": "http://evil.example"})
    assert answer.status_code == 403
    assert ORIGIN["Origin"] != "http://evil.example"
