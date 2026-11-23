"""Native handover enters the persisted guide for newly installed local workspaces."""

import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from src.web.auth import SESSION_COOKIE
from src.workflow.desktop_setup import FILE, LEGACY_FILE
from src.utils.state_dir import state_dir

OWNER_TOKEN = "desktop-setup-owner-for-test"
BASE = "http://127.0.0.1:9381"
ORIGIN = {"Origin": BASE}
BEARER = {"Authorization": f"Bearer {OWNER_TOKEN}"}
ROUTE = "/api/app/desktop-setup"


@pytest.fixture
def installed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOONE_API_TOKEN", OWNER_TOKEN)
    monkeypatch.setenv("VORTOONE_BUNDLED_DESKTOP", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    from src.web.server import app

    marker = state_dir(tmp_path) / FILE
    marker.parent.mkdir(exist_ok=True)
    marker.write_text('{"step":"purpose"}', encoding="utf-8")

    def browser(peer="127.0.0.1", address=BASE):
        return TestClient(app, base_url=address, follow_redirects=False, client=(peer, 8012))

    return browser, marker


def sign_in(browser, expected="/onboarding", headers=None):
    minted = browser.post("/api/auth/handover", headers=BEARER)
    assert minted.status_code == 200
    landed = browser.get(f"/api/auth/handover/{minted.json()['code']}", headers=headers or {})
    assert (landed.status_code, landed.headers["location"]) == (303, expected)
    assert landed.headers["cache-control"] == "no-store"


def advance(browser, step, next_step, **answer):
    response = browser.patch(ROUTE, json={"step": step, **answer}, headers=ORIGIN)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["step"] == next_step


def test_team_guide_restarts_at_model_and_finishes_with_editable_settings(installed):
    spawn, marker = installed
    browser = spawn()
    sign_in(browser)
    initial = browser.get("/api/app/me")
    assert initial.headers["cache-control"] == "no-store"
    assert initial.json()["desktopSetup"] == {"step": "purpose", "modelConfigured": False}
    agent_name = initial.json()["organizations"][0]["name"]
    org = f"/api/app/organizations/{initial.json()['organizations'][0]['id']}"
    advance(browser, "purpose", "name", mode="team")
    advance(browser, "name", "business", name="测试工作区")
    advance(browser, "business", "about", context="一起处理样例数据")
    advance(browser, "about", "team", workArea="dev")
    advance(browser, "team", "model")
    assert json.loads(marker.read_text())["step"] == "model"

    # Closing the app and signing in again never counts as answering the model step.
    again = spawn()
    sign_in(again)
    assert again.get("/api/app/me").json()["desktopSetup"] == {
        "step": "model", "mode": "team", "name": "测试工作区", "context": "一起处理样例数据", "workArea": "dev",
        "modelConfigured": False,
    }
    stale = browser.patch(ROUTE, json={"step": "team"}, headers=ORIGIN)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "setup_changed"
    advance(again, "model", "invite")
    advance(again, "invite", "done")

    final = spawn()
    sign_in(final, "/")
    profile = final.get("/api/app/me").json()
    assert "desktopSetup" not in profile
    assert profile["user"]["workArea"] == "dev"
    assert profile["organizations"][0]["mode"] == "team"
    assert profile["organizations"][0]["name"] == "测试工作区"
    assert final.get(f"{org}/agents").json()["agents"][0]["name"] == agent_name
    settings = final.get(f"{org}/settings").json()
    assert settings["context"] == "一起处理样例数据"
    assert settings["name"] == "测试工作区"
    renamed = final.patch(f"{org}/settings", json={"name": "改名后的工作区"}, headers=ORIGIN)
    assert renamed.status_code == 200
    assert final.get("/api/app/me").json()["organizations"][0]["name"] == "改名后的工作区"


def test_personal_guide_and_legacy_progress_never_reset(installed):
    spawn, marker = installed
    marker.unlink()
    old = Path(LEGACY_FILE)
    old.write_text('{"step":"about","mode":"personal"}', encoding="utf-8")
    browser = spawn()
    sign_in(browser)
    assert browser.get("/api/app/me").json()["desktopSetup"]["step"] == "about"
    invalid = browser.patch(ROUTE, json={"step": "about", "workArea": "made-up"}, headers=ORIGIN)
    assert invalid.status_code == 400
    assert not marker.exists()
    advance(browser, "about", "team", workArea="skipped")
    advance(browser, "team", "model")
    advance(browser, "model", "done")
    assert json.loads(old.read_text())["step"] == "about"
    sign_in(spawn(), "/")


def test_other_owner_transports_and_unconfigured_dev_never_receive_the_marker(installed, monkeypatch):
    spawn, marker = installed
    for browser, headers in (
        (spawn(peer="192.0.2.2"), {}),
        (spawn(address="https://remote.example.test"), {}),
        (spawn(), {"X-Forwarded-For": "192.0.2.2"}),
    ):
        sign_in(browser, "/", headers)
        assert "desktopSetup" not in browser.get("/api/app/me", headers=headers).json()
        refused = browser.patch(ROUTE, json={"step": "purpose", "mode": "team"}, headers={**ORIGIN, **headers})
        assert refused.status_code == 404

    monkeypatch.delenv("VORTOONE_BUNDLED_DESKTOP")
    developer = spawn()
    sign_in(developer, "/")
    assert "desktopSetup" not in developer.get("/api/app/me").json()
    assert developer.patch(ROUTE, json={"step": "purpose", "mode": "team"}, headers=ORIGIN).status_code == 404
    assert json.loads(marker.read_text()) == {"step": "purpose"}


def test_writes_need_the_owner_browser_cookie_and_same_origin(installed):
    spawn, marker = installed
    browser = spawn()
    body = {"step": "purpose", "mode": "team"}
    assert browser.get("/api/app/me").status_code == 401
    assert browser.patch(ROUTE, json=body, headers=ORIGIN).status_code == 401
    assert browser.patch(ROUTE, json=body, headers={**BEARER, **ORIGIN}).status_code == 403
    sign_in(browser)
    assert browser.patch(ROUTE, json=body).status_code == 403
    assert browser.patch(ROUTE, json=body, headers={"Origin": "https://other.example"}).status_code == 403
    assert browser.patch(ROUTE, json={**body, "name": "extra"}, headers=ORIGIN).status_code == 400
    org = browser.get("/api/app/me").json()["organizations"][0]["id"]
    invite = browser.post(f"/api/app/organizations/{org}/invitations",
                          json={"email": "desktop-member@example.test"}, headers=ORIGIN)
    assert invite.status_code == 200, invite.text
    piece = invite.json()["url"].rsplit("/", 1)[1]
    member = spawn()
    joined = member.post("/api/auth/sign-up/email",
                         json={"name": "测试成员", "email": "desktop-member@example.test", "password": "tests-example-12"},
                         headers={**ORIGIN, "X-Invitation-Token": piece})
    assert joined.status_code == 200, joined.text
    accepted = member.post(f"/api/app/invitations/{piece}/accept", json={}, headers=ORIGIN)
    assert accepted.status_code == 200, accepted.text
    assert member.cookies.get(SESSION_COOKIE) is None
    assert "desktopSetup" not in member.get("/api/app/me").json()
    assert member.patch(ROUTE, json=body, headers=ORIGIN).status_code == 403
    assert json.loads(marker.read_text()) == {"step": "purpose"}


def test_existing_workspace_without_a_marker_uses_normal_landing(installed):
    spawn, marker = installed
    marker.unlink()
    browser = spawn()
    sign_in(browser, "/")
    assert "desktopSetup" not in browser.get("/api/app/me").json()
    assert browser.patch(ROUTE, json={"step": "purpose", "mode": "team"}, headers=ORIGIN).status_code == 404
    assert not marker.exists()


def test_unreadable_progress_never_looks_completed(installed):
    spawn, marker = installed
    marker.write_text('{"step":"bad"}', encoding="utf-8")
    browser = spawn()
    sign_in(browser)
    refused = browser.get("/api/app/me")
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "setup_unavailable"
    assert json.loads(marker.read_text()) == {"step": "bad"}
