"""Native handover enters the persisted guide for newly installed local workspaces."""

import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from src.web.auth import SESSION_COOKIE
from src.web.member_auth import MEMBER_COOKIE
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
    return response.json()


def configured_model(monkeypatch):
    from src.web import app_credentials

    monkeypatch.setenv("DEFAULT_MODEL", "synthetic-model")
    monkeypatch.setenv("OPENAI_API_BASE", "http://localhost:11434/v1")
    calls = []

    async def probe(base, key, model, *, trust_env):
        calls.append((base, key, model, trust_env))
        return {"model": model, "check": "ok"}

    monkeypatch.setattr(app_credentials, "check_model", probe)
    return calls


def test_native_owner_name_is_display_metadata_but_changes_no_credentials(installed):
    from src.workflow.members.owner_account import OwnerAccount

    spawn, marker = installed
    marker.write_text(json.dumps({"step": "purpose", "ownerDisplayName": "安装者名字"}), encoding="utf-8")
    owner = spawn()
    sign_in(owner)
    profile = owner.get("/api/app/me").json()
    assert profile["user"]["name"] == "安装者名字"
    assert profile["user"]["email"] == ""
    assert profile["desktopSetup"] == {"step": "purpose", "modelConfigured": False}
    advance(owner, "purpose", "model", mode="personal")
    assert json.loads(marker.read_text())["ownerDisplayName"] == "安装者名字"

    account = OwnerAccount(str(Path.cwd()))
    assert not account.path.exists()
    assert account.profile() is None
    assert not account.is_owner_email("local-contact@example.test")
    attempt = spawn().post("/api/auth/sign-in/email",
                           json={"email": "local-contact@example.test", "password": "unused-fixture-password"},
                           headers=ORIGIN)
    assert attempt.status_code == 401

    org = profile["organizations"][0]["id"]
    members = owner.get(f"/api/app/organizations/{org}/members").json()["members"]
    [row] = members
    assert row["name"] == "安装者名字" and row["email"] == ""


def test_native_handover_replaces_member_cookie_without_skipping_setup(installed):
    from src.workflow.members.accounts import MemberAccounts

    spawn, _marker = installed
    browser = spawn()
    accounts = MemberAccounts(str(Path.cwd()))
    account = accounts.create("member@example.test", "测试成员", "fixture-member-password")
    browser.cookies.set(MEMBER_COOKIE, accounts.open_session(account), domain="127.0.0.1")
    sign_in(browser)

    assert MEMBER_COOKIE not in browser.cookies
    assert browser.cookies.get(SESSION_COOKIE)
    assert browser.get("/api/app/me").json()["desktopSetup"]["step"] == "purpose"


def test_claimed_account_name_takes_precedence_over_install_display_name(installed):
    from src.workflow.members.owner_account import OwnerAccount

    spawn, marker = installed
    marker.write_text(json.dumps({"step": "purpose", "ownerDisplayName": "安装者名字"}), encoding="utf-8")
    account = OwnerAccount(str(Path.cwd()))
    claim, _expires = account.claim_link()
    account.claim(claim, "认领的名字", "claimed@example.test", "a-claimed-fixture-password")

    owner = spawn()
    sign_in(owner)
    profile = owner.get("/api/app/me").json()
    assert profile["user"]["name"] == "认领的名字"
    assert profile["user"]["email"] == "claimed@example.test"
    assert json.loads(marker.read_text())["ownerDisplayName"] == "安装者名字"


