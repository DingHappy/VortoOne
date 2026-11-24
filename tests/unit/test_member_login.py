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


def test_permission_row_uses_current_owner_proof_and_denies_existing_member_sessions(home):
    owner, base = home["owner"], home["base"]
    ordinary = _join(home)
    managers = _group(home, "成员管理员", ["manage_roles", "post"])
    manager = _join(home, "manager@example.com", roleIds=[managers])
    path = f"{base}/permissions/post"
    body = {"everyone": 0, "roles": []}
    before = owner.get(f"{base}/roles").json()
    for member in (ordinary, manager):
        assert member.put(path, json=body).status_code == 403
        assert member.patch(f"{base}/roles/everyone", json={"permissions": []}).status_code == 403

    guest = TestClient(home["app"])
    assert guest.put(path, json=body).status_code == 401
    assert guest.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    assert guest.put(path, json=body, headers={"Origin": "https://elsewhere.example"}).status_code == 403
    assert owner.get(f"{base}/roles").json() == before
    allowed = guest.put(path, json=body, headers=ORIGIN)
    assert allowed.status_code == 200, allowed.text
    assert "post" not in ordinary.get("/api/app/me").json()["organizations"][0]["permissions"]


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


def test_workspace_mail_provider_catalog_does_not_use_the_host_mailbox(home, monkeypatch):
    from src.agents import mcp_tools
    from src.plugins import mail

    def unexpected(*args, **kwargs):
        pytest.fail("组织邮件目录不应读取或连接本机邮箱")

    monkeypatch.setattr(mcp_tools, "connector_secret_path", unexpected)
    monkeypatch.setattr(mail.imaplib, "IMAP4_SSL", unexpected)
    browser = _join(home)
    url = f"{home['base']}/mail/providers"
    for client in (home["owner"], browser):
        response = client.get(url)
        assert response.status_code == 200
        assert response.json() == {"composio": False, "imap": False, "presets": []}
        assert client.get("/api/app/organizations/another-home/mail/providers").status_code == 404
    assert browser.get(url, headers={"Origin": "https://other.example"}).status_code == 403
    guest = TestClient(home["app"], headers=ORIGIN)
    assert guest.get(url).status_code == 401

    token = _invite(home, email="invited@example.com")
    signed = guest.post("/api/auth/sign-up/email", headers={"x-invitation-token": token},
                        json={"name": "还没加入", "email": "invited@example.com", "password": PASSWORD})
    assert signed.status_code == 200
    assert guest.get(url).status_code == 403


def test_device_accounts_lists_only_the_current_signed_in_profile(home):
    path = "/api/app/device-accounts"
    owner = home["owner"]
    first = _join(home, email="first@example.test")
    second = _join(home, email="second@example.test")
    seen = []
    for client, role in ((owner, "owner"), (first, "member"), (second, "member")):
        profile = client.get("/api/app/me").json()
        response = client.get(path)
        assert response.status_code == 200, response.text
        assert response.headers["Cache-Control"] == "no-store"
        [account] = response.json()["accounts"]
        assert account["current"] is True
        assert account["user"] == {key: profile["user"][key] for key in ("id", "name", "email", "image")}
        assert account["organizations"] == profile["organizations"]
        assert [org["role"] for org in account["organizations"]] == [role]
        assert account["unread"] == [{"orgId": home["org"], "unread": 0, "waiting": 0}]
        seen.append(account["user"]["id"])
    assert len(set(seen)) == 3

    assert first.post("/api/app/device-accounts/sign-out", json={}).status_code == 200
    assert first.get(path).status_code == 401
    assert second.get(path).json()["accounts"][0]["user"]["id"] == seen[-1]


