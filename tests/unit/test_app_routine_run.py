"""例行工作的「立即运行」，以及完整能力的 agent 不能担任例行工作时当场说清楚。

#1027 以后界面里新建的 agent 都是完整能力，而执行时一律拒绝它跑例行工作：建得出来、批得了、到点必失败。
"""
import asyncio
import json
import time
from contextvars import copy_context
from types import SimpleNamespace

import httpx
import pytest

from src.utils.state_dir import state_dir
from src.workflow.staff.approvals import digest
from src.workflow.staff.owner_confirmation import require_owner_route
from src.workflow.staff.routines import Routines
from src.workflow.staff.service import StaffService
from src.workflow.tasks.tasks import TaskLedger, TaskRunner
from tests.unit.test_member_agents import _join, home  # noqa: F401 - pytest fixture


def _propose(home, agent, **extra):
    return home["owner"].post(f"{home['base']}/routines", json={
        "name": "晨报", "agentId": agent, "prompt": "整理昨天的动态",
        "trigger": {"type": "schedule", "phrase": "每天早上9点", "timezone": "Asia/Shanghai"}, **extra})


def _approve(home):
    [proposal] = home["owner"].get(f"{home['base']}/routines/proposals/review").json()["proposals"]
    decided = home["owner"].post(f"{home['base']}/routines/proposals/{proposal['id']}/decision",
                                 json={"approve": True, **proposal["proof"]})
    assert decided.status_code == 200, decided.text


def test_a_full_ability_agent_is_refused_when_the_routine_is_created_not_when_it_is_due(home):
    made = home["owner"].post(f"{home['base']}/agents", json={"name": "全能", "handle": "helper"})
    assert made.status_code == 200, made.text
    refused = _propose(home, made.json()["id"])
    assert refused.status_code >= 400 and "完整能力" in refused.json()["error"]["message"]
    assert Routines(home["root"]).list() == []


@pytest.fixture
def pool(home, monkeypatch):
    state = SimpleNamespace(release=None, runs=0)

    async def execute(task, work, role, member, check):
        state.runs += 1
        state.audit_at_start = _manual_audits(home["root"])
        state.worker_context = copy_context()
        if state.release is not None:
            await state.release.wait()
        return "做完了"
    service = StaffService(home["root"], TaskRunner(home["root"], None, max_concurrent=2), execute=execute)
    monkeypatch.setattr("src.scheduling.staff_runner.get_staff_service", lambda root: service)
    made = _propose(home, home["agent"]["id"])
    assert made.status_code == 200, made.text
    _approve(home)
    return SimpleNamespace(**{**home, "state": state, "service": service, "routine": made.json()["id"]})


def _client(pool, headers=None):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=pool.app), base_url="http://testserver",
                             headers=dict(pool.owner.headers) if headers is None else headers)


