"""电脑配对只产生独立授权，重启、并发、认领及恢复均不复活旧信任。"""
import json
import multiprocessing
import os
import uuid

import pytest

from src.gateway.connected_computers import ConnectedComputers, ComputerDenied, ComputerUnavailable
from src.workflow.home_identity import (
    HomeIdentityError, ensure_home_identity, initialize_unclaimed_home,
    pairing_owner,
)
from src.workflow.members.owner_account import OwnerAccount


@pytest.fixture(autouse=True)
def cheap_passwords(monkeypatch):
    from src.workflow.members import accounts
    monkeypatch.setattr(accounts, "_SCRYPT", {"n": 16, "r": 8, "p": 1})


def _claimed(root):
    store = OwnerAccount(str(root))
    token, _ = store.claim_link()
    store.claim(token, "主人", "owner@example.com", "a-good-owner-password")
    return store


def _pair(root, **kwargs):
    store = ConnectedComputers(root, **kwargs)
    code = store.create_pairing()["code"]
    return store, store.pair(code, uuid.uuid4().hex, "我的电脑")


def test_legacy_needs_explicit_confirmation_but_subject_then_stays_stable(tmp_path):
    identity = ensure_home_identity(tmp_path)
    assert identity["ownership_state"] == "legacy_candidate" and identity["owner_subject"] is None
    with pytest.raises(PermissionError):
        pairing_owner(tmp_path)
    first = pairing_owner(tmp_path, legacy_confirmed=True)
    assert first["ownership_state"] == "claimed" and first["owner_subject"]
    assert pairing_owner(tmp_path) == first == ensure_home_identity(tmp_path)


def test_unclaimed_new_home_cannot_become_legacy_from_owner_key(tmp_path):
    (tmp_path / "owner-key").write_text("not-an-auth-proof")
    initialize_unclaimed_home(tmp_path)
    with pytest.raises(PermissionError):
        pairing_owner(tmp_path, legacy_confirmed=True)
    _claimed(tmp_path)
    assert pairing_owner(tmp_path)["ownership_state"] == "claimed"


def test_expired_claim_is_not_legacy(tmp_path):
    from datetime import datetime, timedelta, timezone
    owner = OwnerAccount(str(tmp_path))
    owner.claim_link(now=datetime.now(timezone.utc) - timedelta(days=2))
    assert not owner.waiting()
    with pytest.raises(PermissionError):
        pairing_owner(tmp_path, legacy_confirmed=True)


def test_claimed_owner_migration_preserves_subject_across_profile_edits(tmp_path):
    owner = _claimed(tmp_path)
    # 模拟 C1 之前已存在的邮箱密码家：有账号，没有公共身份文件。
    (tmp_path / ".vortoone/home-identity/identity.json").unlink()
    before = pairing_owner(tmp_path)
    value = owner._load()
    value.update(name="新名字", email="new@example.com")
    owner._save(value)
    assert pairing_owner(tmp_path) == before


def test_failed_reset_remains_unclaimed_even_if_old_password_was_not_removed(tmp_path, monkeypatch):
    owner = _claimed(tmp_path)
    store, granted = _pair(tmp_path)
    def fail_save(value):
        raise OSError("injected write failure")
    monkeypatch.setattr(owner, "_save", fail_save)
    with pytest.raises(OSError):
        owner.claim_link(reset=True)
    assert owner.profile() is not None
    assert not store.check(granted)
    with pytest.raises(PermissionError):
        pairing_owner(tmp_path, legacy_confirmed=True)


