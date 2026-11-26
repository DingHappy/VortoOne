"""One locally enabled fixture slot that survives control socket reconnects."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import time
import uuid

from src.gateway.computer_file_wire import digest_value, object_fields
from src.gateway.computer_job_contract import JobAction, fixture_policy, job_action_value, verify_fixture_action
from src.gateway.computer_job_fixture import FixtureJournal, FixtureReceipt, FixtureStateUnavailable
from src.gateway.computer_protocol import identifier
from src.utils.home_maintenance import HomeMaintenanceError


@dataclass(frozen=True)
class _RunningFixture:
    action: JobAction
    cancel: asyncio.Event
    task: asyncio.Task


class FixtureWorker:
    """The trusted connector owns the opt-in, journal, and process lifetime."""

    def __init__(self, directory: Path, binding: dict):
        self.journal = FixtureJournal(directory, binding)
        self._revision = uuid.uuid4().hex
        self._active: _RunningFixture | None = None
        self._closed = False
        self._requests = asyncio.Lock()

    async def request(self, method: str, payload: dict) -> dict | None:
        async with self._requests:
            if self._closed:
                return None
            try:
                if method == "policy" and payload == {}:
                    return fixture_policy(self._revision, locally_approved=True)
                if method == "start":
                    return await self._start(job_action_value(payload))
                if method in {"status", "cancel"}:
                    object_fields(payload, ("action_id", "digest"))
                    return await self._reference(identifier(payload["action_id"]), digest_value(payload["digest"]),
                                                 cancel=method == "cancel")
            except (ValueError, FixtureStateUnavailable, HomeMaintenanceError, OSError):
                pass
            return None

    async def _start(self, action: JobAction) -> dict | None:
        verify_fixture_action(action, binding=self.journal.binding, policy_revision=self._revision,
                              locally_approved=True, now=time.time())
        active = self._running()
        if active is not None:
            if active.action.action_id == action.action_id:
                if active.action.digest != action.digest:
                    return None
                status = "stopping" if active.cancel.is_set() else "started"
            else:
                status = "busy"
            return FixtureReceipt(action.action_id, action.digest, status).public()

        stored = await asyncio.to_thread(self.journal.status, action.action_id)
        if stored is not None:
            return stored.public() if stored.digest == action.digest else None
        if self._closed:
            return None

        started, cancel = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(self.journal.execute(
            action, policy_revision=self._revision, locally_approved=True, cancel=cancel, started=started,
        ))
        self._active = _RunningFixture(action, cancel, task)
        task.add_done_callback(self._completed)
        ready = asyncio.create_task(started.wait())
        try:
            await asyncio.wait((task, ready), return_when=asyncio.FIRST_COMPLETED)
            if task.done():
                receipt = task.result()
                return receipt.public()
            return FixtureReceipt(action.action_id, action.digest, "accepted").public()
        finally:
            ready.cancel()
            await asyncio.gather(ready, return_exceptions=True)

    def _running(self) -> _RunningFixture | None:
        active = self._active
        return active if active is not None and not active.task.done() else None

    def _completed(self, task: asyncio.Task) -> None:
        if not task.cancelled():
            task.exception()
        if self._active is not None and self._active.task is task:
            self._active = None

    async def _reference(self, action_id: str, digest: str, *, cancel: bool) -> dict | None:
        active = self._running()
        if active is not None and active.action.action_id == action_id:
            if active.action.digest != digest:
                return None
            if cancel:
                active.cancel.set()
            return FixtureReceipt(action_id, digest, "stopping" if active.cancel.is_set() else "started").public()
        stored = await asyncio.to_thread(self.journal.status, action_id)
        if stored is None:
            return FixtureReceipt(action_id, digest, "missing").public()
        # A started row alone cannot tell this process whether the requested
        # job is alive. The journal can reconcile it once the lease is free.
        if stored.digest != digest or stored.status == "started":
            return None
        return stored.public()

    def request_stop(self) -> None:
        self._closed = True
        active = self._running()
        if active is not None:
            active.cancel.set()

    async def close(self) -> None:
        self.request_stop()
        active = self._running()
        if active is not None:
            await asyncio.shield(asyncio.gather(active.task, return_exceptions=True))
