"""成员注销自己的账号（主人 2026-11-24 定：做；整个家不删）。

发信开着用邮件验证码确认，没开用自己的密码。注销后所有登录立刻结束；7 天内再登录只看得到「注销中」，
能恢复；到期后真正移出这个家，账号删掉。所有者是这个家唯一的所有者，不能注销。
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.web import app_members
from src.workflow.members import accounts as accounts_module
from src.workflow.members import join_codes
from tests.unit.test_member_agents import ORIGIN, PASSWORD, _join, home  # noqa: F401 - pytest fixture


def _sign_in(home, email="ana@example.com", password=PASSWORD):
    browser = TestClient(home["app"], headers=ORIGIN)
    answer = browser.post("/api/auth/sign-in/email", json={"email": email, "password": password})
    return browser, answer.status_code


def _accounts_file(home):
    return next(Path(home["root"]).rglob("accounts.json"))


def _member_ids(home):
    return [row["userId"] for row in home["owner"].get(f"{home['base']}/members").json()["members"]]


def test_a_member_deletes_with_their_password_and_can_restore_within_seven_days(home):
    member = _join(home)
    plan = member.get("/api/app/account/deletion").json()
    assert plan["leaves"] == [{"id": home["base"].rsplit("/", 1)[1], "name": plan["leaves"][0]["name"], "role": "member"}]
    assert (plan["closes"], plan["soleOwner"], plan["serverOwner"]) == ([], [], False)

    assert member.request("DELETE", "/api/app/account", json={}).json()["error"]["code"] == "reauth.password"
    wrong = member.request("DELETE", "/api/app/account", json={"password": "not it at all"})
    assert (wrong.status_code, wrong.json()["error"]["code"]) == (403, "reauth.password")
    assert member.get("/api/app/me").status_code == 200                      # 输错不动账号
    phone, _ = _sign_in(home)                                                # 另一台设备上也登着
    assert phone.get("/api/app/me").status_code == 200
    assert member.request("DELETE", "/api/app/account", json={"password": PASSWORD}).status_code == 204
    assert member.get("/api/app/me").status_code == 401                      # 所有登录都结束了
    assert phone.get("/api/app/me").status_code == 401

    again, status = _sign_in(home)
    assert status == 200                                                     # 7 天内还能登录，只为了恢复
    me = again.get("/api/app/me").json()
    due = datetime.fromisoformat(me["user"]["deletion"]["dueAt"])
    assert timedelta(days=6, hours=23) < due - datetime.now(timezone.utc) <= timedelta(days=7)
    assert me["organizations"] == []
    assert again.get(f"{home['base']}/agents").status_code == 403            # 注销中碰不到这个家
    assert again.get("/api/app/account/deletion").status_code == 200
    assert member.member_id in _member_ids(home)                             # 还没到期：还在名单上

    assert again.request("DELETE", "/api/app/account/deletion").status_code == 204
    me = again.get("/api/app/me").json()
    assert "deletion" not in me["user"] and me["organizations"][0]["id"] == home["base"].rsplit("/", 1)[1]
    assert again.get(f"{home['base']}/agents").status_code == 200


def test_when_the_date_passes_the_member_leaves_and_the_account_goes(home):
    member = _join(home)
    assert member.request("DELETE", "/api/app/account", json={"password": PASSWORD}).status_code == 204
    path = _accounts_file(home)
    state = json.loads(path.read_text())
    for account in state["accounts"].values():
        if "deletion" in account:
            account["deletion"]["due"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    path.write_text(json.dumps(state))
    assert _sign_in(home)[1] == 401                                          # 到期了：登不上
    assert app_members.finish_due_deletions(home["root"]) == 1
    assert member.member_id not in _member_ids(home)
    assert json.loads(path.read_text())["accounts"] == {}
    assert app_members.finish_due_deletions(home["root"]) == 0


def test_restore_after_the_date_is_refused(home):
    _join(home)
    accounts = accounts_module.MemberAccounts(home["root"])
    account_id = next(iter(json.loads(_accounts_file(home).read_text())["accounts"]))
    token = accounts.open_session(account_id)
    accounts.request_deletion(account_id, token)
    later = datetime.now(timezone.utc) + timedelta(days=7, minutes=1)
    with pytest.raises(accounts_module.NotRestorable):
        accounts.restore(account_id, token, now=later)


def test_with_mail_on_it_takes_the_emailed_code(home, monkeypatch):
    sent = []
    monkeypatch.setenv("VORTOONE_JOIN_EMAIL_CODES", "1")
    monkeypatch.setenv("VORTOONE_SMTP_URL", "smtp+starttls://user:secret@mail.example:587")
    monkeypatch.setenv("VORTOONE_MAIL_FROM", "home@example.com")
    monkeypatch.setattr(join_codes, "CODES", join_codes.JoinCodes(send=lambda to, subject, text: sent.append((to, text))))
    member = _join(home)
    assert member.request("DELETE", "/api/app/account", json={"password": PASSWORD}).json()["error"]["code"] == "reauth.code"
    out = member.post("/api/app/account/deletion/code", json={})
    assert out.status_code == 200 and out.json() == {"email": "ana@example.com", "resendIn": 60}
    again = member.post("/api/app/account/deletion/code", json={})
    assert (again.status_code, again.json()["error"]["code"]) == (429, "reauth.code_wait")
    assert again.json()["error"]["retryIn"] > 0
    code = re.search(r"\b(\d{6})\b", sent[-1][1]).group(1)
    wrong = member.request("DELETE", "/api/app/account", json={"code": "000000" if code != "000000" else "111111"})
    assert (wrong.status_code, wrong.json()["error"]["code"]) == (400, "reauth.code_wrong")
    assert member.request("DELETE", "/api/app/account", json={"code": code}).status_code == 204
    assert member.get("/api/app/me").status_code == 401


def test_the_owner_cannot_delete_their_account_here(home):
    owner = home["owner"]
    plan = owner.get("/api/app/account/deletion", headers=ORIGIN).json()
    assert plan["serverOwner"] is True and [row["id"] for row in plan["soleOwner"]] == [home["base"].rsplit("/", 1)[1]]
    for method, path in (("DELETE", "/api/app/account"), ("POST", "/api/app/account/deletion/code")):
        refused = owner.request(method, path, json={"password": "x"}, headers=ORIGIN)
        assert (refused.status_code, refused.json()["error"]["code"]) == (409, "sole_owner")
    assert owner.get("/api/app/me").status_code == 200
    assert TestClient(home["app"]).request("DELETE", "/api/app/account", json={}).status_code == 401


def test_deleting_the_whole_home_is_refused_honestly(home):
    owner, base = home["owner"], home["base"]
    for path in (f"{base}/deletion/code", f"{base}/deletion"):
        refused = owner.post(path, json={"password": "x"}, headers=ORIGIN)
        assert (refused.status_code, refused.json()["error"]["code"]) == (409, "org.delete_unsupported")
    assert owner.request("DELETE", f"{base}/deletion", headers=ORIGIN).status_code == 204
    assert "deletion" not in owner.get("/api/app/me").json()["organizations"][0]
    member = _join(home)
    assert member.post(f"{base}/deletion", json={}).status_code == 403
