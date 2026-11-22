"""例行工作的「立即运行」。完整能力的 agent 担任例行工作时的降档见 test_routine_unattended。"""
import asyncio
import time
from types import SimpleNamespace

import httpx
import pytest

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


@pytest.fixture
def pool(home, monkeypatch):
    state = SimpleNamespace(release=None, runs=0)

    async def execute(task, work, role, member, check):
        state.runs += 1
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


async def test_run_now_really_runs_it(pool):
    async with _client(pool) as owner:
        ran = await _act(owner, pool, "run")
        assert ran.status_code == 200, ran.text
        assert ran.json()["id"] == pool.routine
        await _until(lambda: _task(pool) is not None and _task(pool).status == "done")
    assert pool.state.runs == 1 and _task(pool).collaboration["dispatch"]["routine"]["manual"] is True


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
