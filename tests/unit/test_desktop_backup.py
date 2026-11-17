"""旧 Desktop raw tar 恢复必须经过电脑信任失效，并保留稳定的 home/维护锁。"""
import io
import multiprocessing
import os
from pathlib import Path
import subprocess
import tarfile
import uuid

import pytest

from src.gateway.connected_computers import ConnectedComputers, ComputerDenied
from src.gateway.process_lease import RuntimeLease
from src.utils.home_maintenance import MAINTENANCE_FILE
from src.utils.state_dir import state_dir
from src.workflow import desktop_backup as module
from src.workflow.home_identity import HomeIdentityError, ensure_home_identity
from src.workflow.members.owner_account import OwnerAccount


@pytest.fixture(autouse=True)
def cheap_passwords(monkeypatch):
    from src.workflow.members import accounts
    monkeypatch.setattr(accounts, "_SCRYPT", {"n": 16, "r": 8, "p": 1})


def _claimed(data):
    home = data / "home"
    owner = OwnerAccount(str(home))
    token, _ = owner.claim_link()
    owner.claim(token, "主人", "owner@example.com", "a-good-owner-password")
    store = ConnectedComputers(str(home))
    code = store.create_pairing()["code"]
    granted = store.pair(code, uuid.uuid4().hex, "Mac")
    return home, store, granted


def _raw(data, archive):
    with tarfile.open(archive, "w:gz") as output:
        output.add(data / "home", arcname="home")
        for extra in ("owner-key", "model.env"):
            if (data / extra).exists():
                output.add(data / extra, arcname=extra)
    return archive


@pytest.mark.parametrize("legacy_migration", [False, True])
def test_old_raw_backup_cannot_revive_revoked_token_and_preserves_home_extras(tmp_path, monkeypatch, legacy_migration):
    data = tmp_path / "app-data"
    home, store, granted = _claimed(data)
    (home / "研究").mkdir()
    (home / "研究/笔记-🧠.md").write_text("备份中的用户资料")
    (data / "owner-key").write_text("old app owner key")
    (data / "model.env").write_text("old app model settings")
    if legacy_migration:
        state_dir(home).rename(home / ".vortocode")
        store = ConnectedComputers(str(home))
    archive = _raw(data, tmp_path / "vortoone-old.tar.gz")
    store.revoke(granted["connector_id"])
    (home / "研究/笔记-🧠.md").write_text("当前用户资料保持")
    (data / "owner-key").write_text("current app owner key")
    (data / "model.env").write_text("current app model settings")
    home_inode, lock_inode = home.stat().st_ino, (home / MAINTENANCE_FILE).stat().st_ino
    if legacy_migration:
        monkeypatch.setenv("VORTOONE_STATE_DIR_MIGRATION", "on")
    assert module.restore_desktop(data, archive) == {"ok": True}
    identity = ensure_home_identity(home)
    assert identity["home_instance_id"] == granted["home_instance_id"]
    assert identity["owner_subject"] == granted["paired_principal_id"]
    assert identity["trust_epoch"] != granted["trust_epoch"]
    with pytest.raises(ComputerDenied):
        ConnectedComputers(str(home)).authenticate(granted["token"])
    assert ConnectedComputers(str(home)).list() == []
    assert home.stat().st_ino == home_inode and (home / MAINTENANCE_FILE).stat().st_ino == lock_inode
    assert (home / "研究/笔记-🧠.md").read_text() == "当前用户资料保持"
    recovered = list(data.glob("recovered-extras-*"))
    assert len(recovered) == 1
    assert (recovered[0] / "研究/笔记-🧠.md").read_text() == "备份中的用户资料"
    assert (data / "owner-key").read_text() == "current app owner key"
    assert (data / "model.env").read_text() == "current app model settings"
    assert len(list(home.glob("*.before-desktop-restore-*"))) == 1
    if os.name != "nt":
        for path in [state_dir(home), *state_dir(home).rglob("*"), recovered[0], *recovered[0].rglob("*")]:
            if not path.is_symlink():
                assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)


def test_actual_native_tar_with_exact_migration_alias_restores_without_reviving_grants(tmp_path, monkeypatch):
    data = tmp_path / "app-data"
    home, store, granted = _claimed(data)
    state_dir(home).rename(home / ".vortocode")
    monkeypatch.setenv("VORTOONE_STATE_DIR_MIGRATION", "on")
    canonical = state_dir(home)
    assert canonical.name == ".vortoone" and (home / ".vortocode").is_symlink()
    assert os.readlink(home / ".vortocode") == ".vortoone"
    archive = tmp_path / "vortoone-native.tar.gz"
    subprocess.run(["tar", "-czf", str(archive), "-C", str(data), "home"], check=True,
                   capture_output=True)
    with tarfile.open(archive, "r:gz") as source:
        alias = source.getmember("home/.vortocode")
        assert alias.issym() and alias.linkname == ".vortoone"
    store.revoke(granted["connector_id"])
    assert module.restore_desktop(data, archive) == {"ok": True}
    with pytest.raises(ComputerDenied):
        ConnectedComputers(str(home)).authenticate(granted["token"])
    assert ensure_home_identity(home)["trust_epoch"] != granted["trust_epoch"]


