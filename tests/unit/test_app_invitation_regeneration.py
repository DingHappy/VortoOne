"""Desktop 邮箱邀请失去明文后，可由所有者更换仍有效的单次链接。"""

from datetime import datetime, timedelta, timezone
import stat

from fastapi.testclient import TestClient

from src.agents.principal import OWNER, Principal
from src.workflow.members import registry
from tests.unit.test_member_login import ORIGIN, PASSWORD, TOKEN, _group, _invite, _join, home  # noqa: F401


def _listed(client, base):
    response = client.get(f"{base}/invitations/all")
    assert response.status_code == 200, response.text
    return {row["email"]: row for row in response.json()["invitations"]}


def _action(base, invitation_id):
    return f"{base}/invitations/{invitation_id}/resend"


def _preview(token):
    return f"/api/app/invitations/{token}"


def test_rotate_returns_new_link_once_and_preserves_identity_and_grants(home):
    owner, base = home["owner"], home["base"]
    role = _group(home, "团队资料", ["view_usage"])
    email = "recipient@example.com"
    old = _invite(home, email, roleIds=[role])
    original = _listed(owner, base)[email]
    assert original["canRegenerateLink"] is True and "url" not in original
    old_listing = owner.get(f"{base}/invitations").json()["invitations"]
    assert [row["url"] for row in old_listing] == [None]
    assert "canRegenerateLink" not in old_listing[0]
    before = registry.MemberRegistry(home["root"]).snapshot()["invitations"][original["id"]]
    action = _action(base, original["id"])

    assert owner.post(action, json={"roleIds": ["admins"]}).status_code == 400
    guest = TestClient(home["app"], headers=ORIGIN)
    assert guest.get(_preview(old)).status_code == 200
    response = owner.post(action, json={})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    result = response.json()
    assert result == {"url": result["url"], "expiresAt": original["expiresAt"]}
    assert result["url"].startswith("http://testserver/invite/")
    new = result["url"].rsplit("/", 1)[1]
    assert new != old
    assert _listed(owner, base)[email] == original
    after = registry.MemberRegistry(home["root"]).snapshot()["invitations"][original["id"]]
    assert after["digest"] != before["digest"]
    assert {**after, "digest": before["digest"]} == before
    assert new not in str(after)
    assert guest.get(_preview(old)).status_code == 404
    preview = guest.get(_preview(new))
    assert preview.status_code == 200 and preview.json()["email"] == email
    assert [role["name"] for role in preview.json()["roles"]] == ["团队资料"]

    signup = "/api/auth/sign-up/email"
    person = {"name": "收件人", "email": email, "password": PASSWORD}
    assert guest.post(signup, json=person, headers={"x-invitation-token": old}).status_code == 403
    assert guest.post(signup, json={**person, "email": "wrong@example.com"},
                      headers={"x-invitation-token": new}).status_code == 403
    assert guest.post(signup, json=person, headers={"x-invitation-token": new}).status_code == 200
    assert guest.post(f"{_preview(old)}/accept", json={}).status_code == 404
    assert guest.post(f"{_preview(new)}/accept", json={}).json() == {"orgId": home["org"]}
    assert guest.get(_preview(new)).status_code == 404
    assert owner.post(action, json={}).status_code == 404


def test_only_proven_owner_may_rotate_and_receive_the_capability(home, monkeypatch):
    from src.web.auth import SESSION_COOKIE

    owner, base = home["owner"], home["base"]
    can_invite = _group(home, "招募", ["invite_people"])
    recruiter = _join(home, "recruiter@example.com", roleIds=[can_invite])
    member = _join(home, "member@example.com")
    email = "recipient@example.com"
    old = _invite(home, email, by=recruiter)
    invitation = _listed(recruiter, base)[email]
    assert invitation["canRegenerateLink"] is False and "url" not in invitation
    assert _listed(owner, base)[email]["canRegenerateLink"] is True
    action = _action(base, invitation["id"])
    assert recruiter.post(action, json={}).status_code == 403
    assert member.post(action, json={}).status_code == 403

    browser = TestClient(home["app"])
    assert browser.post(action, json={}).status_code == 401
    browser.cookies.set(SESSION_COOKIE, TOKEN)
    assert _listed(browser, base)[email]["canRegenerateLink"] is True
    assert browser.post(action, json={}).status_code == 403
    assert browser.post(action, headers={"Origin": "http://foreign.example"}, json={}).status_code == 403
    assert browser.get(_preview(old)).status_code == 200
    assert browser.post(action, headers=ORIGIN, json={}).status_code == 200
    assert browser.get(_preview(old)).status_code == 404

    monkeypatch.delenv("VORTOONE_API_TOKEN")
    assert _listed(browser, base)[email]["canRegenerateLink"] is False
    assert browser.post(action, headers=ORIGIN, json={}).status_code == 403


