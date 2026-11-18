"""Verify the local file boundary before any network caller is connected to it."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import pytest

from src.gateway.computer_file_paths import FileDenied, PinnedFile
from src.gateway import computer_file_capacity
from src.gateway.computer_file_store import ComputerFiles, FileAction, FileStateUnavailable
from src.gateway.computer_protocol import BINDING_FIELDS
from src.utils.home_maintenance import FileLease


pytestmark = pytest.mark.skipif(os.name != "posix", reason="requires pinned POSIX directory handles")
BINDING = {field: format(index, "x") * 32 for index, field in enumerate(BINDING_FIELDS, 1)}
REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def paired(tmp_path):
    root = tmp_path.resolve()
    folder = root / "approved"
    folder.mkdir(mode=0o700)
    store = ComputerFiles(root / "private", BINDING)
    grant = store.grant(str(folder), "文稿", read=True, write=True)
    return store, grant, folder


def action(store, grant, operation, path, content=""):
    revision = store.policy()["policy_revision"]
    version = store.preview(operation, grant["folder_id"], path, revision)
    return FileAction(uuid.uuid4().hex, grant["folder_id"], revision,
                      operation, path, version, int(time.time()) + 60, "session", "turn", "call", content)


def test_default_closed_and_pairing_epochs_do_not_inherit_grants(paired):
    store, grant, folder = paired
    public = store.policy()
    assert public["folders"] == [grant]
    assert str(folder) not in json.dumps(public)
    new_pairing = ComputerFiles(store.directory, {**BINDING, "trust_epoch": "f" * 32})
    assert new_pairing.policy()["folders"] == []
    with pytest.raises(FileDenied):
        new_pairing.preview("create", grant["folder_id"], "new.txt", "0" * 32)


def test_reads_and_patches_pin_the_observed_content_without_disk_writes(paired):
    store, grant, folder = paired
    source = folder / "draft.txt"
    source.write_text("原稿\n")
    read = action(store, grant, "read", "draft.txt")
    assert store.execute(read)["text"] == "原稿\n"
    patch = action(store, grant, "patch", "draft.txt", "修订\n")
    output = store.execute(patch)
    assert output["status"] == "patch"
    assert "-原稿\n+修订\n" in output["text"]
    assert source.read_text() == "原稿\n"
    source.write_text("同时编辑\n")
    assert store.execute(read)["status"] == "blocked"
    assert store.execute(patch)["status"] == "blocked"
    assert source.read_text() == "同时编辑\n"


def test_exclusive_create_and_durable_dedup_survive_database_reopen(paired):
    store, grant, folder = paired
    initial = action(store, grant, "create", "new.txt", "唯一一次")
    result = store.execute(initial)
    assert result["status"] == "created"
    dest = folder / "new.txt"
    assert dest.stat().st_mode & 0o777 == 0o600
    assert dest.read_text() == "唯一一次"
    restarted = ComputerFiles(store.directory, BINDING)
    assert restarted.pending() == [result]
    assert not restarted.acknowledge(initial.action_id, "0" * 64)
    assert restarted.acknowledge(initial.action_id, initial.digest)
    assert not restarted.pending()
    dest.unlink()
    assert restarted.execute(initial) == result
    assert restarted.execute(replace(initial, content="different"))["status"] == "blocked"
    assert not dest.exists()


def test_name_taken_after_approval_is_never_overwritten(paired):
    store, grant, folder = paired
    approved = action(store, grant, "create", "new.txt", "approved")
    (folder / "new.txt").write_text("other writer")
    assert store.execute(approved)["status"] == "blocked"
    assert (folder / "new.txt").read_text() == "other writer"
    assert [item.name for item in folder.iterdir()] == ["new.txt"]


def test_failed_directory_check_after_publication_reports_unknown(paired, monkeypatch):
    store, grant, folder = paired
    approved = action(store, grant, "create", "new.txt", "one effect")
    check = PinnedFile._check_parent

    def moved_after_publication(target, chain):
        if (folder / "new.txt").exists():
            raise FileDenied("directory moved at publication")
        check(target, chain)

    monkeypatch.setattr(PinnedFile, "_check_parent", moved_after_publication)
    assert store.execute(approved)["status"] == "unknown"
    assert (folder / "new.txt").read_text() == "one effect"
    assert store.pending()[0]["status"] == "unknown"


def test_deadline_passed_while_staging_blocks_publication_and_preserves_receipt(paired, monkeypatch):
    store, grant, folder = paired
    approved = action(store, grant, "create", "new.txt", "late")
    check_parent = PinnedFile._check_parent

    def expire_at_publication(target, chain):
        check_parent(target, chain)
        store.clock = lambda: approved.deadline

    monkeypatch.setattr(PinnedFile, "_check_parent", expire_at_publication)
    result = store.execute(approved)
    assert result["status"] == "blocked"
    assert not list(folder.iterdir())
    assert store.pending() == [result]


def test_read_or_create_capabilities_are_independent_and_revocation_fences_queued_action(paired):
    store, grant, folder = paired
    approved = action(store, grant, "create", "new.txt")
    store.revoke(grant["folder_id"])
    assert store.execute(approved)["status"] == "blocked"
    only_read = store.grant(str(folder), "文稿", read=True, write=False)
    with pytest.raises(FileDenied):
        action(store, only_read, "create", "new.txt")
    store.revoke(only_read["folder_id"])
    only_write = store.grant(str(folder), "文稿", read=False, write=True)
    (folder / "new.txt").write_text("does not reveal contents")
    for operation in ("read", "patch"):
        with pytest.raises(FileDenied):
            action(store, only_write, operation, "new.txt")


@pytest.mark.parametrize("path", [
    "../outside.txt", "/etc/hosts", "./draft.txt", "a//draft.txt", "a\\draft.txt",
    ".git/config", "server.env", "owner-key", "secret.pem", "a/../a.txt",
])
def test_paths_outside_scope_or_secret_names_are_refused(paired, path):
    store, grant, _ = paired
    with pytest.raises(FileDenied):
        action(store, grant, "read", path)


def test_symlinks_hardlinks_and_fifo_cannot_turn_a_file_grant_into_other_access(paired):
    store, grant, folder = paired
    outside = folder.parent / "outside"
    outside.mkdir()
    source = outside / "outside.txt"
    source.write_text("private")
    (folder / "link").symlink_to(outside, target_is_directory=True)
    (folder / "soft.txt").symlink_to(source)
    os.link(source, folder / "hard.txt")
    os.mkfifo(folder / "pipe")
    for name in ("link/outside.txt", "soft.txt", "hard.txt", "pipe"):
        with pytest.raises(FileDenied):
            action(store, grant, "read", name)
    with pytest.raises(FileDenied):
        action(store, grant, "create", "link/new.txt")


def test_replacement_parent_or_selected_directory_rejects_prepared_action(paired):
    store, grant, folder = paired
    sub = folder / "sub"
    sub.mkdir(mode=0o700)
    (sub / "draft.txt").write_text("first parent")
    approved = action(store, grant, "read", "sub/draft.txt")
    with pytest.raises(FileDenied):
        action(store, grant, "create", "sub/new.txt")
    sub.rename(folder / "old")
    sub.mkdir(mode=0o700)
    (sub / "draft.txt").write_text("replacement parent")
    assert store.execute(approved)["status"] == "blocked"
    new = action(store, grant, "create", "new.txt")
    aside = folder.rename(folder.parent / "moved")
    folder.mkdir(mode=0o700)
    assert store.execute(new)["status"] != "created"
    assert not (folder / "new.txt").exists()
    assert not (aside / "new.txt").exists()


def test_single_flight_and_expired_unstarted_actions_fail_closed(paired):
    store, grant, folder = paired
    approved = action(store, grant, "create", "new.txt")
    with FileLease(store.directory / "file-action.lock", private=True):
        assert store.execute(approved)["status"] == "busy"
    assert store.pending() == []
    expired = replace(approved, deadline=int(time.time()) - 1)
    rejected = store.execute(expired)
    assert rejected["status"] == "blocked"
    assert not (folder / "new.txt").exists()
    assert store.pending() == [rejected]


def test_low_memory_retains_unstarted_identity_and_rechecks_on_retry(paired, monkeypatch):
    store, grant, folder = paired
    approved = action(store, grant, "create", "new.txt", "本次批准的内容")
    threshold = computer_file_capacity.SYSTEM_RESERVE + computer_file_capacity.FILE_RESERVATION["create"]
    memory = [threshold - 1]
    monkeypatch.setattr(computer_file_capacity, "available_memory", lambda: memory[0])
    assert store.execute(approved)["status"] == "busy"
    assert store.pending() == []
    assert not (folder / "new.txt").exists()

    memory[0] = threshold
    restarted = ComputerFiles(store.directory, BINDING)
    assert restarted.execute(replace(approved, content="其它文本"))["status"] == "blocked"
    assert not (folder / "new.txt").exists()
    result = restarted.execute(approved)
    assert result["status"] == "created"
    assert (folder / "new.txt").read_text() == approved.content

    memory[0] = 0
    assert restarted.execute(approved) == result
    assert restarted.pending() == [result]


def test_missing_memory_measurement_still_uses_the_exclusive_slot(paired, monkeypatch):
    store, grant, folder = paired
    monkeypatch.setattr(computer_file_capacity, "available_memory", lambda: None)
    approved = action(store, grant, "create", "new.txt", "only one slot")
    with FileLease(store.directory / "file-action.lock", private=True):
        assert store.execute(approved)["status"] == "busy"
    assert not (folder / "new.txt").exists()
    assert store.execute(approved)["status"] == "created"


def test_actual_process_death_after_effect_retains_unknown_and_never_replays(paired):
    store, grant, folder = paired
    approved = action(store, grant, "create", "new.txt", "saved before process died")
    from dataclasses import asdict
    program = """
