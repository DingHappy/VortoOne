"""成员登录：凭邀请注册、接受邀请、登录、重置密码；成员会话只在白名单接口上算数。"""
import re

import pytest
from fastapi.testclient import TestClient

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
    accounts_module._RECENT.update(since=0.0, count=0)
    from src.web.server import app
    owner = TestClient(app)
    owner.headers["Authorization"] = f"Bearer {TOKEN}"
    org = owner.get("/api/app/me").json()["organizations"][0]["id"]
    yield {"root": str(tmp_path), "app": app, "owner": owner, "org": org, "base": f"/api/app/organizations/{org}"}
    accounts_module._FAILURES.clear()


def _invite(home, email="ana@example.com", by=None, **extra):
    response = (by or home["owner"]).post(f"{home['base']}/invitations", json={"email": email, **extra},
                                          headers=ORIGIN)
    assert response.status_code == 200, response.text
    return response.json()["url"].rsplit("/", 1)[1]


def _join(home, email="ana@example.com", name="小王", **extra):
    """走完整条路：所有者邀请 → 对方凭邀请注册 → 接受。返回这个成员的浏览器。"""
    token = _invite(home, email, **extra)
    browser = TestClient(home["app"], headers=ORIGIN)
    signed = browser.post("/api/auth/sign-up/email", json={"name": name, "email": email, "password": PASSWORD},
                          headers={"x-invitation-token": token})
    assert signed.status_code == 200, signed.text
    accepted = browser.post(f"/api/app/invitations/{token}/accept", json={})
    assert accepted.status_code == 200 and accepted.json() == {"orgId": home["org"]}, accepted.text
    return browser


def _group(home, name, permissions):
    return home["owner"].post(f"{home['base']}/roles", json={"name": name, "permissions": permissions}).json()["id"]


# ── 整条路 ────────────────────────────────────────────────────────────────────

def test_invite_sign_up_accept_and_see_myself(home):
    group = _group(home, "财务", ["view_usage"])
    token = _invite(home, "Ana@Example.com", roleIds=[group])
    guest = TestClient(home["app"], headers=ORIGIN)
    preview = guest.get(f"/api/app/invitations/{token}").json()
    assert preview["email"] == "ana@example.com" and preview["invitedBy"] == "我"
    assert [role["name"] for role in preview["roles"]] == ["财务"]
    assert preview["account"] == {"exists": False, "password": False, "providers": []}

    browser = _join_with(home, guest, token)
    me = browser.get("/api/app/me").json()
    assert me["user"]["name"] == "小王" and me["user"]["email"] == "ana@example.com"
    [org] = me["organizations"]
    assert org["id"] == home["org"] and org["role"] == "member"
    assert "view_usage" in org["permissions"] and "talk_to_agents" in org["permissions"]
    assert "administrator" not in org["permissions"] and "manage_members" not in org["permissions"]

    rows = home["owner"].get(f"{home['base']}/members").json()["members"]
    assert [(row["role"], row["name"], row["email"]) for row in rows] == [
        ("owner", "我", ""), ("member", "小王", "ana@example.com")]
    assert rows[1]["roleIds"] == [group] and rows[1]["userId"] == me["user"]["id"]
    assert guest.get(f"/api/app/invitations/{token}").status_code == 404          # 邀请用掉了
    assert browser.post(f"/api/app/invitations/{token}/accept", json={}).json() == {"orgId": home["org"]}


def _join_with(home, browser, token, name="小王", email="ana@example.com"):
    assert browser.post("/api/auth/sign-up/email", json={"name": name, "email": email, "password": PASSWORD},
                        headers={"x-invitation-token": token}).status_code == 200
    assert browser.post(f"/api/app/invitations/{token}/accept", json={}).status_code == 200
    return browser


def test_sign_out_then_sign_in_again(home):
    browser = _join(home)
    assert browser.post("/api/auth/sign-out", json={}).status_code == 200
    assert browser.get("/api/app/me").status_code == 401
    wrong = browser.post("/api/auth/sign-in/email", json={"email": "ana@example.com", "password": "not it at all"})
    nobody = browser.post("/api/auth/sign-in/email", json={"email": "nobody@example.com", "password": PASSWORD})
    assert wrong.status_code == nobody.status_code == 401 and wrong.json() == nobody.json()
    assert browser.post("/api/auth/sign-in/email",
                        json={"email": "ANA@example.com", "password": PASSWORD}).status_code == 200
    assert browser.get("/api/app/me").json()["organizations"][0]["role"] == "member"