def test_claim_identity_commit_failure_resumes_only_with_original_proof_and_details(tmp_path, monkeypatch):
    import src.workflow.members.owner_account as owner_module
    from src.workflow.members.owner_account import WrongClaim, Taken
    from datetime import datetime, timedelta, timezone
    owner = OwnerAccount(str(tmp_path))
    token, _ = owner.claim_link()
    def failed_identity(root):
        raise HomeIdentityError("injected identity commit failure")
    with monkeypatch.context() as patch:
        patch.setattr(owner_module, "complete_owner_claim", failed_identity)
        with pytest.raises(HomeIdentityError):
            owner.claim(token, "主人", "owner@example.com", "a-good-owner-password")
    pending = owner._load()["claim_completion"]
    assert token not in owner.path.read_text() and owner.waiting()
    assert ensure_home_identity(tmp_path)["ownership_state"] == "unclaimed"
    with pytest.raises(PermissionError):
        pairing_owner(tmp_path, legacy_confirmed=True)
    for bad_token, name, email, password in (
        ("wrong-token", "主人", "owner@example.com", "a-good-owner-password"),
        (token, "另一人", "owner@example.com", "a-good-owner-password"),
        (token, "主人", "other@example.com", "a-good-owner-password"),
        (token, "主人", "owner@example.com", "a-different-password"),
    ):
        with pytest.raises(WrongClaim):
            owner.claim(bad_token, name, email, password)
    assert owner._load()["claim_completion"] == pending
    # 首次提交已在原期限内接受；过期后也只能完成原账号/原密码的这一笔。
    OwnerAccount(str(tmp_path)).claim(token, "主人", "owner@example.com", "a-good-owner-password",
                                    now=datetime.now(timezone.utc) + timedelta(days=2))
    identity = pairing_owner(tmp_path)
    assert identity["owner_subject"] == pending["subject"] and identity["trust_epoch"] == pending["trust_epoch"]
    assert "claim_completion" not in owner._load() and not owner.waiting()
    with pytest.raises(Taken):
        owner.claim(token, "主人", "owner@example.com", "a-good-owner-password")


def test_claim_cleanup_failure_retries_without_rotating_already_committed_identity(tmp_path, monkeypatch):
    owner = OwnerAccount(str(tmp_path))
    token, _ = owner.claim_link()
    save = owner._save
    def fail_cleanup(value):
        if value.get("password") and "claim_completion" not in value:
            raise OSError("injected journal cleanup failure")
        save(value)
    monkeypatch.setattr(owner, "_save", fail_cleanup)
    with pytest.raises(OSError):
        owner.claim(token, "主人", "owner@example.com", "a-good-owner-password")
    committed = ensure_home_identity(tmp_path)
    assert committed["ownership_state"] == "claimed" and owner.waiting()
    with pytest.raises(PermissionError):
        pairing_owner(tmp_path)
    OwnerAccount(str(tmp_path)).claim(token, "主人", "owner@example.com", "a-good-owner-password")
    assert pairing_owner(tmp_path) == committed
    assert "claim_completion" not in owner._load()


def test_failed_new_reset_cannot_resume_a_superseded_claim_completion(tmp_path, monkeypatch):
    import src.workflow.members.owner_account as owner_module
    owner = OwnerAccount(str(tmp_path))
    token, _ = owner.claim_link()
    def failed_identity(root):
        raise HomeIdentityError("injected identity commit failure")
    with monkeypatch.context() as patch:
        patch.setattr(owner_module, "complete_owner_claim", failed_identity)
        with pytest.raises(HomeIdentityError):
            owner.claim(token, "主人", "owner@example.com", "a-good-owner-password")
    old_pending = owner._load()["claim_completion"]
    def failed_save(value):
        raise OSError("injected reset failure")
    with monkeypatch.context() as patch:
        patch.setattr(owner, "_save", failed_save)
        with pytest.raises(OSError):
            owner.claim_link(reset=True)
    identity = ensure_home_identity(tmp_path)
    assert identity["trust_epoch"] != old_pending["admission_epoch"]
    with pytest.raises(HomeIdentityError):
        OwnerAccount(str(tmp_path)).claim(token, "主人", "owner@example.com", "a-good-owner-password")
    assert ensure_home_identity(tmp_path) == identity
    with pytest.raises(PermissionError):
        pairing_owner(tmp_path, legacy_confirmed=True)


