"""个人库的 App API 只回元数据，凭据和测试状态都在临时目录。"""

from concurrent.futures import ThreadPoolExecutor
from importlib.util import find_spec
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.web import app_personal_vault as implementation
from src.workflow.home_identity import pairing_owner
from tests.unit.test_member_login import _join
from tests.unit.test_member_login import home  # noqa: F401 —— 成员登录的整套夹具


FIXTURE_VALUE = "synthetic-vault-value-a-DO-NOT-EXPOSE"
ROTATED_VALUE = "synthetic-vault-value-b-DO-NOT-EXPOSE"


@pytest.fixture
def configured(home, tmp_path_factory, monkeypatch):
    assert find_spec("cryptography") is not None, "Vault fixtures must run with the existing crypto extra installed"
    directory = tmp_path_factory.mktemp("vault") / implementation.DIRECTORY
    directory.mkdir(mode=0o700)
    key = directory / "master.key"
    key.write_bytes(bytes(range(32)))
    key.chmod(0o600)
    monkeypatch.setenv("VORTOONE_PERSONAL_VAULT_DIR", str(directory))
    pairing_owner(home["root"], legacy_confirmed=True)
    return directory


def _tls(home, browser=None):
    return TestClient(home["app"], base_url="https://testserver",
                      headers=(dict(home["owner"].headers) if browser is None else {"Origin": "https://testserver"}),
                      cookies=(None if browser is None else dict(browser.cookies)))


def _login(title="示例账户", **extra):
    return {"title": title, "kind": "login", "scope": "personal", "website": "https://example.test:1443",
            "username": "operator", "secret": FIXTURE_VALUE, **extra}


def _assert_masked(response, status):
    assert response.status_code == status, response.text
    assert FIXTURE_VALUE not in response.text and ROTATED_VALUE not in response.text
    assert '"secret":' not in response.text


def test_personal_vault_lifecycle_reads_metadata_only(home, configured, caplog):
    url, client = home["base"] + "/vault", _tls(home)
    before = client.get(url)
    assert before.json() == {"items": [], "connections": [], "sources": [], "canManageTeam": False}
    assert before.headers["cache-control"] == "no-store"

    saved = client.post(url + "/items", json=_login())
    _assert_masked(saved, 200)
    item = saved.json()
    assert item["scope"] == "personal" and item["hasSecret"] is True and item["keyHint"] is None
    assert item["access"] == {"everyone": False, "roleIds": [], "userIds": [],
                              "allAgents": False, "agentIds": []}
    assert item["ownerUserId"] == client.get("/api/app/me").json()["user"]["id"]
    item_url = url + "/items/" + item["id"]
    fetched = client.get(item_url)
    _assert_masked(fetched, 200)
    assert fetched.json() == {**item, "unreadable": False, "uses": []}

    sealed, = configured.glob("*.sealed")
    assert FIXTURE_VALUE.encode() not in sealed.read_bytes()
    assert sealed.stat().st_mode & 0o777 == 0o600
    edited = client.patch(item_url, json={"description": "改了说明", "secret": ROTATED_VALUE})
    _assert_masked(edited, 200)
    assert edited.json()["description"] == "改了说明"
    assert implementation.PersonalVault(home["root"], item["ownerUserId"])._read()[item["id"]]["secret"] == ROTATED_VALUE
    assert ROTATED_VALUE.encode() not in sealed.read_bytes()
    listing = client.get(url)
    _assert_masked(listing, 200)
    assert len(listing.json()["items"]) == 1

    assert client.delete(item_url).status_code == 204
    assert client.get(item_url).status_code == 404
    assert client.delete(item_url).status_code == 404
    assert client.get(url).json()["items"] == []
    assert FIXTURE_VALUE not in caplog.text and ROTATED_VALUE not in caplog.text


