"""助理在主要聊天里提的新建 agent，以卡片出现在对话里：「创建」按这一版批准，「不要」拒绝；和审批列表是同一份提议。"""
from fastapi.testclient import TestClient

from src.web import app_views as views
from src.workflow.staff.approvals import StaffApprovals
from src.workflow.staff.registry import StaffRegistry
from tests.unit.test_member_agents import ORIGIN, TOKEN, _join, home  # noqa: F401 - pytest fixture

NEW = {"id": "staff-reader-two", "name": "第二位读者", "role": "reader", "engine": "vortocode"}


def _propose(home, data=NEW, origin="assistant"):
    return StaffApprovals(home["root"]).propose("sid-owner", "member", dict(data), "主人想多一个读材料的帮手",
                                                origin=origin)


def _main_messages(client, home):
    channel = views.dm_channel_id(views.main_agent_id(home["root"]))
    response = client.get(f"{home['base']}/channels/{channel}/messages")
    assert response.status_code == 200, response.text
    return response.json()["messages"]


def _cards(client, home):
    return [m for m in _main_messages(client, home) if (m.get("meta") or {}).get("actionCard")]


def test_the_card_shows_in_the_main_chat_and_creates_the_agent(home):
    owner, base = home["owner"], home["base"]
    proposal = _propose(home)
    message, = _cards(owner, home)
    card = message["meta"]["actionCard"]
    assert message["author"]["type"] == "agent" and "第二位读者" in message["body"]
    assert card["kind"] == "agent_proposal" and card["state"] == "pending"
    assert card["params"]["draft"]["name"] == "第二位读者" and card["params"]["why"] == "主人想多一个读材料的帮手"
    assert message["id"] == views.agent_proposal_card_id(proposal["id"])

    shown = owner.get(f"{base}/agent-proposals/{message['id']}")
    assert shown.status_code == 200 and shown.json()["canDecide"] is True and shown.json()["expired"] is False
    made = owner.post(f"{base}/agent-proposals/{message['id']}/create", json={})
    assert made.status_code == 200, made.text
    body = made.json()
    assert body["proposal"]["state"] == "created" and body["agent"]["handle"] == "staff-reader-two"
    assert body["proposal"]["agentId"] == body["agent"]["id"]
    assert StaffRegistry(home["root"]).member("staff-reader-two")["name"] == "第二位读者"
    # 卡片留在对话里，显示已创建；审批列表里不再等待；再点创建 409
    assert _cards(owner, home)[0]["meta"]["actionCard"]["state"] == "created"
    assert [a for a in owner.get(f"{base}/approvals").json()["approvals"] if a["tool"] == "staff_propose"] == []
    assert owner.post(f"{base}/agent-proposals/{message['id']}/create", json={}).status_code == 409


def test_deny_and_the_approvals_list_share_one_proposal(home):
    owner, base = home["owner"], home["base"]
    _propose(home)
    message, = _cards(owner, home)
    denied = owner.post(f"{base}/agent-proposals/{message['id']}/deny", json={})
    assert denied.status_code == 200 and denied.json()["state"] == "denied" and denied.json()["canDecide"] is False
    assert "staff-reader-two" not in {m["id"] for m in StaffRegistry(home["root"]).members()}
    # 在审批列表里批过的，卡片也跟着变
    _propose(home, {**NEW, "id": "staff-reader-three", "name": "第三位读者"})
    approval = next(a for a in owner.get(f"{base}/approvals").json()["approvals"] if a["tool"] == "staff_propose")
    assert owner.post(f"{base}/approvals/{approval['id']}/approve", json={}).status_code == 200
    states = {m["meta"]["actionCard"]["params"]["draft"]["handle"]: m["meta"]["actionCard"]["state"]
              for m in _cards(owner, home)}
    assert states == {"staff-reader-two": "denied", "staff-reader-three": "created"}


def test_only_assistant_new_agents_get_cards_and_only_the_owner_decides(home):
    owner, base = home["owner"], home["base"]
    _propose(home, origin=None)                 # 旧版 Desktop 里提的：只在审批列表
    assert _cards(owner, home) == []
    _propose(home, {**NEW, "id": "staff-reader-four", "name": "第四位"})
    message, = _cards(owner, home)
    route = f"{base}/agent-proposals/{message['id']}"
    member = _join(home)
    assert member.get(route).status_code == 403
    assert member.post(f"{route}/create", json={}).status_code == 403
    browser = TestClient(home["app"], headers=ORIGIN)
    assert browser.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    assert browser.post(f"{route}/create", json={}, headers={"Origin": "https://elsewhere.example"}).status_code == 403
    for extra in ("ask", "routine"):
        answer = owner.post(f"{route}/{extra}", json={})
        assert answer.status_code == 409, extra
    assert owner.get(f"{base}/agent-proposals/not-a-card").status_code == 404
    assert browser.post(f"{route}/create", json={}).status_code == 200