def test_team_guide_restarts_at_model_and_finishes_with_editable_settings(installed, monkeypatch):
    spawn, marker = installed
    browser = spawn()
    sign_in(browser)
    initial = browser.get("/api/app/me")
    assert initial.headers["cache-control"] == "no-store"
    assert initial.json()["user"]["canCreateWorkspace"] is False
    assert initial.json()["desktopManaged"] is True
    assert initial.json()["desktopSetup"] == {"step": "purpose", "modelConfigured": False}
    agent_name = initial.json()["organizations"][0]["name"]
    org = f"/api/app/organizations/{initial.json()['organizations'][0]['id']}"
    advance(browser, "purpose", "name", mode="team")
    advance(browser, "name", "model", name="测试工作区", context="一起处理样例数据")
    assert json.loads(marker.read_text())["step"] == "model"

    # Closing the app and signing in again never counts as answering the model step.
    again = spawn()
    sign_in(again)
    assert again.get("/api/app/me").json()["desktopSetup"] == {
        "step": "model", "mode": "team", "name": "测试工作区", "context": "一起处理样例数据",
        "modelConfigured": False,
    }
    stale = browser.patch(ROUTE, json={"step": "team"}, headers=ORIGIN)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "setup_changed"
    refused = again.patch(ROUTE, json={"step": "model"}, headers=ORIGIN)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "setup_model_required"
    calls = configured_model(monkeypatch)
    advance(again, "model", "presets")
    assert calls == [("http://127.0.0.1:11434/v1", "", "synthetic-model", False)]
    assert "rightHandId" not in advance(again, "presets", "done")

    final = spawn()
    sign_in(final, "/")
    profile = final.get("/api/app/me").json()
    assert "desktopSetup" not in profile
    assert profile["user"]["canCreateWorkspace"] is False
    assert profile["desktopManaged"] is True
    assert profile["organizations"][0]["mode"] == "team"
    assert profile["organizations"][0]["name"] == "测试工作区"
    assert final.get(f"{org}/agents").json()["agents"][0]["name"] == agent_name
    settings = final.get(f"{org}/settings").json()
    assert settings["context"] == "一起处理样例数据"
    assert settings["name"] == "测试工作区"
    renamed = final.patch(f"{org}/settings", json={"name": "改名后的工作区"}, headers=ORIGIN)
    assert renamed.status_code == 200
    assert final.get("/api/app/me").json()["organizations"][0]["name"] == "改名后的工作区"
    cleared = final.patch(f"{org}/settings", json={"context": ""}, headers=ORIGIN)
    assert cleared.status_code == 200 and cleared.json()["context"] == ""
    assert final.get(f"{org}/settings").json()["context"] == ""
    assert final.put("/api/app/me/work-area", json={"area": "ops"}, headers=ORIGIN).status_code == 204
    assert final.get("/api/app/me").json()["user"]["workArea"] == "ops"


def test_personal_guide_and_legacy_progress_never_reset(installed, monkeypatch):
    spawn, marker = installed
    marker.unlink()
    old = Path(LEGACY_FILE)
    old.write_text('{"step":"about","mode":"personal","workArea":"dev"}', encoding="utf-8")
    browser = spawn()
    sign_in(browser)
    me = browser.get("/api/app/me").json()
    assert me["desktopSetup"]["step"] == "model"
    assert me["user"]["workArea"] == "dev"
    invalid = browser.patch(ROUTE, json={"step": "about", "workArea": "made-up"}, headers=ORIGIN)
    assert invalid.status_code == 409
    assert not marker.exists()
    org = me["organizations"][0]["id"]
    configured_model(monkeypatch)
    advance(browser, "model", "presets")
    done = advance(browser, "presets", "done")
    agents = browser.get(f"/api/app/organizations/{org}/agents").json()["agents"]
    assert done["rightHandId"] == agents[0]["id"]
    assert json.loads(marker.read_text())["workArea"] == "dev"
    assert json.loads(old.read_text())["step"] == "about"
    sign_in(spawn(), "/")


@pytest.mark.parametrize(("legacy_step", "expected_step"),
                         [("business", "name"), ("about", "model"), ("team", "model"), ("invite", "model")])
def test_unfinished_team_markers_keep_all_answers(installed, legacy_step, expected_step):
    spawn, marker = installed
    stored = {"step": legacy_step, "mode": "team", "name": "旧名字", "context": "旧简介",
              "workArea": "dev", "ownerDisplayName": "所有者"}
    marker.write_text(json.dumps(stored), encoding="utf-8")
    browser = spawn()
    sign_in(browser)
    profile = browser.get("/api/app/me").json()
    assert profile["desktopSetup"] == {
        "step": expected_step, "mode": "team", "name": "旧名字", "context": "旧简介",
        "workArea": "dev", "modelConfigured": False,
    }
    assert profile["user"]["name"] == "所有者"
    assert json.loads(marker.read_text()) == stored
    if expected_step == "name":
        advance(browser, "name", "model", name="改名")
    advance(browser, "model", "done", skipModel=True)
    assert json.loads(marker.read_text())["context"] == "旧简介"
    assert json.loads(marker.read_text())["workArea"] == "dev"


