"""Desktop stops the currently claimed execution through the existing audited staff path."""
import asyncio
import json
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from src.utils.state_dir import state_dir
from src.workflow.members.registry import MemberRegistry
from src.workflow.staff.routines import Routines
from src.workflow.staff.service import StaffService
from src.workflow.tasks.tasks import TaskRunner
from tests.unit.test_app_routine_create import _decide, _new, _review
from tests.unit.test_member_agents import ORIGIN, OWNER_P, TOKEN, _join, home  # noqa: F401 - pytest fixture


def _approve_routine(home, **extra):
    made = _new(home, **extra)
    assert made.status_code == 200, made.text
    [proposal] = _review(home)
    decided = _decide(home, proposal, True)
    assert decided.status_code == 200 and decided.json()["status"] == "approved"
    return made.json()["id"]


def _dispatch(home, routines, routine_id):
    item = routines.get(routine_id)
    when = datetime.fromisoformat(item["state"]["cursor"]) + timedelta(days=1)
    [task_id] = routines.dispatch(home["service"], when, only=routine_id)
    return task_id


def _stops(root):
    path = state_dir(root) / "audit.log"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    return [row for row in map(json.loads, lines) if row.get("operation") == "主人请求停止角色任务"]


async def test_stop_preserves_the_schedule_and_cancels_only_the_current_occurrence(home, monkeypatch):
    root, base = home["root"], home["base"]
    routine_id = _approve_routine(home)
    routines = Routines(root)
    path = f"{base}/routines/{routine_id}/stop"
    started = asyncio.Queue()

    async def wait_for_stop(task, work, role, staff, check):
        check()
        started.put_nowait(task.id)
        await asyncio.Event().wait()

    pool = TaskRunner(root, None, max_concurrent=2)
    service = StaffService(root, pool, execute=wait_for_stop)
    monkeypatch.setitem(home, "service", service)
    monkeypatch.setattr("src.scheduling.staff_runner.get_staff_service", lambda _root: service)
    try:
        task_id = _dispatch(home, routines, routine_id)
        assert await asyncio.wait_for(started.get(), 2) == task_id
        unrelated = service.assign("sid-owner", {"member": home["staff"]["id"], "prompt": "另一项无关工作"})
        snapshot = routines.get(routine_id)

        async with AsyncClient(transport=ASGITransport(app=home["app"]), base_url="http://testserver",
                               headers={"Authorization": f"Bearer {TOKEN}"}) as client:
            active = (await client.get(base + "/routines")).json()["routines"][0]
            assert active["state"] == "running" and active["lastRun"]["id"] == task_id
            stopped = await client.post(path, json={})
            assert stopped.status_code == 200, stopped.text
            assert stopped.json()["id"] == routine_id
            await asyncio.wait_for(pool.join(task_id), 2)
            row = (await client.get(base + "/routines")).json()["routines"][0]
            assert row["state"] == "active" and row["lastRun"]["status"] == "canceled"
            assert row["health"] == {"ok": 0, "silent": 0, "failed": 0, "skipped": 1, "missed": 0, "total": 1}
            assert pool.ledger.load(task_id).status == "cancelled"
            assert pool.ledger.load(unrelated.id).status in {"queued", "running"}
            after = routines.get(routine_id)
            for field in ("schedule", "enabled", "revision"):
                assert after[field] == snapshot[field]
            assert after["state"]["cursor"] == snapshot["state"]["cursor"]
            assert after["state"]["task"] is None
            assert (await client.post(path, json={})).status_code == 409
            assert [event["staff_task"] for event in _stops(root)] == [task_id]

            next_id = _dispatch(home, routines, routine_id)
            assert next_id != task_id and routines.get(routine_id)["state"]["task"] == next_id
            assert (await client.post(path, json={})).status_code == 200
            await asyncio.wait_for(pool.join(next_id), 2)
            assert pool.ledger.load(next_id).status == "cancelled"
            assert {event["staff_task"] for event in _stops(root)} == {task_id, next_id}
    finally:
        await pool.shutdown()


def test_stop_is_denied_before_cancel_when_audit_is_unavailable_and_reports_runner_rejection(home, monkeypatch):
    root = home["root"]
    routine_id = _approve_routine(home)
    routines = Routines(root)
    task_id = _dispatch(home, routines, routine_id)
    path = f"{home['base']}/routines/{routine_id}/stop"
    attempts = []
    monkeypatch.setattr(home["pool"], "cancel", lambda tid: attempts.append(tid) or False, raising=False)

    def no_audit(_root, event, *, durable):
        assert event["staff_task"] == task_id and durable
        return False

    with monkeypatch.context() as refusal:
        refusal.setattr("src.workflow.staff.conversations.append_audit", no_audit)
        assert home["owner"].post(path, json={}).status_code == 409
        assert attempts == []
    refused = home["owner"].post(path, json={})
    assert refused.status_code == 409 and "当前执行池" in refused.json()["error"]["message"]
    assert attempts == [task_id]
    assert routines.get(routine_id)["state"]["task"] == task_id


def test_stop_checks_the_claim_and_never_stops_another_routine(home, monkeypatch):
    root, base, owner = home["root"], home["base"], home["owner"]
    first = _approve_routine(home, name="一号")
    second = _approve_routine(home, name="二号")
    routines = Routines(root)
    second_task = _dispatch(home, routines, second)
    state = routines.read()
    next(item for item in state["routines"] if item["id"] == first)["state"]["task"] = second_task
    routines.registry._write("routines.json", state)

    stopped = []
    monkeypatch.setattr(home["pool"], "cancel", lambda tid: stopped.append(tid) or True, raising=False)
    assert owner.post(f"{base}/routines/{first}/stop", json={}).status_code == 409
    assert stopped == [] and _stops(root) == []
    assert owner.post(f"{base}/routines/{second}/stop", json={}).status_code == 200
    assert stopped == [second_task]


def test_stop_requires_owner_proof_and_cannot_stop_unapproved_or_idle_routines(home, monkeypatch):
    base, owner = home["base"], home["owner"]
    pending = _new(home, name="待批准")
    assert pending.status_code == 200
    pending_id = pending.json()["id"]
    pending_path = f"{base}/routines/{pending_id}/stop"
    assert owner.post(pending_path, json={}).status_code == 404
    [proposal] = _review(home)
    assert _decide(home, proposal, True).status_code == 200
    path = pending_path
    assert owner.post(path, json={"taskId": "arbitrary"}).status_code == 422
    assert owner.post(path, json={}).json()["error"]["code"] == "routine.not_running"
    member = _join(home)
    registry = MemberRegistry(home["root"])
    managers = registry.create_group(OWNER_P, "例行管理", items=["roles.manage_routines"])
    registry.set_member_groups(OWNER_P, member.member_id, [managers])
    assert member.post(path, json={}).status_code == 403
    visitor = TestClient(home["app"])
    assert visitor.post(path, json={}).status_code == 401
    assert visitor.post("/api/auth/login", json={"token": TOKEN}, headers=ORIGIN).status_code == 200
    assert visitor.post(path, json={}, headers=ORIGIN).status_code == 409
    assert visitor.post(path, json={}, headers={"Origin": "https://elsewhere.example"}).status_code == 403
    monkeypatch.delenv("VORTOONE_API_TOKEN")
    assert visitor.post(path, json={}, headers=ORIGIN).status_code == 403
