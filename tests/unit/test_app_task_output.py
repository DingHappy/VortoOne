"""后台任务输出读现有台账，只有经过证明的所有者可以看到进度日志。"""
from fastapi.testclient import TestClient

from src.workflow.tasks.tasks import MAX_SCAN_RECORD_BYTES, TaskLedger
from tests.unit.test_member_agents import ORIGIN, TOKEN, _join, home  # noqa: F401 - pytest fixture


def test_owner_reads_saved_task_tail_without_changing_the_task(home):
    ledger = TaskLedger(home["root"])
    task = ledger.create("dev", "不应出现在进度日志里的任务说明")
    task.status = "cancelled"
    task.log = ["准备", "开始\n检查完成"]
    task.result = "不能作为进度日志回退的任务结果"
    assert ledger.save(task)
    before = ledger.load(task.id).to_dict()
    route = f"{home['base']}/tasks/{task.id}/output"

    response = home["owner"].get(route, params={"tail": 2})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"taskId": task.id, "status": "canceled", "source": "saved",
                               "lines": ["开始", "检查完成"], "truncated": True}

    browser = TestClient(home["app"], headers=ORIGIN)
    assert browser.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    response = browser.get(route)
    assert response.status_code == 200, response.text
    assert response.json() == {"taskId": task.id, "status": "canceled", "source": "saved",
                               "lines": ["准备", "开始", "检查完成"], "truncated": False}
    assert browser.get(route, headers={"Origin": "https://elsewhere.example"}).status_code == 403
    assert ledger.load(task.id).to_dict() == before


def test_task_output_requires_owner_proof_even_for_a_members_own_task(home, monkeypatch):
    member = _join(home)
    base = home["base"]
    sent = member.post(f"{base}/channels/{home['channel']}/messages", json={"body": "核对资料"})
    assert sent.status_code == 200, sent.text
    task_id, = home["pool"].jobs
    route = f"{base}/tasks/{task_id}/output"
    assert home["owner"].get(route).json() == {"taskId": task_id, "status": "queued", "source": "saved",
                                               "lines": [], "truncated": False}
    assert member.get(route).status_code == 403
    anonymous = TestClient(home["app"])
    assert anonymous.get(route).status_code == 401

    monkeypatch.delenv("VORTOONE_API_TOKEN")
    assert anonymous.get(route).status_code == 403


def test_saved_task_output_rejects_missing_oversized_and_malformed_records(home):
    owner = home["owner"]
    base = home["base"]
    ledger = TaskLedger(home["root"])
    task = ledger.create("dev", "准备材料")
    route = f"{base}/tasks/{task.id}/output"
    assert owner.get(route, params={"tail": 0}).status_code == 422
    assert owner.get(route, params={"tail": 201}).status_code == 422
    assert owner.get(f"{base}/tasks/task-missing/output").status_code == 404
    assert owner.get(f"{base}/tasks/task.bad/output").status_code == 404
    assert owner.get(f"/api/app/organizations/another-home/tasks/{task.id}/output").status_code == 404

    task.log = ["x" * MAX_SCAN_RECORD_BYTES]
    assert ledger.save(task)
    assert owner.get(route).status_code == 404

    task.log = [{"unexpected": "object"}]
    assert ledger.save(task)
    response = owner.get(route)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "task.output_unavailable"
