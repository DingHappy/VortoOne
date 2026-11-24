"""邀请链接（主人 2026-11-23 定）：所有者开链接；拿到的人只能申请，进门要所有者批准。
邮箱验证码是开关（默认关）：没证实的邮箱批准后生成邀请由所有者转交，证实了且设了密码的批准后直接进门。"""
import json

import pytest
from fastapi.testclient import TestClient

from src.utils.state_dir import state_dir
from src.workflow.members import join_codes
from tests.unit.test_member_agents import ORIGIN, PASSWORD, _join, home  # noqa: F401 - pytest fixture


def _visitor(home):
    return TestClient(home["app"], headers=ORIGIN)


def _token(link):
    return link["url"].rsplit("/", 1)[1]


def _ask(home, token, **body):
    return _visitor(home).post(f"/api/app/invitations/{token}/requests",
                               json={"name": "小李", "email": "li@example.com", **body})


def test_the_owner_opens_lists_renews_and_turns_off_links(home):
    owner, base = home["owner"], home["base"]
    plain = owner.post(f"{base}/invite-links", json={})
    assert plain.status_code == 200, plain.text
    assert plain.json()["url"].startswith("http://testserver/invite/") and plain.json()["maxUses"] is None
    again = owner.post(f"{base}/invite-links", json={})
    assert again.json()["id"] == plain.json()["id"]                         # 这个家自己的那条：续期不另开
    own = owner.post(f"{base}/invite-links", json={"maxUses": 5, "expiresIn": "7d"}).json()
    assert own["id"] != plain.json()["id"] and own["maxUses"] == 5
    links = owner.get(f"{base}/invite-links").json()["links"]
    assert {row["id"] for row in links} == {plain.json()["id"], own["id"]}
    assert all(row["waiting"] == 0 and row["uses"] == 0 for row in links)
    changed = owner.patch(f"{base}/invite-links/{own['id']}", json={"maxUses": 10, "expiresIn": "never"})
    assert changed.json()["maxUses"] == 10 and changed.json()["neverExpires"] is True
    assert changed.json()["url"] == own["url"]                               # 同一个地址
    assert owner.post(f"{base}/invite-links/{own['id']}/renew", json={}).status_code == 200
    for bad in ({"maxUses": 3}, {"expiresIn": "2y"}, {"agentIds": ["x"]}, {"channelIds": ["c"]}, {"other": 1}):
        assert owner.post(f"{base}/invite-links", json=bad).status_code == 400, bad
    assert owner.delete(f"{base}/invite-links/{own['id']}").status_code == 204
    assert [row["id"] for row in owner.get(f"{base}/invite-links").json()["links"]] == [plain.json()["id"]]
    preview = _visitor(home).get(f"/api/app/invitations/{_token(own)}").json()
    assert preview["kind"] == "link" and preview["closed"] is True
    member = _join(home)
    for method, path in (("get", "/invite-links"), ("post", "/invite-links")):
        assert getattr(member, method)(base + path, **({"json": {}} if method == "post" else {})).status_code == 403
    assert TestClient(home["app"]).get(f"{base}/invite-links").status_code == 401


def test_a_visitor_asks_and_the_owner_approval_makes_an_invitation_to_pass_on(home):
    owner, base = home["owner"], home["base"]
    link = owner.post(f"{base}/invite-links", json={}).json()
    token = _token(link)
    preview = _visitor(home).get(f"/api/app/invitations/{token}").json()
    assert preview == {"orgName": preview["orgName"], "email": "", "role": "member", "kind": "link",
                       "invitedBy": None, "mode": "personal", "agents": []}
    assert _visitor(home).get("/api/app/auth-config").json()["passwordReset"] is False          # 默认不发信
    asked = _ask(home, token, note="朋友推荐", password=PASSWORD)
    assert asked.status_code == 200, asked.text
    assert asked.json() == {"status": "pending", "email": "li@example.com", "emailed": False}   # 没证实：不收密码、不给 key
    [row] = owner.get(f"{base}/join-requests").json()["requests"]
    assert (row["name"], row["email"], row["note"], row["verified"]) == ("小李", "li@example.com", "朋友推荐", False)
    assert row["link"]["id"] == link["id"]
    saved = json.loads((state_dir(home["root"]) / "members" / "registry.json").read_text()) \
        if (state_dir(home["root"]) / "members" / "registry.json").exists() else None
    if saved is not None:
        assert all(item.get("sealed") is None for item in saved.get("link_requests", {}).values())
    approved = owner.post(f"{base}/join-requests/{row['id']}/approve", json={})
    assert approved.status_code == 200, approved.text
    assert approved.json()["outcome"] == "invited" and approved.json()["url"].startswith("http://testserver/invite/")
    assert owner.get(f"{base}/join-requests").json()["requests"] == []
    invite = _visitor(home).get(f"/api/app/invitations/{approved.json()['url'].rsplit('/', 1)[1]}").json()
    assert invite["kind"] == "email" and invite["email"] == "li@example.com"            # 写着他的邮箱，转给本人
    assert owner.get(f"{base}/invite-links").json()["links"][0]["uses"] == 1


