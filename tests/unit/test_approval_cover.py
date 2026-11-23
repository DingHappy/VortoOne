"""外出时把批准交给别人：只有所有者能托付；被托付的成员只看得到、只批得了能代批的那些，每次都进审计。"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from src.gateway.audit import audit_snapshot
from src.web import app_views as views
from src.workflow.staff.approvals import StaffApprovals
from tests.unit.test_member_agents import ORIGIN, TOKEN, _join, home  # noqa: F401 - pytest fixture


class FakeBridge:
    def __init__(self, root, rows):
        self.root, self.rows, self.decided = root, rows, []

    def pending_confirmations(self):
        return [row for row in self.rows if row["id"] not in {d[0] for d in self.decided}]

    def pending(self, cid):
        return next((row for row in self.pending_confirmations() if row["id"] == cid), None)

    async def decide(self, cid, approve, *, file_owner=False, browser_owner=False):
        pending = self.pending(cid)
        if pending is None:
            return None
        self.decided.append((cid, approve, file_owner, browser_owner))
        return views.approval(self.root, pending, status="approved" if approve else "refused", decided=True)


ROWS = [
    {"id": "c-read", "text": "读取 notes.md", "operation_kind": "read", "risk": "normal", "created": "2026-11-22T00:00:01Z"},
    {"id": "c-write", "text": "写入 plan.md", "operation_kind": "write", "risk": "normal", "created": "2026-11-22T00:00:02Z"},
    {"id": "c-high", "text": "执行 rm -rf build", "operation_kind": "execute", "risk": "high", "created": "2026-11-22T00:00:03Z"},
    {"id": "c-taint", "text": "读取网页后要写入", "operation_kind": "read", "tainted": True, "created": "2026-11-22T00:00:04Z"},
    {"id": "c-login", "text": "网页登录", "operation_kind": "read", "website": {"origin": "https://x.example"},
     "created": "2026-11-22T00:00:05Z"},
    {"id": "c-key", "text": "要设备钥匙", "operation_kind": "read", "required_key": "device", "created": "2026-11-22T00:00:06Z"},
]


@pytest.fixture
def covered(home, monkeypatch):
    bridge = FakeBridge(home["root"], [dict(row) for row in ROWS])
    monkeypatch.setattr("src.web.routers.app_api.existing_bridge", lambda root: bridge)
    home["bridge"] = bridge
    home["member"] = _join(home)
    return home


def _cover(home, **change):
    body = {"userId": home["member"].member_id, "mode": "always", "risky": False, "endsAt": None, **change}
    return home["owner"].put(f"{home['base']}/me/approval-cover", json=body)


def _ids(client, home):
    response = client.get(f"{home['base']}/approvals")
    assert response.status_code == 200, response.text
    return [a["id"] for a in response.json()["approvals"]]


def test_without_a_cover_the_member_sees_and_decides_nothing(covered):
    member, base = covered["member"], covered["base"]
    assert _ids(member, covered) == []
    assert member.post(f"{base}/approvals/c-read/approve", json={}).status_code == 403
    assert member.get(f"{base}/approvals/c-read").status_code == 404
    assert member.get(f"{base}/me/approval-cover").json() == {"cover": None, "covering": []}
    assert covered["bridge"].decided == []


def test_a_low_risk_cover_decides_only_low_risk_once_and_is_audited(covered):
    owner, member, base = covered["owner"], covered["member"], covered["base"]
    set_ = _cover(covered)
    assert set_.status_code == 200, set_.text
    assert set_.json()["cover"] == {"userId": member.member_id, "mode": "always", "risky": False,
                                    "endsAt": None, "active": True}
    mine = member.get(f"{base}/me/approval-cover").json()
    assert mine["cover"] is None and mine["covering"][0]["userId"] == views.owner_id(covered["root"])

    assert _ids(member, covered) == ["c-read"]
    for blocked in ("c-write", "c-high", "c-taint", "c-login", "c-key"):
        assert member.post(f"{base}/approvals/{blocked}/approve", json={}).status_code == 403, blocked
        assert member.get(f"{base}/approvals/{blocked}").status_code == 404, blocked
    decided = member.post(f"{base}/approvals/c-read/approve", json={})
    assert decided.status_code == 200, decided.text
    assert decided.json()["decidedByUserId"] == member.member_id
    assert decided.json()["decidedForUserId"] == views.owner_id(covered["root"])
    # 只批这一次，不带所有者本人才有的证明
    assert covered["bridge"].decided == [("c-read", True, False, False)]
    event = next(e for e in audit_snapshot(covered["root"])["entries"] if e.get("event") == "approval_covered")
    assert event["data"]["member"] == member.member_id and event["data"]["approved"] is True
    assert member.post(f"{base}/approvals/c-read/approve", json={}).status_code == 409
    # 所有者自己照常看得到全部
    assert {"c-write", "c-high", "c-taint"} <= set(_ids(owner, covered))


def test_risky_cover_adds_writes_but_never_the_reserved_ones(covered):
    member = covered["member"]
    assert _cover(covered, risky=True).status_code == 200
    assert _ids(member, covered) == ["c-read", "c-write", "c-high"]
    StaffApprovals(covered["root"]).propose("sid-owner", "member", {
        "id": "staff-x", "name": "X", "role": "reader", "engine": "vortocode"}, "理由", origin="assistant")
    assert all(not i.startswith("staff-proposal-") for i in _ids(member, covered))
    card = next(i for i in _ids(covered["owner"], covered) if i.startswith("staff-proposal-"))
    assert member.post(f"{covered['base']}/approvals/{card}/approve", json={}).status_code == 403


def test_while_out_follows_the_owner_status_and_endings(covered, monkeypatch):
    owner, member, base = covered["owner"], covered["member"], covered["base"]
    assert _cover(covered, mode="while_out").json()["cover"]["active"] is False
    assert _ids(member, covered) == []
    from src.web import app_prefs
    app_prefs.set_status(covered["root"], views.owner_id(covered["root"]), {"availability": "out"})
    assert _ids(member, covered) == ["c-read"]

    soon = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    assert _cover(covered, endsAt=soon).json()["cover"]["endsAt"] == soon
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    monkeypatch.setattr("src.web.app_covers.datetime", type("D", (), {
        "now": staticmethod(lambda tz=None: later), "fromisoformat": staticmethod(datetime.fromisoformat)}))
    assert member.get(f"{base}/me/approval-cover").json()["covering"] == []
    assert _ids(member, covered) == []


def test_ending_and_bad_input(covered):
    owner, member, base = covered["owner"], covered["member"], covered["base"]
    for bad in ({"userId": "nobody"}, {"mode": "sometimes"}, {"risky": "yes"},
                {"endsAt": "2020-01-01T00:00:00Z"}, {"endsAt": "tomorrow"}):
        assert _cover(covered, **bad).status_code == 400, bad
    assert owner.put(f"{base}/me/approval-cover", json={"userId": member.member_id}).status_code == 400
    assert member.put(f"{base}/me/approval-cover", json={"userId": member.member_id, "mode": "always",
                                                         "risky": False, "endsAt": None}).status_code == 409
    assert _cover(covered).status_code == 200
    # 成员自己不再代批
    assert member.delete(f"{base}/approval-covers/{member.member_id}").status_code == 404
    owner_id = views.owner_id(covered["root"])
    assert member.delete(f"{base}/approval-covers/{owner_id}").status_code == 204
    assert _ids(member, covered) == []
    assert _cover(covered).status_code == 200
    assert owner.delete(f"{base}/approval-covers/{member.member_id}").status_code == 204
    assert owner.get(f"{base}/me/approval-cover").json()["cover"] is None
    assert owner.put(f"{base}/me/approval-cover", json={"userId": None}).json()["cover"] is None
    browser = TestClient(covered["app"], headers=ORIGIN)
    assert browser.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    assert browser.put(f"{base}/me/approval-cover", json={"userId": None},
                       headers={"Origin": "https://elsewhere.example"}).status_code == 403
