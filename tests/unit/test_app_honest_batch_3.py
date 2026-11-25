"""等别的能力的几组如实回答（2026-11-24）：密钥申请、保险库连接、agent 的浏览器和电脑、数据库、GitHub、
服务级 webhook、网页推送。没有的回真实的空，做不了的 409，不存在的记录 404；不收下用不了的东西。"""
import pytest
from fastapi.testclient import TestClient

from src.scheduling import notices
from tests.unit.test_member_agents import ORIGIN, _join, home  # noqa: F401 - pytest fixture


def _calls(home):
    base, agent = home["base"], home["agent"]["id"]
    a = f"{base}/agents/{agent}"
    return [
        ("GET", f"{base}/secret-requests/r1", None, 404, "secret.not_found"),
        ("POST", f"{base}/secret-requests/r1/deliver", {"box": "x"}, 404, "secret.not_found"),
        ("POST", f"{base}/secret-requests/r1/decline", {}, 404, "secret.not_found"),
        ("POST", f"{base}/secret-requests/r1/vault", {}, 404, "secret.not_found"),
        ("GET", f"{a}/secrets", None, 200, {"secrets": []}),
        ("DELETE", f"{a}/secrets/API_KEY", None, 404, "secret.not_found"),
        ("POST", f"{base}/vault/connections", {"provider": "1password"}, 409, "vault.connections_unavailable"),
        ("PATCH", f"{base}/vault/connections/c1", {}, 404, "not_found"),
        ("POST", f"{base}/vault/connections/c1/sync", {}, 404, "not_found"),
        ("DELETE", f"{base}/vault/connections/c1", None, 404, "not_found"),
        ("PUT", f"{a}/browser", {"allowedDomains": ["example.com"]}, 409, "computer.unavailable"),
        ("POST", f"{a}/browser/open", {"url": "https://example.com"}, 409, "computer.unavailable"),
        ("POST", f"{a}/browser/stop", {}, 409, "computer.unavailable"),
        ("POST", f"{a}/browser/restart", {}, 409, "computer.unavailable"),
        ("POST", f"{a}/browser-profile/request", {}, 409, "computer.unavailable"),
        ("PUT", f"{a}/computer/target", {"machineId": "m"}, 409, "computer.unavailable"),
        ("PUT", f"{a}/browser-profile", {"profileId": "p"}, 404, "not_found"),
        ("GET", f"{a}/browser/control", None, 200, {"canControl": False}),
        ("GET", f"{a}/desktop/control", None, 200, {"canControl": False}),
        ("GET", f"{a}/desktop/picture", None, 404, "desktop.no_machine"),
        ("GET", f"{base}/databases", None, 200, {"connections": []}),
        ("POST", f"{base}/databases", {"host": "db"}, 409, "databases.unavailable"),
        ("PATCH", f"{base}/databases/d1", {}, 404, "not_found"),
        ("DELETE", f"{base}/databases/d1", None, 404, "not_found"),
        ("GET", f"{base}/databases/d1/tables", None, 404, "not_found"),
        ("GET", f"{base}/databases/d1/databases", None, 404, "not_found"),
        ("GET", f"{a}/databases", None, 200, {"databases": []}),
        ("PUT", f"{a}/databases/d1", {}, 404, "not_found"),
        ("POST", f"{base}/github-app/install", {"returnTo": "/"}, 409, "github.unavailable"),
        ("POST", f"{base}/github-app/complete", {}, 409, "github.unavailable"),
        ("DELETE", f"{base}/github-app/installations/1", None, 404, "not_found"),
        ("POST", f"{base}/agent-imports/github", {}, 409, "github.unavailable"),
        ("GET", f"{base}/github/repositories", None, 200, {"connected": False, "accounts": [], "truncated": False}),
        ("GET", f"{base}/github/access", None, 200, {"orgs": None, "manageUrl": None}),
        ("GET", f"{base}/webhooks/github", None, 200, None),
        ("PUT", f"{base}/webhooks/sentry", {}, 409, "webhooks.unavailable"),
        ("PUT", f"{base}/webhooks/github/secret", {"secret": "s"}, 409, "webhooks.unavailable"),
        ("POST", f"{base}/webhooks/github/repository", {"repo": "a/b"}, 409, "github.unavailable"),
        ("POST", "/api/app/github-app/manifest", {"returnTo": "/"}, 409, "github.unavailable"),
        ("POST", "/api/app/push/devices", {"token": "t"}, 404, "web_push_off"),
    ]


