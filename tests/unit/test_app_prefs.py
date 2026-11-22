"""新版界面里每个人自己的偏好，和所有者改空间名字与时区。"""

import json

import pytest
from fastapi.testclient import TestClient

from tests.unit.test_member_login import ORIGIN, TOKEN, _join
from tests.unit.test_member_login import home  # noqa: F401 —— 成员登录的整套夹具

PERSONAL_TOOLS = ["waiting", "approvals", "proposals", "routines", "drive", "calendar", "issues", "threads", "team",
                  "wiki", "dashboards", "taskManager"]
PERSONAL_SECTIONS = ["tools", "agents", "channels", "people"]


@pytest.fixture
def local(tmp_path, monkeypatch):
    """没设主人令牌的本机服务：所有请求都算所有者，但改空间设置要已认证的主人连接。"""
    monkeypatch.chdir(tmp_path)
    for name in ("VORTOONE_API_TOKEN", "VORTOCODE_API_TOKEN", "AUTODEV_API_TOKEN", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    from src.web.server import app
    client = TestClient(app)
    org = client.get("/api/app/me").json()["organizations"][0]["id"]
    return {"root": tmp_path, "client": client, "base": f"/api/app/organizations/{org}"}


# ── 侧栏 ──────────────────────────────────────────────────────────────────────

def test_a_new_home_gets_the_personal_defaults(local):
    client, base = local["client"], local["base"]
    assert client.get(f"{base}/me/sidebar").json() == {"items": {}}
    assert client.get(f"{base}/me/sidebar/order").json() == {"order": PERSONAL_SECTIONS}
    assert client.get(f"{base}/me/sidebar/tools").json() == {"order": PERSONAL_TOOLS}
    assert client.get(f"{base}/me/sidebar/view").json() == {
        "view": {"agentSort": "workspace", "showAllAgents": True, "pins": None}}


def test_sidebar_changes_are_kept_and_can_go_back_to_default(local):
    client, base = local["client"], local["base"]
    moved = client.patch(f"{base}/me/sidebar", json={"item": "wiki", "place": "always"})
    assert moved.status_code == 200 and moved.json() == {"items": {"wiki": "always"}}
    client.patch(f"{base}/me/sidebar", json={"item": "drive", "place": "more"})
    assert client.get(f"{base}/me/sidebar").json() == {"items": {"wiki": "always", "drive": "more"}}
    # place 为 null：这一项回到默认
    assert client.patch(f"{base}/me/sidebar", json={"item": "wiki", "place": None}).json() == {
        "items": {"drive": "more"}}
    assert client.put(f"{base}/me/sidebar", json={"items": {}}).json() == {"items": {}}

    order = ["agents", "tools", "people", "channels"]
    assert client.put(f"{base}/me/sidebar/order", json={"order": order}).json() == {"order": order}
    assert client.get(f"{base}/me/sidebar/order").json() == {"order": order}
    assert client.put(f"{base}/me/sidebar/order", json={"order": None}).json() == {"order": PERSONAL_SECTIONS}

    tools = list(reversed(PERSONAL_TOOLS))
    assert client.put(f"{base}/me/sidebar/tools", json={"order": tools}).json() == {"order": tools}
    assert client.put(f"{base}/me/sidebar/tools", json={"order": None}).json() == {"order": PERSONAL_TOOLS}

    view = {"agentSort": "working", "showAllAgents": False, "pins": {"agents": ["a"], "channels": [], "people": []}}
    saved = client.put(f"{base}/me/sidebar/view", json={"view": view}).json()["view"]
    assert saved == {"agentSort": "working", "showAllAgents": False, "pins": None}     # 置顶按合同从不保存
    assert client.get(f"{base}/me/sidebar/view").json()["view"] == saved


def test_a_partial_tool_order_keeps_the_tools_it_left_out(local):
    client, base = local["client"], local["base"]
    answer = client.put(f"{base}/me/sidebar/tools", json={"order": ["wiki", "drive"]}).json()["order"]
    assert answer[:2] == ["wiki", "drive"] and sorted(answer) == sorted(PERSONAL_TOOLS)


@pytest.mark.parametrize("path, body", [
    ("/me/sidebar", {"item": "nope", "place": "always"}),
    ("/me/sidebar", {"item": "wiki", "place": "top"}),
    ("/me/sidebar", {"item": "wiki"}),
])
def test_bad_sidebar_moves_are_refused(local, path, body):
    assert local["client"].patch(local["base"] + path, json=body).status_code == 400
    assert local["client"].get(f"{local['base']}/me/sidebar").json() == {"items": {}}


@pytest.mark.parametrize("path, body", [
    ("/me/sidebar", {"items": {"wiki": "top"}}),
    ("/me/sidebar", {"items": {"secret": "always"}}),
    ("/me/sidebar/order", {"order": ["tools", "agents"]}),                  # 少了分区
    ("/me/sidebar/order", {"order": ["tools", "agents", "channels", "people", "people"]}),
    ("/me/sidebar/tools", {"order": ["wiki", "wiki"]}),
    ("/me/sidebar/tools", {"order": ["admin"]}),
    ("/me/sidebar/view", {"view": {"agentSort": "random", "showAllAgents": True}}),
    ("/me/sidebar/view", {"view": {"agentSort": "mine", "showAllAgents": "yes"}}),
    ("/me/sidebar/view", {"view": {"agentSort": "mine", "showAllAgents": True, "extra": 1}}),
])
def test_bad_sidebar_settings_are_refused(local, path, body):
    assert local["client"].put(local["base"] + path, json=body).status_code == 400


def test_a_damaged_preferences_file_is_reported_not_overwritten(local):
    from src.utils.state_dir import state_dir
    path = state_dir(str(local["root"])) / "app_prefs.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")
    client, base = local["client"], local["base"]
    assert client.patch(f"{base}/me/sidebar", json={"item": "wiki", "place": "always"}).status_code == 400
    assert path.read_text(encoding="utf-8") == "{broken"
    assert client.get("/api/app/me").json()["user"]["locale"] == "zh-Hans"      # 登录不受影响


