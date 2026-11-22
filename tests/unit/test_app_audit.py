"""审计页读共享审计台账；只给证明过身份的所有者，不补造台账里没有的结果与审批。"""
from fastapi.testclient import TestClient

from src.gateway.audit import append_audit, record_decision_audit, record_tool_audit
from src.web import app_views as views
from tests.unit.test_member_agents import ORIGIN, TOKEN, _join, home  # noqa: F401 - pytest fixture


def _seed(root):
    record_tool_audit(root, session="sid-1", mode="build", name="read_file",
                      args={"path": "notes.md", "api_key": "sk-should-not-leave"}, result="内容")
    record_decision_audit(root, session="sid-1", mode="build", operation="shell: ls", decision=False,
                          tainted=True, approval={"decision_source": "user", "risk": "high"})
    record_decision_audit(root, session="sid-1", mode="build", operation="shell: pwd", decision=False,
                          tainted=False, approval={"decision_source": "timeout", "approval_outcome": "expired"})
    append_audit(root, {"category": "event", "session": "sid-1", "event": "internal"})
    record_tool_audit(root, session="sid-2", mode="build", name="web_search", args={}, result="")


def test_owner_reads_tool_calls_and_decisions_newest_first(home):
    root = home["root"]
    _seed(root)
    response = home["owner"].get(f"{home['base']}/audit")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    page = response.json()
    rows = page["rows"]
    assert [row["action"] for row in rows] == ["tool.call", "approval.expired", "approval.refused", "tool.call"]
    assert page["next"] is None and page["status"] == "current"

    main = views.main_agent_id(root)
    search, expired, refused, read = rows
    assert search["actor"]["type"] == "agent" and search["actor"]["id"] == main
    assert search["target"] == {"type": "tool", "id": "web_search"} and search["details"] == {}
    assert read["details"]["input"]["path"] == "notes.md"
    assert "sk-should-not-leave" not in str(read)
    # 台账不记成败，也没有对应的审批卡和频道
    assert all(row["outcome"] is None and row["approval"] is None and row["channel"] is None for row in rows)
    assert refused["actor"] == {"type": "person", "id": views.owner_id(root), "name": views.me(root)["user"]["name"]}
    assert refused["details"] == {"operation": "shell: ls", "tainted": True, "risk": "high"}
    assert expired["actor"] == {"type": "system", "id": None, "name": None}


def test_filters_and_pages_follow_the_ledger(home):
    root, owner, route = home["root"], home["owner"], f"{home['base']}/audit"
    _seed(root)
    main = views.main_agent_id(root)
    assert [r["action"] for r in owner.get(route, params={"kind": "tool"}).json()["rows"]] == ["tool.call"] * 2
    assert [r["action"] for r in owner.get(route, params={"kind": "change"}).json()["rows"]] == [
        "approval.expired", "approval.refused"]
    assert len(owner.get(route, params={"actorType": "person"}).json()["rows"]) == 1
    assert len(owner.get(route, params={"via": main}).json()["rows"]) == 2
    assert owner.get(route, params={"via": "agent-else"}).json()["rows"] == []
    assert owner.get(route, params={"waiting": "true"}).json()["rows"] == []
    assert owner.get(route, params={"outcome": "refused,failed"}).json()["rows"] == []

    first = owner.get(route, params={"limit": 3}).json()
    assert len(first["rows"]) == 3 and first["next"] == first["rows"][-1]["id"]
    rest = owner.get(route, params={"limit": 3, "before": first["next"]}).json()
    assert [r["id"] for r in rest["rows"]] == [r["id"] for r in owner.get(route).json()["rows"][3:]]
    assert rest["next"] is None
    assert owner.get(route, params={"before": "audit-unknown"}).json()["rows"] == []

    for bad in ({"actorType": "robot"}, {"kind": "x"}, {"outcome": "done,maybe"}, {"limit": 0}, {"limit": 101}):
        response = owner.get(route, params=bad)
        assert response.status_code == 400, bad


def test_audit_is_owner_only(home, monkeypatch):
    _seed(home["root"])
    route = f"{home['base']}/audit"
    member = _join(home)
    assert member.get(route).status_code == 403
    assert TestClient(home["app"]).get(route).status_code == 401
    browser = TestClient(home["app"], headers=ORIGIN)
    assert browser.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    assert browser.get(route).status_code == 200
    assert browser.get(route, headers={"Origin": "https://elsewhere.example"}).status_code == 403


def test_empty_ledger_is_an_empty_page(home):
    assert home["owner"].get(f"{home['base']}/audit").json() == {"rows": [], "next": None, "status": "current"}


def test_run_detail_is_the_listed_run_without_invented_steps(home):
    member = _join(home)
    base = home["base"]
    sent = member.post(f"{base}/channels/{home['channel']}/messages", json={"body": "核对资料"})
    assert sent.status_code == 200, sent.text
    task_id, = home["pool"].jobs
    listed = next(row for row in home["owner"].get(f"{base}/runs").json()["runs"] if row["id"] == task_id)
    response = home["owner"].get(f"{base}/runs/{task_id}")
    assert response.status_code == 200, response.text
    assert response.json() == {"run": listed, "steps": []}
    assert home["owner"].get(f"{base}/runs/task-missing").status_code == 404
    assert member.get(f"{base}/runs/{task_id}").status_code == 403