def test_device_accounts_does_not_infer_a_session_or_workspace(home, monkeypatch):
    from src.web.auth import SESSION_COOKIE

    path = "/api/app/device-accounts"
    visitor = TestClient(home["app"], headers=ORIGIN)
    assert visitor.get(path).status_code == 401
    token = _invite(home)
    assert visitor.post("/api/auth/sign-up/email", headers={"x-invitation-token": token}, json={
        "name": "未加入", "email": "ana@example.com", "password": PASSWORD,
    }).status_code == 200
    pending = visitor.get(path)
    assert pending.status_code == 200, pending.text
    [account] = pending.json()["accounts"]
    assert account["current"] is True
    assert account["user"]["email"] == "ana@example.com"
    assert account["organizations"] == []
    assert account["unread"] == []
    assert visitor.get(path, headers={"Origin": "https://other.example.test"}).status_code == 403

    owner_cookie = TestClient(home["app"], cookies={SESSION_COOKIE: TOKEN})
    assert owner_cookie.get(path).status_code == 200
    assert owner_cookie.get(path, headers={"Origin": "https://other.example.test"}).status_code == 403
    monkeypatch.delenv("VORTOONE_API_TOKEN")
    assert TestClient(home["app"]).get(path).json() == {"accounts": []}


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


def test_optional_invitation_mail_query_cannot_replace_account_or_invitation(home):
    token = _invite(home)
    another_token = _invite(home, "other@example.com")
    url = f"/api/app/invitations/{token}/accept"
    mail = {"mail": "p" * 43}
    guest = TestClient(home["app"], headers=ORIGIN)
    assert guest.get("/api/app/auth-config").json()["passwordReset"] is False
    assert guest.post(url, params=mail, json={}).status_code == 401
    assert home["owner"].post(url, params=mail, json={}, headers=ORIGIN).status_code == 401
    assert guest.post("/api/auth/sign-up/email", json={"name": "另一人", "email": "other@example.com", "password": PASSWORD},
                      headers={"x-invitation-token": another_token}).status_code == 200
    assert guest.post(url, params=mail, json={}).status_code == 403
    assert guest.get("/api/app/me").json()["organizations"] == []
    assert guest.get(f"/api/app/invitations/{token}").status_code == 200

    invited = TestClient(home["app"], headers=ORIGIN)
    assert invited.post("/api/auth/sign-up/email", json={"name": "受邀人", "email": "ana@example.com", "password": PASSWORD},
                        headers={"x-invitation-token": token}).status_code == 200
    assert invited.post(f"/api/app/invitations/{'x' * 43}/accept", params=mail, json={}).status_code == 404
    assert invited.post(url, params=mail, json={}, headers={"Origin": "https://other.invalid"}).status_code == 401
    for query in (mail, {}):
        accepted = invited.post(url, params=query, json={})
        assert accepted.status_code == 200 and accepted.json() == {"orgId": home["org"]}
    assert guest.get(f"/api/app/invitations/{token}").status_code == 404
    rows = home["owner"].get(f"{home['base']}/members").json()["members"]
    assert [row["email"] for row in rows if row["role"] == "member"] == ["ana@example.com"]


def test_owner_identity_takes_precedence_when_both_sessions_are_present(home):
    from src.web.auth import SESSION_COOKIE
    from src.web.member_auth import MEMBER_COOKIE

    token = _invite(home)
    browser = TestClient(home["app"], headers=ORIGIN)
    assert browser.post("/api/auth/sign-up/email", json={"name": "受邀人", "email": "ana@example.com", "password": PASSWORD},
                        headers={"x-invitation-token": token}).status_code == 200
    member_session = browser.cookies.get(MEMBER_COOKIE)
    assert member_session
    owner = TestClient(home["app"], headers=ORIGIN)
    assert owner.post("/api/auth/sign-in/email", json={"password": TOKEN}).status_code == 200
    owner_session = owner.cookies.get(SESSION_COOKIE)
    assert owner_session
    both = TestClient(home["app"], headers=ORIGIN,
                      cookies={SESSION_COOKIE: owner_session, MEMBER_COOKIE: member_session})
    assert both.get("/api/app/me").json()["organizations"][0]["role"] == "owner"
    url = f"/api/app/invitations/{token}/accept"
    assert both.post(url, json={}).status_code == 401
    assert both.get(f"/api/app/invitations/{token}").status_code == 200
    assert browser.post(url, json={}).json() == {"orgId": home["org"]}