def test_owner_and_member_each_keep_their_own_sidebar(home):  # noqa: F811
    owner, base = home["owner"], home["base"]
    member = _join(home)
    assert member.patch(f"{base}/me/sidebar", json={"item": "wiki", "place": "always"}).status_code == 200
    assert owner.patch(f"{base}/me/sidebar", json={"item": "drive", "place": "more"}).status_code == 200
    assert member.get(f"{base}/me/sidebar").json() == {"items": {"wiki": "always"}}
    assert owner.get(f"{base}/me/sidebar").json() == {"items": {"drive": "more"}}
    assert member.put(f"{base}/me/sidebar/order", json={"order": ["people", "tools", "agents", "channels"]},
                      headers={"Origin": "http://evil.example"}).status_code == 403        # 只认我们自己的页面


def test_a_removed_member_has_no_sidebar_to_save(home):  # noqa: F811
    owner, base = home["owner"], home["base"]
    member = _join(home)
    member_id = member.get("/api/app/me").json()["user"]["id"]
    assert owner.delete(f"{base}/members/{member_id}", headers=ORIGIN).status_code == 204
    assert member.patch(f"{base}/me/sidebar", json={"item": "wiki", "place": "always"}).status_code in {401, 403}


# ── 界面语言 ──────────────────────────────────────────────────────────────────

def test_the_screen_language_is_kept_per_account(home):  # noqa: F811
    owner = home["owner"]
    member = _join(home)
    assert owner.patch("/api/app/me", json={"locale": "en", "auto": True}).status_code == 204
    assert member.patch("/api/app/me", json={"locale": "ja"}).status_code == 204
    theirs = owner.get("/api/app/me").json()["user"]
    assert (theirs["locale"], theirs["localeAuto"]) == ("en", True)
    mine = member.get("/api/app/me").json()["user"]
    assert (mine["locale"], mine["localeAuto"]) == ("ja", False)


@pytest.mark.parametrize("body", [{"locale": "xx"}, {"locale": "en", "auto": "yes"}, {"name": "改名"},
                                  {"locale": "en", "email": "a@b.c"}])
def test_only_a_known_language_can_be_set(local, body):
    assert local["client"].patch("/api/app/me", json=body).status_code == 400
    assert local["client"].get("/api/app/me").json()["user"]["locale"] == "zh-Hans"


# ── 个人时区 ────────────────────────────────────────────────────────────────