async def _until(predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("等不到")


def _task(pool):
    tasks = [task for task in TaskLedger(pool.root).list(limit=50)
             if (task.collaboration or {}).get("dispatch", {}).get("routine")]
    return tasks[0] if len(tasks) == 1 else None


async def _act(client, pool, action):
    return await client.post(f"{pool.base}/routines/{pool.routine}/{action}", json={})


def _manual_audits(root):
    rows = map(json.loads, (state_dir(root) / "audit.log").read_text(encoding="utf-8").splitlines())
    return [row for row in rows if row.get("approval_source") == "routine_manual"]


async def test_run_now_really_runs_it(pool):
    item = Routines(pool.root).get(pool.routine)
    definition = {key: value for key, value in item.items() if key != "state"}
    async with _client(pool) as owner:
        actor = (await owner.get("/api/app/me")).json()["user"]["id"]
        ran = await _act(owner, pool, "run")
        assert ran.status_code == 200, ran.text
        assert ran.json()["id"] == pool.routine
        await _until(lambda: _task(pool) is not None and _task(pool).status == "done")
    reference = _task(pool).collaboration["dispatch"]["routine"]
    assert pool.state.runs == 1 and reference["manual"] is True
    [audit] = pool.state.audit_at_start
    assert audit["actor"] == actor and audit["routine_id"] == pool.routine
    assert audit["routine_revision"] == reference["revision"] and audit["slot"] == reference["slot"]
    assert audit["action_digest"] == digest({"definition": definition, "slot": reference["slot"]})
    assert item["prompt"] not in json.dumps(audit, ensure_ascii=False)
    with pytest.raises(ValueError, match="已认证的主人写入路由"):
        pool.state.worker_context.run(require_owner_route, pool.root)


async def test_run_now_never_claims_a_task_when_the_audit_cannot_be_saved(pool, monkeypatch):
    registry = Routines(pool.root)
    before = registry.get(pool.routine)

    def fail_audit(root, audit, *, durable):
        assert root == pool.root and audit["routine_id"] == pool.routine and durable is True
        assert TaskLedger(root).list() == []
        assert registry.get(pool.routine) == before
        return False

    monkeypatch.setattr("src.gateway.audit.append_audit", fail_audit)
    async with _client(pool) as owner:
        refused = await _act(owner, pool, "run")
    assert refused.status_code == 503 and refused.json()["error"]["code"] == "audit_unavailable"
    assert registry.get(pool.routine) == before
    assert TaskLedger(pool.root).list() == [] and pool.state.runs == 0
    assert _manual_audits(pool.root) == []


async def test_run_now_works_while_paused_and_leaves_it_paused(pool):
    registry = Routines(pool.root)
    registry.control(pool.routine, registry.get(pool.routine)["revision"], enabled=False)
    async with _client(pool) as owner:
        ran = await _act(owner, pool, "run")
        assert ran.status_code == 200, ran.text
        await _until(lambda: _task(pool) is not None and _task(pool).status == "done")
    assert Routines(pool.root).get(pool.routine)["enabled"] is False


async def test_run_twice_does_not_run_two_at_once_and_runs_again_once_stopped(pool):
    pool.state.release = asyncio.Event()
    async with _client(pool) as owner:
        assert (await _act(owner, pool, "run")).status_code == 200
        await _until(lambda: pool.state.runs == 1)
        again = await _act(owner, pool, "run")
        assert again.status_code == 409 and again.json()["error"]["code"] == "routine.running"
        assert len(_manual_audits(pool.root)) == 1
        assert pool.service.runner.cancel(_task(pool).id)
        await _until(lambda: _task(pool).status == "cancelled")
        pool.state.release.set()
        assert (await owner.post(f"{pool.base}/routines/{pool.routine}/run", json={})).status_code == 200
        await _until(lambda: pool.state.runs == 2)
        await _until(lambda: not pool.service.runner._running)


async def test_an_undecided_delivery_is_not_run_over(pool):
    async with _client(pool) as owner:
        await _act(owner, pool, "run")
        await _until(lambda: _task(pool) is not None and _task(pool).status == "done")
        await _until(lambda: not pool.service.runner._running)
        assert Routines(pool.root).get(pool.routine)["delivery"] == "review"
        again = await _act(owner, pool, "run")
        assert again.status_code == 409 and again.json()["error"]["code"] == "routine.awaiting_review"
    assert pool.state.runs == 1


async def test_a_changed_agent_is_explained_without_parking_the_routine(pool, monkeypatch):
    def changed(self, item):
        raise ValueError("专员或岗位版本已变化，请重新提议例行工作")
    monkeypatch.setattr(Routines, "check_binding", changed)
    async with _client(pool) as owner:
        refused = await _act(owner, pool, "run")
    assert refused.status_code == 409 and "重新提议" in refused.json()["error"]["message"]
    assert not Routines(pool.root).get(pool.routine)["state"].get("error")      # 点一下按钮不能把它停摆


@pytest.mark.parametrize("action", ["run"])
async def test_only_the_proven_owner_may(pool, monkeypatch, action):
    member = _join(vars(pool))
    assert member.post(f"{pool.base}/routines/{pool.routine}/{action}", json={}).status_code in {401, 403}
    async with _client(pool, headers={}) as nobody:
        assert (await _act(nobody, pool, action)).status_code in {401, 403}
    monkeypatch.setattr("src.web.app_staff.owner_proof", lambda request: False)
    async with _client(pool) as owner:
        assert (await _act(owner, pool, action)).status_code == 403
    assert pool.state.runs == 0


@pytest.mark.parametrize("action", ["run"])
async def test_an_unknown_routine_is_not_found(pool, action):
    async with _client(pool) as owner:
        assert (await owner.post(f"{pool.base}/routines/routine-none/{action}", json={})).status_code == 404


def _made_before_the_check(home, monkeypatch, *, approve):
    """#1027 到这次修复之间留下的记录：完整能力的 agent 已有提议或已登记。"""
    agent = home["owner"].post(f"{home['base']}/agents", json={"name": "全能", "handle": "helper"}).json()["id"]
    with monkeypatch.context() as before:
        before.setattr(Routines, "check_unattended", staticmethod(lambda role: None))
        made = _propose(home, agent)
        assert made.status_code == 200, made.text
        if approve:
            _approve(home)
    return made.json()["id"]


def test_an_old_proposal_for_a_full_agent_can_still_be_refused_but_not_approved(home, monkeypatch):
    _made_before_the_check(home, monkeypatch, approve=False)
    [proposal] = home["owner"].get(f"{home['base']}/routines/proposals/review").json()["proposals"]
    path = f"{home['base']}/routines/proposals/{proposal['id']}/decision"
    approved = home["owner"].post(path, json={"approve": True, **proposal["proof"]})
    assert approved.status_code == 409 and "完整能力" in approved.json()["error"]["message"]
    refused = home["owner"].post(path, json={"approve": False, **proposal["proof"]})
    assert refused.status_code == 200, refused.text
    assert refused.json()["status"] == "rejected" and Routines(home["root"]).list() == []


def test_an_old_routine_of_a_full_agent_can_be_paused_and_deleted_but_not_turned_on(home, monkeypatch):
    path = f"{home['base']}/routines/{_made_before_the_check(home, monkeypatch, approve=True)}"
    assert home["owner"].patch(path, json={"active": False}).status_code == 200
    turned_on = home["owner"].patch(path, json={"active": True})
    assert turned_on.status_code == 409 and "完整能力" in turned_on.json()["error"]["message"]
    assert home["owner"].delete(path).status_code == 204
