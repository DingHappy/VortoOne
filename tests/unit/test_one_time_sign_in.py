"""一次性登录：已登录的人给自己的另一台设备开；只换得到开它的那个人，用一次、过期即作废。"""
import pytest
from fastapi.testclient import TestClient

from src.workflow.members import accounts as accounts_module
from src.workflow.members import one_time
from src.workflow.members.one_time import OWNER, OneTimeSignIns, TooMany

TOKEN = "owner-token-for-test"
ORIGIN = {"Origin": "http://testserver"}
PASSWORD = "correct horse battery"


# ── 存储本身 ──────────────────────────────────────────────────────────────────

def test_a_sign_in_works_once_and_not_after_it_expires():
    now = [100.0]
    store = OneTimeSignIns(clock=lambda: now[0])
    token, seconds = store.mint(OWNER)
    assert seconds == one_time.SECONDS
    assert store.redeem(token) == OWNER
    assert store.redeem(token) is None                         # 用过了
    late, _ = store.mint("account:a1")
    now[0] += one_time.SECONDS
    assert store.redeem(late) is None                          # 过期了
    assert store.redeem("x" * 10) is None and store.redeem(None) is None


def test_each_person_has_a_limit_and_can_be_forgotten():
    store = OneTimeSignIns(clock=lambda: 0.0)
    mine = [store.mint("account:a1")[0] for _ in range(one_time.EACH_MAX)]
    with pytest.raises(TooMany):
        store.mint("account:a1")
    other, _ = store.mint("account:a2")                        # 别人不受影响
    store.forget("account:a1")
    assert all(store.redeem(token) is None for token in mine)
    assert store.redeem(other) == "account:a2"


# ── 接口 ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("VORTOCODE_API_TOKEN", "AUTODEV_API_TOKEN", "OPENAI_API_KEY", "VORTOONE_PUBLIC_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VORTOONE_API_TOKEN", TOKEN)
    monkeypatch.setattr(accounts_module, "_SCRYPT", {"n": 2 ** 4, "r": 8, "p": 1})
    monkeypatch.setattr(accounts_module, "_DECOY", accounts_module._sealed("decoy-password"))
    monkeypatch.setattr(one_time, "PENDING", OneTimeSignIns())
    accounts_module._FAILURES.clear()
    accounts_module._RECENT.update(since=0.0, count=0)
    from src.web.server import app
    owner = TestClient(app, headers=ORIGIN)
    assert owner.post("/api/auth/login", json={"token": TOKEN}).status_code == 200      # 页面里的所有者：Cookie
    org = owner.get("/api/app/me").json()["organizations"][0]["id"]
    yield {"app": app, "owner": owner, "base": f"/api/app/organizations/{org}"}
    accounts_module._FAILURES.clear()


def _member(home, email="ana@example.com"):
    invited = home["owner"].post(f"{home['base']}/invitations", json={"email": email})
    token = invited.json()["url"].rsplit("/", 1)[1]
    browser = TestClient(home["app"], headers=ORIGIN)
    assert browser.post("/api/auth/sign-up/email", json={"name": "小王", "email": email, "password": PASSWORD},
                        headers={"x-invitation-token": token}).status_code == 200
    assert browser.post(f"/api/app/invitations/{token}/accept", json={}).status_code == 200
    return browser


def _phone(home):
    return TestClient(home["app"], headers=ORIGIN)


def test_the_owner_signs_in_on_another_device(home, monkeypatch):
    monkeypatch.setenv("VORTOONE_PUBLIC_URL", "https://box.tail1234.ts.net/")
    minted = home["owner"].post("/api/app/phone-sign-in", json={}).json()
    assert minted["server"] == "https://box.tail1234.ts.net" and minted["expiresIn"] == one_time.SECONDS
    phone = _phone(home)
    assert phone.get("/api/app/me").status_code == 401
    signed = phone.post("/api/auth/sign-in/one-time-token", json={"token": minted["token"]})
    assert signed.status_code == 200, signed.text
    assert [org["role"] for org in phone.get("/api/app/me").json()["organizations"]] == ["owner"]
    again = _phone(home).post("/api/auth/sign-in/one-time-token", json={"token": minted["token"]})
    assert again.status_code == 401 and again.json()["code"] == "INVALID_TOKEN"


def test_a_members_link_signs_in_only_that_member(home):
    browser = _member(home)
    minted = browser.post("/api/app/phone-sign-in", json={})
    assert minted.status_code == 200, minted.text
    phone = _phone(home)
    assert phone.post("/api/auth/sign-in/one-time-token", json={"token": minted.json()["token"]}).status_code == 200
    me = phone.get("/api/app/me").json()
    assert me["user"]["email"] == "ana@example.com"
    assert [org["role"] for org in me["organizations"]] == ["member"]
    assert phone.get("/api/sessions").status_code == 403              # 不在成员白名单里的接口照样进不去


def test_a_removed_member_cannot_use_a_link_made_before(home):
    browser = _member(home)
    token = browser.post("/api/app/phone-sign-in", json={}).json()["token"]
    rows = home["owner"].get(f"{home['base']}/members").json()["members"]
    member = next(row["userId"] for row in rows if row["role"] == "member")
    assert home["owner"].delete(f"{home['base']}/members/{member}").status_code in (200, 204)
    phone = _phone(home)
    assert phone.post("/api/auth/sign-in/one-time-token", json={"token": token}).status_code == 401
    assert phone.get("/api/app/me").status_code == 401


def test_other_sites_and_signed_out_callers_get_nothing(home):
    stranger = TestClient(home["app"], headers={"Origin": "https://evil.example"})
    stranger.cookies.update(home["owner"].cookies)
    assert stranger.post("/api/app/phone-sign-in", json={}).status_code == 403     # 借登录状态的别的网站
    assert _phone(home).post("/api/app/phone-sign-in", json={}).status_code == 401
    token = home["owner"].post("/api/app/phone-sign-in", json={}).json()["token"]
    elsewhere = TestClient(home["app"], headers={"Origin": "https://evil.example"})
    assert elsewhere.post("/api/auth/sign-in/one-time-token", json={"token": token}).status_code == 401


def test_through_the_tailnet_address_the_same_rules_hold(home, monkeypatch):
    """经 tailscale serve 进来的请求：Host 是 tailnet 里的名字，页面来源一致才算同源，登录 Cookie 带 Secure。"""
    monkeypatch.setenv("VORTOONE_PUBLIC_URL", "https://box.tail1.ts.net")
    token = home["owner"].post("/api/app/phone-sign-in", json={}).json()["token"]
    phone = TestClient(home["app"], base_url="https://box.tail1.ts.net", headers={"Origin": "https://box.tail1.ts.net"})
    assert phone.get("/api/instance").json()["url"] == "https://box.tail1.ts.net"
    signed = phone.post("/api/auth/sign-in/one-time-token", json={"token": token})
    assert signed.status_code == 200 and "secure" in signed.headers["set-cookie"].lower()
    assert [org["role"] for org in phone.get("/api/app/me").json()["organizations"]] == ["owner"]