def test_the_screens_sign_out_works_for_a_member(home):
    browser = _join(home)
    left = browser.post("/api/app/device-accounts/sign-out", json={})
    assert left.status_code == 200 and left.json() == {"signedIn": False}
    assert browser.get("/api/app/me").status_code == 401


def test_a_stolen_cookie_dies_with_sign_out(home):
    browser = _join(home)
    stolen = TestClient(home["app"], headers=ORIGIN, cookies=dict(browser.cookies))
    assert stolen.get("/api/app/me").status_code == 200
    browser.post("/api/auth/sign-out", json={})
    assert stolen.get("/api/app/me").status_code == 401


def test_too_many_wrong_passwords_lock_the_account(home):
    browser = _join(home)
    browser.post("/api/auth/sign-out", json={})
    for _ in range(accounts_module.LOCK_AFTER):
        assert browser.post("/api/auth/sign-in/email",
                            json={"email": "ana@example.com", "password": "wrong password"}).status_code == 401
    assert browser.post("/api/auth/sign-in/email",
                        json={"email": "ana@example.com", "password": PASSWORD}).status_code == 429


def test_the_owner_still_signs_in_with_the_token(home):
    browser = TestClient(home["app"])                   # 不带 Origin：所有者登录不靠它
    assert browser.post("/api/auth/sign-in/email", json={"email": "", "password": TOKEN}).status_code == 200
    assert browser.get("/api/app/me").json()["organizations"][0]["role"] == "owner"


# ── 注册只能凭邀请 ────────────────────────────────────────────────────────────

def test_sign_up_needs_a_live_invitation_for_that_email(home):
    token = _invite(home, "ana@example.com")
    guest = TestClient(home["app"], headers=ORIGIN)
    body = {"name": "小王", "email": "ana@example.com", "password": PASSWORD}
    assert guest.post("/api/auth/sign-up/email", json=body).status_code == 403
    assert guest.post("/api/auth/sign-up/email", json=body, headers={"x-invitation-token": "guess"}).status_code == 403
    assert guest.post("/api/auth/sign-up/email", json={**body, "email": "eve@example.com"},
                      headers={"x-invitation-token": token}).status_code == 403
    assert guest.post("/api/auth/sign-up/email", json={**body, "password": "short"},
                      headers={"x-invitation-token": token}).status_code == 422
    assert home["owner"].get(f"{home['base']}/members").json()["members"][1:] == []
    assert guest.post("/api/auth/sign-up/email", json=body, headers={"x-invitation-token": token}).status_code == 200


def test_an_invitation_is_only_for_its_own_email(home):
    eve = _join(home, "eve@example.com", "小伊")
    eve_id = eve.get("/api/app/me").json()["user"]["id"]
    home["owner"].delete(f"{home['base']}/members/{eve_id}")
    token = _invite(home, "ana@example.com")
    other = _invite(home, "eve@example.com")
    # 伊重新注册，然后想用发给安娜的邀请加入
    guest = TestClient(home["app"], headers=ORIGIN)
    assert guest.post("/api/auth/sign-up/email", json={"name": "小伊", "email": "eve@example.com",
                                                       "password": PASSWORD},
                      headers={"x-invitation-token": other}).status_code == 200
    assert guest.post(f"/api/app/invitations/{token}/accept", json={}).status_code == 403
    assert guest.get("/api/app/me").json()["organizations"] == []
    assert TestClient(home["app"]).get(f"/api/app/invitations/{token}").status_code == 200      # 安娜的还在


def test_accepting_needs_a_signed_in_account_and_our_own_page(home):
    token = _invite(home)
    assert TestClient(home["app"], headers=ORIGIN).post(f"/api/app/invitations/{token}/accept",
                                                        json={}).status_code == 401
    assert home["owner"].post(f"/api/app/invitations/{token}/accept", json={}, headers=ORIGIN).status_code == 401
    browser = TestClient(home["app"], headers=ORIGIN)
    browser.post("/api/auth/sign-up/email", json={"name": "小王", "email": "ana@example.com", "password": PASSWORD},
                 headers={"x-invitation-token": token})
    assert browser.post(f"/api/app/invitations/{token}/accept", json={},
                        headers={"Origin": "http://evil.example"}).status_code == 401
    assert browser.get("/api/app/me").json()["organizations"] == []


# ── 成员会话只在白名单上算数 ──────────────────────────────────────────────────

def _fill(path, *, team_channel=False):
    if team_channel:
        path = path.replace("{channel_id}", "ch_" + "0" * 32)
    return re.sub(r"\{[^}]+\}", "x", path)