def test_expired_unbound_and_unauthorized_issuer_are_refused(home):
    owner, base = home["owner"], home["base"]
    can_invite = _group(home, "招募", ["invite_people"])
    recruiter = _join(home, "recruiter@example.com", roleIds=[can_invite])
    deleg_token = _invite(home, "delegated@example.com", by=recruiter)
    assert owner.patch(f"{base}/roles/{can_invite}", json={"permissions": []}).status_code == 204

    store = registry.MemberRegistry(home["root"])
    identity = Principal(OWNER, "owner")
    unbound, unbound_token = store.invite(identity)
    expired, _ = store.invite(identity, email="expired@example.com",
                              now=datetime.now(timezone.utc) - timedelta(days=8))
    rows = _listed(owner, base)
    assert rows[""].get("canRegenerateLink") is False
    assert rows["expired@example.com"]["status"] == "expired"
    assert rows["expired@example.com"]["canRegenerateLink"] is False
    delegated = rows["delegated@example.com"]
    assert delegated["canRegenerateLink"] is False
    snapshot = store.snapshot()["invitations"]

    for invitation_id in (unbound, expired, delegated["id"], "missing"):
        rejected = owner.post(_action(base, invitation_id), json={})
        assert rejected.status_code == 404 and rejected.headers["cache-control"] == "no-store"
    assert store.snapshot()["invitations"] == snapshot
    assert owner.get(_preview(unbound_token)).status_code == 200
    assert owner.get(_preview(deleg_token)).status_code == 404


def test_rotation_sanitizes_removed_groups_and_fails_closed_on_damaged_record(home):
    owner, base = home["owner"], home["base"]
    old_role = _group(home, "已删身份组", ["view_usage"])
    keep_role = _group(home, "保留身份组", ["edit_wiki"])
    email = "recipient@example.com"
    _invite(home, email, roleIds=[old_role, keep_role])
    invite_id = _listed(owner, base)[email]["id"]
    assert owner.delete(f"{base}/roles/{old_role}").status_code == 204
    result = owner.post(_action(base, invite_id), json={})
    assert result.status_code == 200
    assert _listed(owner, base)[email]["roleIds"] == [keep_role]

    store = registry.MemberRegistry(home["root"])
    with store._change() as state:
        state["invitations"][invite_id]["digest"] = "corrupt"
    snapshot = store.snapshot()["invitations"]
    assert _listed(owner, base)[email]["canRegenerateLink"] is False
    refused = owner.post(_action(base, invite_id), json={})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "unavailable"
    assert store.snapshot()["invitations"] == snapshot


def _fail_fsync(monkeypatch, *, directory):
    fsync = registry.os.fsync

    def fault(fd):
        if stat.S_ISDIR(registry.os.fstat(fd).st_mode) == directory:
            raise OSError("simulated synchronization failure")
        return fsync(fd)

    monkeypatch.setattr(registry.os, "fsync", fault)


def test_failed_file_sync_keeps_old_link(home, monkeypatch):
    owner, base = home["owner"], home["base"]
    email = "recipient@example.com"
    old = _invite(home, email)
    invitation = _listed(owner, base)[email]
    with monkeypatch.context() as mock:
        _fail_fsync(mock, directory=False)
        response = owner.post(_action(base, invitation["id"]), json={})
    assert response.status_code == 409
    assert owner.get(_preview(old)).status_code == 200
    assert _listed(owner, base)[email] == invitation


def test_directory_sync_error_returns_already_replaced_link(home, monkeypatch):
    owner, base = home["owner"], home["base"]
    email = "recipient@example.com"
    old = _invite(home, email)
    invitation = _listed(owner, base)[email]
    with monkeypatch.context() as mock:
        _fail_fsync(mock, directory=True)
        response = owner.post(_action(base, invitation["id"]), json={})
    assert response.status_code == 200
    new = response.json()["url"].rsplit("/", 1)[1]
    assert owner.get(_preview(old)).status_code == 404
    assert owner.get(_preview(new)).status_code == 200
