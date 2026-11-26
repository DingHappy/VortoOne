"""Home job evidence is scoped, durable, and never authorizes a resend."""
from dataclasses import replace
import hashlib
import os
import stat
import time

import pytest

from src.gateway.computer_job_contract import FIXTURE_JOB, JobAction
from src.gateway.computer_job_intents import HomeJobIntents, JobIntentUnavailable
from src.gateway.computer_protocol import BINDING_FIELDS, ComputerProtocolError
from src.utils.state_dir import state_dir

pytestmark = pytest.mark.skipif(os.name != "posix", reason="requires a private POSIX directory")

BINDING = {field: str(index) * 32 for index, field in enumerate(BINDING_FIELDS, 1)}
IDENTITY = {
    "ownership_state": "claimed",
    "home_instance_id": BINDING["home_instance_id"],
    "owner_subject": BINDING["paired_principal_id"],
    "trust_epoch": BINDING["trust_epoch"],
}


@pytest.fixture
def action():
    return JobAction(
        **BINDING,
        action_id="a" * 32,
        policy_revision="b" * 32,
        job_id=FIXTURE_JOB.job_id,
        version=FIXTURE_JOB.version,
        input_digest=FIXTURE_JOB.input_digest,
        deadline=int(time.time()) + 60,
        session_id="owner-session",
        turn_id="turn",
        call_id="call",
    )


def receipt(action, status, *, exit_code=None, log=""):
    return {"action_id": action.action_id, "digest": action.digest, "status": status,
            "exit_code": exit_code, "log": log, "report": None}


def test_intent_survives_reopen_without_granting_another_dispatch(tmp_path, action):
    home = HomeJobIntents(str(tmp_path))
    home.record({**BINDING, "token": "synthetic-token"}, action)
    directory = state_dir(tmp_path) / "computer-job-intents"
    path = directory / "intents.sqlite3"
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "computer-job-intents/" in (directory.parent / ".gitignore").read_text()
    assert b"synthetic-token" not in path.read_bytes()

    reopened = HomeJobIntents(str(tmp_path))
    stored = reopened.get(action.action_id, IDENTITY, action.session_id)
    assert stored.action == action
    assert stored.binding == BINDING
    assert stored.result is None
    with pytest.raises(JobIntentUnavailable):
        reopened.record(BINDING, action)
    assert reopened.get(action.action_id, IDENTITY, action.session_id) == stored


@pytest.mark.parametrize("key,value", [
    ("ownership_state", "unclaimed"),
    ("home_instance_id", "f" * 32),
    ("owner_subject", "f" * 32),
    ("trust_epoch", "f" * 32),
])
def test_owner_scope_excludes_unclaimed_or_changed_identity(tmp_path, action, key, value):
    home = HomeJobIntents(str(tmp_path))
    home.record(BINDING, action)
    assert home.get(action.action_id, {**IDENTITY, key: value}, action.session_id) is None
    assert home.get(action.action_id, IDENTITY, "another-session") is None


def test_receipts_bind_pairing_and_exact_action_digest(tmp_path, action):
    home = HomeJobIntents(str(tmp_path))
    progress = receipt(action, "started")
    assert home.accept(BINDING, action, progress) is False
    home.record(BINDING, action)

    assert home.accept({**BINDING, "credential_epoch": "f" * 32}, action, progress) is False
    changed = replace(action, call_id="new-call")
    assert home.accept(BINDING, changed, receipt(changed, "started")) is False
    with pytest.raises(ComputerProtocolError):
        home.accept(BINDING, action, {**progress, "digest": "0" * 64})
    assert home.get(action.action_id, IDENTITY, action.session_id).result is None


def test_out_of_order_or_missing_receipts_do_not_overwrite_evidence(tmp_path, action):
    home = HomeJobIntents(str(tmp_path))
    home.record(BINDING, action)
    assert home.accept(BINDING, action, receipt(action, "busy")) is True
    assert home.get(action.action_id, IDENTITY, action.session_id).result is None
    assert home.accept(BINDING, action, receipt(action, "accepted")) is True
    assert home.accept(BINDING, action, receipt(action, "started")) is True
    assert home.accept(BINDING, action, receipt(action, "stopping")) is True
    assert home.accept(BINDING, action, receipt(action, "started")) is False
    assert home.accept(BINDING, action, receipt(action, "missing")) is True
    assert home.get(action.action_id, IDENTITY, action.session_id).result["status"] == "stopping"

    final = receipt(action, "cancelled")
    assert home.accept(BINDING, action, final) is True
    assert home.accept(BINDING, action, final) is True
    assert home.accept(BINDING, action, receipt(action, "failed", exit_code=1)) is False
    assert home.accept(BINDING, action, receipt(action, "cancelled", log="different evidence")) is False
    reopened = HomeJobIntents(str(tmp_path))
    assert reopened.get(action.action_id, IDENTITY, action.session_id).result == final


def test_report_is_durable_inside_scope_and_must_match_its_hash(tmp_path, action):
    home = HomeJobIntents(str(tmp_path))
    home.record(BINDING, action)
    body = '<testsuite name="vortoone-isolation-fixture" tests="1" failures="0"/>'
    report = {"name": "isolation.xml", "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(), "text": body}
    success = {**receipt(action, "succeeded", exit_code=0), "report": report}
    with pytest.raises(ComputerProtocolError):
        home.accept(BINDING, action, {**success, "report": {**report, "text": body + "tampered"}})
    assert home.get(action.action_id, IDENTITY, action.session_id).result is None
    assert home.accept(BINDING, action, success) is True

    reopened = HomeJobIntents(str(tmp_path))
    assert reopened.get(action.action_id, IDENTITY, action.session_id).result == success
    assert reopened.get(action.action_id, IDENTITY, "another-session") is None


def test_record_rejects_expired_wrong_pairing_or_wrong_fixture_before_disk_write(tmp_path, action):
    home = HomeJobIntents(str(tmp_path))
    with pytest.raises(JobIntentUnavailable):
        home.record(BINDING, replace(action, deadline=int(time.time()) - 1))
    with pytest.raises(JobIntentUnavailable):
        home.record(BINDING, replace(action, version="f" * 64))
    with pytest.raises(JobIntentUnavailable):
        home.record({**BINDING, "connector_id": "f" * 32}, action)
    assert not state_dir(tmp_path).exists()


def test_storage_refuses_a_wider_permission_directory(tmp_path, action):
    directory = state_dir(tmp_path) / "computer-job-intents"
    directory.mkdir(parents=True, mode=0o700)
    directory.chmod(0o755)
    home = HomeJobIntents(str(tmp_path))
    with pytest.raises(JobIntentUnavailable, match="目录权限"):
        home.record(BINDING, action)
    assert not (directory / "intents.sqlite3").exists()


def test_storage_refuses_a_database_symlink(tmp_path, action):
    directory = state_dir(tmp_path) / "computer-job-intents"
    directory.mkdir(parents=True, mode=0o700)
    destination = tmp_path / "outside.sqlite3"
    (directory / "intents.sqlite3").symlink_to(destination)
    home = HomeJobIntents(str(tmp_path))
    with pytest.raises(JobIntentUnavailable):
        home.record(BINDING, action)
    assert not destination.exists()
