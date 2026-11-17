"""家的备份与恢复：数据库拿一致的快照、凭据也带上、只留最新几份；恢复时原来的挪到旁边，包里每一项都核对。"""
import io
import json
import os
import sqlite3
import tarfile
from pathlib import Path

import pytest

from src.workflow import home_backup as B


def _home(root: Path) -> Path:
    state = root / ".vortoone"
    for relative, text in {"web_sessions/sid-1.json": '{"transcript": ["你好"]}', "members/accounts.json": "{}",
                           "mobile-devices/keys.json": "device-key", "runtime.lock": "", "logs/server.log": "x",
                           "worktrees/team-1/file": "x", "channel.lock": ""}.items():
        path = state / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    db = sqlite3.connect(state / "sessions.db")
    db.execute("create table chats (body text)")
    db.execute("insert into chats values ('第一句')")
    db.commit()
    db.close()
    return state


def _names(path):
    with tarfile.open(path, "r:gz") as archive:
        return set(archive.getnames())


def test_a_backup_carries_data_and_credentials_but_not_locks_or_worktrees(tmp_path):
    state = _home(tmp_path)
    live = sqlite3.connect(state / "sessions.db")             # 服务开着：数据库正被连着
    live.execute("insert into chats values ('还没关的连接写的')")
    live.commit()
    result = B.backup(str(tmp_path), str(tmp_path / "backups"))
    live.close()
    names = _names(result["path"])
    assert {".vortoone/web_sessions/sid-1.json", ".vortoone/mobile-devices/keys.json", ".vortoone/sessions.db"} <= names
    assert not any(part in name for name in names for part in ("runtime.lock", "logs/", "worktrees/", "channel.lock"))
    assert oct(Path(result["path"]).stat().st_mode & 0o777) == "0o600"
    assert oct((tmp_path / "backups").stat().st_mode & 0o777) == "0o700"


def test_only_the_newest_are_kept(tmp_path, monkeypatch):
    _home(tmp_path)
    stamps = iter(f"20261117T0000{n:02d}Z" for n in range(10))
    monkeypatch.setattr(B.time, "strftime", lambda *_: next(stamps))
    for _ in range(5):
        B.backup(str(tmp_path), str(tmp_path / "backups"), keep=3)
    kept = B.backups(str(tmp_path / "backups"))
    assert [p.name[len(B.PREFIX):len(B.PREFIX) + 16] for p in kept] == [
        "20261117T000004Z", "20261117T000003Z", "20261117T000002Z"]


def test_restore_brings_the_home_back_and_keeps_what_was_there(tmp_path):
    state = _home(tmp_path)
    saved = B.backup(str(tmp_path), str(tmp_path / "backups"), reason="upgrade")["path"]
    (state / "web_sessions" / "sid-1.json").write_text('{"transcript": ["升级后改坏了"]}')
    db = sqlite3.connect(state / "sessions.db")
    db.execute("delete from chats")
    db.commit()
    db.close()
    result = B.restore(str(tmp_path), saved)
    assert json.loads((state / "web_sessions" / "sid-1.json").read_text())["transcript"] == ["你好"]
    rows = sqlite3.connect(state / "sessions.db").execute("select body from chats").fetchall()
    assert rows == [("第一句",)]
    aside = Path(result["kept_aside"])
    assert "升级后改坏了" in (aside / "web_sessions" / "sid-1.json").read_text()      # 原来的留着
    assert (state / "logs" / "server.log").read_text() == "x"                          # 日志和工作副本接着用
    assert (state / "worktrees" / "team-1" / "file").exists() and not (state / "runtime.lock").exists()
    assert state.stat().st_mode & 0o777 == 0o700
    assert (state / "mobile-devices").stat().st_mode & 0o777 == 0o700
    assert (state / "mobile-devices" / "keys.json").stat().st_mode & 0o777 == 0o600


def test_online_backup_keeps_a_complete_owner_file_during_atomic_update(tmp_path, monkeypatch):
    from src.workflow.members.owner_account import OwnerAccount

    owner = OwnerAccount(str(tmp_path))
    before = {"name": "Ding", "email": "me@example.com", "password": {"hash": "old"}}
    after = {**before, "name": "A much longer updated owner name", "password": {"hash": "x" * 128}}
    owner._save(before)
    gettarinfo = tarfile.TarFile.gettarinfo
    updated = False

    def update_after_metadata(archive, name=None, arcname=None, fileobj=None):
        nonlocal updated
        info = gettarinfo(archive, name=name, arcname=arcname, fileobj=fileobj)
        if arcname == ".vortoone/members/owner.json" and not updated:
            owner._save(after)       # 真正的落盘路径：临时文件写完后原子替换
            updated = True
        return info

    monkeypatch.setattr(tarfile.TarFile, "gettarinfo", update_after_metadata)
    saved = B.backup(str(tmp_path), str(tmp_path / "backups"))["path"]
    assert updated and owner._load() == after
    B.restore(str(tmp_path), saved)
    assert owner._load() == before


@pytest.mark.parametrize("relative", ["web_sessions/sid.json.tmp", "members/.owner.json.123.tmp",
                                      "personal-skills/example/.pending-123", "state.json.part"])
