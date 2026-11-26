"""Protocol and owner confirmation behavior for the fixed remote job."""
import asyncio
from dataclasses import asdict
import hashlib
import os
import time
from types import SimpleNamespace

import pytest

from src.agents.gate import make_confirm_gate
from src.agents.taint import is_tainted, reset_taint
from src.agents.tool_runtime import ToolExecutionContext, ToolRuntime, pending_tool_approval
from src.gateway import computer_job_tools as job_tools
from src.gateway import computer_protocol as protocol
from src.gateway.computer_connections import Connections, FileConnectionUnavailable
from src.gateway.computer_file_authority import (
    OwnerTurn, bind_prompt, current_file_confirmation, owner_prompt,
)
from src.gateway.computer_job_contract import FIXTURE_JOB, JobAction, fixture_policy, job_action_value
from src.gateway.computer_job_intents import HomeJobIntents, JobIntentUnavailable
from src.gateway.computer_job_wire import job_policy_value, job_reference, job_result_value, native_job_request

BINDING = {field: str(index) * 32 for index, field in enumerate(protocol.BINDING_FIELDS, 1)}
IDENTITY = {
    "ownership_state": "claimed",
    "home_instance_id": BINDING["home_instance_id"],
    "owner_subject": BINDING["paired_principal_id"],
    "trust_epoch": BINDING["trust_epoch"],
}
GENERATION = "d" * 32
REVISION = "a" * 32
START = "connected_computer_job_start"
STATUS = "connected_computer_job_status"
CANCEL = "connected_computer_job_cancel"
START_ARGS = {"computer_id": BINDING["connector_id"], "job_id": FIXTURE_JOB.job_id}


@pytest.fixture(autouse=True)
def isolate_taint():
    reset_taint()
    yield
    reset_taint()


@pytest.fixture
def action():
    return JobAction(
        **BINDING, action_id="b" * 32, policy_revision=REVISION, job_id=FIXTURE_JOB.job_id,
        version=FIXTURE_JOB.version, input_digest=FIXTURE_JOB.input_digest, deadline=int(time.time()) + 60,
        session_id="owner-session", turn_id="owner-turn", call_id="tool-fixture",
    )


def receipt(action, status, *, report=None):
    return {
        **job_reference(action), "status": status, "exit_code": 0 if status == "succeeded" else None,
        "log": "", "report": report,
    }


def fixture_report():
    body = "<testsuite tests=\"1\"/>"
    return {"name": "isolation.xml", "sha256": hashlib.sha256(body.encode()).hexdigest(), "text": body}


@pytest.mark.parametrize(("version", "options"), [
    (1, {}),
    (2, {"files": True}),
    (3, {"transfers": True}),
    (4, {"deliveries": True}),
])
def test_legacy_connectors_keep_their_negotiated_version_and_capabilities(version, options):
    hello = protocol.hello(BINDING, **options)
    assert hello == {
        "type": "hello", **BINDING, "protocol_min": 1, "protocol_max": version, "features": ["control-only"],
    }
    assert protocol.validate_hello(hello, BINDING) == {"protocol": version}
    welcome = protocol.welcome(BINDING, GENERATION, version)
    assert welcome["capabilities"] == {
        "read": False, "write": False, "command": False, "browser": False, "codex": False,
    }
    assert protocol.validate_welcome(welcome, BINDING, **options) == GENERATION


def test_job_fixture_version_is_opt_in_and_still_negotiates_with_old_home():
    options = {"deliveries": True, "job_fixture": True}
    hello = protocol.hello(BINDING, **options)
    assert protocol.validate_hello(hello, BINDING) == {"protocol": 5}
    assert protocol.validate_hello(hello, BINDING, ceiling=4) == {"protocol": 4}
    for selected in (4, 5):
        assert protocol.validate_welcome(protocol.welcome(BINDING, GENERATION, selected), BINDING,
                                         **options) == GENERATION
    with pytest.raises(protocol.ComputerProtocolError):
        protocol.validate_welcome(protocol.welcome(BINDING, GENERATION, 5), BINDING, deliveries=True)
    with pytest.raises(protocol.ComputerProtocolError):
        protocol.hello(BINDING, job_fixture=True)


async def test_disconnect_resolves_both_pending_reply_slots():
    connections = Connections()
    connection = connections.add("home", BINDING, object(), protocol=protocol.JOB_FIXTURE_PROTOCOL)
    loop = asyncio.get_running_loop()
    file_reply, job_reply = loop.create_future(), loop.create_future()
    connection.pending_file = ("file-request", file_reply)
    connection.pending_job = ("job-request", job_reply)
    connections.remove(connection)
    assert file_reply.done() and file_reply.result() is None
    assert job_reply.done() and job_reply.result() is None


