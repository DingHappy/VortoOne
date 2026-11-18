"""真实进程竞争：Runtime 启动和家状态目录交换不能持有两份锁。"""
import asyncio
import json
import multiprocessing
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.gateway.process_lease import ProcessLease
from src.gateway.runtime import runtime_lifespan
from src.utils.home_maintenance import HomeMaintenanceError, MAINTENANCE_FILE
from src.workflow.home_backup import BackupError, backup, restore
from src.workflow.home_move import MoveError, export_home, import_home
from src.workflow.members.owner_account import OwnerAccount


def _runtime_options():
    async def shutdown():
        pass
    class RunManager:
        def recover(self):
            return []
        async def shutdown(self):
            pass
    class TerminalManager:
        def shutdown(self):
            pass
    return dict(runner=SimpleNamespace(recover=lambda: [], shutdown=shutdown), scheduler=None,
                audit_dependencies=lambda: {}, make_run_manager=RunManager, make_terminal_manager=TerminalManager,
                register_im_worker=lambda worker: None)


def _clean_runtime_environment():
    for name in ("VORTOONE_IM", "VORTOONE_IM_BINDINGS_FILE", "VORTOONE_CRON", "VORTOONE_HEARTBEAT",
                 "VORTOONE_SUPERVISOR_PID"):
        os.environ.pop(name, None)


@pytest.fixture(autouse=True)
def clean_runtime_environment(monkeypatch):
    for name in ("VORTOONE_IM", "VORTOONE_IM_BINDINGS_FILE", "VORTOONE_CRON", "VORTOONE_HEARTBEAT",
                 "VORTOONE_SUPERVISOR_PID"):
        monkeypatch.delenv(name, raising=False)


def _hold_real_runtime(root, ready, release, results):
    _clean_runtime_environment()
    async def run():
        async with runtime_lifespan(root, **_runtime_options()):
            ready.set()
            if not await asyncio.to_thread(release.wait, 20):
                raise RuntimeError("test release timeout")
    try:
        asyncio.run(run())
        results.put("stopped")
    except Exception as error:
        results.put(type(error).__name__)


def _pause_restore_between_directory_renames(root, package, ready, release, results):
    rename = Path.rename
    state = Path(root) / ".vortoone"
    def paused(source, destination):
        if source.name.startswith(".vortoone.restoring-") and Path(destination) == state:
            ready.set()
            if not release.wait(20):
                raise RuntimeError("test release timeout")
        return rename(source, destination)
    Path.rename = paused
    try:
        restore(root, package)
        results.put("restored")
    except Exception as error:
        results.put(type(error).__name__)


def _pause_owner_claim(root, token, ready, release, results):
    owner = OwnerAccount(root)
    save = owner._save
    def paused(value):
        save(value)
        if "claim_completion" in value:
            ready.set()
            if not release.wait(20):
                raise RuntimeError("test release timeout")
    owner._save = paused
    try:
        owner.claim(token, "主人", "owner@example.com", "a-good-owner-password")
        results.put("claimed")
    except Exception as error:
        results.put(type(error).__name__)


def _home(root):
    state = root / ".vortoone"
    state.mkdir(parents=True)
    (state / "note.txt").write_text("original data")
    return state


def test_running_runtime_rejects_restore_import_and_moving_export(tmp_path):
    root = tmp_path / "home"
    state = _home(root)
    saved = backup(str(root), str(tmp_path / "backups"))["path"]
    package = str(tmp_path / "move.tar.gz")
    export_home(str(root), package)
    context = multiprocessing.get_context("spawn")
    ready, release, results = context.Event(), context.Event(), context.Queue()
    process = context.Process(target=_hold_real_runtime, args=(str(root), ready, release, results))
    process.start()
    try:
        assert ready.wait(15), "Runtime did not start"
        # 同一运行中的家仍可正常由真人认领；它只互斥状态交换，不要求 Runtime 停止。
        owner = OwnerAccount(str(root))
        token, _ = owner.claim_link()
        owner.claim(token, "主人", "owner@example.com", "a-good-owner-password")
        assert owner.profile()["email"] == "owner@example.com"
        for method, args, kwargs, error in (
            (restore, (str(root), saved), {}, BackupError),
            (import_home, (str(root), package), {"replace": True}, MoveError),
            (export_home, (str(root), str(tmp_path / "cannot-move.tar.gz")), {"moving": True}, MoveError),
        ):
            with pytest.raises(error):
                method(*args, **kwargs)
        assert (state / "note.txt").read_text() == "original data"
        assert not (state / "moved.json").exists()
        assert not list(root.glob(".vortoone.before-*"))
    finally:
        release.set()
        process.join(20)
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert process.exitcode == 0
    assert results.get(timeout=2) == "stopped"
    restore(str(root), saved)                # Runtime 释放后同一操作可以完成


