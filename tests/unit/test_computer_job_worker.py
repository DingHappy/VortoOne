import asyncio
from dataclasses import asdict, replace
import json
from types import SimpleNamespace
import time

import aiohttp
import pytest

from src.gateway import computer_connector, computer_job_fixture
from src.gateway.computer_job_contract import FIXTURE_JOB, JobAction
from src.gateway.computer_job_wire import job_reference
from src.gateway.computer_job_worker import FixtureWorker
from src.gateway.computer_protocol import BINDING_FIELDS, JOB_FIXTURE_PROTOCOL, binding_fields, welcome
from src.sandbox.connected_command import FixtureRun


def _config():
    return {
        "operation": "connect",
        "origin": "https://home.test",
        "token": "vcc_" + "a" * 43,
        **dict(zip(BINDING_FIELDS, ("1" * 32, "2" * 32, "3" * 32, "4" * 32, "5" * 32))),
    }


def _action(policy, binding, **changes):
    values = {
        **binding_fields(binding),
        "action_id": "a" * 32,
        "job_id": FIXTURE_JOB.job_id,
        "policy_revision": policy["policy_revision"],
        "version": FIXTURE_JOB.version,
        "input_digest": FIXTURE_JOB.input_digest,
        "deadline": int(time.time()) + 45,
        "session_id": "main",
        "turn_id": "test",
        "call_id": "fixed",
    }
    return JobAction(**(values | changes))


def test_fixture_requires_source_linux_and_explicit_local_file_protocol(monkeypatch):
    config = _config()
    assert "job_fixture" not in computer_connector.configuration(config)
    monkeypatch.setattr(computer_connector.sys, "platform", "linux")
    with pytest.raises(computer_connector.ConnectorFailure, match="fixture job"):
        computer_connector.configuration(config | {"job_fixture": True})
    with pytest.raises(computer_connector.ConnectorFailure, match="fixture job"):
        computer_connector.configuration(config | {"job_fixture": 1, "file_access": True})
    options = config | {"job_fixture": True, "file_access": True}
    assert computer_connector.configuration(options)["job_fixture"] is True
    monkeypatch.setattr(computer_connector.sys, "frozen", True, raising=False)
    with pytest.raises(computer_connector.ConnectorFailure, match="fixture job"):
        computer_connector.configuration(options)
    monkeypatch.setattr(computer_connector.sys, "frozen", False)
    monkeypatch.setattr(computer_connector.sys, "platform", "darwin")
    with pytest.raises(computer_connector.ConnectorFailure, match="fixture job"):
        computer_connector.configuration(options)


async def test_worker_accepts_durably_and_serializes_exact_action(monkeypatch, tmp_path):
    entered = asyncio.Event()
    release = asyncio.Event()
    count = 0

    async def run_fixture(*, deadline, cancel):
        nonlocal count
        count += 1
        entered.set()
        await release.wait()
        return FixtureRun("succeeded", 0, "test ok", b"<testsuite/>")

    monkeypatch.setattr(computer_job_fixture, "run_fixture", run_fixture)
    worker = FixtureWorker(tmp_path / "journal", _config())
    policy = await worker.request("policy", {})
    original = _action(policy, _config())
    reference = job_reference(original)
    assert await worker.request("start", asdict(replace(original, trust_epoch="f" * 32))) is None
    assert await worker.request("start", asdict(replace(original, policy_revision="f" * 32))) is None
    assert count == 0

    try:
        answer = await worker.request("start", asdict(original))
        assert answer["status"] == "accepted"
        assert entered.is_set()
        assert worker.journal.status(original.action_id).status == "started"
        assert (await worker.request("start", asdict(original)))["status"] == "started"
        assert (await worker.request("start", asdict(replace(original, action_id="e" * 32))))["status"] == "busy"
        assert await worker.request("start", asdict(replace(original, call_id="different"))) is None
        assert await worker.request("cancel", reference | {"digest": "b" * 64}) is None
        assert (await worker.request("status", reference))["status"] == "started"
        new_worker = FixtureWorker(tmp_path / "journal", _config())
        assert await new_worker.request("status", reference) is None
        assert await new_worker.request("cancel", reference) is None
        running = worker._running()
        release.set()
        await asyncio.wait_for(running.task, timeout=2)
        terminal = await worker.request("status", reference)
        assert terminal["status"] == "succeeded"
        assert terminal["report"]["text"] == "<testsuite/>"
        assert await worker.request("start", asdict(original)) == terminal
        assert count == 1
    finally:
        release.set()
        await worker.close()

    assert (await new_worker.request("policy", {}))["policy_revision"] != policy["policy_revision"]
    assert await new_worker.request("start", asdict(original)) is None
    assert await new_worker.request("status", reference) == terminal
    assert await new_worker.request("cancel", reference) == terminal


async def test_worker_refuses_when_started_record_cannot_be_written(monkeypatch, tmp_path):
    ran = False

    async def run_fixture(*, deadline, cancel):
        nonlocal ran
        ran = True
        raise AssertionError("unpersisted action started")

    monkeypatch.setattr(computer_job_fixture, "run_fixture", run_fixture)
    worker = FixtureWorker(tmp_path / "journal", _config())
    policy = await worker.request("policy", {})
    action = _action(policy, _config())
    def cannot_begin(*args, **kwargs):
        raise computer_job_fixture.FixtureStateUnavailable("failed before start")

    monkeypatch.setattr(worker.journal, "_begin", cannot_begin)
    assert await worker.request("start", asdict(action)) is None
    assert (await worker.request("status", job_reference(action)))["status"] == "missing"
    assert ran is False
    await worker.close()