# ── 成员会话只在白名单上算数 ──────────────────────────────────────────────────

def _fill(path, *, team_channel=False):
    if team_channel:
        path = path.replace("{channel_id}", "ch_" + "0" * 32)
        for name in ("{artifact_id}", "{poll_id}", "{choices_id}"):      # 频道里的投票、选择卡（成员只认这种 id）
            path = path.replace(name, "blk_" + "0" * 32)
    path = path.replace("{category_id}", "cat_" + "0" * 32)
    return re.sub(r"\{[^}]+\}", "x", path)


def test_a_member_session_reaches_nothing_outside_the_allow_list(home):
    """把服务上每一条路由都用成员会话打一遍：白名单和免鉴权之外，一条都不能进到处理函数。"""
    from src.web import member_auth
    from src.web.auth import _EXEMPT_EXACT, _EXEMPT_PREFIXES, MOBILE_SELF_AUTHENTICATED, PUBLIC_DOWNLOAD_ROUTES
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
                      or (method, url) in MOBILE_SELF_AUTHENTICATED
                      or (method, url) in PUBLIC_DOWNLOAD_ROUTES)
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
    routes = _live_routes()
    live = {(method, _fill(path, team_channel=team_channel))
            for path, methods in routes for method in methods for team_channel in (False, True)}
    live.update((method, _fill(path.replace("{channel_id}", "pd_" + "0" * 32)))
                for path, methods in routes for method in methods if "{channel_id}" in path)
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


def test_role_mail_loss_preview_unblocks_role_assignment_without_writing(home):
    role = _group(home, "财务", ["view_usage"])
    first = _join(home, email="first@example.test")
    second = _join(home, email="second@example.test", roleIds=[role])
    first_id = first.get("/api/app/me").json()["user"]["id"]
    second_id = second.get("/api/app/me").json()["user"]["id"]
    owner, base = home["owner"], home["base"]
    members = owner.get(base + "/members").json()

    path = f"{base}/roles/{role}/members/preview"
    preview = owner.post(path, json={"userIds": [first_id, second_id, first_id]})
    assert preview.status_code == 200
    assert preview.headers["cache-control"] == "no-store"
    assert preview.json() == {"mailboxes": []}
    assert owner.get(base + "/members").json() == members
    saved = owner.put(f"{base}/members/{first_id}/roles", json={"roleIds": [role], "mailLossSeen": []})
    assert saved.status_code == 200
    assert saved.json() == {"roleIds": [role]}
    assert owner.post(path, json={"userIds": [first_id]}).json() == {"mailboxes": []}


def test_role_mail_loss_preview_uses_the_role_assignment_owner_boundary(home, monkeypatch):
    role = _group(home, "财务", ["view_usage"])
    managers = _group(home, "成员管理员", ["manage_members"])
    manager = _join(home, email="manager@example.test", roleIds=[managers])
    member = _join(home, email="member@example.test")
    member_id = member.get("/api/app/me").json()["user"]["id"]
    path = f"{home['base']}/roles/{role}/members/preview"
    body = {"userIds": [member_id]}

    visitor = TestClient(home["app"])
    assert visitor.post(path, json=body).status_code == 401
    for client in (manager, member):
        assert client.post(path, json=body).status_code == 403
        assert client.put(f"{home['base']}/members/{member_id}/roles", json={"roleIds": [role]}).status_code == 403
    owner_cookie = TestClient(home["app"])
    assert owner_cookie.post("/api/auth/login", json={"token": TOKEN}, headers=ORIGIN).status_code == 200
    assert owner_cookie.post(path, json=body, headers=ORIGIN).json() == {"mailboxes": []}
    assert owner_cookie.post(path, json=body).status_code == 403
    denied = owner_cookie.post(path, json=body, headers={"Origin": "https://other.example.test"})
    assert denied.status_code == 403
    assert denied.headers["cache-control"] == "no-store"
    monkeypatch.delenv("VORTOONE_API_TOKEN")
    assert visitor.post(path, json=body, headers=ORIGIN).status_code == 403


