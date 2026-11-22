"""助理提的专员提议进新界面的审批：按界面上看到的那一版核对，只有所有者能批，不参加批量。"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.workflow.staff.approvals import StaffApprovals
from src.workflow.staff.registry import StaffRegistry
from tests.unit.test_member_agents import ORIGIN, TOKEN, _join, home  # noqa: F401 - pytest fixture

NEW = {"id": "staff-new", "name": "新专员", "role": "reader", "engine": "vortocode"}


def _propose(home, data=NEW, reason="主人说要一个专门读材料的帮手", kind="member", origin="assistant"):
    return StaffApprovals(home["root"]).propose("sid-owner", kind, dict(data), reason, origin=origin)


def _approvals(client, home):
    response = client.get(f"{home['base']}/approvals")
    assert response.status_code == 200, response.text
    return [a for a in response.json()["approvals"] if a["tool"] == "staff_propose"]


def test_the_owner_sees_and_approves_what_the_assistant_proposed(home):
    owner, base = home["owner"], home["base"]
    proposal = _propose(home)
    card, = _approvals(owner, home)
    assert card["title"] == "助理提议：新建 agent「新专员」"
    assert "助理在主要聊天里提的" in card["detail"] and "主人说要一个专门读材料的帮手" in card["detail"]
    assert "联网：否" in card["detail"] and card["canDecide"] is True and card["messageId"] is None
    assert card["id"] == f"{proposal['id']}~{proposal['digest'][:16]}"
    assert owner.get(f"{base}/approvals/{card['id']}").json() == card

    approved = owner.post(f"{base}/approvals/{card['id']}/approve", json={})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved" and approved.json()["decidedByUserId"]
    assert StaffRegistry(home["root"]).member("staff-new")["name"] == "新专员"
    assert _approvals(owner, home) == []
    assert owner.post(f"{base}/approvals/{card['id']}/approve", json={}).status_code == 409


def test_a_refusal_registers_nothing_and_a_changed_proposal_cannot_be_approved(home):
    owner, base = home["owner"], home["base"]
    proposal = _propose(home)
    stale = f"{proposal['id']}~{'0' * 16}"
    assert owner.get(f"{base}/approvals/{stale}").status_code == 404
    answer = owner.post(f"{base}/approvals/{stale}/approve", json={})
    assert answer.status_code == 409 and "变了" in answer.json()["error"]["message"]
    card, = _approvals(owner, home)
    refused = owner.post(f"{base}/approvals/{card['id']}/refuse", json={})
    assert refused.status_code == 200 and refused.json()["status"] == "refused"
    assert "staff-new" not in {m["id"] for m in StaffRegistry(home["root"]).members()}
    assert owner.post(f"{base}/approvals/staff-proposal-missing~abc/approve", json={}).status_code == 409


def test_only_the_owner_decides_and_batches_skip_proposals(home):
    owner, base = home["owner"], home["base"]
    _propose(home)
    card, = _approvals(owner, home)
    member = _join(home)
    assert _approvals(member, home) == []
    assert member.post(f"{base}/approvals/{card['id']}/approve", json={}).status_code == 403
    browser = TestClient(home["app"], headers=ORIGIN)
    assert browser.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    assert browser.post(f"{base}/approvals/{card['id']}/approve", json={},
                        headers={"Origin": "https://elsewhere.example"}).status_code == 403
    batch = owner.post(f"{base}/approvals/decide", json={"ids": [card["id"]], "decision": "approve"})
    assert batch.status_code == 200 and batch.json() == {"approvals": [], "skipped": [card["id"]]}
    assert len(_approvals(owner, home)) == 1
    # 同源登录会话（新版 Desktop）可以批
    assert browser.post(f"{base}/approvals/{card['id']}/approve", json={}).status_code == 200


def test_routine_proposals_show_once_and_member_ones_keep_their_own_page(home):
    owner = home["owner"]
    _propose(home, kind="routine", data={"id": "routine-brief", "name": "晨报", "member": home["staff"]["id"],
                                         "prompt": "整理昨天的进展", "schedule": "0 9 * * *", "timezone": "UTC"},
             reason="主人要每天的晨报")
    card, = _approvals(owner, home)
    assert card["title"] == "助理提议：新建例行工作「晨报」" and "整理昨天的进展" in card["detail"]
    assert "routine-brief" not in {r["id"] for r in owner.get(f"{home['base']}/routines").json()["routines"]}


def test_origin_is_covered_by_the_digest(home):
    proposal = _propose(home)
    path = Path(home["root"]) / ".vortoone" / "staff" / "proposals.json"
    if not path.exists():
        path = next(Path(home["root"]).rglob("proposals.json"))
    data = json.loads(path.read_text(encoding="utf-8"))
    record = next(item for item in data["proposals"] if item["id"] == proposal["id"])
    assert record["origin"] == "assistant"
    record.pop("origin")
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        StaffApprovals(home["root"]).list()
    with pytest.raises(ValueError):
        StaffApprovals(home["root"]).propose("sid-owner", "member", dict(NEW), "x", origin="someone")


def test_an_assistant_bundle_can_be_approved_and_applied(home):
    """开通单登记时自己重算摘要，要带上 origin，否则助理提的开通单批了也登记不了。"""
    from src.workflow.staff.routines import Routines
    owner, base = home["owner"], home["base"]
    StaffApprovals(home["root"]).propose("sid-owner", "bundle", {
        "member": {"id": "staff-brief", "name": "晨报员", "role": "reader", "engine": "vortocode"},
        "routines": [{"id": "routine-morning", "name": "晨报", "member": "staff-brief", "prompt": "整理进展",
                      "schedule": "0 9 * * *", "timezone": "UTC"}]}, "主人要晨报", origin="assistant")
    card, = _approvals(owner, home)
    approved = owner.post(f"{base}/approvals/{card['id']}/approve", json={})
    assert approved.status_code == 200, approved.text
    assert StaffRegistry(home["root"]).member("staff-brief")["name"] == "晨报员"
    assert [r["id"] for r in Routines(home["root"]).list()] == ["routine-morning"]