async def test_worker_cancels_the_confirmed_action_and_drops_its_report(monkeypatch, tmp_path):
    allowed_to_exit = asyncio.Event()

    async def run_fixture(*, deadline, cancel):
        await cancel.wait()
        await allowed_to_exit.wait()
        return FixtureRun("succeeded", 0, "late success", b"<testsuite/>")

    monkeypatch.setattr(computer_job_fixture, "run_fixture", run_fixture)
    worker = FixtureWorker(tmp_path / "journal", _config())
    policy = await worker.request("policy", {})
    action = _action(policy, _config())
    reference = job_reference(action)
    try:
        assert (await worker.request("start", asdict(action)))["status"] == "accepted"
        assert (await worker.request("cancel", reference))["status"] == "stopping"
        assert (await worker.request("status", reference))["status"] == "stopping"
        running = worker._running()
        allowed_to_exit.set()
        await asyncio.wait_for(running.task, timeout=2)
        terminal = await worker.request("status", reference)
        assert terminal["status"] == "cancelled" and terminal["report"] is None
    finally:
        allowed_to_exit.set()
        await worker.close()


class _Socket:
    def __init__(self, generation):
        self.generation = generation
        self.incoming = asyncio.Queue()
        self.outgoing = asyncio.Queue()
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True

    async def receive(self, *, timeout):
        return await asyncio.wait_for(self.incoming.get(), timeout)

    async def send_json(self, value):
        if value["type"] == "ping":
            await self.send({"type": "pong", "generation": self.generation})
        else:
            self.outgoing.put_nowait(value)

    async def send(self, value):
        self.incoming.put_nowait(SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(value)))

    async def disconnect(self):
        self.incoming.put_nowait(SimpleNamespace(type=aiohttp.WSMsgType.CLOSED))

    async def next(self):
        return await asyncio.wait_for(self.outgoing.get(), timeout=2)

    async def negotiate(self, binding, protocol=JOB_FIXTURE_PROTOCOL):
        assert (await self.next())["protocol_max"] == JOB_FIXTURE_PROTOCOL
        await self.send(welcome(binding, self.generation, protocol))

    async def request(self, binding, method, payload):
        request_id = "f" * 32
        await self.send({"type": "job_request", "generation": self.generation, "binding": binding_fields(binding),
                         "request_id": request_id, "method": method, "payload": payload})
        reply = await self.next()
        assert (reply["type"], reply["generation"], reply["request_id"]) == (
            "job_reply", self.generation, request_id,
        )
        assert reply["ok"] is True
        return reply["result"]


async def test_connector_job_survives_disconnect_and_revoke_waits_for_stop(monkeypatch, tmp_path):
    runner_cancelled = asyncio.Event()
    allow_exit = asyncio.Event()
    launches = 0

    async def run_fixture(*, deadline, cancel):
        nonlocal launches
        launches += 1
        await cancel.wait()
        runner_cancelled.set()
        await allow_exit.wait()
        return FixtureRun("cancelled", None, "stopped")

    monkeypatch.setattr(computer_job_fixture, "run_fixture", run_fixture)
    monkeypatch.setattr(computer_connector, "_job_fixture_available", lambda: True)
    monkeypatch.chdir(tmp_path)
    config = computer_connector.configuration(_config() | {"job_fixture": True, "file_access": True})
    first, second = _Socket("6" * 32), _Socket("7" * 32)
    sockets = iter([first, second])
    session = SimpleNamespace(ws_connect=lambda *args, **kwargs: next(sockets))
    stop, persisted = asyncio.Event(), asyncio.Event()
    persisted.set()

    async def retry_wait(stop, delay):
        return stop.is_set()

    running = asyncio.create_task(computer_connector.connect(
        session, config, lambda _: None, stop, persisted, retry_wait=retry_wait,
    ))
    try:
        await first.negotiate(config)
        policy = await first.request(config, "policy", {})
        action = _action(policy, config)
        assert (await first.request(config, "start", asdict(action)))["status"] == "accepted"
        await first.disconnect()
        await second.negotiate(config)
        assert await second.request(config, "policy", {}) == policy
        assert (await second.request(config, "status", job_reference(action)))["status"] == "started"
        assert launches == 1 and not runner_cancelled.is_set()

        await second.send({"type": "revoked", "generation": second.generation,
                           "reason": "authorization_revoked", "credentialRevoked": True})
        await asyncio.wait_for(runner_cancelled.wait(), timeout=2)
        assert not running.done()
        allow_exit.set()
        await asyncio.wait_for(running, timeout=2)
        refreshed = FixtureWorker(tmp_path / "job-fixture", config)
        receipt = await refreshed.request("status", job_reference(action))
        assert receipt["status"] == "cancelled" and receipt["report"] is None
        assert launches == 1
    finally:
        allow_exit.set()
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)
