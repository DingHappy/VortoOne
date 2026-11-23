"""成员邀请：全部列表（含过期）、批量邀请、改邀请给的身份组、成员「换角色」。规矩在成员登记处。"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from tests.unit.test_member_login import ORIGIN, PASSWORD, TOKEN, _group, _invite, _join
from tests.unit.test_member_login import home  # noqa: F401 —— 成员登录的整套夹具


def test_a_batch_invites_each_address_once(home):
    owner, base = home["owner"], home["base"]
    _join(home, email="old@example.com")
    group = _group(home, "财务", ["view_usage"])
    answer = owner.post(f"{base}/invitations/batch", json={
        "emails": ["A@example.com", "old@example.com", "not-an-email", "a@example.com"],
        "invites": [{"email": "b@example.com", "roleIds": [group]}]}, headers=ORIGIN)
    assert answer.status_code == 200, answer.text
    results = [(row["email"], row["status"]) for row in answer.json()["results"]]
    assert results == [("a@example.com", "invited"), ("old@example.com", "already_member"),
                       ("not-an-email", "invalid_email"), ("a@example.com", "already_invited"),
                       ("b@example.com", "invited")]
    rows = {row["email"]: row for row in owner.get(f"{base}/invitations").json()["invitations"]}
    assert set(rows) == {"a@example.com", "b@example.com"} and rows["b@example.com"]["roleIds"] == [group]
    links = [row["url"] for row in answer.json()["results"] if row["status"] == "invited"]
    assert all(link.startswith("http://testserver/invite/") for link in links)


@pytest.mark.parametrize("body", [
    {},
    {"emails": ["a@example.com"], "channelIds": ["c1"]},
    {"emails": ["a@example.com"], "agentIds": ["x"]},
    {"emails": [f"p{i}@example.com" for i in range(51)]},
    {"emails": ["a@example.com"], "sendMail": True},
])
def test_bad_batches_are_refused(home, body):
    answer = home["owner"].post(f"{home['base']}/invitations/batch", json=body, headers=ORIGIN)
    assert answer.status_code == 400
    assert home["owner"].get(f"{home['base']}/invitations").json()["invitations"] == []


def test_a_member_without_invite_rights_cannot_batch(home):
    member = _join(home)
    answer = member.post(f"{home['base']}/invitations/batch", json={"emails": ["x@example.com"]})
    assert answer.status_code == 403


def test_a_member_with_invite_rights_can_batch_but_not_grant_more_than_they_hold(home):
    inviter = _group(home, "招募", ["invite_people"])
    admin = _group(home, "管理", ["administrator"])
    member = _join(home, roleIds=[inviter])
    ok = member.post(f"{home['base']}/invitations/batch", json={"emails": ["x@example.com"]})
    assert ok.status_code == 200 and ok.json()["results"][0]["status"] == "invited"
    over = member.post(f"{home['base']}/invitations/batch", json={"emails": ["y@example.com"], "roleIds": [admin]})
    assert over.status_code == 403


def test_the_full_list_includes_expired_invitations(home, monkeypatch):
    from src.workflow.members.registry import MemberRegistry
    owner, base = home["owner"], home["base"]
    _invite(home, "live@example.com")
    _invite(home, "late@example.com")
    registry = MemberRegistry(home["root"])
    with registry._change() as state:
        late = next(key for key, item in state["invitations"].items() if item["email"] == "late@example.com")
        state["invitations"][late]["expires"] = "2000-01-01T00:00:00+00:00"
    rows = {row["email"]: row["status"] for row in owner.get(f"{base}/invitations/all").json()["invitations"]}
    assert rows == {"live@example.com": "pending", "late@example.com": "expired"}
    assert [row["email"] for row in owner.get(f"{base}/invitations").json()["invitations"]] == ["live@example.com"]
    assert all("url" not in row for row in owner.get(f"{base}/invitations/all").json()["invitations"])
    plain = _join(home, email="plain@example.com")
    assert plain.get(f"{base}/invitations/all").status_code == 403


def test_expired_invitation_state_only_returns_known_preview_metadata(home, monkeypatch):
    from src.workflow.members import registry

    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(registry, "_now", lambda: now)
    role = _group(home, "内部资料", ["view_usage"])
    token = _invite(home, "private-address@example.com", roleIds=[role])
    guest = TestClient(home["app"])
    path = f"/api/app/invitations/{token}"
    current = guest.get(path)
    assert current.status_code == 200
    invited = TestClient(home["app"], headers=ORIGIN)
    login = invited.post("/api/auth/sign-up/email",
                         json={"name": "迟到的受邀人", "email": "private-address@example.com", "password": PASSWORD},
                         headers={"x-invitation-token": token})
    assert login.status_code == 200
    before = registry.MemberRegistry(home["root"]).snapshot()
    assert guest.get(path + "/state").status_code == 404

    now += timedelta(days=registry.INVITE_DAYS_DEFAULT)
    assert guest.get(path).status_code == 404
    gone = guest.get(path + "/state")
    assert gone.status_code == 200 and gone.headers["cache-control"] == "no-store"
    assert gone.json() == {"state": "expired", "orgName": current.json()["orgName"],
                           "invitedBy": current.json()["invitedBy"], "at": current.json()["expiresAt"]}
    assert guest.get(path + "/state?receipt=1").json() == gone.json()
    assert registry.MemberRegistry(home["root"]).snapshot() == before
    missing = guest.get("/api/app/invitations/unknown-token/state")
    assert missing.status_code == 404 and missing.headers["cache-control"] == "no-store"
    assert guest.post(path + "/accept", json={}).status_code == 401
    assert invited.post(path + "/accept", json={}).status_code == 404
    assert invited.get("/api/app/me").json()["organizations"] == []

    _invite(home, "another@example.com")
    assert guest.get(path + "/state").status_code == 404   # 过期记录已被旧流程清理


def test_expired_state_does_not_reconstruct_completed_or_revoked_invitations(home, monkeypatch):
    from src.agents.principal import OWNER, Principal
    from src.workflow.members import registry

    removed = _invite(home, "removed@example.com")
    row, = home["owner"].get(f"{home['base']}/invitations").json()["invitations"]
    assert home["owner"].delete(f"{home['base']}/invitations/{row['id']}", headers=ORIGIN).status_code == 204

    accepted = _invite(home, "accepted@example.com")
    browser = TestClient(home["app"], headers=ORIGIN)
    login = browser.post("/api/auth/sign-up/email",
                         json={"name": "已经加入", "email": "accepted@example.com", "password": PASSWORD},
                         headers={"x-invitation-token": accepted})
    assert login.status_code == 200
    assert browser.post(f"/api/app/invitations/{accepted}/accept", json={}).status_code == 200

    # 没有邮箱绑定的旧入口也不能在这里作为邮箱邀请显示。
    store = registry.MemberRegistry(home["root"])
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    issuer = Principal(OWNER, "owner")
    _, general = store.invite(issuer, days=1, now=now)
    monkeypatch.setattr(registry, "_now", lambda: now + timedelta(days=2))
    guest = TestClient(home["app"])
    for token in (removed, accepted, general):
        result = guest.get(f"/api/app/invitations/{token}/state")
        assert result.status_code == 404
        assert "accepted@example.com" not in result.text


def test_accepted_invitation_state_only_belongs_to_the_member_who_consumed_that_link(home, monkeypatch):
    from src.workflow.members import registry

    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(registry, "_now", lambda: now)
    other = _join(home, "other@example.com")
    token = _invite(home, "recipient@example.com")
    guest = TestClient(home["app"], headers=ORIGIN)
    recipient = TestClient(home["app"], headers=ORIGIN)
    invite_path = f"/api/app/invitations/{token}"
    legacy_path = invite_path + "/state"
    state_path = legacy_path + "?receipt=1"
    preview = guest.get(invite_path).json()
    signup = {"name": "受邀人", "email": "recipient@example.com", "password": PASSWORD}
    auth = {"x-invitation-token": token}
    assert recipient.post("/api/auth/sign-up/email", json=signup, headers=auth).status_code == 200
    assert recipient.get(state_path).status_code == 404
    assert recipient.post(invite_path + "/accept", json={}).status_code == 200
    assert guest.get(invite_path).status_code == 404
    assert guest.post("/api/auth/sign-up/email", json=signup, headers=auth).status_code == 403

    receipt = recipient.get(state_path)
    assert receipt.status_code == 200 and receipt.headers["cache-control"] == "no-store"
    assert receipt.json() == {"state": "accepted", "orgId": home["org"], "orgName": preview["orgName"],
                              "at": now.isoformat(timespec="seconds")}
    state = registry.MemberRegistry(home["root"]).snapshot()
    member_id = recipient.get("/api/app/me").json()["user"]["id"]
    assert state["members"][member_id]["invitation_receipt"] == {
        "digest": registry._digest(token), "expires": preview["expiresAt"],
    }
    assert state["invitations"] == {}

    unknown = guest.get("/api/app/invitations/unknown-token/state")
    for response in (guest.get(state_path), other.get(state_path), home["owner"].get(state_path),
                     recipient.get(state_path, headers={"Origin": "https://elsewhere.example"}),
                     recipient.get(state_path, headers={"Authorization": f"Bearer {TOKEN}"}),
                     recipient.get(legacy_path), recipient.get(legacy_path + "?receipt=0"),
                     recipient.get("/api/app/invitations/unknown-token/state?receipt=1")):
        assert response.status_code == 404 and response.headers["cache-control"] == "no-store"
        assert response.json() == unknown.json()

    now = datetime.fromisoformat(preview["expiresAt"])
    assert recipient.get(state_path).status_code == 404
    assert recipient.get("/api/app/me").json()["organizations"][0]["id"] == home["org"]


def test_rotated_invitation_receipt_requires_an_active_session_and_membership(home):
    owner, base = home["owner"], home["base"]
    email = "rotated@example.com"
    original = _invite(home, email)
    row, = owner.get(f"{base}/invitations").json()["invitations"]
    rotated = owner.post(f"{base}/invitations/{row['id']}/resend", json={}, headers=ORIGIN)
    assert rotated.status_code == 200
    token = rotated.json()["url"].rsplit("/", 1)[1]
    recipient = TestClient(home["app"], headers=ORIGIN)
    credentials = {"email": email, "password": PASSWORD}
    signed = recipient.post("/api/auth/sign-up/email", json={**credentials, "name": "受邀人"},
                            headers={"x-invitation-token": token})
    assert signed.status_code == 200
    assert recipient.post(f"/api/app/invitations/{token}/accept", json={}).status_code == 200
    member_id = recipient.get("/api/app/me").json()["user"]["id"]
    state_path = f"/api/app/invitations/{token}/state?receipt=1"
    assert recipient.get(f"/api/app/invitations/{original}/state?receipt=1").status_code == 404
    assert recipient.get(state_path).status_code == 200

    old_session = TestClient(home["app"], headers=ORIGIN, cookies=dict(recipient.cookies))
    assert recipient.post("/api/auth/sign-out", json={}).status_code == 200
    assert old_session.get(state_path).status_code == 404
    assert recipient.post("/api/auth/sign-in/email", json=credentials).status_code == 200
    assert recipient.get(state_path).status_code == 200
    assert owner.delete(f"{base}/members/{member_id}").status_code == 204
    assert recipient.get(state_path).status_code == 404


def test_accept_retry_reuses_the_receipt_committed_with_the_membership(home, monkeypatch):
    from src.workflow.members.accounts import MemberAccounts
    from src.workflow.members.registry import MemberRegistry

    email = "retry@example.com"
    token = _invite(home, email)
    recipient = TestClient(home["app"], headers=ORIGIN)
    assert recipient.post("/api/auth/sign-up/email", json={
        "email": email, "password": PASSWORD, "name": "重试加入的成员",
    }, headers={"x-invitation-token": token}).status_code == 200
    invite_path = f"/api/app/invitations/{token}"

    def fail_to_bind(*args):
        raise OSError("test account write unavailable")

    with monkeypatch.context() as failed:
        failed.setattr(MemberAccounts, "bind_member", fail_to_bind)
        assert recipient.post(invite_path + "/accept", json={}).status_code == 409

    store = MemberRegistry(home["root"])
    committed = store.snapshot()
    assert committed["invitations"] == {}
    member, = committed["members"].values()
    assert recipient.get(invite_path + "/state?receipt=1").status_code == 404
    assert recipient.post(invite_path + "/accept", json={}).status_code == 200
    receipt = recipient.get(invite_path + "/state?receipt=1")
    assert receipt.status_code == 200 and receipt.json()["at"] == member["joined"]
    assert store.snapshot() == committed


def test_expired_state_does_not_revive_a_former_inviters_authority(home, monkeypatch):
    from src.workflow.members import registry

    role = _group(home, "招募", ["invite_people"])
    member = _join(home, email="host@example.com", name="邀请人", roleIds=[role])
    identity = member.get("/api/app/me").json()["user"]["id"]
    now = datetime(2030, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(registry, "_now", lambda: now)
    invited = member.post(f"{home['base']}/invitations", json={"email": "late@example.com"})
    assert invited.status_code == 200
    token = invited.json()["url"].rsplit("/", 1)[1]
    path = f"/api/app/invitations/{token}/state"
    guest = TestClient(home["app"])
    now += timedelta(days=registry.INVITE_DAYS_DEFAULT)
    assert guest.get(path).json()["invitedBy"] == "邀请人"
    assert home["owner"].put(f"{home['base']}/members/{identity}/roles", json={"roleIds": []}).status_code == 200
    assert guest.get(path).status_code == 404


def test_changing_what_an_invitation_gives(home):
    owner, base = home["owner"], home["base"]
    group = _group(home, "财务", ["view_usage"])
    token = _invite(home, "a@example.com")
    [row] = owner.get(f"{base}/invitations").json()["invitations"]
    changed = owner.patch(f"{base}/invitations/{row['id']}", json={"roleIds": [group]}, headers=ORIGIN)
    assert changed.status_code == 200 and changed.json() == {"roleIds": [group], "channelIds": [], "agentIds": []}
    assert owner.get(f"{base}/invitations").json()["invitations"][0]["roleIds"] == [group]
    # 同一个链接照样能用，加入后带着新的身份组
    browser = TestClient(home["app"], headers=ORIGIN)
    browser.post("/api/auth/sign-up/email", json={"name": "小王", "email": "a@example.com", "password": PASSWORD},
                 headers={"x-invitation-token": token})
    assert browser.post(f"/api/app/invitations/{token}/accept", json={}).status_code == 200
    me = browser.get("/api/app/me").json()
    assert "view_usage" in me["organizations"][0]["permissions"]
    assert owner.patch(f"{base}/invitations/{row['id']}", json={"roleIds": []}, headers=ORIGIN).status_code == 404


def test_invitation_edits_are_checked(home):
    owner, base = home["owner"], home["base"]
    _invite(home, "a@example.com")
    [row] = owner.get(f"{base}/invitations").json()["invitations"]
    url = f"{base}/invitations/{row['id']}"
    assert owner.patch(url, json={"channelIds": ["c1"]}, headers=ORIGIN).status_code == 400
    assert owner.patch(url, json={"roleIds": "all"}, headers=ORIGIN).status_code == 400
    assert owner.patch(url, json={"roleIds": ["no-such-group"]}, headers=ORIGIN).status_code in (400, 403)
    assert owner.patch(f"{base}/invitations/i_nope", json={"roleIds": []}, headers=ORIGIN).status_code == 404
    member = _join(home, email="m@example.com")
    assert member.patch(url, json={"roleIds": []}).status_code == 403        # 不是他发的、他也不管成员


def test_member_roles_are_identity_groups_not_two_tiers(home):
    owner, base = home["owner"], home["base"]
    member = _join(home)
    member_id = member.get("/api/app/me").json()["user"]["id"]
    assert owner.patch(f"{base}/members/{member_id}", json={"role": "member"}).status_code == 204
    answer = owner.patch(f"{base}/members/{member_id}", json={"role": "admin"})
    assert answer.status_code == 400 and "身份组" in answer.json()["error"]["message"]
    assert owner.patch(f"{base}/members/nobody", json={"role": "member"}).status_code == 404
    assert member.patch(f"{base}/members/{member_id}", json={"role": "member"}).status_code == 403
