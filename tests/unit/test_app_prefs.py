"""新版界面里每个人自己的偏好，和所有者改空间名字与时区。"""

import json

import pytest
from fastapi.testclient import TestClient

from tests.unit.test_member_login import ORIGIN, PASSWORD, TOKEN, _invite, _join
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


# ── 引导中的个人工作领域 ────────────────────────────────────────────────

_AREA_URL = "/api/app/me/work-area"


def test_work_area_is_only_read_back_by_its_account(home):
    owner = home["owner"]
    first = _join(home, email="first@example.test")
    second = _join(home, email="second@example.test")
    assert owner.get("/api/app/me").json()["user"]["workArea"] is None

    assert owner.put(_AREA_URL, json={"area": "ops"}).status_code == 204
    assert first.put(_AREA_URL, json={"area": "dev"}).status_code == 204
    assert second.put(_AREA_URL, json={"area": "skipped"}).status_code == 204

    assert first.patch("/api/app/me", json={"locale": "ja"}).status_code == 204
    for browser, area in ((first, "dev"), (second, "skipped"), (owner, "ops")):
        response = browser.get("/api/app/me")
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["user"]["workArea"] == area
    assert all("workArea" not in row for row in owner.get(home["base"] + "/members").json()["members"])


def test_unjoined_account_keeps_area_when_it_joins(home):
    token = _invite(home, "joining@example.test")
    member = TestClient(home["app"], headers=ORIGIN)
    assert member.post("/api/auth/sign-up/email", json={
        "email": "joining@example.test", "password": PASSWORD, "name": "加入者",
    }, headers={"x-invitation-token": token}).status_code == 200
    assert member.get("/api/app/me").json()["organizations"] == []
    assert member.put(_AREA_URL, json={"area": "finance"}).status_code == 204
    assert member.post(f"/api/app/invitations/{token}/accept", json={}).status_code == 200
    assert member.get("/api/app/me").json()["user"]["workArea"] == "finance"


def test_work_area_rejects_untrusted_writes(local):
    # 本地无令牌时仍可显示默认引导，账号字段写入要求认证。
    assert local["client"].put(_AREA_URL, json={"area": "dev"}).status_code == 403


def test_unauthenticated_me_does_not_reveal_an_existing_work_area(home, monkeypatch):
    owner = home["owner"]
    assert owner.patch("/api/app/me", json={"locale": "ja"}).status_code == 204
    assert owner.put(_AREA_URL, json={"area": "ops"}).status_code == 204

    monkeypatch.delenv("VORTOONE_API_TOKEN")
    visitor = TestClient(home["app"])
    response = visitor.get("/api/app/me")
    assert response.status_code == 200
    assert response.json()["user"]["locale"] == "ja"
    assert response.json()["user"]["workArea"] is None

    monkeypatch.setenv("VORTOONE_API_TOKEN", TOKEN)
    assert owner.get("/api/app/me").json()["user"]["workArea"] == "ops"


def test_work_area_requires_authentication_and_same_origin_for_cookie_writes(home):
    visitor = TestClient(home["app"])
    assert visitor.put(_AREA_URL, json={"area": "dev"}).status_code == 401
    member = _join(home)
    foreign = {"Origin": "http://other.example"}
    assert member.put(_AREA_URL, json={"area": "dev"}, headers=foreign).status_code == 403
    browser = TestClient(home["app"])
    assert browser.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    assert browser.put(_AREA_URL, json={"area": "dev"}, headers=foreign).status_code == 403
    assert browser.put(_AREA_URL, json={"area": "dev"}, headers=ORIGIN).status_code == 204


@pytest.mark.parametrize("body", [{}, {"area": None}, {"area": "custom"}, {"area": ["dev"]},
                                  {"area": "dev", "userId": "another-account"}])
def test_invalid_work_area_does_not_replace_saved_area(home, body):
    owner = home["owner"]
    assert owner.put(_AREA_URL, json={"area": "sales"}).status_code == 204
    assert owner.put(_AREA_URL, json=body).status_code == 400
    assert owner.get("/api/app/me").json()["user"]["workArea"] == "sales"