def test_role_mail_loss_preview_checks_the_workspace_role_and_each_member(home):
    role = _group(home, "财务", ["view_usage"])
    member = _join(home, email="member@example.test")
    member_id = member.get("/api/app/me").json()["user"]["id"]
    owner, base = home["owner"], home["base"]
    path = f"{base}/roles/{role}/members/preview"
    assert owner.post(path, json={"userIds": [member_id, "unknown-member"]}).status_code == 404
    owner_id = owner.get("/api/app/me").json()["user"]["id"]
    assert owner.post(path, json={"userIds": [owner_id]}).status_code == 404
    assert owner.post(f"{base}/roles/unknown-role/members/preview", json={"userIds": [member_id]}).status_code == 404
    other = f"/api/app/organizations/unknown-workspace/roles/{role}/members/preview"
    assert owner.post(other, json={"userIds": [member_id]}).status_code == 404
    assert owner.post(path, json={"userIds": member_id}).status_code == 422
    assert owner.post(path, json={"userIds": [1]}).status_code == 422
    assert owner.post(path, json={"userIds": [member_id], "roleIds": [role]}).status_code == 422
    assert owner.post(path, json={"userIds": [member_id] * 501}).status_code == 422


def test_a_removed_member_is_out_at_once(home):
    browser = _join(home)
    member_id = browser.get("/api/app/me").json()["user"]["id"]
    path = f"{home['base']}/members/{member_id}"
    owner = TestClient(home["app"])
    assert owner.post("/api/auth/login", json={"token": TOKEN}, headers=ORIGIN).status_code == 200
    denied = owner.get(path + "/removal", headers={"Origin": "https://other.example.test"})
    assert denied.status_code == 403
    assert denied.headers["cache-control"] == "no-store"
    preview = owner.get(path + "/removal")
    assert preview.status_code == 200
    assert preview.headers["cache-control"] == "no-store"
    assert preview.json() == {"heir": None, "agents": [], "losePlan": [], "computers": [], "routines": 0}
    assert home["owner"].get(path + "/removal").json() == preview.json()
    assert browser.get("/api/app/me").json()["organizations"][0]["id"] == home["org"]
    assert home["owner"].delete(path).status_code == 204
    gone = owner.get(path + "/removal")
    assert gone.status_code == 404
    assert gone.headers["cache-control"] == "no-store"
    assert browser.get("/api/app/me").status_code == 401
    assert browser.get(home["base"]).status_code == 401
    assert browser.post("/api/auth/sign-in/email",
                        json={"email": "ana@example.com", "password": PASSWORD}).status_code == 401


def test_member_removal_preview_matches_existing_delete_permissions(home, monkeypatch):
    group = _group(home, "成员管理员", ["manage_members"])
    manager = _join(home, email="manager@example.test", roleIds=[group])
    member = _join(home, email="target@example.test")
    target = member.get("/api/app/me").json()["user"]["id"]
    path = f"{home['base']}/members/{target}"
    visitor = TestClient(home["app"], headers=ORIGIN)
    assert visitor.get(path + "/removal").status_code == 401
    assert visitor.delete(path).status_code == 401
    for session in (member, manager):
        assert session.get(path + "/removal").status_code == 403
        assert session.delete(path).status_code == 403

    monkeypatch.delenv("VORTOONE_API_TOKEN", raising=False)
    blocked = visitor.get(path + "/removal")
    assert blocked.status_code == 403
    assert blocked.headers["cache-control"] == "no-store"
    assert visitor.delete(path).status_code == 403


