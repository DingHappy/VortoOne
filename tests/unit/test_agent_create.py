"""所有者从界面新建、修改、归档、恢复、删除 agent；谁能和它对话按受众；成员和别的网站做不了这些。"""
import pytest
from fastapi.testclient import TestClient

from src.agents import identity_groups as rules
from src.workflow.members import accounts as accounts_module

TOKEN = "owner-token-for-test"
ORIGIN = {"Origin": "http://testserver"}
PASSWORD = "correct horse battery"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("VORTOCODE_API_TOKEN", "AUTODEV_API_TOKEN", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VORTOONE_API_TOKEN", TOKEN)
    monkeypatch.setattr(accounts_module, "_SCRYPT", {"n": 2 ** 4, "r": 8, "p": 1})
    monkeypatch.setattr(accounts_module, "_DECOY", accounts_module._sealed("decoy-password"))
    accounts_module._FAILURES.clear()
    from src.web.server import app
    owner = TestClient(app, headers=ORIGIN)
    assert owner.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    org = owner.get("/api/app/me").json()["organizations"][0]["id"]
    yield {"root": tmp_path, "app": app, "owner": owner, "base": f"/api/app/organizations/{org}"}
    accounts_module._FAILURES.clear()


def _member(home, email="ana@example.com"):
    token = home["owner"].post(f"{home['base']}/invitations", json={"email": email}).json()["url"].rsplit("/", 1)[1]
    browser = TestClient(home["app"], headers=ORIGIN)
    assert browser.post("/api/auth/sign-up/email", json={"name": "小王", "email": email, "password": PASSWORD},
                        headers={"x-invitation-token": token}).status_code == 200
    assert browser.post(f"/api/app/invitations/{token}/accept", json={}).status_code == 200
    me = browser.get("/api/app/me").json()["user"]["id"]
    return browser, me


def _create(home, **extra):
    body = {"name": "小研", "handle": "xiaoyan", "role": "研究员", "instructions": "帮我查公开资料，附上来源。",
            "model": "auto", "appearance": {"color": "teal", "eyes": "happy", "accessory": "glasses"},
            "setup": {"role": "research", "hello": "你好，我是小研", "apps": {"gmail": "read"}},
            "audience": {"mode": "owner", "userIds": [], "roleIds": [], "consultable": True}, **extra}
    return home["owner"].post(f"{home['base']}/agents", json=body)


def _listed(client, home):
    return {a["handle"]: a for a in client.get(f"{home['base']}/agents").json()["agents"]}


def test_the_owner_creates_an_agent_with_the_least_it_needs(home):
    created = _create(home)
    assert created.status_code == 200, created.text
    agent = created.json()
    assert (agent["name"], agent["handle"], agent["role"]) == ("小研", "xiaoyan", "研究员")
    assert agent["instructions"] == "帮我查公开资料，附上来源。" and agent["model"] == ""
    assert agent["appearance"] == {"color": "teal", "eyes": "happy", "accessory": "glasses"}
    assert agent["setup"] == {"role": "research", "done": [], "hello": "你好，我是小研", "apps": {"gmail": "read"}}
    assert agent["audienceMode"] == "owner" and agent["canTalk"] is True
    assert _listed(home["owner"], home)["xiaoyan"]["id"] == agent["id"]

    from src.workflow.staff.registry import StaffRegistry
    registry = StaffRegistry(str(home["root"]))
    member = registry.member("xiaoyan")
    assert member["engine"] == "vortocode"
    grants = registry.role(member["role"])["grants"]
    assert grants == {"operations": ["read"], "tools": ["web_search", "web_fetch"], "sees": [], "web": True,
                      "project": False}
    from src.workflow.staff.approvals import StaffApprovals
    decided = [p for p in StaffApprovals(str(home["root"])).list() if p["status"] == "approved"]
    assert {p["snapshot"]["kind"] for p in decided} == {"role", "member"}       # 批准历史照样留下


def test_editing_changes_its_card_and_its_instructions(home):
    agent = _create(home).json()
    changed = home["owner"].patch(f"{home['base']}/agents/{agent['id']}", json={
        "name": "小研二号", "instructions": "只查中文资料。", "appearance": {"color": "rose"},
        "audience": {"mode": "everyone", "userIds": [], "roleIds": []}})
    assert changed.status_code == 200, changed.text
    view = changed.json()
    assert (view["name"], view["instructions"], view["appearance"], view["audienceMode"]) == (
        "小研二号", "只查中文资料。", {"color": "rose"}, "everyone")
    step = home["owner"].post(f"{home['base']}/agents/{agent['id']}/setup/ai", json={})
    assert step.json()["setup"]["done"] == ["ai"]
    assert home["owner"].post(f"{home['base']}/agents/{agent['id']}/setup/nope", json={}).status_code == 422


def test_archive_restore_and_delete(home):
    agent = _create(home).json()
    url = f"{home['base']}/agents/{agent['id']}"
    assert home["owner"].delete(url).status_code == 204
    assert "xiaoyan" not in _listed(home["owner"], home)
    former = home["owner"].get(f"{home['base']}/former-agents").json()["agents"]
    assert [(f["name"], f["deleted"], f["appearance"]["color"]) for f in former] == [("小研", False, "teal")]
    assert home["owner"].post(f"{url}/unarchive", json={}).status_code == 204
    assert _listed(home["owner"], home)["xiaoyan"]["canTalk"] is True
    assert home["owner"].delete(url, params={"purge": "true"}).status_code == 204
    assert "xiaoyan" not in _listed(home["owner"], home)
    assert home["owner"].get(f"{home['base']}/former-agents").json()["agents"][0]["deleted"] is True
    assert home["owner"].post(f"{url}/unarchive", json={}).status_code == 422          # 删掉的不能恢复
    assert _create(home).status_code == 422                                              # handle 不能再用


@pytest.mark.parametrize("change, words", [
    ({"handle": "Bad Handle"}, "handle"), ({"handle": "assistant"}, "handle"), ({"name": "  "}, "名字"),
    ({"appearance": {"color": "url(evil)"}}, "颜色"), ({"instructions": "x" * 9000}, "说明"),
    ({"audience": {"mode": "people", "userIds": ["nobody"]}}, "成员")])
def test_bad_input_is_refused_and_nothing_is_made(home, change, words):
    answer = _create(home, **change)
    assert answer.status_code == 422 and words in answer.json()["error"]["message"], answer.text
    assert "xiaoyan" not in _listed(home["owner"], home)
    from src.workflow.staff.registry import StaffRegistry
    assert StaffRegistry(str(home["root"])).members() == []                  # 没有建了一半的


def test_members_and_other_sites_cannot_manage_agents(home):
    agent = _create(home, audience={"mode": "everyone", "userIds": [], "roleIds": []}).json()
    browser, _me = _member(home)
    assert browser.post(f"{home['base']}/agents", json={"name": "x", "handle": "xx"}).status_code == 403
    assert browser.patch(f"{home['base']}/agents/{agent['id']}", json={"name": "y"}).status_code == 403
    assert browser.delete(f"{home['base']}/agents/{agent['id']}").status_code == 403
    assert browser.get(f"{home['base']}/former-agents").json() == {"agents": []}
    stranger = TestClient(home["app"], headers={"Origin": "https://evil.example"})
    stranger.cookies.update(home["owner"].cookies)
    assert stranger.post(f"{home['base']}/agents", json={"name": "x", "handle": "xx"}).status_code == 403
    assert stranger.delete(f"{home['base']}/agents/{agent['id']}").status_code == 403
    assert "xx" not in _listed(home["owner"], home) and "xiaoyan" in _listed(home["owner"], home)


def test_who_may_talk_to_it_follows_its_audience(home):
    browser, me = _member(home)
    only_owner = _create(home, handle="mine").json()
    shared = _create(home, handle="shared", audience={"mode": "people", "userIds": [me], "roleIds": []}).json()
    _create(home, handle="others", audience={"mode": "people", "userIds": [], "roleIds": []})
    seen = _listed(browser, home)
    assert "mine" not in seen and "others" not in seen
    assert "shared" in seen and "audience" not in seen["shared"]            # 受众里还有谁，成员看不到
    assert shared["audience"]["userIds"] == [me] and only_owner["audienceMode"] == "owner"


def test_the_kernel_rule_audience_comes_before_any_level():
    assert rules.in_audience(None, "m1", []) is True
    assert rules.in_audience({"mode": "owner"}, "m1", []) is False
    assert rules.in_audience({"mode": "people", "userIds": [], "roleIds": ["g1"]}, "m1", ["g1"]) is True
    assert rules.in_audience({"mode": "weird"}, "m1", []) is False
    # 单独给他定了级别也不行：不在受众里就不能对话
    assert rules.act_level_on({"m1": "act"}, "m1", "act", audience={"mode": "owner"}) == "none"
    with pytest.raises(ValueError):
        rules.check_audience({"mode": "people", "roleIds": [rules.EVERYONE]}, [], [rules.EVERYONE])
