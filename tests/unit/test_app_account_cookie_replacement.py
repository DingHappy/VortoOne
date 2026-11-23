"""Successful Desktop sign-in leaves one account credential in the browser."""

import pytest
from fastapi.testclient import TestClient

from src.scheduling.notices import record_notice
from src.web.auth import SESSION_COOKIE
from src.web.member_auth import MEMBER_COOKIE
from tests.unit.test_member_agents import ORIGIN, PASSWORD, TOKEN, _join, home  # noqa: F401 - pytest fixture


_EMAIL = "switch-member@example.com"


def _owner_browser(home):
    browser = TestClient(home["app"], headers=ORIGIN)
    assert browser.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    return browser


@pytest.mark.parametrize("flow", ["password", "one-time"])
def test_member_sign_in_replaces_owner_cookie_for_http_and_new_sockets(home, flow):
    member = _join(home, email=_EMAIL)
    browser = _owner_browser(home)
    inbox = f"{home['base']}/notifications"
    record_notice(home["root"], "only the owner can see this synthetic notice", source="test")
    assert len(browser.get(inbox).json()["items"]) == 1
    if flow == "password":
        response = browser.post("/api/auth/sign-in/email", json={"email": _EMAIL, "password": PASSWORD})
    else:
        offer = member.post("/api/app/phone-sign-in")
        assert offer.status_code == 200, offer.text
        response = browser.post("/api/auth/sign-in/one-time-token", json={"token": offer.json()["token"]})
    assert response.status_code == 200, response.text
    assert response.json()["user"]["id"] == member.member_id
    assert SESSION_COOKIE not in browser.cookies and browser.cookies.get(MEMBER_COOKIE)
    profile = browser.get("/api/app/me").json()
    assert profile["user"]["id"] == member.member_id
    assert browser.get(inbox).json()["items"] == []
    org = profile["organizations"][0]["id"]
    with browser.websocket_connect(f"/api/ws?orgId={org}") as socket:
        assert socket.receive_json() == {"type": "ready", "orgId": org, "userId": member.member_id}


def test_invited_signup_also_replaces_owner_cookie(home):
    browser = _owner_browser(home)
    invite = home["owner"].post(f"{home['base']}/invitations", json={"email": _EMAIL}, headers=ORIGIN)
    assert invite.status_code == 200, invite.text
    token = invite.json()["url"].rsplit("/", 1)[1]
    made = browser.post("/api/auth/sign-up/email", headers={"x-invitation-token": token},
                        json={"name": "测试成员", "email": _EMAIL, "password": PASSWORD})
    assert made.status_code == 200, made.text
    assert SESSION_COOKIE not in browser.cookies and browser.cookies.get(MEMBER_COOKIE)
    profile = browser.get("/api/app/me").json()
    assert profile["user"]["id"] == made.json()["user"]["id"]
    assert profile["organizations"] == []
    assert browser.post(f"/api/app/invitations/{token}/accept", json={}).status_code == 200
    assert len(browser.get("/api/app/me").json()["organizations"]) == 1


@pytest.mark.parametrize("flow", ["login", "handover", "one-time"])
def test_owner_sign_in_replaces_member_cookie(home, flow):
    browser = _join(home, email=_EMAIL)
    other_owner = home["owner"]
    owner_id = other_owner.get("/api/app/me").json()["user"]["id"]
    if flow == "login":
        response = browser.post("/api/auth/login", json={"token": TOKEN})
        assert response.status_code == 200, response.text
    elif flow == "handover":
        offer = other_owner.post("/api/auth/handover")
        assert offer.status_code == 200, offer.text
        response = browser.get(f"/api/auth/handover/{offer.json()['code']}", follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/"
    else:
        offer = other_owner.post("/api/app/phone-sign-in", headers=ORIGIN)
        assert offer.status_code == 200, offer.text
        response = browser.post("/api/auth/sign-in/one-time-token", json={"token": offer.json()["token"]})
        assert response.status_code == 200, response.text
    assert MEMBER_COOKIE not in browser.cookies and browser.cookies.get(SESSION_COOKIE)
    assert browser.get("/api/app/me").json()["user"]["id"] == owner_id


def test_failed_sign_ins_leave_the_current_cookie_intact(home):
    owner, member = _owner_browser(home), _join(home, email=_EMAIL)
    old_owner_id = owner.get("/api/app/me").json()["user"]["id"]
    bad_member = owner.post("/api/auth/sign-in/email", json={"email": _EMAIL, "password": "wrong"})
    assert bad_member.status_code == 401
    assert owner.get("/api/app/me").json()["user"]["id"] == old_owner_id
    bad_owner = member.post("/api/auth/login", json={"token": "wrong"})
    assert bad_owner.status_code == 401
    assert member.get("/api/app/me").json()["user"]["id"] == member.member_id
