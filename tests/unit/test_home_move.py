"""搬家：导出带上该带的、留下凭据和绑在这台电脑上的；导入只进空的家，包里每一项都核对；搬走的家停用。"""
import io
import json
import tarfile
from pathlib import Path

import pytest

from src.workflow import home_move as M


def _home(root: Path) -> Path:
    state = root / ".vortoone"
    for relative, text in {
        "sessions.db": "chat-db", "web_sessions/sid-1.json": '{"transcript": ["你好"]}',
        "staff/members.json": "{}", "members/accounts.json": '{"pw": "scrypt-hash"}', "audit.log": "a\n",
        "tasks/task-1.json": "{}", "memory/facts.json": "[]",
        # 不带的
        "mobile-devices/keys.json": "device-secret", "ai_apps/state.json": "tokens", "im-bots/telegram.json": "bot",
        "runtime.lock": "", "worktrees/team-x/file": "x", "logs/server.log": "log", "channel.lock": "",
        "computer-job-intents/intents.sqlite3": "previous fixture receipt",
    }.items():
        path = state / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (state / "staff" / "link").symlink_to("/etc/passwd")
    return state


def _names(package: Path):
    with tarfile.open(package, "r:gz") as archive:
        return sorted(archive.getnames())


def test_the_package_carries_the_home_and_leaves_secrets_behind(tmp_path):
    _home(tmp_path / "old")
    result = M.export_home(str(tmp_path / "old"), str(tmp_path / "move.tar.gz"))
    names = _names(tmp_path / "move.tar.gz")
    assert "manifest.json" in names and ".vortoone/web_sessions/sid-1.json" in names
    assert ".vortoone/members/accounts.json" in names and ".vortoone/sessions.db" in names
    for left in ("mobile-devices", "ai_apps", "im-bots", "runtime.lock", "worktrees", "logs", "channel.lock", "link",
                 "computer-job-intents"):
        assert not any(left in name for name in names), left
    assert {"mobile-devices", "ai_apps", "im-bots", "runtime.lock", "worktrees", "logs",
            "computer-job-intents"} <= set(result["left_behind"])
    assert M.moved(str(tmp_path / "old")) is None            # 只导出不搬走：旧的家照常


def test_moving_out_stops_the_old_home_and_keeps_its_data(tmp_path):
    from src.runtime_entry import refuse_if_moved
    state = _home(tmp_path / "old")
    M.export_home(str(tmp_path / "old"), str(tmp_path / "move.tar.gz"), moving=True)
    assert M.moved(str(tmp_path / "old"))["package"].endswith("move.tar.gz")
    assert (state / "sessions.db").read_text() == "chat-db"         # 原数据留着
    with pytest.raises(SystemExit) as stopped:
        refuse_if_moved(str(tmp_path / "old"))
    assert stopped.value.code == 3
    with pytest.raises(M.MoveError):
        M.export_home(str(tmp_path / "old"), str(tmp_path / "again.tar.gz"))


def test_a_round_trip_into_an_empty_home(tmp_path):
    _home(tmp_path / "old")
    M.export_home(str(tmp_path / "old"), str(tmp_path / "move.tar.gz"), moving=True)
    new = tmp_path / "new" / ".vortoone"
    (new / "computer-job-intents").mkdir(parents=True)
    (new / "computer-job-intents/intents.sqlite3").write_text("stale receipt")
    (new / "runtime.lock").write_text("")
    result = M.import_home(str(tmp_path / "new"), str(tmp_path / "move.tar.gz"))
    assert result["replaced"] is False and result["files"] >= 7
    assert json.loads((new / "web_sessions" / "sid-1.json").read_text())["transcript"] == ["你好"]
    assert (new / "members" / "accounts.json").read_text() == '{"pw": "scrypt-hash"}'
    assert not (new / "mobile-devices").exists() and not (new / M.MARKER).exists()
    assert not (new / "computer-job-intents").exists()
    assert (Path(result["kept_aside"]) / "computer-job-intents/intents.sqlite3").read_text() == "stale receipt"
    assert (new / "runtime.lock").exists()                                    # 新家自己的锁还在
    assert M.moved(str(tmp_path / "new")) is None