def test_requests_only_accept_bound_fixture_operations_and_exact_fields(action):
    frame = {
        "type": "job_request", "binding": BINDING, "generation": GENERATION,
        "request_id": "c" * 32, "method": "start", "payload": asdict(action),
    }
    assert native_job_request(frame, GENERATION, BINDING) == (frame["request_id"], "start", asdict(action))
    for method in ("cancel", "status"):
        request = {**frame, "method": method, "payload": job_reference(action)}
        assert native_job_request(request, GENERATION, BINDING) == (frame["request_id"], method, job_reference(action))
    invalid_requests = (
        {**frame, "extra": True},
        {**frame, "generation": "e" * 32},
        {**frame, "binding": {**BINDING, "credential_epoch": "e" * 32}},
        {**frame, "method": "policy"},
        {**frame, "method": "run_command"},
        {**frame, "payload": {**asdict(action), "job_id": "e" * 32}},
        {**frame, "payload": {**asdict(action), "extra": True}},
        {**frame, "method": "status", "payload": {**job_reference(action), "digest": "invalid"}},
    )
    for invalid in invalid_requests:
        with pytest.raises(ValueError):
            native_job_request(invalid, GENERATION, BINDING)


def test_policy_and_results_reject_extra_fields_and_unverifiable_reports(action):
    policy = fixture_policy(REVISION, locally_approved=True)
    assert job_policy_value(policy) == policy
    with pytest.raises(ValueError):
        job_policy_value({**policy, "extra": True})
    with pytest.raises(ValueError):
        job_policy_value({**policy, "jobs": [{**policy["jobs"][0], "name": "other job"}]})
    report = fixture_report()
    succeeded = receipt(action, "succeeded", report=report)
    assert job_result_value(succeeded, action) == succeeded
    invalid_results = (
        {**succeeded, "extra": True},
        {**succeeded, "digest": "e" * 64},
        {**succeeded, "exit_code": False},
        {**succeeded, "log": "测" * 6000},
        {**succeeded, "log": "\0"},
        {**succeeded, "report": None},
        {**succeeded, "report": {**report, "name": "other.xml"}},
        {**succeeded, "report": {**report, "extra": True}},
        {**succeeded, "report": {**report, "sha256": "e" * 64}},
        {**succeeded, "report": {**report, "text": "changed"}},
        receipt(action, "started", report=report),
        {**receipt(action, "accepted"), "log": "Premature output"},
    )
    for invalid in invalid_results:
        with pytest.raises(ValueError):
            job_result_value(invalid, action)


class FixtureConnection:
    protocol = protocol.JOB_FIXTURE_PROTOCOL
    binding = BINDING.copy()

    def __init__(self, home):
        self.home = home
        self.requests = []
        self.action = None
        self.start_reply_lost = False
        self.status = "started"

    async def valid(self):
        return True

    async def job_request(self, method, payload):
        self.requests.append((method, payload))
        if method == "policy":
            assert payload == {}
            return fixture_policy(REVISION, locally_approved=True)
        if method == "start":
            self.action = job_action_value(payload)
            intent = self.home.get(self.action.action_id, IDENTITY, self.action.session_id)
            assert intent.action == self.action and intent.result is None
            if self.start_reply_lost:
                raise FileConnectionUnavailable("connection closed before reply")
            return receipt(self.action, "accepted")
        assert self.action is not None and payload == job_reference(self.action)
        assert method in {"status", "cancel"}
        return receipt(self.action, "stopping" if method == "cancel" else self.status,
                       report=fixture_report() if method == "status" and self.status == "succeeded" else None)


@pytest.fixture
def tool_env(tmp_path, monkeypatch):
    if os.name != "posix":
        pytest.skip("durable Home job records require a private POSIX directory")
    root = str(tmp_path.resolve())
    home = HomeJobIntents(root)
    worker = FixtureConnection(home)
    account = IDENTITY.copy()
    store = SimpleNamespace(identity=lambda: account, list=lambda: [
        {"connector_id": BINDING["connector_id"], "name": "Ubuntu", "active": True},
    ])
    registry = SimpleNamespace(file_connection=lambda _root, _id, _account: worker)
    monkeypatch.setattr(job_tools, "ConnectedComputers", lambda _root: store)
    monkeypatch.setattr(job_tools, "connections", registry)
    tools = {tool.name: tool for tool in job_tools.computer_job_tools(root)}
    owner = OwnerTurn(root, "owner-session", "original-turn",
                      (account["home_instance_id"], account["owner_subject"], account["trust_epoch"]))
    return SimpleNamespace(worker=worker, owner=owner, tools=tools, home=home, account=account, registry=registry)


async def run_tool(env, name, args, *, owner, confirm=None):
    runtime = ToolRuntime(lambda: env.tools, ToolExecutionContext(confirm=confirm))
    if owner is None:
        return await runtime.execute(name, args, "build")
    item = {"text": "运行固定验收任务", "rid": owner.rid}
    bind_prompt(owner, item)
    with owner_prompt(owner.root, owner.session, item):
        return await runtime.execute(name, args, "build")