def test_work_area_disk_error_keeps_server_paths_private(home, monkeypatch):
    from src.web import app_prefs

    member = _join(home)
    assert member.put(_AREA_URL, json={"area": "dev"}).status_code == 204

    def writing_failed(*_args):
        raise OSError("fixture-private-file-path")

    monkeypatch.setattr(app_prefs, "_write", writing_failed)
    response = member.put(_AREA_URL, json={"area": "ops"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "unavailable"
    assert "fixture-private-file-path" not in response.text
    assert member.get("/api/app/me").json()["user"]["workArea"] == "dev"


def test_a_damaged_account_preference_stays_readable_without_overwriting_it(home):
    from src.utils.state_dir import state_dir
    owner = home["owner"]
    owner_id = owner.get("/api/app/me").json()["user"]["id"]
    path = state_dir(home["root"]) / "app_prefs.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    corrupt = json.dumps({"version": 1, "people": {owner_id: []}})
    path.write_text(corrupt, encoding="utf-8")

    assert owner.get("/api/app/me").json()["user"]["workArea"] is None
    assert owner.put(_AREA_URL, json={"area": "ops"}).status_code == 400
    assert path.read_text(encoding="utf-8") == corrupt


# ── 个人用户名 ──────────────────────────────────────────────────────────────

def test_owner_and_member_keep_their_own_handles_and_account_ids(home):
    owner, base = home["owner"], home["base"]
    member = _join(home)
    owner_before = owner.get("/api/app/me").json()["user"]
    member_before = member.get("/api/app/me").json()["user"]

    saved = owner.patch(f"{base}/me", json={"handle": "Home-Owner", "timezone": "Asia/Tokyo"})
    assert saved.status_code == 200 and saved.headers["cache-control"] == "no-store"
    assert saved.json()["handle"] == "home-owner" and saved.json()["timezone"] == "Asia/Tokyo"

    saved = member.patch(f"{base}/me", json={"handle": "Blue_Bird", "timezone": "Europe/Paris"})
    assert saved.status_code == 200
    assert saved.json()["handle"] == "blue_bird" and saved.json()["timezone"] == "Europe/Paris"
    assert member.get("/api/app/me").json()["user"] == member_before
    assert owner.get("/api/app/me").json()["user"] == owner_before

    expected = {owner_before["id"]: "home-owner", member_before["id"]: "blue_bird"}
    for collection in ("members", "people"):
        rows = member.get(f"{base}/{collection}").json()[collection]
        assert {row["userId"]: row["handle"] for row in rows} == expected
    for browser, handle in ((owner, "home-owner"), (member, "blue_bird")):
        status = browser.put(f"{base}/me/status", json={"availability": "away"})
        assert status.status_code == 200 and status.json()["handle"] == handle
    assert member.patch(f"{base}/me", json={"handle": member_before["id"]}).json()["handle"] == member_before["id"]
    assert owner.patch(f"{base}/me", json={"handle": "owner"}).json()["handle"] == "owner"


def test_owner_can_restore_default_handle_with_legacy_staff_data(home):
    from src.workflow.staff.registry import StaffRegistry
    from tests.unit.test_staff_contract import role

    registry = StaffRegistry(home["root"])
    registry.register_role(role())
    registry.register_member({"id": "owner", "name": "Legacy staff", "role": "reader", "engine": "vortocode"})

    owner, base = home["owner"], home["base"]
    assert owner.patch(f"{base}/me", json={"handle": "home-owner"}).status_code == 200
    restored = owner.patch(f"{base}/me", json={"handle": "owner"})
    assert restored.status_code == 200 and restored.json()["handle"] == "owner"
    assert registry.member("owner")["name"] == "Legacy staff"


def test_agent_person_and_historical_handles_cannot_be_reassigned(home):
    owner, base = home["owner"], home["base"]
    member = _join(home)
    member_id = member.get("/api/app/me").json()["user"]["id"]
    another = _join(home, email="another@example.com")
    reserved = owner.post(f"{base}/agents", json={"name": "Maya", "handle": "maya"})
    assert reserved.status_code == 200, reserved.text

    assert member.patch(f"{base}/me", json={"handle": "BlueBird"}).status_code == 200
    for handle in ("BLUEBIRD", "MAYA", "Todos", "OWNER", member_id, "staff-cron-future"):
        assert another.patch(f"{base}/me", json={"handle": handle}).status_code == 400
    assert member.patch(f"{base}/me", json={"handle": "sky-bird"}).status_code == 200
    assert another.patch(f"{base}/me", json={"handle": "bluebird"}).status_code == 400
    assert owner.delete(f"{base}/agents/{reserved.json()['id']}").status_code == 204
    assert another.patch(f"{base}/me", json={"handle": "maya"}).status_code == 400

    assert owner.delete(f"{base}/members/{member_id}", headers=ORIGIN).status_code == 204
    assert member.patch(f"{base}/me", json={"handle": "other"}).status_code == 401
    assert another.patch(f"{base}/me", json={"handle": "sky-bird"}).status_code == 400
    before = owner.get(f"{base}/agents").json()
    response = owner.post(f"{base}/agents", json={"name": "Taken", "handle": "bluebird"})
    assert response.status_code == 422
    assert owner.get(f"{base}/agents").json() == before


def test_a_member_cannot_claim_a_handle_in_an_interrupted_approved_registration(home, monkeypatch):
    from src.workflow.staff.approvals import StaffApprovals
    from src.workflow.staff.registry import StaffRegistry
    from tests.unit.staff_owner_helpers import owner_decide
    from tests.unit.test_staff_bundles import bundle

    member = _join(home)
    url = f"{home['base']}/me"
    approvals = StaffApprovals(home["root"])
    proposal = approvals.propose("owner", "bundle", bundle(), "核对资料")
    original = StaffRegistry._write

    def fail_member_write(registry, name, value):
        if name == "members.json":
            raise OSError("fixture interrupted registration")
        return original(registry, name, value)

    with monkeypatch.context() as patch:
        patch.setattr(StaffRegistry, "_write", fail_member_write)
        with pytest.raises(OSError, match="interrupted registration"):
            owner_decide(approvals, proposal["id"], True, proposal["digest"])
    response = member.patch(url, json={"handle": "staff-reader"})
    assert response.status_code == 400
    assert owner_decide(approvals, proposal["id"], True, proposal["digest"])["status"] == "approved"
    assert member.patch(url, json={"handle": "staff-reader"}).status_code == 400


@pytest.mark.parametrize("kind", ["member", "bundle"])
def test_a_new_profile_claim_refuses_a_pending_registration_before_it_writes(home, kind):
    from src.workflow.staff.approvals import StaffApprovals
    from src.workflow.staff.registry import StaffRegistry
    from tests.unit.staff_owner_helpers import owner_decide
    from tests.unit.test_staff_bundles import bundle
    from tests.unit.test_staff_contract import role

    registry = StaffRegistry(home["root"])
    if kind == "member":
        registry.register_role(role())
        handle = "bluebird"
        definition = {"id": handle, "name": "Reader", "role": "reader", "engine": "vortocode"}
    else:
        definition = bundle()
        handle = definition["member"]["id"]
    roles_before = registry.roles()
    approvals = StaffApprovals(home["root"])
    proposal = approvals.propose("owner", kind, definition, "核对资料")
    member = _join(home)
    assert member.patch(f"{home['base']}/me", json={"handle": handle}).status_code == 200
    with pytest.raises(ValueError, match="已留给成员"):
        owner_decide(approvals, proposal["id"], True, proposal["digest"])
    assert approvals.get(proposal["id"])["status"] == "pending"
    assert registry.roles() == roles_before
    assert registry.members() == []
    assert registry.departments() == []
    assert owner_decide(approvals, proposal["id"], False, proposal["digest"])["status"] == "rejected"


def test_per_person_handle_limit_allows_default_and_previously_claimed_handles(home):
    owner = home["owner"]
    member = _join(home)
    url = f"{home['base']}/me"
    for i in range(16):
        assert member.patch(url, json={"handle": f"custom-{i}"}).status_code == 200

    assert member.patch(url, json={"handle": "custom-16"}).status_code == 400
    assert member.patch(url, json={"handle": "custom-15"}).json()["handle"] == "custom-15"
    default = member.get("/api/app/me").json()["user"]["id"]
    assert member.patch(url, json={"handle": default}).json()["handle"] == default
    assert member.patch(url, json={"handle": "custom-0"}).json()["handle"] == "custom-0"
    assert owner.patch(url, json={"handle": "custom-16"}).json()["handle"] == "custom-16"


def test_workspace_handle_limit_counts_removed_members_and_preserves_existing_claims(home, monkeypatch):
    from src.workflow.members import profile_preferences

    monkeypatch.setattr(profile_preferences, "_MAX_HANDLES_PER_WORKSPACE", 2)
    owner, base = home["owner"], home["base"]
    member = _join(home)
    url = f"{base}/me"
    assert owner.patch(url, json={"handle": "first"}).status_code == 200
    assert member.patch(url, json={"handle": "second"}).status_code == 200
    member_id = member.get("/api/app/me").json()["user"]["id"]
    assert owner.delete(f"{base}/members/{member_id}", headers=ORIGIN).status_code == 204

    new_member = _join(home, email="next@example.com")
    assert new_member.patch(url, json={"handle": "fresh"}).status_code == 400
    assert owner.patch(url, json={"handle": "owner"}).json()["handle"] == "owner"
    assert owner.patch(url, json={"handle": "first"}).json()["handle"] == "first"
    assert new_member.patch(url, json={"handle": "second"}).status_code == 400


def test_invalid_or_failed_profile_change_leaves_both_fields_intact(home, monkeypatch):
    from src.workflow.members import profile_preferences

    owner, base = home["owner"], home["base"]
    member = _join(home)
    assert owner.patch(f"{base}/me", json={"handle": "mine", "timezone": "Asia/Tokyo"}).status_code == 200
    assert member.patch(f"{base}/me", json={"handle": "theirs"}).status_code == 200

    for body in ({"handle": "theirs", "timezone": "Europe/Paris"},
                 {"handle": "free", "timezone": "Mars/Base"}):
        assert owner.patch(f"{base}/me", json=body).status_code == 400
    assert member.patch(f"{base}/me", json={"handle": "free"}).status_code == 200

    def writing_failed(*_args):
        raise OSError("fixture-private-file-path")

    monkeypatch.setattr(profile_preferences, "write_preferences", writing_failed)
    result = owner.patch(f"{base}/me", json={"handle": "still-free", "timezone": "Europe/Paris"})
    assert result.status_code == 409 and result.json()["error"]["code"] == "unavailable"
    assert "fixture-private-file-path" not in result.text
    row = owner.get(f"{base}/members").json()["members"][0]
    assert row["handle"] == "mine" and row["timezone"] == "Asia/Tokyo"


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


@pytest.mark.parametrize("bad", [{"handle": "bad.handle"}, {"handle": None}, {"name": "新名字"},
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