def test_a_member_session_reaches_nothing_outside_the_allow_list(home):
    """把服务上每一条路由都用成员会话打一遍：白名单和免鉴权之外，一条都不能进到处理函数。"""
    from src.web import member_auth
    from src.web.auth import _EXEMPT_EXACT, _EXEMPT_PREFIXES, MOBILE_SELF_AUTHENTICATED
    from tests.integration.test_server_smoke import _live_routes
    browser = _join(home)
    cookie = browser.cookies.get(member_auth.MEMBER_COOKIE)
    assert cookie
    member = TestClient(home["app"], headers=ORIGIN, cookies={member_auth.MEMBER_COOKIE: cookie},
                        raise_server_exceptions=False)
    checked = 0
    for path, methods in _live_routes():
        if not path.startswith(("/api/", "/ws")):
            continue
        for method in set(methods) - {"HEAD", "OPTIONS"}:
            url = _fill(path).replace("/organizations/x", f"/organizations/{home['org']}")
            exempt = (url in _EXEMPT_EXACT or url.startswith(_EXEMPT_PREFIXES)
                      or (method, url) in MOBILE_SELF_AUTHENTICATED)
            allowed = any(method == m and pattern.match(url) for m, pattern in member_auth.MEMBER_ROUTES)
            if exempt or allowed:
                continue
            answer = member.request(method, url, json={})
            empty = member_auth.empty_answer(method, url)
            if empty is not None:          # 对成员回固定空结构的：回答必须正是那个空结构
                assert answer.status_code == 200 and answer.json() == empty, f"{method} {path}"
            else:
                assert answer.status_code == 403, f"{method} {path} 对成员会话回了 {answer.status_code}"
            checked += 1
    assert checked > 150                               # 确实扫到了整个服务，而不是空跑


def test_every_allow_listed_route_exists(home):
    from src.web import member_auth
    from tests.integration.test_server_smoke import _live_routes
    live = {(method, _fill(path, team_channel=team_channel))
            for path, methods in _live_routes() for method in methods for team_channel in (False, True)}
    for method, pattern in member_auth.MEMBER_ROUTES:
        assert any(method == m and pattern.match(path) for m, path in live), pattern.pattern


def test_a_member_cookie_is_never_the_owner(home):
    browser = _join(home)
    assert browser.get("/api/auth/status").json()["authed"] is False
    assert browser.get(f"{home['base']}/agents").json()["agents"] == []       # 只列他用得了的，主助理不在内
    assert browser.patch(f"{home['base']}/routines/r1", json={"active": False}).status_code == 403
    assert browser.post(f"{home['base']}/roles", json={"name": "我想当管理员",
                                                       "permissions": ["administrator"]}).status_code == 403
    with pytest.raises(Exception):                      # 长连接握手不认成员会话
        with browser.websocket_connect("/api/ws"):
            pass


def test_member_writes_must_come_from_our_own_page(home):
    group = _group(home, "招募", ["invite_people"])
    browser = _join(home, roleIds=[group])
    body = {"email": "new@example.com"}
    assert browser.post(f"{home['base']}/invitations", json=body,
                        headers={"Origin": "http://evil.example"}).status_code == 403
    assert browser.post(f"{home['base']}/invitations", json=body).status_code == 200


def test_member_sessions_mean_nothing_without_an_owner_token_on_the_service(home, monkeypatch):
    from src.web import member_auth
    browser = _join(home)
    monkeypatch.delenv("VORTOONE_API_TOKEN")
    assert member_auth.account(_Request(browser.cookies)) is None
    # 这种服务上谁都算所有者（原有行为）：登录框不会去核对成员密码，也不会发成员会话
    fresh = TestClient(home["app"], headers=ORIGIN)
    assert fresh.post("/api/auth/sign-in/email",
                      json={"email": "ana@example.com", "password": "not the password"}).status_code == 200
    assert member_auth.MEMBER_COOKIE not in fresh.cookies


class _Request:
    def __init__(self, cookies):
        self.cookies = dict(cookies)


# ── 谁能邀请、谁能重置密码 ────────────────────────────────────────────────────