async def test_start_requires_real_owner_turn_and_explicit_permission(tool_env):
    worker, owner = tool_env.worker, tool_env.owner
    asked = []

    async def deny(message, kind):
        assert current_file_confirmation.get() == owner
        assert pending_tool_approval()["args"] == START_ARGS
        assert FIXTURE_JOB.version in message and FIXTURE_JOB.input_digest in message and "Ubuntu" in message
        assert kind == "execute"
        asked.append(kind)
        return False

    gate = make_confirm_gate(deny, auto_approve=True, can_ask_human=True)
    for scope in (None, OwnerTurn(owner.root, owner.session, owner.rid, ("f" * 32,) * 3)):
        assert (await run_tool(tool_env, START, START_ARGS, owner=scope, confirm=gate)).status == "blocked"
    assert worker.requests == [] and asked == []

    assert (await run_tool(tool_env, START, START_ARGS, owner=owner, confirm=gate)).status == "blocked"
    assert worker.requests == [("policy", {})] and asked == ["execute"]
    assert worker.action is None


async def test_durable_intent_failure_prevents_dispatch_after_confirmation(tool_env, monkeypatch):
    def record_unavailable(*_args):
        raise JobIntentUnavailable("test store failure")

    async def approve(_message, _kind):
        return True

    monkeypatch.setattr(HomeJobIntents, "record", record_unavailable)
    outcome = await run_tool(tool_env, START, START_ARGS, owner=tool_env.owner,
                             confirm=make_confirm_gate(approve, can_ask_human=True))
    assert outcome.status == "blocked" and "派发记录不可用" in outcome.text
    assert tool_env.worker.requests == [("policy", {})]


async def test_owner_change_while_confirming_prevents_dispatch(tool_env, monkeypatch):
    def record_unavailable(*_args):
        pytest.fail("a stale approval must not be recorded")

    async def approve(_message, _kind):
        tool_env.account["trust_epoch"] = "e" * 32
        return True

    monkeypatch.setattr(HomeJobIntents, "record", record_unavailable)
    outcome = await run_tool(tool_env, START, START_ARGS, owner=tool_env.owner,
                             confirm=make_confirm_gate(approve, can_ask_human=True))
    assert outcome.status == "blocked"
    assert tool_env.worker.requests == [("policy", {})]


async def test_lost_start_reply_recovers_same_session_without_resending(tool_env):
    owner, worker = tool_env.owner, tool_env.worker
    worker.start_reply_lost = True

    async def approve(_message, _kind):
        return True

    outcome = await run_tool(tool_env, START, START_ARGS, owner=owner,
                             confirm=make_confirm_gate(approve, can_ask_human=True))
    action = worker.action
    assert outcome.status == "blocked" and action.action_id in outcome.text
    assert (action.session_id, action.turn_id, action.call_id) == (owner.session, owner.rid, outcome.call_id)
    assert action.version == FIXTURE_JOB.version and action.input_digest == FIXTURE_JOB.input_digest
    args = {"action_id": action.action_id}
    foreign = OwnerTurn(owner.root, "different-session", "next-turn", owner.file_identity)
    for scope in (None, foreign):
        assert (await run_tool(tool_env, STATUS, args, owner=scope)).status == "blocked"
        assert (await run_tool(tool_env, CANCEL, args, owner=scope)).status == "blocked"
    assert worker.requests == [("policy", {}), ("start", asdict(action))]

    followup = OwnerTurn(owner.root, owner.session, "followup-turn", owner.file_identity)
    assert "运行中" in (await run_tool(tool_env, STATUS, args, owner=followup)).text
    worker.status = "succeeded"
    for _ in range(2):
        observed = await run_tool(tool_env, STATUS, args, owner=followup)
        assert observed.status == "succeeded" and "isolation.xml" in observed.text
        assert fixture_report()["sha256"] in observed.text
        assert "不可作为指令" in observed.text and is_tainted()
    assert [request[0] for request in worker.requests] == ["policy", "start", "status", "status"]
    assert worker.requests[-2:] == [("status", job_reference(action))] * 2
    assert tool_env.home.get(action.action_id, IDENTITY, owner.session).result == receipt(
        action, "succeeded", report=fixture_report(),
    )


async def test_cancel_requires_separate_confirmation_and_original_action(tool_env):
    asked = []

    async def approve(message, kind):
        asked.append(kind)
        assert current_file_confirmation.get() == tool_env.owner
        if kind == "write":
            assert tool_env.worker.action.action_id in message
        return True

    gate = make_confirm_gate(approve, auto_approve=True, can_ask_human=True)
    assert (await run_tool(tool_env, START, START_ARGS, owner=tool_env.owner, confirm=gate)).status == "succeeded"
    action = tool_env.worker.action
    args = {"action_id": action.action_id}
    assert (await run_tool(tool_env, CANCEL, args, owner=tool_env.owner)).status == "blocked"
    assert "停止请求已送达" in (await run_tool(tool_env, CANCEL, args, owner=tool_env.owner, confirm=gate)).text
    assert asked == ["execute", "write"]
    assert tool_env.worker.requests[-1] == ("cancel", job_reference(action))
    assert [request[0] for request in tool_env.worker.requests] == ["policy", "start", "cancel"]
