"""新版界面的运行记录、本月用量与例行工作历史：来自任务台账和用量账本。"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from tests.unit.test_staff_contract import staff_setup


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("VORTOONE_API_TOKEN", "VORTOCODE_API_TOKEN", "AUTODEV_API_TOKEN", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VORTOONE_USAGE_DIR", str(tmp_path / "usage"))
    registry, member, pool, service = staff_setup(tmp_path)
    pool.jobs.clear()
    from src.web.server import app
    client = TestClient(app)
    org = client.get("/api/app/me").json()["organizations"][0]["id"]
    base = f"/api/app/organizations/{org}"
    agents = client.get(f"{base}/agents").json()["agents"]
    return {"root": tmp_path, "client": client, "base": base, "main": agents[0]["id"],
            "agent": next(a for a in agents if a["handle"] == member["id"])["id"],
            "member": member, "pool": pool, "service": service}


async def _finish(pool, task_id):
    execute, callbacks = pool.jobs[task_id]
    async with callbacks["admission"]():
        await execute()


async def test_runs_are_the_members_tasks(home):
    client, base, service, member, pool = (home[k] for k in ("client", "base", "service", "member", "pool"))
    assert client.get(f"{base}/runs").json() == {"runs": []}
    done = service.assign("sid-owner", {"member": member["id"], "prompt": "整理材料"})
    await _finish(pool, done.id)
    waiting = service.assign("sid-owner", {"member": member["id"], "prompt": "第二件"})

    listed = client.get(f"{base}/runs", params={"agentId": home["agent"]}).json()["runs"]
    assert {r["id"]: (r["status"], r["what"], r["trigger"]) for r in listed} == {
        done.id: ("done", "整理材料", "mention"), waiting.id: ("queued", "第二件", "mention")}
    finished = next(r for r in listed if r["id"] == done.id)
    assert finished["finishedAt"] and finished["agentId"] == home["agent"]
    assert next(r for r in listed if r["id"] == waiting.id)["finishedAt"] is None
    assert client.get(f"{base}/runs", params={"agentId": home["main"]}).json() == {"runs": []}

    detail = client.get(f"{base}/agents/{home['agent']}").json()
    assert detail["runsThisMonth"] == 2 and detail["failuresThisMonth"] == 0
    assert sum(day["runs"] for day in detail["runsByDay"]) == 2


def test_usage_is_summed_per_agent_from_the_ledger(home):
    from src.llm import usage_ledger
    client, base, member = home["client"], home["base"], home["member"]
    assert client.get(f"{base}/usage").json() == {"usage": []}
    today = date.today()
    usage_ledger.record(100, 20, source=f"staff:inst-a:dept:{member['id']}:task-1", today=today)
    usage_ledger.record(30, 5, source=f"staff:inst-b:dept:{member['id']}:task-2", today=today)   # 重启后换了实例
    usage_ledger.record(7, 3, source="staff:inst-a:dept:someone-gone:task-9", today=today)
    usage_ledger.record(40, 10, source="对话", today=today)
    usage_ledger.record(8, 2, source="heartbeat", today=today)
    usage_ledger.record(999, 999, source="对话", today=date(today.year - 1, today.month, 1))    # 不是本月

    rows = {row["agentId"]: row for row in client.get(f"{base}/usage").json()["usage"]}
    assert (rows[home["agent"]]["calls"], rows[home["agent"]]["inputTokens"],
            rows[home["agent"]]["outputTokens"]) == (2, 130, 25)
    assert (rows[home["main"]]["calls"], rows[home["main"]]["inputTokens"],
            rows[home["main"]]["outputTokens"]) == (2, 48, 12)
    assert set(rows) == {home["agent"], home["main"]}            # 已不在名册的专员不混进别人名下
    assert client.get(f"{base}/agents/{home['agent']}").json()["tokensThisMonth"] == 155
    assert client.get(f"{base}/agents/{home['main']}").json()["tokensThisMonth"] == 60


async def test_routine_history_comes_from_its_tasks(home):
    from src.workflow.staff.routines import Routines
    client, base, service, member, pool = (home[k] for k in ("client", "base", "service", "member", "pool"))
    Routines(str(home["root"])).register({"id": "routine-report", "name": "例行核对", "member": member["id"],
        "prompt": "核对公开数字", "schedule": "at 09:00", "timezone": "UTC", "delivery": "notify"})
    fresh = client.get(f"{base}/routines").json()["routines"][0]
    assert fresh["lastRun"] is None and fresh["health"]["total"] == 0

    marker = {"id": "routine-report", "revision": 1, "slot": "2026-11-16T09:00:00+00:00", "legacy": None,
              "token_budget": 0}
    ran = service.assign("sid-owner", {"member": member["id"], "prompt": "核对公开数字"}, routine=marker)
    await _finish(pool, ran.id)
    service.assign("sid-owner", {"member": member["id"], "prompt": "与例行无关"})

    routine = client.get(f"{base}/routines").json()["routines"][0]
    assert [run["id"] for run in routine["recentRuns"]] == [ran.id]
    assert routine["lastRun"]["status"] == "reported" and routine["lastRun"]["summary"] == "逐条交回结论"
    assert routine["health"] == {"ok": 1, "silent": 0, "failed": 0, "skipped": 0, "missed": 0, "total": 1}
    assert routine["failuresInRow"] == 0
    assert [r["trigger"] for r in client.get(f"{base}/runs").json()["runs"] if r["id"] == ran.id] == ["routine"]
