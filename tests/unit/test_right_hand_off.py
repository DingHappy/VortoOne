"""「右手代批」如实关着：会提议的只有助理自己，代批就是自己批自己的提议。"""
from fastapi.testclient import TestClient

from tests.unit.test_member_agents import _join, home  # noqa: F401 - pytest fixture


def test_it_reads_off_and_refuses_to_turn_on(home):
    owner, base = home["owner"], home["base"]
    route = f"{base}/me/right-hand-approvals"
    state = owner.get(route).json()
    assert state["enabled"] is False and state["rightHand"]["id"] == home["main"]["id"]
    assert state["mayDecide"] == {"newAgents": True, "agentChanges": True}
    assert owner.put(route, json={"enabled": False}).json() == state
    for body in ({"enabled": True}, {"newAgents": True}, {"budgetCapNanoUsd": 5}, {"guidance": "都批"}):
        answer = owner.put(route, json=body)
        assert answer.status_code == 409 and "自己批自己" in answer.json()["error"]["message"], body
    assert owner.put(route, json={"other": 1}).status_code == 400
    assert owner.put(route, json={}).status_code == 400
    assert owner.get(route).json() == state
    assert owner.post(f"{base}/right-hand-decisions/anything/undo", json={}).status_code == 404


def test_members_see_it_off_too(home):
    member = _join(home)
    state = member.get(f"{home['base']}/me/right-hand-approvals").json()
    assert state["enabled"] is False and state["rightHand"] is None
    assert state["mayDecide"] == {"newAgents": False, "agentChanges": False}
    assert member.put(f"{home['base']}/me/right-hand-approvals", json={"enabled": True}).status_code == 409
    assert TestClient(home["app"]).get(f"{home['base']}/me/right-hand-approvals").status_code == 401
