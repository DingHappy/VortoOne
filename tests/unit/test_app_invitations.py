"""成员邀请：全部列表（含过期）、批量邀请、改邀请给的身份组、成员「换角色」。规矩在成员登记处。"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from tests.unit.test_member_login import ORIGIN, PASSWORD, TOKEN, _group, _invite, _join
from tests.unit.test_member_login import home  # noqa: F401 —— 成员登录的整套夹具


PUBLIC_ORIGIN = "https://workspace.example.test:8443"
LOCAL_ORIGIN = "http://127.0.0.1:4310"


def _native_owner(home):
    handover = home["owner"].post("/api/auth/handover")
    assert handover.status_code == 200, handover.text
    owner = TestClient(home["app"], base_url=LOCAL_ORIGIN, headers={"Origin": LOCAL_ORIGIN})
    joined = owner.get("/api/auth/handover/" + handover.json()["code"], follow_redirects=False)
    assert joined.status_code == 303 and joined.headers["location"] == "/"
    assert owner.get("/api/app/me").json()["organizations"][0]["role"] == "owner"
    return owner


def _accept_and_sign_in(home, url, email, name):
    origin, token = url.rsplit("/invite/", 1)
    assert origin == PUBLIC_ORIGIN
    browser = TestClient(home["app"], base_url=origin, headers={"Origin": origin})
    assert browser.get(f"/invite/{token}").text == "<p>Desktop UI fixture</p>"
    path = f"/api/app/invitations/{token}"
    preview = browser.get(path)
    assert preview.status_code == 200 and preview.json()["email"] == email
    registered = browser.post("/api/auth/sign-up/email",
                              json={"email": email, "password": PASSWORD, "name": name},
                              headers={"x-invitation-token": token})
    assert registered.status_code == 200, registered.text
    assert "Secure" in registered.headers["set-cookie"]
    entered = browser.post(path + "/accept", json={})
    assert entered.status_code == 200 and entered.json() == {"orgId": home["org"]}
    assert browser.post("/api/app/device-accounts/sign-out", json={}).status_code == 200
    assert browser.get("/api/app/me").status_code == 401

    signed_in = TestClient(home["app"], base_url=origin, headers={"Origin": origin})
    login = signed_in.post("/api/auth/sign-in/email", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    me = signed_in.get("/api/app/me").json()
    assert me["user"]["email"] == email and me["user"]["name"] == name
    assert [(org["id"], org["role"]) for org in me["organizations"]] == [(home["org"], "member")]
    return signed_in, me["user"]["id"]


def test_native_invitation_paths_lead_to_https_member_sign_in_and_chat(home, monkeypatch, tmp_path):
    ui = tmp_path / "desktop-ui"
    ui.mkdir()
    (ui / "index.html").write_text("<p>Desktop UI fixture</p>", encoding="utf-8")
    monkeypatch.setenv("VORTOONE_APP_UI_DIR", str(ui))
    monkeypatch.setenv("VORTOONE_PUBLIC_URL", PUBLIC_ORIGIN + "/")
    owner, base = _native_owner(home), home["base"]

    first_email, second_email = "first@example.test", "second@example.test"
    invite = owner.post(f"{base}/invitations", json={"email": first_email})
    assert invite.status_code == 200, invite.text
    first_url = invite.json()["url"]
    assert invite.json()["invitation"]["url"] == first_url
    first, first_id = _accept_and_sign_in(home, first_url, first_email, "First")

    invited = owner.post(f"{base}/invitations/batch", json={"emails": [second_email]})
    assert invited.status_code == 200, invited.text
    [row] = invited.json()["results"]
    issued_origin, old_token = row["url"].rsplit("/invite/", 1)
    assert issued_origin == PUBLIC_ORIGIN
    rotated = owner.post(f"{base}/invitations/{row['invitationId']}/resend", json={})
    assert rotated.status_code == 200, rotated.text
    assert first.get(f"/api/app/invitations/{old_token}").status_code == 404
    second, second_id = _accept_and_sign_in(home, rotated.json()["url"], second_email, "Second")
    assert first_id != second_id

    suggested_email = "suggested@example.test"
    assert first.post(f"{base}/invitations", json={"email": suggested_email}).status_code == 403
    suggestion = first.post(f"{base}/join-requests/suggest", json={"email": suggested_email})
    assert suggestion.status_code == 200, suggestion.text
    approved = owner.post(f"{base}/join-requests/{suggestion.json()['id']}/approve", json={})
    assert approved.status_code == 200, approved.text
    assert approved.json()["emailed"] is False
    approved_origin, token = approved.json()["url"].rsplit("/invite/", 1)
    assert approved_origin == PUBLIC_ORIGIN
    assert second.get(f"/api/app/invitations/{token}").json()["email"] == suggested_email

    opened = first.post(f"{base}/dms", json={"userId": second_id})
    assert opened.status_code == 200, opened.text
    channel = opened.json()["channelId"]
    path = f"{base}/channels/{channel}/messages"
    wss_url = PUBLIC_ORIGIN.replace("https://", "wss://", 1)
    with second.websocket_connect(f"{wss_url}/api/ws?orgId={home['org']}") as socket:
        assert socket.receive_json()["userId"] == second_id
        socket.send_json({"type": "subscribe", "channelId": channel})
        assert socket.receive_json() == {"type": "subscribed", "channelId": channel}
        sent = first.post(path, json={"body": "Hello from the first joined member"})
        assert sent.status_code == 200, sent.text
        assert socket.receive_json()["event"]["messageId"] == sent.json()["id"]
    replied = second.post(path, json={"body": "Hello back", "replyTo": sent.json()["id"]})
    assert replied.status_code == 200, replied.text
    assert [row["body"] for row in first.get(path).json()["messages"]] == [
        "Hello from the first joined member", "Hello back",
    ]


@pytest.mark.parametrize("configured", [
    "", "http://workspace.example.test", "https://localhost", "https://127.1", "https://[::1]",
    "https://[::ffff:127.0.0.1]", "https://user@workspace.example.test",
    "https://workspace.example.test/reserved-path", "https://workspace.example.test?query",
    "https://workspace.example.test:invalid", "https://broken host.example.test",
])
def test_invalid_public_address_keeps_its_local_origin(home, monkeypatch, configured):
    monkeypatch.setenv("VORTOONE_PUBLIC_URL", configured)
    owner = _native_owner(home)
    invite = owner.post(home["base"] + "/invitations", json={"email": "member@example.test"},
                        headers={"X-Forwarded-Host": "elsewhere.example.test", "X-Forwarded-Proto": "https"})
    assert invite.status_code == 200, invite.text
    assert invite.json()["url"].startswith(LOCAL_ORIGIN + "/invite/")


def test_without_public_address_uses_the_https_served_origin(home, monkeypatch):
    monkeypatch.delenv("VORTOONE_PUBLIC_URL", raising=False)
    owner = TestClient(home["app"], base_url=PUBLIC_ORIGIN, headers={"Origin": PUBLIC_ORIGIN})
    assert owner.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    invite = owner.post(home["base"] + "/invitations", json={"email": "member@example.test"},
                        headers={"X-Forwarded-Host": "elsewhere.example.test", "X-Forwarded-Proto": "http"})
    assert invite.status_code == 200, invite.text
    assert invite.json()["url"].startswith(PUBLIC_ORIGIN + "/invite/")


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
