"""Wire and local admission contract for the fixed C5 validation job."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import hashlib
import json
import re

from src.gateway.computer_protocol import (
    BINDING_FIELDS,
    ComputerProtocolError,
    binding_fields,
    identifier,
)

_DIGEST = re.compile(r"[a-f0-9]{64}\Z")
_CALL_ID = re.compile(r"[A-Za-z0-9_.:-]{1,120}\Z")
_MAX_TIMESTAMP = 2**53


@dataclass(frozen=True)
class FixtureJob:
    job_id: str
    name: str
    version: str
    input_digest: str
    max_seconds: int


FIXTURE_INPUT = b"vortoone-isolation-probe\n"
FIXTURE_JOB = FixtureJob(
    job_id="63ddbaf8f2f14a4da1413d4801679b02",
    name="Linux 隔离测试",
    version=hashlib.sha256(b"vortoone:c5:linux-isolation-fixture:v1").hexdigest(),
    input_digest=hashlib.sha256(FIXTURE_INPUT).hexdigest(),
    max_seconds=90,
)


@dataclass(frozen=True)
class JobAction:
    action_id: str
    job_id: str
    policy_revision: str
    version: str
    input_digest: str
    deadline: int
    home_instance_id: str
    connector_id: str
    paired_principal_id: str
    credential_epoch: str
    trust_epoch: str
    session_id: str
    turn_id: str
    call_id: str

    def __post_init__(self):
        for key in (*BINDING_FIELDS, "action_id", "job_id", "policy_revision"):
            identifier(getattr(self, key))
        if (
            type(self.deadline) is not int
            or not 0 < self.deadline < _MAX_TIMESTAMP
            or not all(isinstance(value, str) and _DIGEST.fullmatch(value)
                       for value in (self.version, self.input_digest))
            or not all(isinstance(value, str) and _CALL_ID.fullmatch(value)
                       for value in (self.session_id, self.turn_id, self.call_id))
        ):
            raise ComputerProtocolError("invalid computer job action")

    @property
    def digest(self) -> str:
        data = {"operation": "computer-job-v1", **asdict(self)}
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("ascii")
        return hashlib.sha256(encoded).hexdigest()


def job_action_value(value: object) -> JobAction:
    if not isinstance(value, dict) or set(value) != {field.name for field in fields(JobAction)}:
        raise ComputerProtocolError("invalid computer job fields")
    return JobAction(**value)


def fixture_policy(policy_revision: str, *, locally_approved: bool = False) -> dict:
    """Publish bounded metadata from a trusted local approval."""
    identifier(policy_revision)
    if type(locally_approved) is not bool:
        raise ComputerProtocolError("invalid local computer job approval")
    return {"policy_revision": policy_revision, "jobs": [asdict(FIXTURE_JOB)] if locally_approved else []}


def verify_fixture_action(
    action: JobAction,
    *,
    binding: dict,
    policy_revision: str,
    locally_approved: bool = False,
    now: int | float,
) -> None:
    """Intersect the confirmed action with current local authorization before launch."""
    identifier(policy_revision)
    scope = {key: getattr(action, key) for key in BINDING_FIELDS}
    if (
        locally_approved is not True
        or scope != binding_fields(binding)
        or action.policy_revision != policy_revision
        or action.job_id != FIXTURE_JOB.job_id
        or action.version != FIXTURE_JOB.version
        or action.input_digest != FIXTURE_JOB.input_digest
        or type(now) not in (int, float)
        or not 0 < now < action.deadline <= now + FIXTURE_JOB.max_seconds
    ):
        raise ComputerProtocolError("computer job is not authorized")