def test_personal_vault_isolates_members_and_other_homes(home, configured, monkeypatch, tmp_path):
    eva = _join(home, "eva@example.com", "Eva")
    wei = _join(home, "wei@example.com", "Wei")
    owner, eva_tls, wei_tls = _tls(home), _tls(home, eva), _tls(home, wei)
    url = home["base"] + "/vault"
    saved = eva_tls.post(url + "/items", json=_login())
    _assert_masked(saved, 200)
    item = saved.json()
    assert item["ownerUserId"] == eva_tls.get("/api/app/me").json()["user"]["id"]
    item_url = f"{url}/items/{item['id']}"
    for other in (owner, wei_tls):
        assert other.get(url).json()["items"] == []
        assert other.get(item_url).status_code == 404
        assert other.patch(item_url, json={"title": "不能改"}).status_code == 404
        assert other.delete(item_url).status_code == 404
    assert owner.delete(f"{home['base']}/members/{item['ownerUserId']}").status_code == 204
    assert eva_tls.get(item_url).status_code == 401
    new_eva = _tls(home, _join(home, "eva@example.com", "Eva"))
    assert new_eva.get(url).json()["items"] == []
    assert new_eva.get(item_url).status_code == 404

    old_owner_item = owner.post(url + "/items", json=_login("旧家主人的账户")).json()
    assert owner.get(url).json()["items"] == [old_owner_item]
    other_home = tmp_path / "another-home"
    other_home.mkdir()
    monkeypatch.chdir(other_home)
    pairing_owner(other_home, legacy_confirmed=True)
    org = owner.get("/api/app/me").json()["organizations"][0]["id"]
    another_url = "/api/app/organizations/" + org + "/vault"
    assert owner.get(url).status_code == 404
    assert owner.get(another_url).json()["items"] == []
    assert owner.get(f"{another_url}/items/{old_owner_item['id']}").status_code == 404


@pytest.mark.parametrize("body,code", [
    (_login(access={"allAgents": True}), "vault.sharing_unavailable"),
    (_login(access={"userIds": ["some-member"]}), "vault.sharing_unavailable"),
    (_login(scope="team"), "vault.personal_only"),
    (_login(website="https://" + FIXTURE_VALUE + "@example.test"), "vault.invalid"),
    (_login(website="https://example.test:broken"), "vault.invalid"),
    (_login(kind="contact"), "vault.invalid"),
    (_login(secret=""), "vault.invalid"),
])
def test_personal_vault_rejects_unapproved_or_malformed_inputs(home, configured, body, code):
    client, url = _tls(home), home["base"] + "/vault"
    response = client.post(url + "/items", json=body)
    _assert_masked(response, 403 if code != "vault.invalid" else 422)
    assert response.json()["error"]["code"] == code
    assert not list(configured.glob("*.sealed"))


def test_personal_vault_body_errors_do_not_echo_input(home, configured):
    client, url = _tls(home), home["base"] + "/vault/items"
    broken = client.post(url, content=b"{" + FIXTURE_VALUE.encode(), headers={"Content-Type": "application/json"})
    _assert_masked(broken, 422)
    oversized = client.post(url, json=_login(secret=FIXTURE_VALUE + "a" * implementation.MAX_BODY))
    _assert_masked(oversized, 413)
    assert not list(configured.glob("*.sealed"))


def test_vault_ciphertext_from_a_different_account_is_unreadable(home, configured):
    client = _tls(home)
    owner_id = client.get("/api/app/me").json()["user"]["id"]
    personal = implementation.PersonalVault(home["root"], owner_id)
    personal.create(_login())
    unrelated = implementation.PersonalVault(home["root"], "m-other", member_account="account-other")
    unrelated.path.write_bytes(personal.path.read_bytes())
    unrelated.path.chmod(0o600)

    with pytest.raises(implementation.VaultError) as refused:
        unrelated.overview()
    assert refused.value.code == "vault.unavailable"