def test_a_manager_invites_but_a_plain_member_does_not(home):
    recruiters = _group(home, "招募", ["invite_people"])
    plain = _join(home, "plain@example.com", "小李")
    manager = _join(home, "boss@example.com", "小赵", roleIds=[recruiters])
    assert plain.post(f"{home['base']}/invitations", json={"email": "x@example.com"}).status_code == 403
    assert plain.get(f"{home['base']}/invitations").status_code == 403
    made = manager.post(f"{home['base']}/invitations", json={"email": "x@example.com"})
    assert made.status_code == 200 and made.json()["invitation"]["invitedBy"] == "小赵"
    assert manager.post(f"{home['base']}/invitations", json={"email": "y@example.com",
                                                             "roleIds": ["admins"]}).status_code == 403
    listed = manager.get(f"{home['base']}/invitations").json()["invitations"]
    assert [row["email"] for row in listed] == ["x@example.com"] and listed[0]["url"] is None
    assert manager.delete(f"{home['base']}/invitations/{listed[0]['id']}").status_code == 204
    assert manager.get(f"{home['base']}/invitations").json()["invitations"] == []


def test_invitations_are_checked(home):
    owner, base = home["owner"], home["base"]
    for body in ({}, {"email": "not-an-email"}, {"email": "a@b.c", "role": "owner"}, {"email": "a@b.c", "roleIds": "x"}):
        assert owner.post(f"{base}/invitations", json=body, headers=ORIGIN).status_code == 400
    _join(home)
    assert owner.post(f"{base}/invitations", json={"email": "ana@example.com"}, headers=ORIGIN).status_code == 400
    first = _invite(home, "new@example.com")
    _invite(home, "new@example.com")                    # 再发一次：旧的那份作废
    assert TestClient(home["app"]).get(f"/api/app/invitations/{first}").status_code == 404
    assert len(owner.get(f"{base}/invitations").json()["invitations"]) == 1


def test_password_reset_by_whoever_manages_the_member(home):
    managers = _group(home, "人事", ["manage_members"])
    target = _join(home, "ana@example.com", "小王")
    peer = _join(home, "peer@example.com", "小李")
    manager = _join(home, "boss@example.com", "小赵", roleIds=[managers])
    target_id = target.get("/api/app/me").json()["user"]["id"]
    manager_id = manager.get("/api/app/me").json()["user"]["id"]
    assert peer.post(f"{home['base']}/members/{target_id}/password-link", json={}).status_code == 403
    assert target.post(f"{home['base']}/members/{manager_id}/password-link", json={}).status_code == 403
    assert manager.post(f"{home['base']}/members/{manager_id}/password-link", json={}).status_code == 403
    assert manager.post(f"{home['base']}/members/m_missing/password-link", json={}).status_code == 404
    link = manager.post(f"{home['base']}/members/{target_id}/password-link", json={})
    assert link.status_code == 200 and link.json()["url"].startswith("http://testserver/reset-password?token=")
    token = link.json()["url"].split("token=")[1]

    guest = TestClient(home["app"], headers=ORIGIN)
    assert guest.post("/api/auth/reset-password", json={"token": "guess", "newPassword": "a new password"}
                      ).status_code == 400
    assert guest.post("/api/auth/reset-password", json={"token": token, "newPassword": "short"}).status_code == 422
    assert guest.post("/api/auth/reset-password", json={"token": token, "newPassword": "a new password"}
                      ).status_code == 200
    assert target.get("/api/app/me").status_code == 401                 # 原来的登录全部退出
    assert guest.post("/api/auth/reset-password", json={"token": token, "newPassword": "another password"}
                      ).status_code == 400
    assert guest.post("/api/auth/sign-in/email", json={"email": "ana@example.com", "password": PASSWORD}
                      ).status_code == 401
    assert guest.post("/api/auth/sign-in/email", json={"email": "ana@example.com", "password": "a new password"}
                      ).status_code == 200


def test_a_removed_member_is_out_at_once(home):
    browser = _join(home)
    member_id = browser.get("/api/app/me").json()["user"]["id"]
    assert home["owner"].delete(f"{home['base']}/members/{member_id}").status_code == 204
    assert browser.get("/api/app/me").status_code == 401
    assert browser.post("/api/auth/sign-in/email",
                        json={"email": "ana@example.com", "password": PASSWORD}).status_code == 401


def test_removal_holds_even_if_the_account_was_not_cleaned_up(home, monkeypatch):
    """登记里没有这个成员了，就不再是成员——哪怕账号文件没删掉。"""
    from src.agents.principal import OWNER, Principal
    from src.workflow.members.registry import MemberRegistry
    browser = _join(home)
    member_id = browser.get("/api/app/me").json()["user"]["id"]
    MemberRegistry(home["root"]).remove_member(Principal(OWNER, "owner"), member_id)
    assert browser.get("/api/app/me").json()["organizations"] == []
    assert browser.get(f"{home['base']}/members").status_code == 403
    assert browser.get(f"{home['base']}/roles").status_code == 403
    assert browser.post(f"{home['base']}/invitations", json={"email": "x@example.com"}).status_code == 403