def test_personal_model_skip_opens_workspace_without_creating_agents(installed):
    spawn, marker = installed
    browser = spawn()
    sign_in(browser)
    org = browser.get("/api/app/me").json()["organizations"][0]["id"]
    agents_url = f"/api/app/organizations/{org}/agents"
    original = browser.get(agents_url).json()
    bad = browser.patch(ROUTE, json={"step": "purpose", "mode": "personal", "skipModel": True}, headers=ORIGIN)
    assert bad.status_code == 400
    advance(browser, "purpose", "model", mode="personal")
    assert browser.patch(ROUTE, json={"step": "model", "skipModel": 1}, headers=ORIGIN).status_code == 422
    done = advance(browser, "model", "done", skipModel=True)
    assert "rightHandId" not in done and done["modelConfigured"] is False
    assert browser.get(agents_url).json() == original
    assert json.loads(marker.read_text()) == {"step": "done", "mode": "personal", "modelDeferred": True}
    me = browser.get("/api/app/me").json()
    assert me["desktopManaged"] is True and me["desktopModelDeferred"] is True
    assert "desktopSetup" not in me
    refused = browser.patch(ROUTE, json={"step": "model"}, headers=ORIGIN)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "setup_model_required"
    assert browser.get("/api/app/me").json()["desktopModelDeferred"] is True
    sign_in(spawn(), "/")


def test_missing_model_cannot_finish_an_unfinished_presets_step(installed):
    spawn, marker = installed
    marker.write_text('{"step":"presets","mode":"personal"}', encoding="utf-8")
    browser = spawn()
    sign_in(browser)
    assert browser.get("/api/app/me").json()["desktopSetup"]["step"] == "model"
    stale = browser.patch(ROUTE, json={"step": "presets"}, headers=ORIGIN)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "setup_changed"
    assert json.loads(marker.read_text())["step"] == "presets"


def test_failed_model_test_can_be_deferred_and_retried_from_home(installed, monkeypatch):
    from src.web import app_credentials

    spawn, marker = installed
    browser = spawn()
    sign_in(browser)
    advance(browser, "purpose", "model", mode="personal")
    exposed_key = "sk-synthetic-test-key"
    monkeypatch.setenv("DEFAULT_MODEL", "synthetic-model")
    monkeypatch.setenv("OPENAI_API_BASE", "https://models.example.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", exposed_key)
    calls = []

    async def probe(base, key, model, *, trust_env):
        calls.append((base, key, model, trust_env))
        return {"check": "failed", "error": key}

    monkeypatch.setattr(app_credentials, "check_model", probe)
    refused = browser.patch(ROUTE, json={"step": "model"}, headers=ORIGIN)
    assert refused.status_code == 502 and refused.json()["error"]["code"] == "setup_model_probe_failed"
    assert exposed_key not in refused.text and json.loads(marker.read_text())["step"] == "model"
    assert calls == [("https://models.example.test/v1", exposed_key, "synthetic-model", True)]
    advance(browser, "model", "done", skipModel=True)
    assert len(calls) == 1 and browser.get("/api/app/me").json()["desktopModelDeferred"] is True
    assert browser.patch(ROUTE, json={"step": "model"}).status_code == 403
    assert len(calls) == 1
    still_refused = browser.patch(ROUTE, json={"step": "model"}, headers=ORIGIN)
    assert still_refused.status_code == 502 and browser.get("/api/app/me").json()["desktopModelDeferred"] is True

    monkeypatch.delenv("OPENAI_API_KEY")
    retry_calls = configured_model(monkeypatch)
    done = advance(browser, "model", "done")
    assert done["modelConfigured"] is True and "rightHandId" not in done
    assert retry_calls == [("http://127.0.0.1:11434/v1", "", "synthetic-model", False)]
    profile = browser.get("/api/app/me").json()
    assert "desktopSetup" not in profile and "desktopModelDeferred" not in profile
    assert json.loads(marker.read_text()) == {"step": "done", "mode": "personal"}
    duplicate = browser.patch(ROUTE, json={"step": "model"}, headers=ORIGIN)
    assert duplicate.status_code == 409 and len(retry_calls) == 1


def test_probe_result_cannot_advance_using_a_different_saved_model(installed, monkeypatch):
    from src.web import app_credentials

    spawn, marker = installed
    browser = spawn()
    sign_in(browser)
    advance(browser, "purpose", "model", mode="personal")
    configured_model(monkeypatch)

    async def changed(base, key, model, *, trust_env):
        monkeypatch.setenv("DEFAULT_MODEL", "another-model")
        return {"check": "ok"}

    monkeypatch.setattr(app_credentials, "check_model", changed)
    refused = browser.patch(ROUTE, json={"step": "model"}, headers=ORIGIN)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "setup_changed"
    assert json.loads(marker.read_text())["step"] == "model"


