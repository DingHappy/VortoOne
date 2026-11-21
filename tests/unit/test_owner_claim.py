"""所有者认领：装好时的一次性链接，第一个凭它填名字、邮箱、密码的人成为所有者；之后用邮箱密码登录。"""
import json

import pytest
from fastapi.testclient import TestClient

from src.workflow.members import accounts as accounts_module
from src.workflow.members.owner_account import OwnerAccount, Taken, WrongClaim

TOKEN = "owner-token-for-test"
ORIGIN = {"Origin": "http://testserver"}
PASSWORD = "correct horse battery"
OWNER = {"name": "丁", "email": "Ding@Example.com", "password": PASSWORD}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("VORTOCODE_API_TOKEN", "AUTODEV_API_TOKEN", "OPENAI_API_KEY", "VORTOONE_PUBLIC_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VORTOONE_API_TOKEN", TOKEN)
    monkeypatch.setattr(accounts_module, "_SCRYPT", {"n": 2 ** 4, "r": 8, "p": 1})
    monkeypatch.setattr(accounts_module, "_DECOY", accounts_module._sealed("decoy-password"))
    accounts_module._FAILURES.clear()
    accounts_module._RECENT.update(since=0.0, count=0)
    from src.web.server import app
    yield {"root": str(tmp_path), "app": app}
    accounts_module._FAILURES.clear()


def _browser(home):
    return TestClient(home["app"], headers=ORIGIN)


def _claim(home, token, **change):
    return _browser(home).post("/api/auth/owner-claim", json={"token": token, **OWNER, **change})


def test_a_fresh_home_is_claimed_once_and_then_signs_in_by_email(home):
    token, _expires = OwnerAccount(home["root"]).claim_link()
    browser = _browser(home)
    assert browser.get("/api/instance").json()["ownerClaim"] is True
    assert browser.get("/api/app/auth-config").json()["ownerClaim"] is True
    claimed = browser.post("/api/auth/owner-claim", json={"token": token, **OWNER})
    assert claimed.status_code == 200, claimed.text
    me = browser.get("/api/app/me").json()
    assert me["user"]["name"] == "丁" and me["user"]["email"] == "ding@example.com"
    assert [org["role"] for org in me["organizations"]] == ["owner"]
    assert browser.get("/api/instance").json()["ownerClaim"] is False
    assert _claim(home, token).status_code == 409                                # 已经有所有者了

    phone = _browser(home)
    signed = phone.post("/api/auth/sign-in/email", json={"email": "ding@example.com", "password": PASSWORD})
    assert signed.status_code == 200, signed.text
    assert [org["role"] for org in phone.get("/api/app/me").json()["organizations"]] == ["owner"]
    wrong = _browser(home).post("/api/auth/sign-in/email", json={"email": "ding@example.com", "password": "nope-nope"})
    assert wrong.status_code == 401
    assert _browser(home).post("/api/auth/sign-in/email", json={"email": "x", "password": TOKEN}).status_code == 200


def test_a_wrong_or_expired_link_claims_nothing(home):
    from datetime import datetime, timedelta, timezone
    store = OwnerAccount(home["root"])
    token, _ = store.claim_link()
    assert _claim(home, "x" * 43).status_code == 401
    assert _claim(home, token, password="short").status_code == 422
    assert _claim(home, token, password="丁").status_code == 422
    with pytest.raises(WrongClaim):
        store.claim(token, "丁", "ding@example.com", PASSWORD, now=datetime.now(timezone.utc) + timedelta(hours=25))
    newer, _ = store.claim_link()                                                # 新开的顶掉旧的
    assert _claim(home, token).status_code == 401
    assert _claim(home, newer).status_code == 200
    with pytest.raises(Taken):
        store.claim_link()


def test_while_a_claim_waits_members_cannot_sign_in_or_up(home):
    from src.workflow.members.accounts import MemberAccounts
    MemberAccounts(home["root"]).create("ana@example.com", "小王", PASSWORD)
    OwnerAccount(home["root"]).claim_link()
    refused = _browser(home).post("/api/auth/sign-in/email", json={"email": "ana@example.com", "password": PASSWORD})
    assert refused.status_code == 401
    assert _browser(home).post("/api/auth/sign-up/email", json={"name": "x", "email": "b@example.com",
                                                                 "password": PASSWORD}).status_code == 403


def test_the_owner_and_members_do_not_share_an_email(home):
    from src.workflow.members.accounts import MemberAccounts
    MemberAccounts(home["root"]).create("ana@example.com", "小王", PASSWORD)
    token, _ = OwnerAccount(home["root"]).claim_link()
    assert _claim(home, token, email="ANA@example.com").status_code == 422
    assert _claim(home, token).status_code == 200
    owner = TestClient(home["app"], headers=ORIGIN)
    owner.post("/api/auth/login", json={"token": TOKEN})
    base = f"/api/app/organizations/{owner.get('/api/app/me').json()['organizations'][0]['id']}"
    invite = owner.post(f"{base}/invitations", json={"email": "ding@example.com"}).json()["url"].rsplit("/", 1)[1]
    taken = _browser(home).post("/api/auth/sign-up/email", headers={"x-invitation-token": invite},
                                json={"name": "冒名", "email": "ding@example.com", "password": PASSWORD})
    assert taken.status_code == 422


def test_an_old_home_without_an_owner_account_is_unchanged(home):
    browser = _browser(home)
    assert browser.get("/api/instance").json()["ownerClaim"] is False
    assert browser.post("/api/auth/sign-in/email", json={"email": "", "password": TOKEN}).status_code == 200
    assert browser.get("/api/app/me").json()["user"]["name"] == "我"


def test_the_command_prints_one_link(home, monkeypatch, capsys):
    from src.runtime_entry import main
    monkeypatch.setenv("VORTOONE_PUBLIC_URL", "https://box.tail1.ts.net")
    assert main(["owner-claim"]) == 0
    answer = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert answer["link"].startswith("https://box.tail1.ts.net/owner#claim=")
    assert main(["owner-claim", "--url", "http://evil.example"]) == 2
    token = answer["link"].split("#claim=", 1)[1]
    assert _claim(home, token).status_code == 200
    assert main(["owner-claim"]) == 2                                            # 已经有所有者账号
    capsys.readouterr()
    assert main(["owner-claim", "--reset"]) == 0                                 # 忘了密码：在这台电脑上重新认领
    again = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["link"].split("#claim=", 1)[1]
    assert _browser(home).post("/api/auth/sign-in/email",
                               json={"email": "ding@example.com", "password": PASSWORD}).status_code == 401
    assert _claim(home, again, password="another good one").status_code == 200
