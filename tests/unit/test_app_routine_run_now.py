"""The Desktop manual gesture uses the existing reviewed routine and shared worker pool."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
import pytest

from src.utils.state_dir import state_dir
from src.workflow.members.registry import MemberRegistry
from src.workflow.staff.owner_confirmation import require_owner_route
from src.workflow.staff.routines import Routines
from src.workflow.staff.registry import StaffRegistry
from src.workflow.staff.service import StaffService
from src.workflow.tasks.tasks import TaskLedger, TaskRunner
from tests.unit.test_app_routine_create import _decide, _new, _review
from tests.unit.test_member_agents import ORIGIN, OWNER_P, TOKEN, _finish, _join, home  # noqa: F401 - pytest fixture


def _approve(home, **extra):
    made = _new(home, **extra)
    assert made.status_code == 200, made.text
    [proposal] = _review(home)
    decided = _decide(home, proposal, True)
    assert decided.status_code == 200 and decided.json()["status"] == "approved"
    return made.json()["id"]


def _runs(root):
    path = state_dir(root) / "audit.log"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    return [row for row in map(json.loads, lines) if row.get("operation") == "主人手动运行例行工作"]


async def test_run_now_queues_the_reviewed_binding_without_changing_schedule(home):
    root, base, owner = home["root"], home["base"], home["owner"]
    routine_id = _approve(home)
    routines = Routines(root)
    previous = routines.get(routine_id)
    path = f"{base}/routines/{routine_id}/run"

    with ThreadPoolExecutor(max_workers=2) as pool:
        def click():
            browser = TestClient(home["app"], headers={"Authorization": f"Bearer {TOKEN}"})
            return browser.post(path, json={})

        first, second = sorted((pool.submit(click), pool.submit(click)), key=lambda future: future.result().status_code)
        started, duplicate = first.result(), second.result()
    assert [started.status_code, duplicate.status_code] == [200, 409]
    row = started.json()
    assert row["id"] == routine_id and row["state"] == "running"
    [task_id] = home["pool"].jobs
    assert row["lastRun"]["id"] == task_id and row["lastRun"]["status"] == "queued"
    task = TaskLedger(root).load(task_id)
    assert task.prompt == previous["prompt"] and task.status == "queued"
    dispatch = task.collaboration["dispatch"]
    assert dispatch["member"] == previous["member"]
    assert dispatch["routine"]["id"] == routine_id
    assert dispatch["routine"]["manual"] is True and dispatch["routine"]["revision"] == previous["revision"]
    recorded = routines.get(routine_id)
    for field in ("revision", "binding", "schedule", "enabled"):
        assert recorded[field] == previous[field]
    assert recorded["state"]["cursor"] == previous["state"]["cursor"]
    [audit] = _runs(root)
    assert audit["routine_id"] == routine_id
    assert audit["decision_source"] == "user" and audit["approval_scope"] == "once"
    assert len(audit["action_digest"]) == 64

    await _finish(home["pool"], task_id)
    task = TaskLedger(root).load(task_id)
    assert task.status == "done" and task.collaboration["review"] == "pending"
    assert owner.post(path, json={}).status_code == 409
    assert list(home["pool"].jobs) == [task_id] and _runs(root) == [audit]


async def test_manual_routine_worker_cannot_reuse_the_http_owner_proof(home, monkeypatch):
    root, base = home["root"], home["base"]
    routine_id = _approve(home)
    runner = TaskRunner(root, None)
    checked = []

    async def execute(task, work, role, member, check):
        check()
        with pytest.raises(ValueError, match="已认证的主人写入路由"):
            require_owner_route(root)
        checked.append(("execute", task.id))
        return "核对完成"

    original = runner.enqueue_existing

    def enqueue(task_id, *args, **kwargs):
        with pytest.raises(ValueError, match="已认证的主人写入路由"):
            require_owner_route(root)
        checked.append(("enqueue", task_id))
        return original(task_id, *args, **kwargs)

    service = StaffService(root, runner, execute=execute)
    monkeypatch.setattr(runner, "enqueue_existing", enqueue)
    monkeypatch.setattr("src.scheduling.staff_runner.get_staff_service", lambda _root: service)
    try:
        async with AsyncClient(transport=ASGITransport(app=home["app"]), base_url="http://testserver",
                               headers={"Authorization": f"Bearer {TOKEN}"}) as client:
            response = await client.post(f"{base}/routines/{routine_id}/run", json={})
            assert response.status_code == 200, response.text
            task_id = response.json()["lastRun"]["id"]
        await asyncio.wait_for(runner.join(task_id), 2)
        assert checked == [("enqueue", task_id), ("execute", task_id)]
        assert runner.ledger.load(task_id).status == "done"
    finally:
        await runner.shutdown()


def test_run_now_requires_durable_audit_and_reports_existing_worker_rejection(home, monkeypatch):
    root = home["root"]
    routine_id = _approve(home)
    routines = Routines(root)
    earlier = routines.get(routine_id)
    path = f"{home['base']}/routines/{routine_id}/run"

    def unavailable(_root, audit, *, durable):
        assert audit["routine_id"] == routine_id and durable
        return False

    with monkeypatch.context() as denied:
        denied.setattr("src.gateway.audit.append_audit", unavailable)
        result = home["owner"].post(path, json={})
    assert result.status_code == 409 and "审计" in result.json()["error"]["message"]
    assert routines.get(routine_id) == earlier
    assert home["pool"].jobs == {} and TaskLedger(root).list() == []

    monkeypatch.setattr(home["pool"], "enqueue_existing", lambda *args, **kwargs: False)
    refused = home["owner"].post(path, json={})
    assert refused.status_code == 409 and "后台未接到" in refused.json()["error"]["message"]
    parked = routines.get(routine_id)["state"]
    assert parked["error"] and parked["cursor"] == earlier["state"]["cursor"]
    assert home["owner"].post(path, json={}).status_code == 409
    assert home["pool"].jobs == {} and len(_runs(root)) == 1


def test_run_now_refuses_unapproved_paused_one_off_and_changed_bindings(home):
    root, base, owner = home["root"], home["base"], home["owner"]
    pending = _new(home)
    assert pending.status_code == 200
    pending_id = pending.json()["id"]
    path = f"{base}/routines/{pending_id}/run"
    assert owner.post(path, json={}).status_code == 404
    [proposal] = _review(home)
    assert _decide(home, proposal, True).status_code == 200
    settings = f"{base}/routines/{pending_id}"
    assert owner.patch(settings, json={"active": False}).status_code == 200
    assert owner.post(path, json={}).status_code == 409
    assert owner.patch(settings, json={"active": True}).status_code == 200
    once = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    once_id = _approve(home, trigger={"type": "once", "at": once})
    assert owner.post(f"{base}/routines/{once_id}/run", json={}).status_code == 409
    registry = StaffRegistry(root)
    registry.register_member({**home["staff"], "model": "changed-for-regression"})
    changed = owner.post(path, json={})
    assert changed.status_code == 409 and "重新提议" in changed.json()["error"]["message"]
    assert home["pool"].jobs == {} and TaskLedger(root).list() == [] and _runs(root) == []


def test_run_now_does_not_accept_payload_overrides_or_member_or_cross_origin_requests(home):
    routine_id = _approve(home)
    path = f"{home['base']}/routines/{routine_id}/run"
    assert home["owner"].post(path, json={"prompt": "override"}).status_code == 422
    member = _join(home)
    registry = MemberRegistry(home["root"])
    managers = registry.create_group(OWNER_P, "例行管理", items=["roles.manage_routines"])
    registry.set_member_groups(OWNER_P, member.member_id, [managers])
    assert member.post(path, json={}).status_code == 403
    browser = TestClient(home["app"])
    assert browser.post(path, json={}).status_code == 401
    assert browser.post("/api/auth/login", json={"token": TOKEN}, headers=ORIGIN).status_code == 200
    assert browser.post(path, json={}, headers={"Origin": "https://elsewhere.example"}).status_code == 403
    assert home["pool"].jobs == {} and _runs(home["root"]) == []
    assert browser.post(path, json={}, headers=ORIGIN).status_code == 200
