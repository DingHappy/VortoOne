"""Durable home dispatch intents and receipts. File contents stay out of this index."""
from __future__ import annotations

from contextlib import closing, contextmanager
import json
import os
import sqlite3
import stat
import time

from src.gateway.computer_file_store import ACTION_SECONDS, FileAction
from src.gateway.computer_file_wire import receipt_value
from src.gateway.computer_protocol import binding_fields, identifier
from src.utils.state_dir import ensure_state_gitignore, state_dir

MAX_INTENTS = 10_000


class FileIntentUnavailable(RuntimeError):
    pass


class HomeFileIntents:
    def __init__(self, root: str):
        self.root = root

    @contextmanager
    def _db(self):
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
            raise FileIntentUnavailable("此系统尚未支持持久派发")
        try:
            directory = state_dir(self.root) / "computer-file-intents"
            if directory.parent.is_symlink():
                raise FileIntentUnavailable("无法核对家的派发记录")
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = directory.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077):
                raise FileIntentUnavailable("派发目录权限不正确")
            path = directory / "intents.sqlite3"
            descriptor = os.open(path, os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            try:
                info = os.fstat(descriptor)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid != os.geteuid() or info.st_mode & 0o077):
                    raise FileIntentUnavailable("派发记录权限不正确")
            finally:
                os.close(descriptor)
            with closing(sqlite3.connect(path, timeout=1)) as db:
                db.execute("PRAGMA synchronous=FULL")
                db.execute("CREATE TABLE IF NOT EXISTS intents (id TEXT PRIMARY KEY, binding TEXT NOT NULL,"
                           " digest TEXT NOT NULL, session TEXT NOT NULL, turn TEXT NOT NULL, call TEXT NOT NULL,"
                           " deadline INTEGER NOT NULL, status TEXT NOT NULL)")
                db.execute("BEGIN IMMEDIATE")
                with db:
                    yield db
        except (sqlite3.Error, OSError) as error:
            raise FileIntentUnavailable("家的派发记录暂时不可用") from error

    @staticmethod
    def _scope(binding: dict) -> str:
        return json.dumps(binding_fields(binding), sort_keys=True, separators=(",", ":"))

    def record(self, binding: dict, action: FileAction) -> None:
        if action.operation != "create" or not 0 < action.deadline - time.time() <= ACTION_SECONDS:
            raise FileIntentUnavailable("新建派发已过期或无效")
        with self._db() as db:
            ensure_state_gitignore(self.root)
            if db.execute("SELECT count(*) FROM intents").fetchone()[0] >= MAX_INTENTS:
                raise FileIntentUnavailable("家的派发记录已达上限")
            db.execute("INSERT INTO intents VALUES (?, ?, ?, ?, ?, ?, ?, 'dispatched')",
                       (action.action_id, self._scope(binding), action.digest,
                        action.session_id, action.turn_id, action.call_id, action.deadline))

    def accept(self, binding: dict, receipt: dict) -> bool:
        receipt_value(receipt)
        with self._db() as db:
            row = db.execute("SELECT binding, digest, status FROM intents WHERE id = ?",
                             (receipt["action_id"],)).fetchone()
            if row is None or row[:2] != (self._scope(binding), receipt["digest"]):
                return False
            status = receipt["status"]
            if row[2] not in ("dispatched", "waiting", status):
                return False
            if row[2] != status:
                db.execute("UPDATE intents SET status = ? WHERE id = ?", (status, receipt["action_id"]))
            return True

    def set_waiting(self, binding: dict, action: FileAction, *, waiting: bool) -> bool:
        """Fence every retry before sending. Terminal receipts cannot be reset."""
        before, after = ("dispatched", "waiting") if waiting else ("waiting", "dispatched")
        with self._db() as db:
            if not waiting and action.deadline <= time.time():
                return False
            updated = db.execute("UPDATE intents SET status = ? WHERE id = ? AND binding = ? "
                                 "AND digest = ? AND status = ?",
                                 (after, action.action_id, self._scope(binding), action.digest, before))
            return updated.rowcount == 1

    def status(self, action_id: str, identity: dict) -> str | None:
        identifier(action_id)
        with self._db() as db:
            row = db.execute("SELECT binding, status FROM intents WHERE id = ?", (action_id,)).fetchone()
            if row:
                binding = json.loads(row[0])
                if (binding["home_instance_id"], binding["paired_principal_id"], binding["trust_epoch"]) == (
                        identity["home_instance_id"], identity["owner_subject"], identity["trust_epoch"]):
                    return "unknown" if row[1] == "dispatched" else row[1]
        return None