import json, os, sys
from src.gateway.computer_file_store import ComputerFiles, FileAction
directory, binding, values = json.loads(sys.stdin.read())
store = ComputerFiles(directory, binding)
store._terminal = lambda *args: os._exit(89)
store.execute(FileAction(**values))
"""
    result = subprocess.run([sys.executable, "-c", program],
                            input=json.dumps([str(store.directory), BINDING, asdict(approved)]),
                            text=True, capture_output=True, cwd=REPO, timeout=10)
    assert result.returncode == 89, result.stderr
    dest = folder / "new.txt"
    assert dest.read_text() == "saved before process died"
    dest.unlink()
    restarted = ComputerFiles(store.directory, BINDING)
    assert restarted.pending()[0]["status"] == "unknown"
    assert restarted.execute(approved)["status"] == "unknown"
    assert not dest.exists()


def test_private_database_path_does_not_follow_link(paired):
    store, _, folder = paired
    database = store.directory / "file-actions.sqlite3"
    database.unlink()
    target = folder / "outside.sqlite3"
    target.write_text("user data")
    database.symlink_to(target)
    with pytest.raises(FileStateUnavailable):
        store.policy()
    assert target.read_text() == "user data"


@pytest.mark.parametrize("entry", ["src.runtime_entry", "desktop.runtime_entry"])
def test_settings_pipe_can_store_and_revoke_but_cannot_execute(entry, tmp_path):
    root = tmp_path.resolve()
    folder = root / "documents"
    folder.mkdir(mode=0o700)
    data = root / "private"
    data.mkdir(mode=0o700)
    env = {**os.environ, "PYTHONPATH": str(REPO)}

    def invoke(operation, **fields):
        args = {"binding": BINDING, "operation": operation, **fields}
        result = subprocess.run([sys.executable, "-m", entry, "computer-folder-settings"],
                                input=json.dumps(args) + "\n", text=True, capture_output=True,
                                cwd=data, env=env, timeout=10)
        return json.loads(result.stdout)

    assert invoke("list")["folders"] == []
    info = folder.stat()
    args = {"directory": str(folder), "alias": "文稿", "read": True, "write": False}
    assert not invoke("grant", **args, directory_identity=["0", "0"])["ok"]
    saved = invoke("grant", **args, directory_identity=[str(info.st_dev), str(info.st_ino)])
    assert len(saved["folders"]) == 1
    assert saved["folders"][0]["directory"] == str(folder)
    folder_id = saved["folders"][0]["folder_id"]
    assert not invoke("create", folder_id=folder_id, path="do-not-create.txt")["ok"]
    assert invoke("revoke", folder_id=folder_id)["folders"] == []
    assert not list(folder.iterdir())
    assert not (data / ".vortoone").exists()


def test_verified_delivery_never_publishes_wrong_bytes_and_never_replays(paired):
    from dataclasses import asdict
    import hashlib
    import tempfile

    from src.gateway.computer_file_store import FileDelivery

    store, grant, folder = paired
    data = b"binary\0data" * 8192
    create = action(store, grant, "create", "delivered.bin")
    delivery = FileDelivery(**asdict(create), size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                            source_version="a" * 64)
    destination = folder / create.path
    with tempfile.TemporaryFile(dir=store.directory) as source:
        source.write(b"x" * len(data))
        source.flush()
        with pytest.raises(FileDenied):
            store.execute_delivery(delivery, source.fileno(), active=lambda: True)
        assert not list(folder.iterdir()) and not store.pending()

        source.seek(0)
        source.write(data)
        source.flush()
        receipt = store.execute_delivery(delivery, source.fileno(), active=lambda: True)
        assert receipt["status"] == "created" and destination.read_bytes() == data
        inode = destination.stat().st_ino
        assert store.execute_delivery(delivery, source.fileno(), active=lambda: True) == receipt
        assert destination.stat().st_ino == inode
        with pytest.raises(FileDenied):
            store.execute_delivery(replace(delivery, sha256="0" * 64), source.fileno(), active=lambda: True)
        assert list(folder.iterdir()) == [destination]
