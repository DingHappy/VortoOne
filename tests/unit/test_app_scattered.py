"""零散的接口（2026-11-24）：编号小标签按各自的可见性解析；其余没有对应能力的如实回答。"""
import pytest
from fastapi.testclient import TestClient

from tests.unit.test_member_agents import ORIGIN, _join, home  # noqa: F401 - pytest fixture
from tests.unit.test_team_channels import _create


def test_refs_resolve_tasks_and_messages_only_for_people_who_can_see_them(home):
    owner, base = home["owner"], home["base"]
    issue = owner.post(f"{base}/issues", json={"title": "整理发票"})
    assert issue.status_code in (200, 201), issue.text
    issue = issue.json()
    ref = owner.get(f"{base}/refs/{issue['id']}").json()
    assert ref == {"kind": "task", "id": issue["id"], "number": issue["number"], "title": "整理发票"}
    channel = _create(home, "notes")
    posted = owner.post(channel + "/messages", json={"body": "会议纪要在这里"}).json()
    shown = owner.get(f"{base}/refs/{posted['id']}").json()
    assert shown["kind"] == "message" and shown["message"]["id"] == posted["id"]
    assert owner.get(f"{base}/refs/nothing-here").status_code == 404
    member = _join(home)
    private = _create(home, "secret", visibility="private")
    hidden = owner.post(private + "/messages", json={"body": "只给我看"}).json()
    assert member.get(f"{base}/refs/{hidden['id']}").status_code == 404                 # 看不到的不说是什么
    assert TestClient(home["app"]).get(f"{base}/refs/{issue['id']}").status_code == 401


@pytest.mark.parametrize(("method", "path", "body", "status", "code"), [
    ("post", "/approvals/a1/cancel", {}, 409, "approval.not_ahead"),
    ("post", "/autonomy-proposals/s1", {"decision": "approve"}, 404, "autonomy_proposal.not_found"),
    ("post", "/routines/r1/continue", {}, 409, "routine.continue_unavailable"),
    ("put", "/me/agent-style", {"tone": "casual"}, 409, "agent_style.unavailable"),
    ("post", "/vault/items/v1/move", {"folder": "x"}, 409, "vault.no_folders"),
])
def test_missing_features_say_so(home, method, path, body, status, code):
    response = getattr(home["owner"], method)(home["base"] + path, json=body)
    assert (response.status_code, response.json()["error"]["code"]) == (status, code)


def test_email_undo_needs_no_sign_in_and_has_nothing_to_undo(home):
    visitor = TestClient(home["app"], headers=ORIGIN)
    response = visitor.post("/api/app/email-undo", json={"token": "x" * 32})
    assert response.status_code == 404 and response.json()["error"]["code"] == "email.undo_gone"
    hostile = TestClient(home["app"], headers={"Origin": "https://elsewhere.example"})
    assert hostile.post("/api/app/email-undo", json={"token": "x" * 32}).status_code == 403