def test_claim_and_claim_link_cannot_write_into_a_restore_directory_gap(tmp_path):
    root = tmp_path / "home"
    state = _home(root)
    owner = OwnerAccount(str(root))
    token, _ = owner.claim_link()
    saved = backup(str(root), str(tmp_path / "backups"))["path"]
    context = multiprocessing.get_context("spawn")
    ready, release, results = context.Event(), context.Event(), context.Queue()
    process = context.Process(target=_pause_restore_between_directory_renames,
                              args=(str(root), saved, ready, release, results))
    process.start()
    try:
        assert ready.wait(15)
        assert not state.exists()
        with pytest.raises(HomeMaintenanceError):
            owner.claim_link(reset=True)
        with pytest.raises(HomeMaintenanceError):
            owner.claim(token, "主人", "owner@example.com", "a-good-owner-password")
        assert not state.exists()
    finally:
        release.set()
        process.join(20)
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert process.exitcode == 0 and results.get(timeout=2) == "restored"
    owner.claim(token, "主人", "owner@example.com", "a-good-owner-password")
    assert owner.profile()["email"] == "owner@example.com"


def test_restore_cannot_replace_state_inside_a_pending_claim_transaction(tmp_path):
    root = tmp_path / "home"
    state = _home(root)
    owner = OwnerAccount(str(root))
    token, _ = owner.claim_link()
    saved = backup(str(root), str(tmp_path / "backups"))["path"]
    context = multiprocessing.get_context("spawn")
    ready, release, results = context.Event(), context.Event(), context.Queue()
    process = context.Process(target=_pause_owner_claim,
                              args=(str(root), token, ready, release, results))
    process.start()
    try:
        assert ready.wait(15)
        with pytest.raises(BackupError):
            restore(str(root), saved)
        assert (state / "note.txt").read_text() == "original data"
        assert owner._load()["claim_completion"]
        assert not list(root.glob(".vortoone.before-*"))
    finally:
        release.set()
        process.join(20)
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert process.exitcode == 0 and results.get(timeout=2) == "claimed"
    assert "claim_completion" not in owner._load()


@pytest.mark.asyncio
async def test_startup_cannot_recreate_runtime_lock_during_restore_directory_gap(tmp_path):
    root = tmp_path / "home"
    state = _home(root)
    with ProcessLease(state / "runtime.lock"):
        pass
    saved = backup(str(root), str(tmp_path / "backups"))["path"]
    context = multiprocessing.get_context("spawn")
    ready, release, results = context.Event(), context.Event(), context.Queue()
    process = context.Process(target=_pause_restore_between_directory_renames,
                              args=(str(root), saved, ready, release, results))
    process.start()
    try:
        assert ready.wait(15), "Restore did not reach the swap boundary"
        assert not state.exists()             # 旧 inode 已移开，新的状态目录还没发布
        maintenance_inode = (root / MAINTENANCE_FILE).stat().st_ino
        with pytest.raises(HomeMaintenanceError):
            async with runtime_lifespan(root, **_runtime_options()):
                pytest.fail("Runtime started through the directory swap")
        assert not state.exists()             # 失败的启动没有建立一把新的 runtime.lock
    finally:
        release.set()
        process.join(20)
        if process.is_alive():
            process.terminate()
            process.join(5)
    assert process.exitcode == 0
    assert results.get(timeout=2) == "restored"
    assert (root / MAINTENANCE_FILE).stat().st_ino == maintenance_inode
    async with runtime_lifespan(root, **_runtime_options()):
        assert (state / "runtime.lock").is_file()


def test_maintenance_lock_symlink_is_rejected_without_changing_target(tmp_path):
    root = tmp_path / "home"
    _home(root)
    target = tmp_path / "not-a-lock"
    target.write_text("keep")
    (root / MAINTENANCE_FILE).symlink_to(target)
    with pytest.raises(BackupError):
        restore(str(root), "missing.tar.gz")
    assert target.read_text() == "keep"


def test_identity_hook_failure_keeps_backup_and_move_cli_json_contract(tmp_path, monkeypatch, capsys):
    import src.workflow.home_identity as identity_module
    from src.runtime_entry import run_home_backup, run_home_move
    root = tmp_path / "home"
    state = _home(root)
    saved = backup(str(root), str(tmp_path / "backups"))["path"]
    package = str(tmp_path / "move.tar.gz")
    export_home(str(root), package)
    def fail(*args):
        raise identity_module.HomeIdentityError("injected identity failure")
    monkeypatch.setattr(identity_module, "prepare_replaced_home", fail)
    monkeypatch.chdir(root)
    assert run_home_backup(SimpleNamespace(command="home-restore", archive=saved)) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert run_home_move(SimpleNamespace(command="home-import", package=package, replace=True)) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert (state / "note.txt").read_text() == "original data"
    assert not list(root.glob(".vortoone.before-*"))
