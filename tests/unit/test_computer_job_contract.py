"""Strict admission for the fixed C5 validation job."""
from dataclasses import asdict, replace
import hashlib

import pytest

from src.gateway.computer_job_contract import (
    FIXTURE_INPUT,
    FIXTURE_JOB,
    JobAction,
    fixture_policy,
    job_action_value,
    verify_fixture_action,
)
from src.gateway.computer_protocol import BINDING_FIELDS, ComputerProtocolError


BINDING = {key: str(index) * 32 for index, key in enumerate(BINDING_FIELDS, 1)}
REVISION = "6" * 32
NOW = 1_700_000_000
ACTION = JobAction(
    action_id="7" * 32,
    job_id=FIXTURE_JOB.job_id,
    version=FIXTURE_JOB.version,
    input_digest=FIXTURE_JOB.input_digest,
    policy_revision=REVISION,
    deadline=NOW + FIXTURE_JOB.max_seconds,
    session_id="session",
    turn_id="turn",
    call_id="call",
    **BINDING,
)


def test_fixture_policy_defaults_closed_and_discloses_only_confirmation_metadata():
    assert fixture_policy(REVISION) == {"policy_revision": REVISION, "jobs": []}
    assert fixture_policy(REVISION, locally_approved=True) == {
        "policy_revision": REVISION,
        "jobs": [
            {
                "job_id": FIXTURE_JOB.job_id,
                "name": "Linux 隔离测试",
                "version": FIXTURE_JOB.version,
                "input_digest": hashlib.sha256(FIXTURE_INPUT).hexdigest(),
                "max_seconds": 90,
            }
        ],
    }
    with pytest.raises(ComputerProtocolError):
        fixture_policy(REVISION, locally_approved=1)


def test_fixture_action_roundtrips_regardless_of_field_order():
    encoded = dict(reversed(tuple(asdict(ACTION).items())))
    parsed = job_action_value(encoded)
    assert parsed == ACTION
    assert parsed.digest == ACTION.digest
    assert verify_fixture_action(
        parsed, binding=BINDING, policy_revision=REVISION, locally_approved=True, now=NOW,
    ) is None


@pytest.mark.parametrize("extra", ["command", "cwd", "args", "environment", "approved"])
def test_fixture_action_rejects_extra_fields(extra):
    with pytest.raises(ComputerProtocolError, match="invalid computer job fields"):
        job_action_value({**asdict(ACTION), extra: None})
    missing = asdict(ACTION)
    del missing["call_id"]
    with pytest.raises(ComputerProtocolError, match="invalid computer job fields"):
        job_action_value(missing)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("action_id", "a" * 33),
        ("version", "A" * 64),
        ("input_digest", "a" * 63),
        ("deadline", True),
        ("deadline", 2**53),
        ("session_id", "a" * 121),
        ("turn_id", "a\n"),
        ("call_id", "../call"),
    ],
)
def test_wire_rejects_malformed_action_values(field, value):
    with pytest.raises(ComputerProtocolError):
        job_action_value({**asdict(ACTION), field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        *((key, "a" * 32) for key in BINDING_FIELDS),
        ("job_id", "a" * 32),
        ("policy_revision", "a" * 32),
        ("version", "a" * 64),
        ("input_digest", "a" * 64),
        ("deadline", NOW),
        ("deadline", ACTION.deadline + 1),
    ],
)
def test_valid_wire_action_can_exceed_current_local_authorization(field, value):
    modified = replace(ACTION, **{field: value})
    assert modified.digest != ACTION.digest
    with pytest.raises(ComputerProtocolError, match="not authorized"):
        verify_fixture_action(
            modified, binding=BINDING, policy_revision=REVISION, locally_approved=True, now=NOW,
        )


def test_fixture_action_rejects_when_local_approval_has_been_revoked():
    with pytest.raises(ComputerProtocolError, match="not authorized"):
        verify_fixture_action(ACTION, binding=BINDING, policy_revision=REVISION, now=NOW)