def test_online_backup_excludes_unfinished_atomic_writes(tmp_path, relative):
    _home(tmp_path)
    pending = tmp_path / ".vortoone" / relative
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text('{"unfinished":')
    saved = B.backup(str(tmp_path), str(tmp_path / "backups"))["path"]
    assert f".vortoone/{relative}" not in _names(saved)
    B.restore(str(tmp_path), saved)
    assert not pending.exists()
    assert (tmp_path / ".vortoone" / "mobile-devices" / "keys.json").read_text() == "device-key"


def test_manifest_only_backup_restores_an_empty_home_and_keeps_old_state(tmp_path):
    state = tmp_path / ".vortoone"
    state.mkdir()
    (state / "runtime.lock").write_text("old lock")
    saved = B.backup(str(tmp_path), str(tmp_path / "backups"))
    assert saved["files"] == 0
    result = B.restore(str(tmp_path), saved["path"])
    assert state.is_dir() and list(state.iterdir()) == []
    assert state.stat().st_mode & 0o777 == 0o700
    assert (Path(result["kept_aside"]) / "runtime.lock").read_text() == "old lock"


def test_backup_of_hardlinked_files_can_restore_without_archive_links(tmp_path):
    state = _home(tmp_path)
    source = state / "memory.txt"
    source.write_text("keep both files")
    os.link(source, state / "memory-copy.txt")
    saved = B.backup(str(tmp_path), str(tmp_path / "backups"))["path"]
    with tarfile.open(saved, "r:gz") as archive:
        assert all(member.isfile() for member in archive.getmembers())
    B.restore(str(tmp_path), saved)
    assert source.read_text() == (state / "memory-copy.txt").read_text() == "keep both files"


def test_restore_puts_original_state_back_if_installing_staging_fails(tmp_path, monkeypatch):
    state = _home(tmp_path)
    saved = B.backup(str(tmp_path), str(tmp_path / "backups"))["path"]
    source = state / "web_sessions" / "sid-1.json"
    source.write_text("newer data")
    rename = Path.rename

    def fail_staging_install(path, target):
        if path.name.startswith(".vortoone.restoring-") and Path(target) == state:
            raise OSError("cannot install restored directory")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_staging_install)
    with pytest.raises(OSError, match="cannot install"):
        B.restore(str(tmp_path), saved)
    assert source.read_text() == "newer data"
    assert not list(tmp_path.glob(".vortoone.restoring-*"))


def test_online_sqlite_backup_contains_committed_wal_rows(tmp_path):
    state = _home(tmp_path)
    with sqlite3.connect(state / "sessions.db") as live:
        assert live.execute("pragma journal_mode=wal").fetchone()[0] == "wal"
        live.execute("insert into chats values ('committed in WAL')")
        live.commit()
        saved = B.backup(str(tmp_path), str(tmp_path / "backups"))["path"]
        with tarfile.open(saved, "r:gz") as archive:
            snapshot = archive.extractfile(".vortoone/sessions.db").read()
            assert not any(name.endswith(("-wal", "-shm")) for name in archive.getnames())
    copied = tmp_path / "snapshot.db"
    copied.write_bytes(snapshot)
    with sqlite3.connect(copied) as database:
        assert database.execute("select body from chats").fetchall() == [("第一句",), ("committed in WAL",)]


def _package(path, name, kind="file"):
    with tarfile.open(path, "w:gz") as archive:
        data = json.dumps({"format": B.FORMAT, "version": B.VERSION}).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
        info = tarfile.TarInfo(name)
        if kind == "symlink":
            info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
            archive.addfile(info)
        else:
            info.size = 2
            archive.addfile(info, io.BytesIO(b"hi"))
    return path


@pytest.mark.parametrize("name, kind", [("../escape", "file"), ("/etc/evil", "file"), ("other/x", "file"),
                                        (".vortoone/link", "symlink")])
def test_a_backup_cannot_write_outside_or_smuggle_links(tmp_path, name, kind):
    state = _home(tmp_path)
    with pytest.raises(B.BackupError):
        B.restore(str(tmp_path), str(_package(tmp_path / "bad.tar.gz", name, kind)))
    assert (state / "web_sessions" / "sid-1.json").exists() and not (tmp_path / "escape").exists()


def test_other_files_are_refused(tmp_path):
    _home(tmp_path)
    (tmp_path / "x.tar.gz").write_bytes(b"nope")
    with pytest.raises(B.BackupError, match="打不开"):
        B.restore(str(tmp_path), str(tmp_path / "x.tar.gz"))
    with pytest.raises(B.BackupError, match="还没有"):
        B.backup(str(tmp_path / "empty"), str(tmp_path / "b"))


def test_the_commands_answer_in_one_json_line(tmp_path, monkeypatch, capsys):
    from src.runtime_entry import main
    _home(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["home-backup", "--dir", str(tmp_path / "backups"), "--reason", "manual"]) == 0
    made = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert made["ok"] and made["reason"] == "manual"
    assert main(["home-restore", made["path"]]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["ok"] is True