def test_member_removal_preview_requires_an_existing_joined_target(home):
    token = _invite(home, email="pending@example.test")
    pending = TestClient(home["app"], headers=ORIGIN)
    signed = pending.post("/api/auth/sign-up/email", headers={"x-invitation-token": token}, json={
        "name": "还没加入的人", "email": "pending@example.test", "password": PASSWORD,
    })
    assert signed.status_code == 200
    me = pending.get("/api/app/me").json()
    assert me["organizations"] == []
    owner = home["owner"]
    owner_id = owner.get("/api/app/me").json()["user"]["id"]
    for target in ("unknown-member", me["user"]["id"], owner_id):
        path = f"{home['base']}/members/{target}"
        preview = owner.get(path + "/removal")
        assert preview.status_code == 404
        assert preview.headers["cache-control"] == "no-store"
        assert owner.delete(path).status_code == 404


def test_removal_holds_even_if_the_account_was_not_cleaned_up(home, monkeypatch):
    """登记里没有这个成员了，就不再是成员——哪怕账号文件没删掉。"""
    from src.agents.principal import OWNER, Principal
    from src.workflow.members.registry import MemberRegistry
    browser = _join(home)
    member_id = browser.get("/api/app/me").json()["user"]["id"]
    MemberRegistry(home["root"]).remove_member(Principal(OWNER, "owner"), member_id)
    assert browser.get("/api/app/me").json()["organizations"] == []
    assert browser.get(home["base"]).status_code == 403
    assert browser.get(f"{home['base']}/members").status_code == 403
    assert browser.get(f"{home['base']}/roles").status_code == 403
    assert browser.post(f"{home['base']}/invitations", json={"email": "x@example.com"}).status_code == 403


def test_workspace_details_return_the_current_principals_workspace(home):
    owner, path = home["owner"], home["base"]
    team = _group(home, "财务", ["view_usage"])
    member = _join(home, "workspace-user@example.com", roleIds=[team])
    plain = _join(home, "workspace-user-two@example.com")
    assert owner.patch(path, json={"name": "工作室", "context": "空间背景"}).status_code == 204
    assert member.put(f"{path}/me/start", json={"page": "last"}).status_code == 200

    for client, role in ((owner, "owner"), (member, "member"), (plain, "member")):
        response = client.get(path)
        assert response.status_code == 200, response.text
        assert response.headers["Cache-Control"] == "no-store"
        [summary] = client.get("/api/app/me").json()["organizations"]
        details = response.json()
        assert details["name"] == "工作室" and details["role"] == role
        if role == "owner":
            assert details.pop("context") == "空间背景"
        assert details == summary
        assert client.get("/api/app/organizations/an-unknown-workspace").status_code == 404

    assert "view_usage" in member.get(path).json()["permissions"]
    assert "view_usage" not in plain.get(path).json()["permissions"]
    assert member.get(path).json()["start"] == {"page": "last", "channelId": None}
    assert plain.get(path).json()["start"] == {"page": "home", "channelId": None}


def test_workspace_details_require_join_or_proven_owner(home, monkeypatch):
    path = home["base"]
    owner = TestClient(home["app"])
    assert owner.get(path).status_code == 401
    assert owner.post("/api/auth/login", json={"token": TOKEN}, headers=ORIGIN).status_code == 200
    assert owner.get(path).status_code == 200
    assert owner.get(path, headers={"Origin": "https://foreign.example.test"}).status_code == 403

    pending = TestClient(home["app"], headers=ORIGIN)
    invite = _invite(home, email="workspace-pending@example.com")
    signed = pending.post("/api/auth/sign-up/email", headers={"x-invitation-token": invite},
                          json={"name": "未加入", "email": "workspace-pending@example.com", "password": PASSWORD})
    assert signed.status_code == 200, signed.text
    assert pending.get(path).status_code == 403

    monkeypatch.delenv("VORTOONE_API_TOKEN")
    assert TestClient(home["app"]).get(path).status_code == 403