@pytest.mark.parametrize("target,kind,canonical", [
    (".vortoone", "symlink", False),
    ("./.vortoone", "symlink", True),
    ("../.vortoone", "symlink", True),
    ("/tmp/.vortoone", "symlink", True),
    (".vortoone", "hardlink", True),
])
def test_only_exact_alias_with_real_canonical_state_is_allowed(tmp_path, target, kind, canonical):
    archive = tmp_path / "bad-alias.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        if canonical:
            state = tarfile.TarInfo("home/.vortoone/memory.json")
            state.size = 2
            output.addfile(state, io.BytesIO(b"{}"))
        alias = tarfile.TarInfo("home/.vortocode")
        alias.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
        alias.linkname = target
        output.addfile(alias)
    with pytest.raises(module.DesktopRestoreError):
        module.restore_desktop(tmp_path / "app-data", archive)
    assert not (tmp_path / "app-data").exists()


def _package(path, entries):
    with tarfile.open(path, "w:gz") as output:
        for name, kind, data in entries:
            member = tarfile.TarInfo(name)
            if kind == "file":
                member.size = len(data)
                output.addfile(member, io.BytesIO(data))
            elif kind == "dir":
                member.type = tarfile.DIRTYPE
                output.addfile(member)
            elif kind in ("symlink", "hardlink"):
                member.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
                member.linkname = "/tmp/not-a-restore-target"
                output.addfile(member)
            else:
                member.type = tarfile.CHRTYPE
                output.addfile(member)
    return path


@pytest.mark.parametrize("name,kind", [
    ("../escape", "file"), ("home/../../escape", "file"), ("/home/escape", "file"),
    ("home/C:/escape", "file"), ("home\\..\\escape", "file"), ("home/a\nb", "file"),
    ("home/link", "symlink"), ("owner-key", "hardlink"), ("home/device", "device"),
    ("home/.vortoone/memory.json", "file"), ("home/.vortoone/MEMORY.json", "file"),
    ("home/.vortoone", "file"), ("home/.vortocode/other.json", "file"),
])
def test_bad_paths_types_duplicates_and_ambiguous_state_do_not_touch_home(tmp_path, name, kind):
    data = tmp_path / "app-data"
    home, store, granted = _claimed(data)
    before = (state_dir(home) / "home-identity/identity.json").read_bytes()
    archive = _package(tmp_path / "bad.tar.gz", [("home/.vortoone/memory.json", "file", b"{}"), (name, kind, b"bad")])
    with pytest.raises(module.DesktopRestoreError):
        module.restore_desktop(data, archive)
    assert (state_dir(home) / "home-identity/identity.json").read_bytes() == before
    assert store.check(granted)
    assert not (tmp_path / "escape").exists()
    assert not list(home.glob("*.before-desktop-restore-*"))


def test_missing_state_and_quota_excess_do_not_create_a_new_legacy_home(tmp_path, monkeypatch):
    data = tmp_path / "app-data"
    archive = _package(tmp_path / "no-state.tar.gz", [("home/notes.txt", "file", b"notes")])
    with pytest.raises(module.DesktopRestoreError):
        module.restore_desktop(data, archive)
    assert not data.exists()
    archive = _package(tmp_path / "too-large.tar.gz", [("home/.vortoone/memory.json", "file", b"1234")])
    monkeypatch.setattr(module, "MAX_BYTES", 3)
    with pytest.raises(module.DesktopRestoreError):
        module.restore_desktop(data, archive)
    assert not data.exists()


@pytest.mark.parametrize("failure", ["hook", "publish"])
def test_hook_or_publish_failure_preserves_original_state(tmp_path, monkeypatch, failure):
    data = tmp_path / "app-data"
    home, store, granted = _claimed(data)
    archive = _raw(data, tmp_path / "vortoone-old.tar.gz")
    store.revoke(granted["connector_id"])
    identity = ensure_home_identity(home)
    target = state_dir(home)
    lock_inode = (home / MAINTENANCE_FILE).stat().st_ino
    if failure == "hook":
        def fail(*args):
            raise HomeIdentityError("injected identity failure")
        monkeypatch.setattr(module, "prepare_replaced_home", fail)
    else:
        rename = Path.rename
        def fail(source, destination):
            if Path(destination) == target and any(part.startswith(".desktop-restore-") for part in source.parts):
                raise OSError("injected publish failure")
            return rename(source, destination)
        monkeypatch.setattr(Path, "rename", fail)
    with pytest.raises(module.DesktopRestoreError):
        module.restore_desktop(data, archive)
    assert ensure_home_identity(home) == identity
    assert (home / MAINTENANCE_FILE).stat().st_ino == lock_inode
    with pytest.raises(ComputerDenied):
        store.authenticate(granted["token"])
    assert not list(home.glob("*.before-desktop-restore-*"))


def _hold_runtime(home, ready, release):
    with RuntimeLease(home):
        ready.set()
        release.wait(15)


def test_live_runtime_refuses_desktop_restore_then_allows_it_after_stop(tmp_path):
    data = tmp_path / "app-data"
    home, store, granted = _claimed(data)
    archive = _raw(data, tmp_path / "vortoone-old.tar.gz")
    identity = ensure_home_identity(home)
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    process = context.Process(target=_hold_runtime, args=(str(home), ready, release))
    process.start()
    try:
        assert ready.wait(10)
        with pytest.raises(module.DesktopRestoreError):
            module.restore_desktop(data, archive)
        assert ensure_home_identity(home) == identity and store.check(granted)
    finally:
        release.set()
        process.join(20)
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert process.exitcode == 0
    assert module.restore_desktop(data, archive) == {"ok": True}
    assert not store.check(granted)
