"""Local folder grants and durable single-flight small-file actions."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import uuid

from src.gateway.computer_file_paths import (
    FileDenied, PinnedFile, relative_parts, select_directory, text_bytes,
)
from src.gateway.computer_file_capacity import has_file_capacity
from src.gateway.computer_protocol import binding_fields, identifier
from src.utils.home_maintenance import FileLease, HomeMaintenanceError

MAX_GRANTS = 16
MAX_ACTIONS = 10_000
ACTION_SECONDS = 120
_VERSION = re.compile(r"(?:absent:)?[a-f0-9]{64}\Z")
_CALL_ID = re.compile(r"[A-Za-z0-9_.:-]{1,120}\Z")
_OPERATIONS = frozenset({"read", "create", "patch"})
_STATUSES = frozenset({"created", "blocked", "unknown"})


class FileStateUnavailable(RuntimeError):
    """Local authorization or durable state cannot be proved."""


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True)
class FileAction:
    action_id: str
    folder_id: str
    policy_revision: str
    operation: str
    path: str
    version: str
    deadline: int
    session_id: str
    turn_id: str
    call_id: str
    content: str = ""

    def __post_init__(self):
        for value in (self.action_id, self.folder_id, self.policy_revision):
            identifier(value)
        if (not isinstance(self.operation, str) or self.operation not in _OPERATIONS
                or not isinstance(self.version, str) or not _VERSION.fullmatch(self.version)
                or self.version.startswith("absent:") != (self.operation == "create")
                or type(self.deadline) is not int or not 0 < self.deadline < 2 ** 53
                or not all(isinstance(value, str) and _CALL_ID.fullmatch(value)
                           for value in (self.session_id, self.turn_id, self.call_id))
                or (self.operation == "read" and self.content != "")):
            raise FileDenied("文件动作参数无效")
        relative_parts(self.path)
        text_bytes(self.content)

    @property
    def digest(self) -> str:
        return hashlib.sha256(_json(asdict(self)).encode("ascii")).hexdigest()


@dataclass(frozen=True)
class FolderGrant:
    folder_id: str
    alias: str
    directory: str
    identity: tuple[tuple[str, str], ...]
    read: bool
    write: bool

    def public(self) -> dict:
        return {"folder_id": self.folder_id, "alias": self.alias, "read": self.read, "write": self.write}


class ComputerFiles:
    """The native host owns the state directory and the full authenticated pairing binding."""
    def __init__(self, directory, binding: dict, *, clock=time.time):
        self.directory = Path(directory)
        self.scope = _json(binding_fields(binding))
        self.clock = clock

    def _private_directory(self):
        if os.name != "posix":
            raise FileStateUnavailable("此系统尚未支持本机文件授权")
        self.directory.mkdir(mode=0o700, exist_ok=True)
        directory = self.directory.lstat()
        if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.geteuid()
                or directory.st_mode & 0o077):
            raise FileStateUnavailable("本机文件授权目录权限不正确")

    @contextmanager
    def _db(self, *, write=False):
        connection = None
        try:
            self._private_directory()
            path = self.directory / "file-actions.sqlite3"
            fd = os.open(path, os.O_CREAT | os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid != os.geteuid() or info.st_mode & 0o077):
                    raise FileStateUnavailable("本机文件授权存储权限不正确")
            finally:
                os.close(fd)
            connection = sqlite3.connect(path, timeout=1)
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS revisions (
                    scope TEXT PRIMARY KEY, revision TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS folders (
                    scope TEXT NOT NULL, folder_id TEXT NOT NULL,
                    grant_json TEXT NOT NULL, PRIMARY KEY (scope, folder_id));
                CREATE TABLE IF NOT EXISTS actions (
                    scope TEXT NOT NULL, action_id TEXT NOT NULL, digest TEXT NOT NULL,
                    status TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (scope, action_id));
            """)
            if write:
                connection.execute("BEGIN IMMEDIATE")
            else:
                connection.execute("BEGIN")
            with connection:
                yield connection
        except (OSError, sqlite3.Error) as error:
            raise FileStateUnavailable("本机文件授权存储暂时不可用") from error
        finally:
            if connection is not None:
                connection.close()

    def _revision(self, db) -> str:
        row = db.execute("SELECT revision FROM revisions WHERE scope = ?", (self.scope,)).fetchone()
        return identifier(row[0]) if row else "0" * 32

    def _change_revision(self, db):
        db.execute("INSERT OR REPLACE INTO revisions VALUES (?, ?)", (self.scope, uuid.uuid4().hex))

    def _folders(self, db) -> list[FolderGrant]:
        rows = db.execute("SELECT grant_json FROM folders WHERE scope = ? ORDER BY folder_id LIMIT ?",
                          (self.scope, MAX_GRANTS + 1)).fetchall()
        if len(rows) > MAX_GRANTS:
            raise FileStateUnavailable("本机文件授权记录超出限制")
        result = []
        for (raw,) in rows:
            value = json.loads(raw)
            value["identity"] = tuple(tuple(item) for item in value["identity"])
            result.append(FolderGrant(**value))
        return result

    def policy(self, *, local_paths: bool = False) -> dict:
        with self._db() as db:
            folders = self._folders(db)
            return {"policy_revision": self._revision(db),
                    "folders": [grant.public() | ({"directory": grant.directory} if local_paths else {})
                                for grant in folders]}

    def grant(self, directory: str, alias: str, *, read: bool, write: bool,
              expected_directory: tuple[str, str] | None = None) -> dict:
        """Only a user-confirmed native folder picker may supply a filesystem path."""
        if (type(read) is not bool or type(write) is not bool or not (read or write)
                or not isinstance(alias, str) or not alias.strip() or len(alias) > 80
                or any(not char.isprintable() for char in alias)):
            raise FileDenied("文件夹名称或授权范围无效")
        try:
            canonical, identity = select_directory(directory)
        except OSError:
            raise FileDenied("所选文件夹不可用") from None
        if expected_directory is not None and identity[-1] != expected_directory:
            raise FileDenied("所选文件夹在确认时已变化，请重新选择")
        if (Path(canonical) == self.directory or self.directory.is_relative_to(canonical)
                or Path(canonical).is_relative_to(self.directory)):
            raise FileDenied("不能开放本机连接器数据")
        grant = FolderGrant(uuid.uuid4().hex, alias.strip(), canonical, identity, read, write)
        with self._db(write=True) as db:
            folders = self._folders(db)
            if any(folder.directory == canonical for folder in folders):
                raise FileDenied("此文件夹已有授权，请先移除旧授权")
            if len(folders) >= MAX_GRANTS:
                raise FileDenied("文件夹授权已达上限")
            db.execute("INSERT INTO folders VALUES (?, ?, ?)", (self.scope, grant.folder_id, _json(asdict(grant))))
            self._change_revision(db)
        return grant.public()

    def revoke(self, folder_id: str) -> None:
        identifier(folder_id)
        with self._db(write=True) as db:
            removed = db.execute("DELETE FROM folders WHERE scope = ? AND folder_id = ?", (self.scope, folder_id))
            if removed.rowcount:
                self._change_revision(db)

    def _target(self, db, operation, folder_id, path, policy_revision) -> PinnedFile:
        if operation not in _OPERATIONS or self._revision(db) != policy_revision:
            raise FileDenied("本机授权已改变，请重新发起操作")
        grant = next((item for item in self._folders(db) if item.folder_id == folder_id), None)
        if (grant is None or (operation == "read" and not grant.read)
                or (operation == "create" and not grant.write)
                or (operation == "patch" and not (grant.read and grant.write))):
            raise FileDenied("这台电脑尚未开放该文件操作")
        target = PinnedFile(grant.directory, grant.identity, path)
        if operation == "create" and len(target.components) != 1:
            # Pin the directory the person actually selected. A nested parent
            # could be moved out of that directory during exclusive publication.
            raise FileDenied("要在子文件夹新建，请先在本机单独授权该文件夹")
        return target

    def preview(self, operation: str, folder_id: str, path: str, policy_revision: str) -> str:
        """A metadata-only check. File contents and mutations need the separate action."""
        with self._db() as db:
            try:
                return self._target(db, operation, folder_id, path, policy_revision).preview(operation)
            except OSError:
                raise FileDenied("目标文件或文件夹不可用") from None

    def _check_deadline(self, action: FileAction) -> None:
        if not 0 < action.deadline - self.clock() <= ACTION_SECONDS:
            raise FileDenied("文件动作已过期或期限无效")

    def _action_row(self, db, action_id):
        return db.execute("SELECT digest, status, acknowledged FROM actions WHERE scope = ? AND action_id = ?",
                          (self.scope, action_id)).fetchone()

    def _terminal(self, db, action: FileAction, status: str):
        db.execute("UPDATE actions SET status = ? WHERE scope = ? AND action_id = ?",
                   (status, self.scope, action.action_id))

    def _create(self, action: FileAction, active) -> str:
        # This first commit is deliberately separate from the transaction that fences
        # local revocation at the actual write. A crash between them is unknown.
        with self._db(write=True) as db:
            row = self._action_row(db, action.action_id)
            if row:
                if row[0] != action.digest:
                    raise FileDenied("动作编号已绑定其它参数")
                if row[1] == "started":
                    self._terminal(db, action, "unknown")
                    return "unknown"
                if row[1] in _STATUSES:
                    return row[1]
                if row[1] != "waiting":
                    raise FileStateUnavailable("动作记录无效")
            elif db.execute("SELECT count(*) FROM actions").fetchone()[0] >= MAX_ACTIONS:
                raise FileStateUnavailable("本机动作记录已达上限")
            try:
                self._check_deadline(action)
                if not active():
                    raise FileDenied("本机已停止连接")
                self._target(db, action.operation, action.folder_id, action.path, action.policy_revision)
            except FileDenied:
                status = "blocked"
            else:
                status = "started" if has_file_capacity(action.operation) else "waiting"
            db.execute("INSERT INTO actions VALUES (?, ?, ?, ?, 0)"
                       " ON CONFLICT(scope, action_id) DO UPDATE SET status = excluded.status",
                       (self.scope, action.action_id, action.digest, status))
            if status == "waiting":
                return "busy"
            if status == "blocked":
                return status

        with self._db(write=True) as db:
            try:
                self._check_deadline(action)
                target = self._target(db, action.operation, action.folder_id, action.path, action.policy_revision)
                target.create(text_bytes(action.content), action.version, before_publish=lambda: self._check_deadline(action))
                status = "created"
            except (FileDenied, FileExistsError):
                status = "blocked"
            except OSError:
                # An I/O failure (including a directory flush after publication)
                # cannot establish that the original action had no side effect.
                status = "unknown"
            self._terminal(db, action, status)
        return status

    def execute(self, action: FileAction, *, active=lambda: True) -> dict:
        base = {"action_id": action.action_id, "digest": action.digest}
        try:
            self._private_directory()
            # The lock reserves capacity across every pairing, from admission through the effect and its receipt.
            with FileLease(self.directory / "file-action.lock", private=True):
                if action.operation == "create":
                    return {**base, "status": self._create(action, active)}
                self._check_deadline(action)
                if not active():
                    raise FileDenied("本机已停止连接")
                with self._db(write=True) as db:
                    target = self._target(db, action.operation, action.folder_id, action.path, action.policy_revision)
                    if not has_file_capacity(action.operation):
                        return {**base, "status": "busy"}
                    value = (target.read(action.version) if action.operation == "read"
                             else target.patch(action.version, action.content))
                return {**base, "status": action.operation, "text": value}
        except HomeMaintenanceError:
            return {**base, "status": "busy"}
        except (FileDenied, OSError):
            return {**base, "status": "blocked"}

    def pending(self, limit=16) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 16:
            raise ValueError("invalid receipt limit")
        # No executor holds the lock. A durable start without a terminal receipt
        # may have published a file before the process was interrupted.
        self._private_directory()
        with FileLease(self.directory / "file-action.lock", private=True), self._db(write=True) as db:
            db.execute("UPDATE actions SET status = 'unknown' WHERE scope = ? AND status = 'started'",
                       (self.scope,))
            return [{"action_id": action_id, "digest": digest, "status": status} for action_id, digest, status
                    in db.execute("SELECT action_id, digest, status FROM actions WHERE scope = ? "
                                  "AND status IN ('created', 'blocked', 'unknown') AND acknowledged = 0 "
                                  "ORDER BY rowid LIMIT ?", (self.scope, limit))]

    def acknowledge(self, action_id: str, digest: str) -> bool:
        identifier(action_id)
        with self._db(write=True) as db:
            row = self._action_row(db, action_id)
            if row is None or row[0] != digest or row[1] not in _STATUSES:
                return False
            db.execute("UPDATE actions SET acknowledged = 1 WHERE scope = ? AND action_id = ?", (self.scope, action_id))
            return True
