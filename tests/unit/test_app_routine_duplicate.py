"""Copy the approved definition through the existing Desktop approval path."""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from src.workflow.staff.approvals import StaffApprovals
from src.workflow.staff.registry import StaffRegistry
from src.workflow.staff.routines import Routines
from tests.unit.test_app_routine_create import _decide, _new, _review
from tests.unit.test_member_agents import ORIGIN, TOKEN, _join, _member_routine, home  # noqa: F401


def _saved(home, *, registry=None, **fields):
    if registry is None:
        registry = Routines(home["root"])
    return registry.register({
        "id": "routine-daily-report", "name": "晨报", "member": home["staff"]["id"],
        "prompt": "汇总昨天的记录", "acceptance": ["列出来源"], "delivery": "notify",
        "schedule": "9 8 * * 1,3,5", "timezone": "Asia/Shanghai", "token_budget": 1200,
        **fields,
    }, owner="sid-owner")


def _path(home, routine_id):
    return f"{home['base']}/routines/{routine_id}/duplicate"


def test_desktop_copy_uses_only_the_approved_definition_and_owner_digest(home):
    original = _saved(home)
    store = Routines(home["root"])
    approvals = StaffApprovals(home["root"])
    assistant = approvals.propose("sid-owner", "routine", {
        "id": "routine-assistant-copy", "name": "助理待审", "member": home["staff"]["id"],
        "prompt": "另一个提议", "schedule": "0 9 * * *", "timezone": "UTC",
    }, "助理提议")

    response = home["owner"].post(_path(home, original["id"]), json={"name": "晨报（副本）"})
    assert response.status_code == 200, response.text
    copy = response.json()
    assert copy["id"] != original["id"] and copy["name"] == "晨报（副本）"
    assert copy["agentId"] == home["agent"]["id"] and copy["prompt"] == original["prompt"]
    assert copy["state"] == "waiting_person" and copy["active"] is False and copy["nextRunAt"] is None
    assert copy["canManage"] is False and copy["waitingFor"]["kind"] == "approval"
    assert copy["recentRuns"] == [] and copy["report"] == "always"
    assert copy["trigger"]["phrase"] == original["schedule"]
    assert copy["trigger"]["timezone"] == original["timezone"]
    assert store.list() == [original] and home["pool"].jobs == {}

    [review] = _review(home)
    assert review["id"] == copy["waitingFor"]["proposalId"] and review["id"] != assistant["id"]
    assert review["acceptance"] == original["acceptance"] and review["tokenBudget"] == original["token_budget"]
    assert review["enabled"] is False and review["delivery"] == original["delivery"]
    stale = {**review, "proof": {**review["proof"], "digest": "0" * 64}}
    assert _decide(home, stale, True).status_code == 409
    assert store.list() == [original]
    assert _decide(home, review, True).json()["status"] == "approved"

    saved = store.get(copy["id"])
    for field in ("member", "prompt", "acceptance", "delivery", "schedule", "timezone", "token_budget", "binding"):
        assert saved[field] == original[field]
    assert not saved["enabled"] and saved["revision"] == 1 and saved["state"]["task"] is None
    assert store.get(original["id"]) == original and home["pool"].jobs == {}
    assert approvals.get(assistant["id"])["status"] == "pending" and _review(home) == []
    items = home["owner"].get(home["base"] + "/routines").json()["routines"]
    assert next(row for row in items if row["id"] == copy["id"])["state"] == "paused"


def test_copy_refusals_and_localized_name_at_limit(home):
    owner = home["owner"]
    saved = _saved(home, name="晨" * 80)
    path = _path(home, saved["id"])
    assert owner.post(path, json={"name": "   "}).status_code == 400
    assert owner.post(path, json={"enabled": True}).status_code == 422
    assert owner.post(path, json={"name": "一" * 100}).status_code == 400
    assert owner.post(_path(home, "routine-unknown"), json={}).status_code == 404
    assert _review(home) == []

    for body, suffix in (({"name": saved["name"] + " (copy)"}, " (copy)"), ({}, "（副本）")):
        result = owner.post(path, json=body)
        assert result.status_code == 200, result.text
        name = result.json()["name"]
        assert len(name) == 80 and name.endswith(suffix)
    for pending in _review(home):
        assert _decide(home, pending, False).json()["status"] == "rejected"
    assert Routines(home["root"]).list() == [saved]


def test_copy_requires_owner_proof_and_rejects_unapproved_sources(home, monkeypatch):
    owner = home["owner"]
    saved = _saved(home)
    member = _join(home)
    path = _path(home, saved["id"])
    assert member.post(path, json={}).status_code == 403
    assert member.get(home["base"] + "/routines").json() == {"routines": []}

    cookie = TestClient(home["app"])
    assert cookie.post("/api/auth/sign-in/email", json={"email": "", "password": TOKEN}).status_code == 200
    assert cookie.post(path, json={}, headers={"Origin": "http://another.test"}).status_code == 403
    copied = cookie.post(path, json={}, headers=ORIGIN)
    assert copied.status_code == 200, copied.text
    pending_source = _new(home).json()
    assert owner.post(_path(home, pending_source["id"]), json={}).status_code == 404
    registry = Routines(home["root"])
    registry.control(saved["id"], saved["revision"], retire=True)
    assert owner.post(path, json={}).status_code == 404

    monkeypatch.delenv("VORTOONE_API_TOKEN")
    assert TestClient(home["app"]).post(path, json={}).status_code == 403


def test_copy_does_not_upgrade_member_ownership_or_rebind_a_stale_source(home):
    owner = home["owner"]
    creator = _join(home)
    saved = _saved(home)
    _, _, body = _member_routine(home, creator)
    assert creator.post(home["base"] + "/routines/proposals", json=body).status_code == 201
    [review] = _review(home)
    assert _decide(home, review, True).json()["status"] == "approved"
    member_id = body["definition"]["id"]
    assert Routines(home["root"]).get(member_id)["initiator"] == creator.member_id
    assert owner.post(_path(home, member_id), json={}).status_code == 409

    staff = home["staff"]
    StaffRegistry(home["root"]).set_member_status(staff["id"], "paused", staff["revision"])
    assert owner.post(_path(home, saved["id"]), json={}).status_code == 409
    assert _review(home) == [] and home["pool"].jobs == {}


def test_expired_one_off_copy_requires_a_new_date(home):
    clock = lambda: datetime(2000, 1, 1, tzinfo=timezone.utc)
    saved = _saved(home, registry=Routines(home["root"], clock=clock), schedule="once 2001-01-01T12:00:00Z")
    response = home["owner"].post(_path(home, saved["id"]), json={})
    assert response.status_code == 409 and "未来时间" in response.json()["error"]["message"]
    assert _review(home) == [] and Routines(home["root"]).list() == [saved]