def test_link_limits_closing_and_denial(home):
    owner, base = home["owner"], home["base"]
    link = owner.post(f"{base}/invite-links", json={"maxUses": 1}).json()
    token = _token(link)
    assert _ask(home, token).status_code == 200
    assert _ask(home, token, email="wang@example.com").status_code == 200
    rows = owner.get(f"{base}/join-requests").json()["requests"]
    assert len(rows) == 2
    assert owner.post(f"{base}/join-requests/{rows[0]['id']}/approve", json={}).status_code == 200
    full = owner.post(f"{base}/join-requests/{rows[1]['id']}/approve", json={})
    assert full.status_code == 409 and full.json()["error"]["code"] == "join.link_full_approval"
    assert _visitor(home).get(f"/api/app/invitations/{token}").json().get("full") is True
    late = _ask(home, token, email="zhao@example.com")
    assert late.status_code == 409 and late.json()["error"]["code"] == "join.link_full"
    assert owner.post(f"{base}/join-requests/{rows[1]['id']}/deny", json={}).status_code == 204
    other = owner.post(f"{base}/invite-links", json={"expiresIn": "1d"}).json()
    assert _ask(home, _token(other), email="zhou@example.com").status_code == 200
    assert owner.delete(f"{base}/invite-links/{other['id']}").status_code == 204
    assert owner.get(f"{base}/join-requests").json()["requests"] == []                  # 关掉：它的申请一起关
    closed = _ask(home, _token(other), email="wu@example.com")
    assert closed.status_code == 409 and closed.json()["error"]["code"] == "join.closed"
    assert _visitor(home).post("/api/app/invitations/nope/requests", json={"name": "x", "email": "a@b.co"}).status_code == 404


def test_bad_requests_and_signed_in_people(home):
    owner, base = home["owner"], home["base"]
    token = _token(owner.post(f"{base}/invite-links", json={}).json())
    for body in ({"name": "", "email": "li@example.com"}, {"name": "小李", "email": "nope"},
                 {"name": "小李", "email": "li@example.com", "note": "x" * 201},
                 {"name": "小李", "email": "li@example.com", "extra": 1}):
        assert _visitor(home).post(f"/api/app/invitations/{token}/requests", json=body).status_code == 400, body
    hostile = TestClient(home["app"], headers={"Origin": "https://elsewhere.example"})
    assert hostile.post(f"/api/app/invitations/{token}/requests",
                        json={"name": "小李", "email": "li@example.com"}).status_code == 403
    member = _join(home)
    assert member.post(f"/api/app/invitations/{token}/requests", json={}).status_code == 409
    assert owner.get(f"{base}/join-requests").json()["requests"] == []


def test_codes_are_off_by_default_and_say_so(home, monkeypatch):
    owner, base = home["owner"], home["base"]
    token = _token(owner.post(f"{base}/invite-links", json={}).json())
    off = _visitor(home).post(f"/api/app/invitations/{token}/code", json={"email": "li@example.com"})
    assert off.status_code == 409 and off.json()["error"]["code"] == "join.codes_off"
    monkeypatch.setenv("VORTOONE_JOIN_EMAIL_CODES", "1")                           # 只开开关、没配发信：照样关着
    assert _visitor(home).post(f"/api/app/invitations/{token}/code", json={"email": "li@example.com"}).status_code == 409
    assert _visitor(home).get("/api/app/auth-config").json()["passwordReset"] is False


@pytest.fixture
def mail(monkeypatch):
    sent = []
    monkeypatch.setenv("VORTOONE_JOIN_EMAIL_CODES", "1")
    monkeypatch.setenv("VORTOONE_SMTP_URL", "smtp+starttls://user:secret@mail.example:587")
    monkeypatch.setenv("VORTOONE_MAIL_FROM", "home@example.com")
    monkeypatch.setattr(join_codes, "CODES", join_codes.JoinCodes(send=lambda to, subject, text: sent.append((to, text))))
    return sent


def _code(mail):
    return mail[-1][1].split("验证码是 ")[1][:6]