def test_personal_vault_fails_closed_before_exposure(home, configured, monkeypatch):
    url, client = home["base"] + "/vault", _tls(home)
    assert TestClient(home["app"], base_url="https://testserver").get(url).status_code == 401
    assert home["owner"].get(url).json()["error"]["code"] == "vault.secure_transport_required"
    local = TestClient(home["app"], base_url="http://127.0.0.1", client=("127.0.0.1", 56743),
                       headers=dict(home["owner"].headers))
    assert local.get(url).status_code == 200
    assert local.get(url, headers={"X-Forwarded-For": "192.0.2.40"}).status_code == 403
    monkeypatch.delenv("VORTOONE_PERSONAL_VAULT_DIR")
    assert client.get(url).status_code == 503
    monkeypatch.setenv("VORTOONE_PERSONAL_VAULT_DIR", str(configured))
    with monkeypatch.context() as patch:
        def missing_crypto(_directory):
            raise ImportError("synthetic missing optional crypto")
        patch.setattr(implementation, "_cipher", missing_crypto)
        assert client.get(url).status_code == 503
    key = configured / "master.key"
    key.chmod(0o644)
    assert client.get(url).status_code == 503
    key.chmod(0o600)
    moved = key.with_name("external.key")
    key.rename(moved)
    key.symlink_to(moved)
    assert client.get(url).status_code == 503
    key.unlink()
    key.hardlink_to(moved)
    assert client.get(url).status_code == 503
    key.unlink()
    moved.rename(key)
    configured.chmod(0o755)
    assert client.get(url).status_code == 503
    configured.chmod(0o700)
    item = client.post(url + "/items", json=_login()).json()
    sealed, = configured.glob("*.sealed")
    ciphertext = sealed.read_bytes()
    sealed.write_bytes(ciphertext[:-1] + bytes([ciphertext[-1] ^ 1]))
    assert client.get(url).status_code == 503
    assert client.patch(url + "/items/" + item["id"], json={"title": "不覆盖坏库"}).status_code == 503
    assert sealed.read_bytes() != ciphertext


def test_personal_vault_rejects_storage_under_home(home, configured, monkeypatch):
    inside = Path(home["root"]) / implementation.DIRECTORY
    inside.mkdir(mode=0o700)
    monkeypatch.setenv("VORTOONE_PERSONAL_VAULT_DIR", str(inside))
    assert _tls(home).get(home["base"] + "/vault").status_code == 503
    assert not list(inside.iterdir())


def test_personal_vault_stays_sealed_after_home_restore(home, configured, tmp_path):
    from src.workflow.home_backup import backup, restore

    url, owner = home["base"] + "/vault", _tls(home)
    member = _tls(home, _join(home))
    package = backup(home["root"], str(tmp_path / "backups"))["path"]
    saved = member.post(url + "/items", json=_login()).json()
    item_url = url + "/items/" + saved["id"]
    assert member.get(item_url).status_code == 200
    sealed, = configured.glob("*.sealed")
    ciphertext = sealed.read_bytes()

    restore(home["root"], package)
    assert owner.get(url).status_code == 200
    assert member.get(item_url).status_code == 404
    assert member.get(url).json()["items"] == []
    assert member.post(url + "/items", json=_login("新的个人库")).status_code == 200
    assert sealed.read_bytes() == ciphertext
    assert len(list(configured.glob("*.sealed"))) == 2


def test_personal_vault_concurrent_writes_are_serialized(home, configured):
    client, url = _tls(home), home["base"] + "/vault"
    requests = [{"title": f"并发-{index}", "kind": "key", "scope": "personal",
                 "keyName": f"FIXTURE_{index}", "secret": FIXTURE_VALUE} for index in range(10)]
    with ThreadPoolExecutor(max_workers=4) as workers:
        responses = list(workers.map(lambda data: client.post(url + "/items", json=data), requests))
    for response in responses:
        _assert_masked(response, 200)
    assert {item["keyName"] for item in client.get(url).json()["items"]} == {f"FIXTURE_{n}" for n in range(10)}