def test_owner_and_member_keep_their_own_timezones_without_changing_routines(home):
    from src.workflow.staff.routines import Routines

    owner, base = home["owner"], home["base"]
    ana = _join(home)
    bei = _join(home, email="bei@example.com", name="小贝")
    owner_id, ana_id, bei_id = [client.get("/api/app/me").json()["user"]["id"] for client in (owner, ana, bei)]
    assert owner.patch(f"{base}/settings", json={"timezone": "Asia/Shanghai"}).status_code == 200
    routines_before = Routines(home["root"]).read()
    settings_before = owner.get(f"{base}/settings").json()

    saved_owner = owner.patch(f"{base}/me", json={"handle": "owner", "timezone": "Asia/Tokyo"})
    saved_ana = ana.patch(f"{base}/me", json={"handle": ana_id, "timezone": "Asia/Shanghai"})
    assert saved_owner.status_code == saved_ana.status_code == 200
    assert saved_owner.headers["Cache-Control"] == saved_ana.headers["Cache-Control"] == "no-store"
    assert saved_owner.json()["userId"] == owner_id and saved_owner.json()["timezone"] == "Asia/Tokyo"
    assert saved_ana.json()["userId"] == ana_id and saved_ana.json()["timezone"] == "Asia/Shanghai"
    for client, expected_zone in ((owner, "Asia/Tokyo"), (ana, "Asia/Shanghai")):
        status = client.put(f"{base}/me/status", json={"availability": "away"})
        assert status.status_code == 200
        assert status.json()["timezone"] == expected_zone
    assert owner.get(f"{base}/settings").json() == settings_before
    assert Routines(home["root"]).read() == routines_before

    assert owner.patch(f"{base}/settings", json={"timezone": "Europe/London"}).status_code == 200
    expected = {owner_id: "Asia/Tokyo", ana_id: "Asia/Shanghai", bei_id: "Europe/London"}
    for collection in ("members", "people"):
        rows = bei.get(f"{base}/{collection}").json()[collection]
        assert {row["userId"]: row["timezone"] for row in rows} == expected

    reset = ana.patch(f"{base}/me", json={"timezone": None})
    assert reset.status_code == 200 and reset.json()["timezone"] == "Europe/London"
    assert owner.patch(f"{base}/settings", json={"timezone": "UTC"}).status_code == 200
    rows = owner.get(f"{base}/members").json()["members"]
    assert {row["userId"]: row["timezone"] for row in rows} == {owner_id: "Asia/Tokyo", ana_id: "UTC", bei_id: "UTC"}


def test_personal_timezone_can_return_to_browser_default(home):
    client, base = home["owner"], home["base"]
    assert client.get(f"{base}/settings").json()["timezone"] == ""
    assert client.get(f"{base}/members").json()["members"][0]["timezone"] == ""
    assert client.patch(f"{base}/me", json={"timezone": "America/Toronto"}).json()["timezone"] == "America/Toronto"
    assert client.patch(f"{base}/me", json={"timezone": None}).json()["timezone"] == ""
    assert client.get(f"{base}/settings").json()["timezone"] == ""


@pytest.mark.parametrize("bad", [{"handle": "new-handle"}, {"handle": None}, {"name": "新名字"},
                                 {"userId": "another-person"}, {"availability": "hidden"}, {"locale": "en"}])
def test_an_unsupported_profile_change_does_not_also_update_timezone(home, bad):
    client, base = home["owner"], home["base"]
    original = client.patch(f"{base}/me", json={"timezone": "Europe/Paris"}).json()
    assert client.patch(f"{base}/me", json={"timezone": "Asia/Tokyo", **bad}).status_code == 400
    assert client.patch(f"{base}/me", json={"handle": original["handle"]}).json()["timezone"] == "Europe/Paris"
    assert client.get("/api/app/me").json()["user"]["locale"] == "zh-Hans"


@pytest.mark.parametrize("value", ["Mars/Base", "", 10, True, "../UTC", "UTC\x00"])
def test_personal_timezone_requires_an_iana_name_or_null(home, value):
    client, base = home["owner"], home["base"]
    assert client.patch(f"{base}/me", json={"timezone": value}).status_code == 400
    assert client.get(f"{base}/members").json()["members"][0]["timezone"] == ""


def test_unconfigured_owner_cannot_change_timezone(local):
    client, base = local["client"], local["base"]
    assert client.get(f"{base}/members").json()["members"][0]["timezone"] == ""
    assert client.patch(f"{base}/me", json={"timezone": "Asia/Tokyo"}).status_code == 403


def test_unconfigured_owner_does_not_expose_saved_timezone(home, monkeypatch):
    owner, base = home["owner"], home["base"]
    assert owner.patch(f"{base}/me", json={"timezone": "Europe/Paris"}).status_code == 200
    monkeypatch.delenv("VORTOONE_API_TOKEN")
    anonymous = TestClient(home["app"])
    assert anonymous.get(f"{base}/members").json()["members"][0]["timezone"] == ""
    assert anonymous.get(f"{base}/people").json()["people"][0]["timezone"] == ""
    monkeypatch.setenv("VORTOONE_API_TOKEN", TOKEN)
    assert owner.get(f"{base}/members").json()["members"][0]["timezone"] == "Europe/Paris"