def test_pairing_returns_secrets_once_and_private_storage_saves_only_digests(tmp_path):
    _claimed(tmp_path)
    store = ConnectedComputers(tmp_path, clock=lambda: 1000)
    pairing = store.create_pairing()
    assert len(pairing["code"]) == 12 and pairing["expires_at"] == 1300
    granted = store.pair(pairing["code"], uuid.uuid4().hex, " Mac ")
    binding = {k: v for k, v in granted.items() if k != "token"}
    assert store.authenticate(granted["token"]) == binding
    assert ConnectedComputers(tmp_path).check(binding)
    raw = store.path.read_text()
    assert granted["token"] not in raw and pairing["code"] not in raw
    assert store.list()[0]["name"] == "Mac" and store.list()[0]["active"]
    assert "digest" not in store.list()[0] and "token" not in store.list()[0]
    if os.name != "nt":
        assert store.path.stat().st_mode & 0o777 == 0o600
        assert store.directory.stat().st_mode & 0o777 == 0o700
        identity_path = tmp_path / ".vortoone/home-identity/identity.json"
        assert identity_path.stat().st_mode & 0o777 == 0o600
        assert identity_path.parent.stat().st_mode & 0o777 == 0o700
    with pytest.raises(ComputerDenied):
        store.pair(pairing["code"], uuid.uuid4().hex, "另一个")


def test_native_pairing_has_only_control_identity_and_preserves_human_pairing_window(tmp_path):
    from src.gateway.computer_protocol import BINDING_FIELDS, welcome
    _claimed(tmp_path)
    store = ConnectedComputers(tmp_path)
    pending = store.create_pairing()
    with pytest.raises(ComputerDenied):
        store.pair("wrong-code", uuid.uuid4().hex, "错误尝试")
    before = json.loads(store.path.read_text())
    granted = store.pair_native(uuid.uuid4().hex, "本机电脑")
    assert set(granted) == set(BINDING_FIELDS) | {"token"}
    assert store.authenticate(granted["token"]) == {k: granted[k] for k in BINDING_FIELDS}
    advertised = welcome(granted, uuid.uuid4().hex)
    assert advertised["folders"] == [] and not any(advertised["capabilities"].values())
    after = json.loads(store.path.read_text())
    for field in ("pairing", "attempts", "created_pairings"):
        assert after[field] == before[field]
    assert granted["token"] not in store.path.read_text()
    human = store.pair(pending["code"], uuid.uuid4().hex, "真人配对")
    assert store.authenticate(human["token"])["connector_id"] == human["connector_id"]


def test_native_pairing_refuses_unconfirmed_legacy_unclaimed_and_incomplete_claim(tmp_path, monkeypatch):
    import src.workflow.members.owner_account as owner_module
    for name in ("legacy", "unclaimed", "incomplete"):
        root = tmp_path / name
        if name == "unclaimed":
            initialize_unclaimed_home(root)
        elif name == "incomplete":
            owner = OwnerAccount(str(root))
            token, _ = owner.claim_link()
            def fail_identity(root):
                raise HomeIdentityError("injected identity commit failure")
            with monkeypatch.context() as patch:
                patch.setattr(owner_module, "complete_owner_claim", fail_identity)
                with pytest.raises(HomeIdentityError):
                    owner.claim(token, "主人", "owner@example.com", "a-good-owner-password")
        with pytest.raises(ComputerDenied):
            ConnectedComputers(root).pair_native(uuid.uuid4().hex, "Mac")
        assert ConnectedComputers(root).list() == []


def test_native_pairing_can_migrate_a_verified_existing_owner_account(tmp_path):
    from src.gateway.mobile_devices import MobileDevices
    _claimed(tmp_path)
    instance = MobileDevices(str(tmp_path)).ensure_instance()
    (tmp_path / ".vortoone/home-identity/identity.json").unlink()
    granted = ConnectedComputers(tmp_path).pair_native(uuid.uuid4().hex, "本机电脑")
    assert granted["home_instance_id"] == instance
    assert pairing_owner(tmp_path)["owner_subject"] == granted["paired_principal_id"]


def test_native_pairing_retains_revoked_ids_and_authorization_cap_without_touching_human_counters(tmp_path, monkeypatch):
    import src.gateway.connected_computers as computer_module
    _claimed(tmp_path)
    store = ConnectedComputers(tmp_path)
    pending = store.create_pairing()
    first = store.pair_native(uuid.uuid4().hex, "Mac")
    store.revoke(first["connector_id"])
    snapshot = json.loads(store.path.read_text())
    with pytest.raises(ComputerDenied):
        store.pair_native(first["connector_id"], "不能复用")
    assert json.loads(store.path.read_text()) == snapshot
    monkeypatch.setattr(computer_module, "MAX_COMPUTERS", 2)
    store.pair_native(uuid.uuid4().hex, "第二台")
    with pytest.raises(ComputerDenied):
        store.pair_native(uuid.uuid4().hex, "第三台")
    current = json.loads(store.path.read_text())
    for field in ("pairing", "attempts", "created_pairings"):
        assert current[field] == snapshot[field]
    assert current["pairing"]["pairing_id"] == pending["pairing_id"] and len(store.list()) == 2


