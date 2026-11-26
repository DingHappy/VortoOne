"""Local C5 acceptance entry point for one fixed, isolated Ubuntu workload."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import stat
import time
import uuid

from src.gateway.computer_job_contract import FIXTURE_JOB, JobAction, verify_fixture_action
from src.gateway.computer_protocol import BINDING_FIELDS, ComputerProtocolError, binding_fields, identifier
from src.sandbox.connected_command import run_fixture
from src.utils.home_maintenance import FileLease, HomeMaintenanceError

_MAX_RECORDS = 1_000
_MAX_LOG_BYTES = 16 * 1024
_MAX_REPORT_BYTES = 16 * 1024
_TERMINAL = frozenset({"succeeded", "failed", "cancelled", "timed_out", "blocked", "unknown"})


class FixtureStateUnavailable(RuntimeError):
    """No durable acceptance record is available."""


@dataclass(frozen=True)
class FixtureReceipt:
    action_id: str
    digest: str
    status: str
    exit_code: int | None = None
    log: str = ""
    report: bytes | None = None

    def public(self) -> dict:
        report = self.report
        return {
            **asdict(self),
            "report": None if report is None else {
                "name": "isolation.xml",
                "sha256": hashlib.sha256(report).hexdigest(),
                "text": report.decode("utf-8"),
            },
        }


class FixtureJournal:
    """Durable local evidence, scoped to one authenticated pairing."""

    def __init__(self, directory: Path, binding: dict):
        self.directory = Path(directory)
        self.binding = binding_fields(binding)
        self._scope = json.dumps(self.binding, sort_keys=True, separators=(",", ":"))

    def _private_directory(self) -> None:
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
            raise FixtureStateUnavailable("本机夹具需要 POSIX 私有目录")
        self.directory.mkdir(mode=0o700, exist_ok=True)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise FixtureStateUnavailable("本机夹具目录权限不正确")

    @contextmanager
    def _db(self):
        try:
            self._private_directory()
            path = self.directory / "fixture.sqlite3"
            fd = os.open(path, os.O_CREAT | os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            try:
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid != os.geteuid() or info.st_mode & 0o077):
                    raise FixtureStateUnavailable("本机夹具记录权限不正确")
            finally:
                os.close(fd)
            with closing(sqlite3.connect(path, timeout=1)) as db:
                db.execute("PRAGMA synchronous=FULL")
                db.execute("""
                    CREATE TABLE IF NOT EXISTS fixture (
                        scope TEXT NOT NULL, action_id TEXT NOT NULL, digest TEXT NOT NULL,
                        status TEXT NOT NULL, exit_code INTEGER, log TEXT NOT NULL, report BLOB,
                        PRIMARY KEY (scope, action_id))
                """)
                db.execute("BEGIN IMMEDIATE")
                with db:
                    yield db
        except (OSError, sqlite3.Error) as error:
            raise FixtureStateUnavailable("本机夹具记录不可用") from error

    def _get(self, db, action_id: str) -> FixtureReceipt | None:
        row = db.execute("SELECT digest, status, exit_code, log, report FROM fixture "
                         "WHERE scope = ? AND action_id = ?", (self._scope, action_id)).fetchone()
        return FixtureReceipt(action_id, *row) if row is not None else None

    def status(self, action_id: str) -> FixtureReceipt | None:
        identifier(action_id)
        with self._db() as db:
            receipt = self._get(db, action_id)
        if receipt is None or receipt.status != "started":
            return receipt
        lease = FileLease(self.directory / "fixture.lock", private=True)
        try:
            lease.acquire()
        except HomeMaintenanceError:
            return receipt
        try:
            with self._db() as db:
                receipt = self._get(db, action_id)
                if receipt is not None and receipt.status == "started":
                    receipt = FixtureReceipt(action_id, receipt.digest, "unknown")
                    self._save(db, receipt)
                return receipt
        finally:
            lease.release()

    def _save(self, db, receipt: FixtureReceipt) -> None:
        db.execute("INSERT OR REPLACE INTO fixture VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (self._scope, receipt.action_id, receipt.digest, receipt.status,
                    receipt.exit_code, receipt.log, receipt.report))

    def _begin(self, action: JobAction, *, cancelled: bool) -> FixtureReceipt:
        # Only called with the execution lease. A previous started row with no
        # lease has no verifiable terminal evidence and is never launched again.
        with self._db() as db:
            previous = self._get(db, action.action_id)
            if previous is not None:
                if previous.digest != action.digest:
                    raise ComputerProtocolError("computer job action already bound")
                if previous.status in _TERMINAL:
                    return previous
                if previous.status != "started":
                    raise FixtureStateUnavailable("本机夹具状态无效")
                status = "unknown"
            else:
                count = db.execute("SELECT count(*) FROM fixture").fetchone()[0]
                if count >= _MAX_RECORDS:
                    raise FixtureStateUnavailable("本机夹具记录已达上限")
                status = "cancelled" if cancelled else "started"
            receipt = FixtureReceipt(action.action_id, action.digest, status)
            self._save(db, receipt)
            return receipt

    async def execute(
        self, action: JobAction, *, policy_revision: str, locally_approved: bool, cancel: asyncio.Event,
        started: asyncio.Event | None = None,
    ) -> FixtureReceipt:
        verify_fixture_action(action, binding=self.binding, policy_revision=policy_revision,
                              locally_approved=locally_approved, now=time.time())
        self._private_directory()
        with FileLease(self.directory / "fixture.lock", private=True):
            receipt = self._begin(action, cancelled=cancel.is_set())
            if receipt.status != "started":
                return receipt
            if started is not None:
                started.set()
            result = await run_fixture(deadline=action.deadline, cancel=cancel)
            if cancel.is_set():
                # A local cancellation observed before the terminal commit
                # discards publication. The runner has already reaped its child.
                receipt = FixtureReceipt(action.action_id, action.digest, "cancelled")
            else:
                receipt = FixtureReceipt(action.action_id, action.digest, result.status,
                                         result.exit_code, result.log, result.report)
            if (receipt.status not in _TERMINAL or len(receipt.log.encode("utf-8")) > _MAX_LOG_BYTES
                    or (receipt.report is not None and (receipt.status != "succeeded"
                                                       or len(receipt.report) > _MAX_REPORT_BYTES))):
                raise FixtureStateUnavailable("本机夹具结果无效")
            with self._db() as db:
                self._save(db, receipt)
            return receipt


# Stable synthetic scope for the local command, separate from connector pairings.
_LOCAL_BINDING = {key: uuid.uuid5(uuid.NAMESPACE_URL, f"vortoone:c5:fixture:{key}").hex
                  for key in BINDING_FIELDS}
_LOCAL_REVISION = uuid.uuid5(uuid.NAMESPACE_URL, "vortoone:c5:fixture:policy").hex


async def _run(journal: FixtureJournal) -> FixtureReceipt:
    action_id = uuid.uuid4().hex
    action = JobAction(action_id=action_id, job_id=FIXTURE_JOB.job_id, policy_revision=_LOCAL_REVISION,
                       version=FIXTURE_JOB.version, input_digest=FIXTURE_JOB.input_digest,
                       deadline=int(time.time()) + FIXTURE_JOB.max_seconds, **_LOCAL_BINDING,
                       session_id=action_id, turn_id=action_id, call_id=action_id)
    cancel = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, cancel.set)
    print(json.dumps({"action_id": action_id}), flush=True)
    try:
        return await journal.execute(action, policy_revision=_LOCAL_REVISION,
                                     locally_approved=True, cancel=cancel)
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


def main() -> int:
    parser = argparse.ArgumentParser(description="运行或查询本地 C5 隔离验收夹具")
    parser.add_argument("--state-dir", type=Path, required=True, help="私有验收目录")
    parser.add_argument("--status", metavar="ACTION_ID", help="查询该目录中已保存的动作")
    options = parser.parse_args()
    journal = FixtureJournal(options.state_dir, _LOCAL_BINDING)
    try:
        receipt = journal.status(options.status) if options.status is not None else asyncio.run(_run(journal))
    except (OSError, NotImplementedError, ComputerProtocolError, FixtureStateUnavailable, HomeMaintenanceError):
        print(json.dumps({"status": "unavailable"}))
        return 1
    if receipt is None:
        print(json.dumps({"status": "missing"}))
        return 1
    print(json.dumps(receipt.public(), ensure_ascii=False))
    return 0 if options.status is not None or receipt.status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
