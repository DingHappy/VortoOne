"""改登录邮箱（2026-11-24）。登录邮箱就是登录名，改错了等于把账号交出去，所以：
没发信时要 10 分钟内凭密码登录过；发信开着时先过现在邮箱的验证码（或刚登录过），再点新邮箱里的确认链接；
换好以后旧邮箱能「不是我改的」换回来，所有登录都结束。"""
import re

import pytest
from fastapi.testclient import TestClient

from src.workflow.members import join_codes
from tests.unit.test_member_agents import ORIGIN, PASSWORD, _join, home  # noqa: F401 - pytest fixture

ACCOUNT_EMAIL = "/api/app/account/email"


def _sign_in(home, email):
    browser = TestClient(home["app"], headers=ORIGIN)
    answer = browser.post("/api/auth/sign-in/email", json={"email": email, "password": PASSWORD})
    return browser, answer.status_code


def _member_emails(home):
    return {row["email"] for row in home["owner"].get(f"{home['base']}/members").json()["members"]}


@pytest.fixture
def mail(monkeypatch):
    sent = []
    monkeypatch.setenv("VORTOONE_JOIN_EMAIL_CODES", "1")
    monkeypatch.setenv("VORTOONE_SMTP_URL", "smtp+starttls://user:secret@mail.example:587")
    monkeypatch.setenv("VORTOONE_MAIL_FROM", "home@example.com")
    monkeypatch.setattr(join_codes, "CODES", join_codes.JoinCodes(send=lambda to, subject, text: sent.append((to, text))))
    return sent


def test_without_mail_it_changes_at_once_after_a_fresh_sign_in(home):
    member = _join(home)
    stale = member.post(ACCOUNT_EMAIL, json={"newEmail": "new@example.com"})
    assert (stale.status_code, stale.json()["error"]["code"]) == (403, "reauth.sign_in")       # 注册时开的会话不算
    fresh, _ = _sign_in(home, "ana@example.com")
    changed = fresh.post(ACCOUNT_EMAIL, json={"newEmail": "New@Example.com"})
    assert changed.status_code == 200 and changed.json() == {"status": "changed", "email": "new@example.com"}
    assert fresh.get("/api/app/me").json()["user"]["email"] == "new@example.com"
    assert _sign_in(home, "ana@example.com")[1] == 401 and _sign_in(home, "new@example.com")[1] == 200
    assert "new@example.com" in _member_emails(home) and "ana@example.com" not in _member_emails(home)


def test_same_held_invalid_and_the_owner(home):
    _join(home, "li@example.com", "小李")
    _join(home)
    fresh, _ = _sign_in(home, "ana@example.com")
    assert fresh.post(ACCOUNT_EMAIL, json={"newEmail": "ana@example.com"}).json()["error"]["code"] == "email.same"
    assert fresh.post(ACCOUNT_EMAIL, json={"newEmail": "li@example.com"}).json()["error"]["code"] == "email.held"
    assert fresh.post(ACCOUNT_EMAIL, json={"newEmail": "not an email"}).status_code == 400
    owner = home["owner"].post(ACCOUNT_EMAIL, json={"newEmail": "boss@example.com"}, headers=ORIGIN)
    assert (owner.status_code, owner.json()["error"]["code"]) == (409, "account.owner_email")


def test_with_mail_a_code_then_a_link_then_the_old_address_can_undo(home, mail):
    member = _join(home)
    asked = member.post(ACCOUNT_EMAIL, json={"newEmail": "new@example.com"})
    assert asked.json()["status"] == "code" and asked.json()["email"] == "ana@example.com"
    to, text = mail[-1]
    code = re.search(r"\b(\d{6})\b", text).group(1)
    assert to == "ana@example.com"
    wrong = member.post(ACCOUNT_EMAIL, json={"newEmail": "new@example.com", "code": "000000" if code != "000000" else "1"})
    assert wrong.json()["error"]["code"].startswith("reauth.code")
    sent = member.post(ACCOUNT_EMAIL, json={"newEmail": "new@example.com", "code": code})
    assert sent.json() == {"status": "sent", "email": "new@example.com"}
    assert member.get("/api/app/me").json()["user"]["email"] == "ana@example.com"         # 没点链接之前不换
    to, text = mail[-1]
    assert to == "new@example.com"
    link = re.search(r"(/api/app/account/email/confirm\?token=\S+)", text).group(1)
    anyone = TestClient(home["app"])
    opened = anyone.get(link, follow_redirects=False)
    assert opened.status_code == 303 and opened.headers["location"] == "/?email=changed"
    assert _sign_in(home, "new@example.com")[1] == 200 and "new@example.com" in _member_emails(home)
    assert anyone.get(link, follow_redirects=False).headers["location"] == "/?email=failed"     # 用过的链接
    to, text = mail[-1]
    assert to == "ana@example.com" and "不是你改的话" in text
    undo = re.search(r"/email-undo\?token=(\S+)", text).group(1)
    phone, _ = _sign_in(home, "new@example.com")
    back = TestClient(home["app"], headers=ORIGIN).post("/api/app/email-undo", json={"token": undo})
    assert back.status_code == 200 and back.json() == {"email": "ana@example.com"}
    assert phone.get("/api/app/me").status_code == 401                                       # 所有登录都结束
    assert _sign_in(home, "ana@example.com")[1] == 200 and _sign_in(home, "new@example.com")[1] == 401
    assert "ana@example.com" in _member_emails(home)
    gone = TestClient(home["app"], headers=ORIGIN).post("/api/app/email-undo", json={"token": undo})
    assert gone.status_code == 404


def test_with_mail_a_fresh_sign_in_skips_the_code(home, mail):
    _join(home)
    fresh, _ = _sign_in(home, "ana@example.com")
    sent = fresh.post(ACCOUNT_EMAIL, json={"newEmail": "new@example.com"})
    assert sent.json()["status"] == "sent" and mail[-1][0] == "new@example.com"
    cross = TestClient(home["app"]).post("/api/app/email-undo", json={"token": "x"},
                                         headers={"Origin": "https://evil.example"})
    assert cross.status_code == 403


def test_the_account_store_itself_refuses_an_address_another_account_holds(home):
    # 路由先查过；这里再查一次，挡住两次请求之间被人抢先占用
    from src.workflow.members.accounts import EmailHeld, MemberAccounts
    _join(home, "li@example.com", "小李")
    _join(home)
    accounts = MemberAccounts(home["root"])
    ana = accounts.account_with_email("ana@example.com")
    with pytest.raises(EmailHeld):
        accounts.change_email(ana, "li@example.com")
    with pytest.raises(EmailHeld):
        accounts.email_link(ana, "li@example.com")