def test_pairing_expiry_five_wrong_attempts_cancel_and_new_code_replaces_old(tmp_path):
    _claimed(tmp_path)
    clock = [1000]
    store = ConnectedComputers(tmp_path, clock=lambda: clock[0])
    first = store.create_pairing()
    for _ in range(5):
        with pytest.raises(ComputerDenied):
            store.pair("!" * 12, uuid.uuid4().hex, "Mac")
    with pytest.raises(ComputerDenied):
        store.pair(first["code"], uuid.uuid4().hex, "Mac")
    second = store.create_pairing()
    store.cancel_pairing(second["pairing_id"])
    with pytest.raises(ComputerDenied):
        store.pair(second["code"], uuid.uuid4().hex, "Mac")
    third = store.create_pairing()
    fourth = store.create_pairing()
    with pytest.raises(ComputerDenied):
        store.pair(third["code"], uuid.uuid4().hex, "Mac")
    clock[0] = fourth["expires_at"]
    with pytest.raises(ComputerDenied):
        store.pair(fourth["code"], uuid.uuid4().hex, "Mac")


def test_home_pairing_rate_limits_persist_across_store_instances(tmp_path):
    _claimed(tmp_path)
    store = ConnectedComputers(tmp_path, clock=lambda: 1000)
    for _ in range(6):
        store.create_pairing()
    with pytest.raises(ComputerDenied):
        ConnectedComputers(tmp_path, clock=lambda: 1001).create_pairing()
    for _ in range(30):
        with pytest.raises(ComputerDenied):
            store.pair("BAD", uuid.uuid4().hex, "Mac")
    with pytest.raises(ComputerDenied, match="频繁"):
        ConnectedComputers(tmp_path, clock=lambda: 1001).pair("BAD", uuid.uuid4().hex, "Mac")


def test_revocation_is_immediate_persistent_and_connector_id_cannot_be_reused(tmp_path):
    _claimed(tmp_path)
    store, granted = _pair(tmp_path)
    assert store.rename(granted["connector_id"], "新电脑")["name"] == "新电脑"
    revoked = store.revoke(granted["connector_id"])
    assert revoked["credential_epoch"] != granted["credential_epoch"]
    assert not ConnectedComputers(tmp_path).check(granted)
    with pytest.raises(ComputerDenied):
        ConnectedComputers(tmp_path).authenticate(granted["token"])
    code = store.create_pairing()["code"]
    with pytest.raises(ComputerDenied):
        store.pair(code, granted["connector_id"], "重新用")
    assert not store.list()[0]["active"]


def test_reset_revokes_before_reclaim_and_new_owner_subject_does_not_reuse_old(tmp_path):
    owner = _claimed(tmp_path)
    store, granted = _pair(tmp_path)
    pending = store.create_pairing()
    token, _ = owner.claim_link(reset=True)
    assert not store.check(granted)
    with pytest.raises(ComputerDenied):
        store.pair(pending["code"], uuid.uuid4().hex, "Mac")
    with pytest.raises(ComputerDenied):
        store.create_pairing(legacy_confirmed=True)
    owner.claim(token, "新主人", "new@example.com", "another-good-password")
    identity = ensure_home_identity(tmp_path)
    assert identity["owner_subject"] != granted["paired_principal_id"]
    assert identity["trust_epoch"] != granted["trust_epoch"]
    with pytest.raises(ComputerDenied):
        store.authenticate(granted["token"])


def test_restore_same_backup_twice_never_revives_old_computer_credentials(tmp_path):
    from src.workflow.home_backup import backup, restore
    _claimed(tmp_path)
    store, granted = _pair(tmp_path)
    pending = store.create_pairing()
    saved = backup(str(tmp_path), str(tmp_path / "backups"))["path"]
    store.revoke(granted["connector_id"])
    restore(str(tmp_path), saved)
    first = ensure_home_identity(tmp_path)
    assert first["home_instance_id"] == granted["home_instance_id"]
    assert first["owner_subject"] == granted["paired_principal_id"]
    with pytest.raises(ComputerDenied):
        store.authenticate(granted["token"])
    with pytest.raises(ComputerDenied):
        store.pair(pending["code"], uuid.uuid4().hex, "Mac")
    store2, new_grant = _pair(tmp_path)
    restore(str(tmp_path), saved)
    assert ensure_home_identity(tmp_path)["trust_epoch"] not in {first["trust_epoch"], granted["trust_epoch"]}
    assert not store2.check(new_grant)


