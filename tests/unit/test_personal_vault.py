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


def _csv(text, *, dry_run=False, scope="personal"):
    return {"scope": scope, "csv": text, "dryRun": dry_run}


def _key_import(name, secret=FIXTURE_VALUE, *, replace=False):
    return {"keyName": name, "secret": secret, "replace": replace}


def test_csv_preview_matches_import_and_keeps_saved_login_metadata(home, configured, caplog):
    url, client = home["base"] + "/vault", _tls(home)
    saved = client.post(url + "/items", json=_login(description="之前的说明")).json()
    sealed, = configured.glob("*.sealed")
    original = sealed.read_bytes()
    csv = (
        "\ufeffname,url,username,password,note\r\n"
        f'"导入, 标题",https://EXAMPLE.test:1443/sign-in,operator,{ROTATED_VALUE},plaintext notes omitted\r\n'
        f"再次导入,https://example.test:1443/again,operator,{FIXTURE_VALUE},\r\n"
        f'新账户,https://new.example.test/login?target=feed,reporter,"{FIXTURE_VALUE},quote ""value""",\r\n'
        f"非加密,http://plain.example.test,operator,{FIXTURE_VALUE},\r\n"
        f"无密码,https://other.example.test,publisher,,\r\n"
        f"列数不对,https://other.example.test,{FIXTURE_VALUE}\r\n"
    )
    preview = client.post(url + "/import", json=_csv(csv, dry_run=True))
    _assert_masked(preview, 200)
    assert preview.headers["cache-control"] == "no-store"
    expected = {"added": 1, "updated": 1, "format": "chrome", "skipped": [
        {"reason": reason, "count": 1} for reason in ("duplicate", "invalid", "no_password", "not_https")
    ]}
    assert preview.json() == expected
    assert sealed.read_bytes() == original
    assert client.get(url).json()["items"] == [saved]

    imported = client.post(url + "/import", json=_csv(csv))
    _assert_masked(imported, 200)
    assert imported.json() == expected
    listing = client.get(url).json()["items"]
    saved_again, added = listing
    assert saved_again["id"] == saved["id"]
    assert saved_again["title"] == saved["title"]
    assert saved_again["description"] == saved["description"]
    assert added["website"] == "https://new.example.test"
    private = implementation.PersonalVault(home["root"], saved["ownerUserId"])._read()
    assert private[saved["id"]]["secret"] == ROTATED_VALUE
    assert private[added["id"]]["secret"] == f'{FIXTURE_VALUE},quote "value"'
    assert FIXTURE_VALUE.encode() not in sealed.read_bytes()
    assert ROTATED_VALUE.encode() not in sealed.read_bytes()
    retry = client.post(url + "/import", json=_csv(csv))
    assert retry.json() == {**expected, "added": 0, "updated": 0, "skipped": [
        {"reason": "duplicate", "count": 3}, *expected["skipped"][1:],
    ]}
    assert client.get(url).json()["items"] == listing
    assert FIXTURE_VALUE not in caplog.text and ROTATED_VALUE not in caplog.text


@pytest.mark.parametrize("header,row,format_name", [
    ("Title,Website,Username,Password,Notes", "Test,https://one.example.test,member,{value},extra", "1password"),
    ("folder,favorite,type,name,notes,fields,reprompt,login_uri,login_username,login_password,login_totp",
     ",,login,Test,,,,https://one.example.test,member,{value},", "bitwarden"),
    ("url,username,password", "https://one.example.test,member,{value}", "generic"),
])
def test_csv_recognizes_supported_capitalization_and_export_formats(home, configured, header, row, format_name):
    url, client = home["base"] + "/vault", _tls(home)
    uploaded = client.post(url + "/import", json=_csv(header + "\n" + row.format(value=FIXTURE_VALUE)))
    _assert_masked(uploaded, 200)
    assert uploaded.json() == {"added": 1, "updated": 0, "skipped": [], "format": format_name}
    item, = client.get(url).json()["items"]
    assert item["scope"] == "personal" and item["kind"] == "login" and item["description"] == ""


def test_csv_matches_explicit_https_default_port_without_merging_other_ports(home, configured):
    url, client = home["base"] + "/vault", _tls(home)
    saved = client.post(url + "/items", json=_login(website="https://example.test:443")).json()
    data = (f"url,username,password\nhttps://example.test/login,operator,{ROTATED_VALUE}\n"
            f"https://example.test:1443/login,operator,{FIXTURE_VALUE}")
    imported = client.post(url + "/import", json=_csv(data))
    _assert_masked(imported, 200)
    assert imported.json() == {"added": 1, "updated": 1, "skipped": [], "format": "generic"}
    private = implementation.PersonalVault(home["root"], saved["ownerUserId"])._read()
    assert private[saved["id"]]["secret"] == ROTATED_VALUE
    assert {entry["website"] for entry in private.values()} == {"https://example.test:443", "https://example.test:1443"}


def test_key_import_skips_or_replaces_explicitly_and_returns_only_counts(home, configured, caplog):
    url, client = home["base"] + "/vault", _tls(home)
    existing = client.post(url + "/items", json={
        "scope": "personal", "kind": "key", "title": "原名称", "description": "原说明",
        "keyName": "EXISTING", "secret": FIXTURE_VALUE,
    }).json()
    path = url + "/keys"
    result = client.post(path, json={"scope": "personal", "keys": [
        _key_import("EXISTING", ROTATED_VALUE), _key_import("NEW"),
    ]})
    _assert_masked(result, 200)
    assert result.json() == {"added": 1, "replaced": 0, "skipped": 1}
    private = implementation.PersonalVault(home["root"], existing["ownerUserId"])
    assert private._read()[existing["id"]]["secret"] == FIXTURE_VALUE
    request = {"scope": "personal", "keys": [_key_import("EXISTING", ROTATED_VALUE, replace=True)]}
    updated = client.post(path, json=request)
    _assert_masked(updated, 200)
    assert updated.json() == {"added": 0, "replaced": 1, "skipped": 0}
    seen = private._read()[existing["id"]]
    assert seen["secret"] == ROTATED_VALUE and seen["title"] == "原名称" and seen["description"] == "原说明"
    assert client.post(path, json=request).json() == updated.json()
    assert private._read()[existing["id"]] == seen
    assert ROTATED_VALUE.encode() not in private.path.read_bytes()
    assert FIXTURE_VALUE not in caplog.text and ROTATED_VALUE not in caplog.text