def test_member_can_update_only_own_password_after_explicit_sign_in(home):
    from starlette.websockets import WebSocketDisconnect
    from src.workflow.ai_apps.store import AiApps
    from src.workflow.members.owner_account import OwnerAccount

    path = "/api/app/me/password"
    proposed = "a brand new password"
    payload = {"password": proposed}
    origin = ORIGIN
    claimed_by = TestClient(home["app"], headers=origin)
    claim, _ = OwnerAccount(home["root"]).claim_link()
    assert claimed_by.post("/api/auth/owner-claim", json={
        "token": claim, "name": "所有者", "email": "owner@example.test", "password": PASSWORD,
    }).status_code == 200

    activation = _join(home)
    other = _join(home, "other@example.test")
    member_id = activation.get("/api/app/me").json()["user"]["id"]
    assert activation.get("/api/app/me").json()["user"]["hasPassword"] is True
    bearer_refused = home["owner"].post(path, json=payload, headers=origin)
    assert bearer_refused.status_code == 409 and bearer_refused.json()["error"]["code"] == "account.password_unsupported"
    refused = claimed_by.post(path, json=payload)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "account.password_unsupported"
    assert TestClient(home["app"], headers=origin).post(path, json=payload).status_code == 401

    def sign_in(client, password):
        return client.post("/api/auth/sign-in/email", json={"email": "ana@example.com", "password": password})

    refused = activation.post(path, json=payload)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "reauth.sign_in"
    issued = activation.post("/api/app/phone-sign-in", json={})
    assert issued.status_code == 200
    phone = TestClient(home["app"], headers=origin)
    assert phone.post("/api/auth/sign-in/one-time-token", json={"token": issued.json()["token"]}).status_code == 200
    refused = phone.post(path, json=payload)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "reauth.sign_in"
    pending = phone.post("/api/app/phone-sign-in", json={})
    assert pending.status_code == 200

    current, sibling = TestClient(home["app"], headers=origin), TestClient(home["app"], headers=origin)
    assert sign_in(current, PASSWORD).status_code == 200
    assert sign_in(sibling, PASSWORD).status_code == 200
    assert current.post(path, json=payload, headers={"Origin": "https://cross-origin.example.test"}).status_code == 403
    assert current.post(path, json={**payload, "userId": other.get("/api/app/me").json()["user"]["id"]}).status_code == 422

    apps = AiApps(home["root"])
    _, secret = apps.create_token(member_id, "Member testing token", "read", None)
    other_id = other.get("/api/app/me").json()["user"]["id"]
    _, other_secret = apps.create_token(other_id, "Other testing token", "read", None)
    assert apps.authenticate(secret) is not None
    with sibling.websocket_connect("/api/ws", params={"orgId": home["org"]}) as subscription:
        assert subscription.receive_json()["type"] == "ready"
        response = current.post(path, json=payload)
        assert response.status_code == 204 and response.content == b""
        subscription.send_json({"type": "ping"})
        with pytest.raises(WebSocketDisconnect) as closed:
            subscription.receive_json()
        assert closed.value.code == 1008
    assert current.get("/api/app/me").json()["user"]["id"] == member_id
    assert other.get("/api/app/me").status_code == 200
    assert apps.authenticate(secret) is None and apps.authenticate(other_secret) is not None
    for ended in (activation, sibling, phone):
        assert ended.get("/api/app/me").status_code == 401
    one_time = {"token": pending.json()["token"]}
    assert TestClient(home["app"], headers=origin).post("/api/auth/sign-in/one-time-token", json=one_time).status_code == 401
    new_session = TestClient(home["app"], headers=origin)
    assert sign_in(new_session, PASSWORD).status_code == 401
    assert sign_in(new_session, proposed).status_code == 200
    assert claimed_by.post("/api/auth/sign-in/email", json={
        "email": "owner@example.test", "password": PASSWORD,
    }).status_code == 200