def _check(answer, status, expected):
    assert answer.status_code == status, answer.text
    if isinstance(expected, str):
        assert answer.json()["error"]["code"] == expected
    elif expected is not None:
        assert answer.json() == expected


def test_the_owner_gets_honest_answers(home):
    owner = home["owner"]
    for method, path, body, status, expected in _calls(home):
        answer = owner.request(method, path, json=body, headers=ORIGIN)
        _check(answer, status, expected)
    hook = owner.get(f"{home['base']}/webhooks/sentry", headers=ORIGIN).json()
    assert hook["provider"] == "sentry" and hook["canManage"] is False and hook["url"] is None
    assert owner.get(f"{home['base']}/webhooks/gitlab", headers=ORIGIN).status_code == 422
    assert owner.get(f"{home['base']}/agents/{home['agent']['id']}/screen/control", headers=ORIGIN).status_code == 422
    assert owner.request("DELETE", "/api/app/github-app/settings", headers=ORIGIN).json()["state"] == "off"
    assert owner.post("/api/app/push/devices/forget", json={"token": "t"}).status_code == 204


def test_unknown_agents_and_missing_proof(home):
    owner, base = home["owner"], home["base"]
    for path in ("secrets", "browser/control", "desktop/picture", "databases"):
        assert owner.get(f"{base}/agents/nobody/{path}", headers=ORIGIN).status_code == 404, path
    browser = TestClient(home["app"])
    from tests.unit.test_member_agents import TOKEN
    assert browser.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    elsewhere = {"Origin": "https://evil.example"}
    assert browser.post(f"{base}/databases", json={}, headers=elsewhere).status_code == 403   # 改要所有者证明
    assert browser.get(f"{base}/databases", headers=elsewhere).status_code == 200


def test_members_get_the_same_honest_answers_where_they_may_ask(home):
    member, base, agent = _join(home), home["base"], home["agent"]["id"]
    assert member.get(f"{base}/agents/{agent}/browser/control").json() == {"canControl": False}
    assert member.get(f"{base}/github/repositories").json()["connected"] is False
    assert member.get(f"{base}/webhooks/github").json()["canManage"] is False
    assert member.get(f"{base}/secret-requests/r1").json()["error"]["code"] == "secret.not_found"
    assert member.post(f"{base}/vault/connections", json={}).json()["error"]["code"] == "vault.connections_unavailable"
    assert member.post(f"{base}/agents/{agent}/browser/stop", json={}).json()["error"]["code"] == "computer.unavailable"
    assert member.get(f"{base}/databases").status_code == 403                                 # 数据库只给所有者
    assert member.post("/api/app/push/stale", json={"ids": ["a", "b"]}).json() == {"stale": ["a", "b"]}


@pytest.mark.parametrize("too_many", [101])
def test_stale_follows_the_owners_inbox(home, too_many):
    owner, base = home["owner"], home["base"]
    notices.record_notice(home["root"], "一条新通知", source="assistant")
    notices.record_notice(home["root"], "另一条", source="assistant")
    rows = owner.get(f"{base}/notifications", headers=ORIGIN).json()["items"]
    open_id, read_id = rows[0]["id"], rows[1]["id"]
    assert owner.post(f"{base}/notifications/{read_id}/read", json={"read": True}, headers=ORIGIN).status_code == 200
    stale = owner.post("/api/app/push/stale", json={"ids": [open_id, read_id, "gone"]}, headers=ORIGIN).json()
    assert stale == {"stale": [read_id, "gone"]}
    assert owner.post("/api/app/push/stale", json={"ids": ["x"] * too_many}, headers=ORIGIN).status_code == 422


def test_asking_the_owner_for_a_webhook_is_refused_honestly(home):
    member = _join(home)
    refused = member.post(f"{home['base']}/admin-asks", json={"what": "webhook", "provider": "github"})
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "webhooks.unavailable")
    assert member.post(f"{home['base']}/admin-asks", json={"what": "webhook", "provider": "gitlab"}).status_code == 422