def test_key_import_refuses_to_choose_between_duplicate_existing_names(home, configured):
    url, client = home["base"] + "/vault", _tls(home)
    for title in ("第一条", "第二条"):
        response = client.post(url + "/items", json={
            "scope": "personal", "kind": "key", "title": title, "keyName": "SAME", "secret": FIXTURE_VALUE,
        })
        _assert_masked(response, 200)
    sealed, = configured.glob("*.sealed")
    original = sealed.read_bytes()
    response = client.post(url + "/keys", json={"scope": "personal", "keys": [
        _key_import("NEW"), _key_import("SAME", ROTATED_VALUE, replace=True),
    ]})
    _assert_masked(response, 409)
    assert response.json()["error"]["code"] == "vault.conflict"
    assert sealed.read_bytes() == original


@pytest.mark.parametrize("suffix,body", [
    ("/import", _csv(f"url,username,password\nhttps://same.example.test,operator,{FIXTURE_VALUE}")),
    ("/keys", {"scope": "personal", "keys": [_key_import("SHARED_NAME")]}),
])
def test_vault_imports_are_available_only_to_each_active_vault_owner(home, configured, suffix, body):
    url, owner = home["base"] + "/vault", _tls(home)
    member = _tls(home, _join(home, "han@example.com", "Han"))
    outsider = _tls(home, _join(home, "lee@example.com", "Lee"))
    member_id = member.get("/api/app/me").json()["user"]["id"]
    anonymous = TestClient(home["app"], base_url="https://testserver")
    assert anonymous.post(url + suffix, json=body).status_code == 401
    assert home["owner"].post(url + suffix, json=body).json()["error"]["code"] == "vault.secure_transport_required"
    assert member.post(url + suffix, json=body, headers={"Origin": "https://other.example.test"}).status_code == 403
    owner_import = owner.post(url + suffix, json=body)
    _assert_masked(owner_import, 200)
    assert owner_import.json()["added"] == 1
    assert member.get(url).json()["items"] == []
    member_import = member.post(url + suffix, json=body)
    _assert_masked(member_import, 200)
    assert member_import.json()["added"] == 1
    owned, = owner.get(url).json()["items"]
    saved, = member.get(url).json()["items"]
    assert owned["id"] != saved["id"] and saved["ownerUserId"] == member_id
    assert outsider.get(url).json()["items"] == []
    assert owner.delete(f"{home['base']}/members/{member_id}").status_code == 204
    assert member.post(url + suffix, json=body).status_code == 401


@pytest.mark.parametrize("suffix,body,status,code", [
    ("/import", _csv(f"unknown,fields,only\n{FIXTURE_VALUE},value,row"), 422, "vault.invalid"),
    ("/import", _csv(f'url,username,password\n"https://one.example.test,operator,{FIXTURE_VALUE}'), 422, "vault.invalid"),
    ("/import", _csv("x" * (2 * 1024 * 1024 + 1)), 413, "vault.too_large"),
    ("/import", _csv("url,username,password", scope="team"), 403, "vault.personal_only"),
    ("/keys", {"scope": "team", "keys": [_key_import("OTHER")]}, 403, "vault.personal_only"),
    ("/keys", {"scope": "personal", "access": {"allAgents": True}, "keys": [_key_import("OTHER")]},
     403, "vault.sharing_unavailable"),
    ("/keys", {"scope": "personal", "keys": [_key_import("GOOD"), _key_import("bad.name")]}, 422, "vault.invalid"),
    ("/keys", {"scope": "personal", "keys": [_key_import("SAME"), _key_import("SAME", ROTATED_VALUE)]},
     422, "vault.invalid"),
    ("/keys", {"scope": "personal", "keys": [_key_import(f"KEY_{n}") for n in range(201)]}, 413, "vault.too_large"),
])
def test_import_rejections_return_no_secrets_or_partial_rows(home, configured, suffix, body, status, code):
    url, client = home["base"] + "/vault", _tls(home)
    response = client.post(url + suffix, json=body)
    _assert_masked(response, status)
    assert response.json()["error"]["code"] == code
    assert not list(configured.glob("*.sealed"))


def test_vault_imports_enforce_capacity_atomically(home, configured, monkeypatch):
    monkeypatch.setattr(implementation, "_MAX_ITEMS", 1)
    url, client = home["base"] + "/vault", _tls(home)
    csv = (f"url,username,password\nhttps://same.example.test,operator,{FIXTURE_VALUE}\n"
           f"https://other.example.test,operator,{FIXTURE_VALUE}")
    for suffix, body in [
        ("/import", _csv(csv, dry_run=True)),
        ("/import", _csv(csv)),
        ("/keys", {"scope": "personal", "keys": [_key_import("FIRST"), _key_import("SECOND")]}),
    ]:
        response = client.post(url + suffix, json=body)
        _assert_masked(response, 409)
        assert response.json()["error"]["code"] == "vault.limit"
        assert not list(configured.glob("*.sealed"))
