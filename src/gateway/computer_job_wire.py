"""Bounded protocol messages for the opt-in fixed job fixture."""
from __future__ import annotations

from dataclasses import asdict
import hashlib

from src.gateway.computer_file_wire import bounded_frame, digest_value, object_fields
from src.gateway.computer_job_contract import FIXTURE_JOB, JobAction, fixture_policy, job_action_value
from src.gateway.computer_protocol import ComputerProtocolError, binding_fields, identifier

JOB_TERMINAL = frozenset({"succeeded", "failed", "cancelled", "timed_out", "blocked", "unknown"})
_OBSERVATIONS = frozenset({"accepted", "started", "stopping", "busy", "missing"})
_TEXT_LIMIT = 16 * 1024


def job_policy_value(value: dict) -> dict:
    object_fields(value, ("policy_revision", "jobs"))
    revision = identifier(value["policy_revision"])
    approved = fixture_policy(revision, locally_approved=True)
    if value["jobs"] not in ([], approved["jobs"]):
        raise ComputerProtocolError("invalid fixture job policy")
    return value


def job_reference(action: JobAction) -> dict:
    return {"action_id": action.action_id, "digest": action.digest}


def job_result_value(value: dict, action: JobAction) -> dict:
    object_fields(value, ("action_id", "digest", "status", "exit_code", "log", "report"))
    status, exit_code, log, report = (value["status"], value["exit_code"], value["log"], value["report"])
    if (value["action_id"] != action.action_id or value["digest"] != action.digest
            or not isinstance(status, str) or status not in JOB_TERMINAL | _OBSERVATIONS
            or (exit_code is not None and (type(exit_code) is not int or not -255 <= exit_code <= 255))
            or not isinstance(log, str) or len(log.encode("utf-8")) > _TEXT_LIMIT
            or any(not ch.isprintable() and ch not in "\n\r\t" for ch in log)):
        raise ComputerProtocolError("invalid fixture job result")
    if status not in JOB_TERMINAL and (exit_code is not None or log or report is not None):
        raise ComputerProtocolError("nonterminal fixture results cannot publish output")
    if status == "succeeded":
        if exit_code != 0:
            raise ComputerProtocolError("invalid fixture exit code")
        object_fields(report, ("name", "sha256", "text"))
        if report["name"] != "isolation.xml" or not isinstance(report["text"], str):
            raise ComputerProtocolError("invalid fixture report")
        body = report["text"].encode("utf-8")
        if (not 0 < len(body) <= _TEXT_LIMIT
                or any(not ch.isprintable() and ch not in "\n\r\t" for ch in report["text"])
                or hashlib.sha256(body).hexdigest() != report["sha256"]):
            raise ComputerProtocolError("invalid fixture report")
    elif report is not None:
        raise ComputerProtocolError("invalid fixture report")
    return value


def native_job_request(frame: dict, generation: str, binding: dict) -> tuple[str, str, dict]:
    object_fields(frame, ("type", "generation", "binding", "request_id", "method", "payload"))
    if (frame["type"] != "job_request" or frame["generation"] != generation
            or frame["binding"] != binding_fields(binding)):
        raise ComputerProtocolError("stale fixture job request")
    request_id, method, payload = identifier(frame["request_id"]), frame["method"], frame["payload"]
    if method == "policy":
        object_fields(payload, ())
    elif method == "start":
        action = job_action_value(payload)
        if action.job_id != FIXTURE_JOB.job_id:
            raise ComputerProtocolError("unknown fixture job")
        payload = asdict(action)
    elif method in ("status", "cancel"):
        object_fields(payload, ("action_id", "digest"))
        identifier(payload["action_id"])
        digest_value(payload["digest"])
    else:
        raise ComputerProtocolError("invalid fixture job method")
    return request_id, method, payload


def job_reply(request_id: str, generation: str, result: dict | None) -> dict:
    return bounded_frame({"type": "job_reply", "generation": generation, "request_id": request_id,
                          "ok": result is not None, "result": {} if result is None else result})