def test_only_current_signed_in_members_can_set_timezones(home):
    owner, base = home["owner"], home["base"]
    member = _join(home)
    body = {"timezone": "Asia/Tokyo"}
    assert TestClient(home["app"], headers=ORIGIN).patch(f"{base}/me", json=body).status_code == 401
    assert member.patch(f"{base}/me", json=body, headers={"Origin": "http://hostile.example"}).status_code == 403
    assert member.patch("/api/app/organizations/org_unknown/me", json=body).status_code == 404
    member_id = member.get("/api/app/me").json()["user"]["id"]
    assert owner.delete(f"{base}/members/{member_id}", headers=ORIGIN).status_code == 204
    assert member.patch(f"{base}/me", json=body).status_code == 401


def test_owner_timezone_write_with_login_cookie_requires_same_origin(home):
    client = TestClient(home["app"], headers=ORIGIN)
    assert client.post("/api/auth/sign-in/email", json={"email": "", "password": TOKEN}).status_code == 200
    url = f"{home['base']}/me"
    saved = client.patch(url, json={"timezone": "Asia/Tokyo"})
    assert saved.status_code == 200
    assert client.patch(url, json={"timezone": "Europe/Paris"},
                        headers={"Origin": "http://hostile.example"}).status_code == 403
    assert client.get(f"{home['base']}/members").json()["members"][0]["timezone"] == "Asia/Tokyo"


# ── 空间设置：名字和时区 ──────────────────────────────────────────────────────

def test_the_owner_renames_the_home_and_sets_its_timezone(home):  # noqa: F811
    from src.memory import assistant_profile
    from src.workflow.staff.routines import Routines
    owner, base = home["owner"], home["base"]
    answer = owner.patch(f"{base}/settings", json={"name": "  小家  ", "timezone": "Asia/Shanghai"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["name"] == "小家" and answer.json()["timezone"] == "Asia/Shanghai"
    assert assistant_profile.load_profile(home["root"])["name"] == "小家"
    assert Routines(home["root"]).read()["timezone"] == "Asia/Shanghai"
    assert owner.get(f"{base}/overview").json()["name"] == "小家"
    # 界面的旧写法：PATCH 空间本身
    assert owner.patch(base, json={"name": "大家"}).status_code == 204
    assert owner.get(f"{base}/settings").json()["name"] == "大家"


def test_settings_without_backing_are_refused_not_pretended(home):  # noqa: F811
    owner, base = home["owner"], home["base"]
    before = owner.get(f"{base}/settings").json()
    for body in ({"welcomePeople": True}, {"actionCheck": False}, {"name": "新名字", "teamWatch": True}):
        answer = owner.patch(f"{base}/settings", json=body)
        assert answer.status_code == 400, body
    assert owner.get(f"{base}/settings").json() == before                 # 一项都没改，包括同批的名字
    # 原样发回现有值不算改（界面保存整张表时会这样）
    same = {key: value for key, value in before.items() if key not in {"gifsAvailable", "voiceAvailable",
                                                                         "agentMediaAvailable"}}
    assert owner.patch(f"{base}/settings", json={**same, "name": "新名字"}).json()["name"] == "新名字"


@pytest.mark.parametrize("body", [{"name": ""}, {"name": "   "}, {"name": "名" * 21}, {"timezone": "Mars/Base"},
                                  {"timezone": ""}, {"timezone": None}])
def test_bad_names_and_timezones_are_refused(home, body):  # noqa: F811
    owner, base = home["owner"], home["base"]
    assert owner.patch(f"{base}/settings", json={"timezone": "Asia/Shanghai"}).status_code == 200
    before = owner.get(f"{base}/settings").json()
    assert owner.patch(f"{base}/settings", json=body).status_code == 400
    assert owner.get(f"{base}/settings").json() == before


def test_members_cannot_change_the_homes_settings(home):  # noqa: F811
    member = _join(home)
    base = home["base"]
    assert member.patch(f"{base}/settings", json={"name": "我的了"}).status_code == 403
    assert member.patch(base, json={"name": "我的了"}).status_code == 403
    assert home["owner"].get(f"{base}/settings").json()["name"] != "我的了"


def test_settings_need_an_authenticated_owner_connection(local):
    answer = local["client"].patch(f"{local['base']}/settings", json={"name": "小家"})
    assert answer.status_code == 403
    assert json.loads(answer.text)["error"]["code"] == "forbidden"


def test_the_owner_token_alone_is_enough(home):  # noqa: F811
    client = TestClient(home["app"])
    client.headers["Authorization"] = f"Bearer {TOKEN}"
    assert client.patch(f"{home['base']}/settings", json={"timezone": "Europe/Paris"}).status_code == 200
