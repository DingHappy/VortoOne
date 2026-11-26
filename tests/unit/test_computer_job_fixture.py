import asyncio
from dataclasses import replace
import hashlib
import time
from types import SimpleNamespace

import pytest

from src.gateway import computer_job_fixture as fixture
from src.gateway.computer_job_contract import FIXTURE_JOB, JobAction
from src.gateway.computer_protocol import BINDING_FIELDS, ComputerProtocolError
from src.utils.home_maintenance import HomeMaintenanceError


@pytest.fixture
def approved_job(tmp_path):
    binding = {key: f"{index:032x}" for index, key in enumerate(BINDING_FIELDS, 1)}
    action = JobAction(
        **binding,
        action_id="a" * 32,
        policy_revision="b" * 32,
        job_id=FIXTURE_JOB.job_id,
        version=FIXTURE_JOB.version,
        input_digest=FIXTURE_JOB.input_digest,
        deadline=int(time.time()) + 60,
        session_id="session",
        turn_id="turn",
        call_id="call",
    )
    return fixture.FixtureJournal(tmp_path / "local", binding), action


def launch(journal, action, cancel=None, *, approved=True):
    return journal.execute(action, policy_revision=action.policy_revision, locally_approved=approved,
                           cancel=asyncio.Event() if cancel is None else cancel)


async def test_fixture_same_action_returns_durable_result_without_running_again(approved_job, monkeypatch):
    journal, action = approved_job
    output = b"<testsuite tests='1' failures='0'/>"
    launches = []

    async def run(**_):
        assert journal.status(action.action_id).status == "started"
        launches.append(action.action_id)
        return SimpleNamespace(status="succeeded", exit_code=0, log="OK", report=output)

    monkeypatch.setattr(fixture, "run_fixture", run)
    original = await launch(journal, action)
    reopened = fixture.FixtureJournal(journal.directory, journal.binding)
    assert reopened.status(action.action_id) == original
    assert await launch(reopened, action) == original
    assert launches == [action.action_id]
    assert original.public()["report"]["sha256"] == hashlib.sha256(output).hexdigest()
    with pytest.raises(ComputerProtocolError, match="already bound"):
        await launch(reopened, replace(action, call_id="other-call"))
    other_device = fixture.FixtureJournal(journal.directory, journal.binding | {"connector_id": "f" * 32})
    assert other_device.status(action.action_id) is None


async def test_fixture_cancel_prevents_artifact_and_does_not_release_running_slot(approved_job, monkeypatch):
    journal, action = approved_job
    cancel, entered, may_exit = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def run(**_):
        entered.set()
        await may_exit.wait()
        return SimpleNamespace(status="succeeded", exit_code=0, log="late", report=b"<late/>")

    monkeypatch.setattr(fixture, "run_fixture", run)
    running = asyncio.create_task(launch(journal, action, cancel))
    await asyncio.wait_for(entered.wait(), 2)
    cancel.set()
    assert journal.status(action.action_id).status == "started"
    with pytest.raises(HomeMaintenanceError):
        await launch(journal, replace(action, action_id="d" * 32))
    may_exit.set()
    receipt = await running
    assert receipt.status == "cancelled"
    assert receipt.public()["report"] is None
    assert journal.status(action.action_id) == receipt


async def test_fixture_recovery_marks_interrupted_started_unknown_without_relaunch(approved_job, monkeypatch):
    journal, action = approved_job
    launches = []

    async def run(**_):
        launches.append(action.action_id)
        raise RuntimeError("interrupted after the durable start")

    monkeypatch.setattr(fixture, "run_fixture", run)
    with pytest.raises(RuntimeError, match="interrupted"):
        await launch(journal, action)
    reopened = fixture.FixtureJournal(journal.directory, journal.binding)
    receipt = reopened.status(action.action_id)
    assert receipt.status == "unknown"
    assert await launch(reopened, action) == receipt
    assert launches == [action.action_id]


async def test_fixture_refuses_unapproved_or_invalid_storage_before_launch(approved_job, monkeypatch):
    journal, action = approved_job

    async def run(**_):
        pytest.fail("invalid job reached the executor")

    monkeypatch.setattr(fixture, "run_fixture", run)
    with pytest.raises(ComputerProtocolError):
        await launch(journal, action, approved=False)
    assert not journal.directory.exists()
    journal.directory.mkdir()
    journal.directory.chmod(0o755)
    with pytest.raises(fixture.FixtureStateUnavailable, match="目录权限"):
        await launch(journal, action)


async def test_fixture_pre_cancel_and_isolation_unavailable_have_terminal_receipts(approved_job, monkeypatch):
    journal, action = approved_job
    calls = []

    async def run(**_):
        calls.append(True)
        return SimpleNamespace(status="blocked", exit_code=None, log="isolation refused", report=None)

    monkeypatch.setattr(fixture, "run_fixture", run)
    cancelled = asyncio.Event()
    cancelled.set()
    assert (await launch(journal, action, cancelled)).status == "cancelled"
    assert calls == []
    another = replace(action, action_id="e" * 32)
    blocked = await launch(journal, another)
    assert blocked.status == "blocked"
    assert journal.status(another.action_id) == blocked
    assert calls == [True]