def test_other_owner_transports_and_unconfigured_dev_never_receive_the_marker(installed, monkeypatch):
    spawn, marker = installed
    for browser, headers in (
        (spawn(peer="192.0.2.2"), {}),
        (spawn(address="https://remote.example.test"), {}),
        (spawn(), {"X-Forwarded-For": "192.0.2.2"}),
    ):
        sign_in(browser, "/", headers)
        profile = browser.get("/api/app/me", headers=headers).json()
        assert "desktopSetup" not in profile and "desktopManaged" not in profile
        refused = browser.patch(ROUTE, json={"step": "purpose", "mode": "team"}, headers={**ORIGIN, **headers})
        assert refused.status_code == 404

    monkeypatch.delenv("VORTOONE_BUNDLED_DESKTOP")
    developer = spawn()
    sign_in(developer, "/")
    profile = developer.get("/api/app/me").json()
    assert "desktopSetup" not in profile and "desktopManaged" not in profile
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
    wrong_scheme = browser.get("/api/app/me", headers={"Origin": BASE.replace("http://", "https://")})
    assert wrong_scheme.status_code == 200
    assert "desktopSetup" not in wrong_scheme.json()
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
    profile = member.get("/api/app/me").json()
    assert "desktopSetup" not in profile and "desktopManaged" not in profile
    assert member.patch(ROUTE, json=body, headers=ORIGIN).status_code == 403
    assert json.loads(marker.read_text()) == {"step": "purpose"}


def test_existing_workspace_without_a_marker_uses_normal_landing(installed):
    spawn, marker = installed
    marker.unlink()
    browser = spawn()
    sign_in(browser, "/")
    profile = browser.get("/api/app/me").json()
    assert "desktopSetup" not in profile and "desktopManaged" not in profile
    assert profile["user"]["canCreateWorkspace"] is False
    assert len(profile["organizations"]) == 1 and profile["organizations"][0]["role"] == "owner"
    refused = browser.post("/api/app/organizations", json={"name": "New workspace"}, headers=ORIGIN)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "organization.single_home"
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


def test_failed_progress_write_cannot_publish_the_answer(installed, monkeypatch):
    from src.memory.assistant_profile import load_home_context
    from src.workflow import desktop_setup

    spawn, marker = installed
    browser = spawn()
    sign_in(browser)
    advance(browser, "purpose", "name", mode="team")

    def disk_error(*args):
        raise OSError("test disk unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(desktop_setup, "_write", disk_error)
        failed = browser.patch(ROUTE, json={"step": "name", "name": "测试工作区", "context": "尚未落盘"}, headers=ORIGIN)
    assert failed.status_code == 503
    assert json.loads(marker.read_text()) == {"step": "name", "mode": "team"}
    assert load_home_context(str(marker.parent.parent)) == ""
    advance(browser, "name", "model", name="测试工作区", context="成功保存")
    assert load_home_context(str(marker.parent.parent)) == "成功保存"


@pytest.mark.parametrize("replace_kind", ["move", "restore"])
def test_home_replacement_cannot_resurrect_legacy_setup(installed, tmp_path, replace_kind):
    from src.workflow import home_backup, home_move

    spawn, marker = installed
    Path(LEGACY_FILE).write_text('{"step":"model","mode":"personal"}', encoding="utf-8")
    marker.write_text('{"step":"done","mode":"personal"}', encoding="utf-8")
    old = tmp_path / "test-source"
    state_dir(old).mkdir(parents=True)
    if replace_kind == "move":
        package = tmp_path / "sample-home.tar.gz"
        home_move.export_home(str(old), str(package))
        with pytest.raises(home_move.MoveError, match="已经有数据"):
            home_move.import_home(str(tmp_path), str(package))
        home_move.import_home(str(tmp_path), str(package), replace=True)
    else:
        created = home_backup.backup(str(old), str(tmp_path / "backups"))
        home_backup.restore(str(tmp_path), created["path"])
    assert json.loads(marker.read_text()) == {"step": "inactive"}
    sign_in(spawn(), "/")


def test_model_step_uses_chat_readiness_without_exposing_settings(installed, monkeypatch):
    spawn, marker = installed
    marker.write_text('{"step":"model","mode":"personal"}', encoding="utf-8")
    browser = spawn()
    sign_in(browser)

    monkeypatch.setenv("DEFAULT_MODEL", "synthetic-openai-model")
    monkeypatch.setenv("OPENAI_API_BASE", "https://remote.example.test/v1")
    setup = browser.get("/api/app/me").json()["desktopSetup"]
    assert setup == {"step": "model", "mode": "personal", "modelConfigured": False}
    monkeypatch.setenv("OPENAI_API_BASE", "http://127.0.0.1:11434/v1")
    setup = browser.get("/api/app/me").json()["desktopSetup"]
    assert setup == {"step": "model", "mode": "personal", "modelConfigured": True}
    assert json.loads(marker.read_text()) == {"step": "model", "mode": "personal"}