def test_move_preserves_public_identity_but_requires_all_computers_to_pair_again(tmp_path):
    from src.workflow.home_move import export_home, import_home
    old, new = tmp_path / "old", tmp_path / "new"
    _claimed(old)
    store, granted = _pair(old)
    export_home(str(old), str(tmp_path / "move.tar.gz"), moving=True)
    assert not store.check(granted)
    import_home(str(new), str(tmp_path / "move.tar.gz"))
    identity = ensure_home_identity(new)
    assert identity["home_instance_id"] == granted["home_instance_id"]
    assert identity["owner_subject"] == granted["paired_principal_id"]
    assert identity["trust_epoch"] != granted["trust_epoch"]
    with pytest.raises(ComputerDenied):
        ConnectedComputers(new).authenticate(granted["token"])


@pytest.mark.parametrize("component", ["identity", "grants"])
def test_damaged_security_files_never_become_empty_stores(tmp_path, component):
    _claimed(tmp_path)
    store, granted = _pair(tmp_path)
    path = store.path if component == "grants" else tmp_path / ".vortoone/home-identity/identity.json"
    path.write_text("{broken")
    with pytest.raises(ComputerUnavailable):
        store.authenticate(granted["token"])
    assert path.read_text() == "{broken"


def test_bad_or_overpermissive_owner_file_cannot_initialize_identity(tmp_path):
    directory = tmp_path / ".vortoone/members"
    directory.mkdir(parents=True)
    path = directory / "owner.json"
    path.write_text(json.dumps({"password": "not-a-password-record"}))
    path.chmod(0o600)
    with pytest.raises(HomeIdentityError):
        pairing_owner(tmp_path, legacy_confirmed=True)
    assert not (tmp_path / ".vortoone/home-identity/identity.json").exists()


def test_public_uuid_is_preserved_from_old_mobile_store_and_shared_by_all_writers(tmp_path):
    from src.gateway.mobile_devices import MobileDevices
    from src.gateway.mobile_pairing import MobilePairing
    directory = tmp_path / ".vortoone/mobile-devices"
    directory.mkdir(mode=0o700, parents=True)
    instance = uuid.uuid4().hex
    path = directory / "grants.json"
    path.write_text(json.dumps({"schema_version": 1, "instance_id": instance, "devices": []}))
    path.chmod(0o600)
    assert ensure_home_identity(tmp_path)["home_instance_id"] == instance
    assert MobileDevices(str(tmp_path)).issue("手机")["instance_id"] == instance
    assert MobilePairing(str(tmp_path)).window(True)["instance_id"] == instance


def test_new_mobile_writers_use_an_already_initialized_public_uuid(tmp_path):
    from src.gateway.mobile_devices import MobileDevices
    from src.gateway.mobile_pairing import MobilePairing
    instance = ensure_home_identity(tmp_path)["home_instance_id"]
    assert MobilePairing(str(tmp_path)).window(True)["instance_id"] == instance
    assert MobileDevices(str(tmp_path)).issue("手机")["instance_id"] == instance


def test_authorization_cap_includes_revoked_ids_and_cannot_be_bypassed(tmp_path, monkeypatch):
    import src.gateway.connected_computers as computer_module
    monkeypatch.setattr(computer_module, "MAX_COMPUTERS", 2)
    _claimed(tmp_path)
    store, first = _pair(tmp_path)
    store.revoke(first["connector_id"])
    _pair(tmp_path)
    pairing = store.create_pairing()
    with pytest.raises(ComputerDenied, match="上限"):
        store.pair(pairing["code"], uuid.uuid4().hex, "第三台")
    assert len(store.list()) == 2