def test_a_home_with_data_needs_an_explicit_replace(tmp_path):
    _home(tmp_path / "old")
    M.export_home(str(tmp_path / "old"), str(tmp_path / "move.tar.gz"))
    existing = tmp_path / "new" / ".vortoone"
    existing.mkdir(parents=True)
    (existing / "sessions.db").write_text("newer chats")
    with pytest.raises(M.MoveError, match="已经有数据"):
        M.import_home(str(tmp_path / "new"), str(tmp_path / "move.tar.gz"))
    assert (existing / "sessions.db").read_text() == "newer chats"
    result = M.import_home(str(tmp_path / "new"), str(tmp_path / "move.tar.gz"), replace=True)
    assert (existing / "sessions.db").read_text() == "chat-db"
    assert (Path(result["kept_aside"]) / "sessions.db").read_text() == "newer chats"      # 原来的挪到旁边留着


def _package(path: Path, members, manifest=None):
    with tarfile.open(path, "w:gz") as archive:
        data = json.dumps(manifest or {"format": M.FORMAT, "version": M.VERSION}).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
        for name, kind in members:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = 2
                archive.addfile(info, io.BytesIO(b"hi"))
            elif kind == "symlink":
                info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
                archive.addfile(info)
            elif kind == "device":
                info.type = tarfile.CHRTYPE
                archive.addfile(info)
    return path


@pytest.mark.parametrize("name, kind", [
    ("../escape.txt", "file"), ("/etc/evil", "file"), (".vortoone/../../escape", "file"),
    ("elsewhere/file", "file"), (".vortoone/link", "symlink"), (".vortoone/dev", "device")])
def test_a_package_cannot_write_outside_or_smuggle_links(tmp_path, name, kind):
    package = _package(tmp_path / "bad.tar.gz", [(name, kind)])
    with pytest.raises(M.MoveError):
        M.import_home(str(tmp_path / "new"), str(package))
    assert not (tmp_path / "escape.txt").exists() and not (tmp_path / "new" / ".vortoone").exists()


def test_things_that_belong_to_the_old_computer_are_not_unpacked(tmp_path):
    package = _package(tmp_path / "p.tar.gz", [(".vortoone/mobile-devices/keys.json", "file"),
                                               (".vortoone/moved.json", "file"), (".vortoone/audit.log", "file"),
                                               (".vortoone/computer-job-intents/intents.sqlite3", "file")])
    M.import_home(str(tmp_path / "new"), str(package))
    new = tmp_path / "new" / ".vortoone"
    assert (new / "audit.log").exists() and not (new / "mobile-devices").exists() and not (new / "moved.json").exists()
    assert not (new / "computer-job-intents").exists()


def test_other_files_and_versions_are_refused(tmp_path):
    (tmp_path / "x.tar.gz").write_bytes(b"not a tarball")
    with pytest.raises(M.MoveError, match="打不开"):
        M.import_home(str(tmp_path / "new"), str(tmp_path / "x.tar.gz"))
    with pytest.raises(M.MoveError, match="不是 VortoOne"):
        M.import_home(str(tmp_path / "new"), str(_package(tmp_path / "a.tar.gz", [], {"format": "other"})))
    with pytest.raises(M.MoveError, match="版本"):
        M.import_home(str(tmp_path / "new"), str(_package(tmp_path / "b.tar.gz", [], {"format": M.FORMAT, "version": 99})))


def test_export_never_overwrites_and_needs_a_home(tmp_path):
    with pytest.raises(M.MoveError, match="还没有"):
        M.export_home(str(tmp_path / "nothing"), str(tmp_path / "x.tar.gz"))
    _home(tmp_path / "old")
    (tmp_path / "taken.tar.gz").write_text("mine")
    with pytest.raises(M.MoveError, match="已经存在"):
        M.export_home(str(tmp_path / "old"), str(tmp_path / "taken.tar.gz"))
    assert (tmp_path / "taken.tar.gz").read_text() == "mine"


def test_the_runtime_commands_answer_in_one_json_line(tmp_path, monkeypatch, capsys):
    from src.runtime_entry import main
    _home(tmp_path / "old")
    monkeypatch.chdir(tmp_path / "old")
    assert main(["home-export", str(tmp_path / "move.tar.gz"), "--move"]) == 0
    answer = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert answer["ok"] is True and answer["moving"] is True
    (tmp_path / "new").mkdir()
    monkeypatch.chdir(tmp_path / "new")
    assert main(["home-import", str(tmp_path / "move.tar.gz")]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["ok"] is True
    assert main(["home-import", str(tmp_path / "move.tar.gz")]) == 2                 # 已有数据又没说替换
    assert "已经有数据" in json.loads(capsys.readouterr().out.strip().splitlines()[-1])["error"]