def test_with_codes_on_a_proven_visitor_with_a_password_gets_in_on_approval(home, mail):
    owner, base = home["owner"], home["base"]
    token = _token(owner.post(f"{base}/invite-links", json={}).json())
    visitor = _visitor(home)
    assert visitor.get("/api/app/auth-config").json()["passwordReset"] is True
    unproven = _ask(home, token, password=PASSWORD)
    assert unproven.status_code == 400 and unproven.json()["error"]["code"] == "join.code_required"
    sent = visitor.post(f"/api/app/invitations/{token}/code", json={"email": "li@example.com"})
    assert sent.status_code == 200 and sent.json() == {"email": "li@example.com", "resendIn": 60}
    wait = visitor.post(f"/api/app/invitations/{token}/code", json={"email": "li@example.com"})
    assert wait.status_code == 429 and wait.json()["error"]["retryIn"] > 0
    wrong = visitor.post(f"/api/app/invitations/{token}/code/check", json={"email": "li@example.com", "code": "000000"
                         if _code(mail) != "000000" else "111111"})
    assert wrong.status_code == 400
    assert visitor.post(f"/api/app/invitations/{token}/code/check",
                        json={"email": "li@example.com", "code": _code(mail)}).json() == {"verified": True}
    asked = _ask(home, token, password=PASSWORD)
    assert asked.status_code == 200 and asked.json()["key"]
    key = asked.json()["key"]
    assert visitor.post(f"/api/app/invitations/{token}/requests/claim", json={"key": key}).json() == {"status": "pending"}
    assert visitor.post(f"/api/app/invitations/{token}/requests/claim", json={"key": "x" * 32}).status_code == 404
    [row] = owner.get(f"{base}/join-requests").json()["requests"]
    assert row["verified"] is True
    approved = owner.post(f"{base}/join-requests/{row['id']}/approve", json={})
    assert approved.json() == {"outcome": "joined"}
    claim = visitor.post(f"/api/app/invitations/{token}/requests/claim", json={"key": key}).json()
    assert claim["status"] == "in" and claim["orgId"]
    signed = visitor.post("/api/auth/sign-in/one-time-token", json={"token": claim["signIn"]})
    assert signed.status_code == 200 and signed.json()["user"]["email"] == "li@example.com"
    assert visitor.get("/api/app/me").json()["user"]["email"] == "li@example.com"       # 用申请时设的密码进了门
    again = TestClient(home["app"], headers=ORIGIN)
    assert again.post("/api/auth/sign-in/email", json={"email": "li@example.com", "password": PASSWORD}).status_code == 200


def test_a_denied_visitor_hears_it_and_the_password_digest_is_dropped(home, mail):
    owner, base = home["owner"], home["base"]
    token = _token(owner.post(f"{base}/invite-links", json={}).json())
    visitor = _visitor(home)
    visitor.post(f"/api/app/invitations/{token}/code", json={"email": "li@example.com"})
    visitor.post(f"/api/app/invitations/{token}/code/check", json={"email": "li@example.com", "code": _code(mail)})
    key = _ask(home, token, password=PASSWORD).json()["key"]
    [row] = owner.get(f"{base}/join-requests").json()["requests"]
    assert owner.post(f"{base}/join-requests/{row['id']}/deny", json={}).status_code == 204
    assert visitor.post(f"/api/app/invitations/{token}/requests/claim", json={"key": key}).json() == {
        "status": "denied", "reason": None}
    raw = "".join(path.read_text() for path in (state_dir(home["root"]) / "members").glob("*.json"))
    assert '"sealed": {' not in raw and PASSWORD not in raw


def test_password_reset_by_mail_only_with_mail_and_never_reveals_accounts(home, monkeypatch):
    member = _join(home)
    visitor = _visitor(home)
    off = visitor.post("/api/auth/request-password-reset", json={"email": "ana@example.com"})
    assert off.status_code == 409 and off.json()["code"] == "MAIL_OFF"
    sent = []
    monkeypatch.setenv("VORTOONE_JOIN_EMAIL_CODES", "1")
    monkeypatch.setenv("VORTOONE_SMTP_URL", "smtp+starttls://user:secret@mail.example:587")
    monkeypatch.setenv("VORTOONE_MAIL_FROM", "home@example.com")
    monkeypatch.setattr(join_codes, "CODES", join_codes.JoinCodes(send=lambda to, subject, text: sent.append((to, text))))
    known = visitor.post("/api/auth/request-password-reset", json={"email": "ana@example.com",
                                                                  "redirectTo": "https://evil.example/x"})
    unknown = visitor.post("/api/auth/request-password-reset", json={"email": "nobody@example.com"})
    assert known.json() == unknown.json() == {"status": True}                     # 回答一样：看不出谁有账号
    [(to, text)] = sent
    assert to == "ana@example.com" and "http://testserver/reset-password?token=" in text and "evil" not in text
    token = text.split("token=")[1].split()[0]
    reset = visitor.post("/api/auth/reset-password", json={"token": token, "newPassword": "another long secret"})
    assert reset.status_code == 200
    assert member is not None


def test_the_mail_password_never_reaches_agent_subprocesses(monkeypatch):
    from src.sandbox.policy import child_env
    monkeypatch.setenv("VORTOONE_SMTP_URL", "smtp+starttls://user:secret@mail.example:587")
    assert "VORTOONE_SMTP_URL" not in child_env()