def test_lost_response_after_durable_pairing_does_not_allow_a_second_exchange(tmp_path, monkeypatch):
    _claimed(tmp_path)
    store = ConnectedComputers(tmp_path)
    code = store.create_pairing()["code"]
    save = store._save
    def saved_but_response_lost(data):
        save(data)
        raise OSError("injected lost response after atomic replacement")
    monkeypatch.setattr(store, "_save", saved_but_response_lost)
    with pytest.raises(OSError):
        store.pair(code, uuid.uuid4().hex, "Mac")
    reloaded = ConnectedComputers(tmp_path)
    with pytest.raises(ComputerDenied):
        reloaded.pair(code, uuid.uuid4().hex, "再试")
    assert len(reloaded.list()) == 1


def _consume_in_process(root, code, connector_id, start, results):
    from src.gateway.connected_computers import ConnectedComputers
    start.wait(10)
    try:
        granted = ConnectedComputers(root).pair(code, connector_id, "Mac")
        results.put(("ok", granted["connector_id"]))
    except (PermissionError, RuntimeError):
        results.put(("denied", ""))


def test_two_processes_can_consume_a_pairing_code_only_once(tmp_path):
    _claimed(tmp_path)
    store = ConnectedComputers(tmp_path)
    code = store.create_pairing()["code"]
    context = multiprocessing.get_context("spawn")
    start, results = context.Event(), context.Queue()
    processes = [context.Process(target=_consume_in_process,
                 args=(str(tmp_path), code, uuid.uuid4().hex, start, results)) for _ in range(2)]
    for process in processes:
        process.start()
    start.set()
    output = [results.get(timeout=15) for _ in processes]
    for process in processes:
        process.join(15)
        assert process.exitcode == 0
    assert [status for status, _ in output].count("ok") == 1
    assert len(store.list()) == 1


def _hold_identity_then_revoke(root, ready, release, results):
    from src.workflow.home_identity import owner_identity_change, invalidate_computer_trust
    try:
        with owner_identity_change(root):
            ready.set()
            if not release.wait(15):
                raise RuntimeError("test release timeout")
            invalidate_computer_trust(root, "concurrent-test-revocation")
        results.put("revoked")
    except Exception as error:
        results.put(type(error).__name__)


def test_winner_keeps_token_during_stable_identity_lock_then_rejects_committed_change(tmp_path, monkeypatch):
    """精确复现消费落盘后的窗口：其它进程持身份锁，不能让 winning 回执丢失。"""
    _claimed(tmp_path)
    store = ConnectedComputers(tmp_path)
    identity = store.identity()
    code = store.create_pairing()["code"]
    context = multiprocessing.get_context("spawn")
    ready, release, results = context.Event(), context.Event(), context.Queue()
    process = context.Process(target=_hold_identity_then_revoke,
                              args=(str(tmp_path), ready, release, results))
    save = store._save
    def pause_after_consumption(data):
        save(data)
        if data["pairing"] is None and data["computers"]:
            process.start()
            assert ready.wait(10)
    monkeypatch.setattr(store, "_save", pause_after_consumption)
    try:
        granted = store.pair(code, uuid.uuid4().hex, "Mac")
        assert ensure_home_identity(tmp_path) == identity
        assert store.authenticate(granted["token"])["connector_id"] == granted["connector_id"]
        assert store.check(granted)
    finally:
        release.set()
        if process.pid is not None:
            process.join(20)
            if process.is_alive():
                process.terminate()
                process.join(5)
    assert process.exitcode == 0 and results.get(timeout=2) == "revoked"
    assert ensure_home_identity(tmp_path)["trust_epoch"] != identity["trust_epoch"]
    assert not store.check(granted)
    with pytest.raises(ComputerDenied):
        store.authenticate(granted["token"])


def test_failed_restore_hook_preserves_live_state(tmp_path, monkeypatch):
    from src.workflow.home_backup import BackupError, backup, restore
    import src.workflow.home_identity as identity_module
    _claimed(tmp_path)
    store, granted = _pair(tmp_path)
    saved = backup(str(tmp_path), str(tmp_path / "backups"))["path"]
    def refuse(*args):
        raise HomeIdentityError("failure injected")
    monkeypatch.setattr(identity_module, "prepare_replaced_home", refuse)
    with pytest.raises(BackupError):
        restore(str(tmp_path), saved)
    assert store.authenticate(granted["token"])["connector_id"] == granted["connector_id"]
