"""Durable dispatch and result evidence for the home's fixed C5 job."""
from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
import json
import os
import sqlite3
import stat
import time

from src.gateway.computer_job_contract import FIXTURE_JOB, JobAction, job_action_value
from src.gateway.computer_job_wire import JOB_TERMINAL, job_result_value
from src.gateway.computer_protocol import BINDING_FIELDS, binding_fields, identifier
from src.utils.state_dir import ensure_state_gitignore, state_dir

MAX_INTENTS = 1_000
_TRANSIENT = frozenset({"busy", "missing"})
_PROGRESS = {"accepted": 0, "started": 1, "stopping": 2}


class JobIntentUnavailable(RuntimeError):
    """The home's durable job evidence cannot be used."""


@dataclass(frozen=True)
class JobIntent:
    action: JobAction
    binding: dict
    result: dict | None


def _action_binding(action: JobAction) -> dict:
    return {key: getattr(action, key) for key in BINDING_FIELDS}


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _stored_result(raw: str | None, action: JobAction) -> dict | None:
    if raw is None:
        return None
    try:
        value = job_result_value(json.loads(raw), action)
        if value["status"] not in JOB_TERMINAL and value["status"] not in _PROGRESS:
            raise ValueError("non-persistent job status")
        return value
    except (KeyError, TypeError, ValueError) as error:
        raise JobIntentUnavailable("家的测试回执记录格式不正确") from error


class HomeJobIntents:
    def __init__(self, root: str):
        self.root = root

    @contextmanager
    def _db(self):
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
            raise JobIntentUnavailable("此系统尚未支持持久测试派发")
        try:
            directory = state_dir(self.root) / "computer-job-intents"
            if directory.parent.is_symlink():
                raise JobIntentUnavailable("无法核对家的测试派发目录")
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise JobIntentUnavailable("测试派发目录权限不正确")
            path = directory / "intents.sqlite3"
            fd = os.open(path, os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid != os.geteuid() or info.st_mode & 0o077):
                    raise JobIntentUnavailable("测试派发记录权限不正确")
            finally:
                os.close(fd)
            with closing(sqlite3.connect(path, timeout=1)) as db:
                db.execute("PRAGMA synchronous=FULL")
                db.execute("""
                    CREATE TABLE IF NOT EXISTS intents (
                        id TEXT PRIMARY KEY, digest TEXT NOT NULL,
                        action TEXT NOT NULL, result TEXT)
                """)
                db.execute("BEGIN IMMEDIATE")
                with db:
                    yield db
        except (OSError, sqlite3.Error) as error:
            raise JobIntentUnavailable("家的测试派发记录暂时不可用") from error

    def record(self, binding: dict, action: JobAction) -> None:
        """Commit the approved action once, before the home can send it."""
        if (binding_fields(binding) != _action_binding(action)
                or (action.job_id, action.version, action.input_digest)
                != (FIXTURE_JOB.job_id, FIXTURE_JOB.version, FIXTURE_JOB.input_digest)
                or not 0 < action.deadline - time.time() <= FIXTURE_JOB.max_seconds):
            raise JobIntentUnavailable("测试派发已过期或不匹配")
        with self._db() as db:
            ensure_state_gitignore(self.root)
            if db.execute("SELECT count(*) FROM intents").fetchone()[0] >= MAX_INTENTS:
                raise JobIntentUnavailable("家的测试派发记录已达上限")
            db.execute("INSERT INTO intents VALUES (?, ?, ?, NULL)",
                       (action.action_id, action.digest, _json(asdict(action))))

    def get(self, action_id: str, identity: dict, session_id: str) -> JobIntent | None:
        identifier(action_id)
        with self._db() as db:
            row = db.execute("SELECT action, digest, result FROM intents WHERE id = ?", (action_id,)).fetchone()
        if row is None:
            return None
        try:
            action = job_action_value(json.loads(row[0]))
            if (action.action_id, action.digest) != (action_id, row[1]):
                raise ValueError("invalid stored job action")
        except (TypeError, ValueError) as error:
            raise JobIntentUnavailable("家的测试派发记录格式不正确") from error
        binding = _action_binding(action)
        if (identity.get("ownership_state") != "claimed"
                or (action.session_id, binding["home_instance_id"], binding["paired_principal_id"], binding["trust_epoch"])
                != (session_id, identity.get("home_instance_id"), identity.get("owner_subject"), identity.get("trust_epoch"))):
            return None
        return JobIntent(action, binding, _stored_result(row[2], action))

    def accept(self, binding: dict, action: JobAction, result: dict) -> bool:
        """Persist progress in order and keep a final receipt immutable."""
        if binding_fields(binding) != _action_binding(action):
            return False
        value = job_result_value(result, action)
        status = value["status"]
        with self._db() as db:
            row = db.execute("SELECT digest, action, result FROM intents WHERE id = ?", (action.action_id,)).fetchone()
            if row is None or row[:2] != (action.digest, _json(asdict(action))):
                return False
            prior = _stored_result(row[2], action)
            if prior == value or status in _TRANSIENT:
                # A poll may lack evidence. It cannot reset a dispatch or grant a retry.
                return True
            if (prior is not None and
                    (prior["status"] in JOB_TERMINAL
                     or _PROGRESS.get(status, len(_PROGRESS)) <= _PROGRESS[prior["status"]])):
                return False
            db.execute("UPDATE intents SET result = ? WHERE id = ?", (_json(value), action.action_id))
            return True
