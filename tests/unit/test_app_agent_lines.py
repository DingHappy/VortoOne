"""Desktop team graph edits are atomic presentation changes, scoped to visible agents."""

from fastapi.testclient import TestClient

from src.agents.principal import OWNER, Principal
from src.web import app_views
from src.workflow.members.registry import MemberRegistry
from src.workflow.staff.registry import StaffRegistry
from tests.unit.test_member_agents import ORIGIN, TOKEN, _join, home  # noqa: F401 - pytest fixture


def _staff(home, handle):
    record = StaffRegistry(home["root"]).register_member({
        "id": handle, "name": handle, "role": home["staff"]["role"], "engine": "vortocode",
    })
    return app_views.member_agent_id(home["root"], record["id"])


def _line(agent, supervisor):
    return {"agentId": agent, "reportsToAgentId": supervisor}


def _set(home, changes):
    return home["owner"].put(f"{home['base']}/agent-lines", json={"changes": changes})


def _lines(home, client, *, endpoint="team"):
    response = client.get(f"{home['base']}/{endpoint}")
    assert response.status_code == 200, response.text
    return {row["id"]: row["reportsToAgentId"] for row in response.json()["agents"] if "reportsToAgentId" in row}


def test_owner_sets_and_undoes_team_chart_without_changing_agent_permissions(home):
    source = home["agent"]["id"]
    middle = _staff(home, "staff-middle")
    top = _staff(home, "staff-top")
    member = _join(home)
    owner, base, root = home["owner"], home["base"], home["root"]
    before = StaffRegistry(root).members(), MemberRegistry(root).snapshot()
    changes = [_line(source, middle), _line(middle, top)]

    response = _set(home, changes)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"previous": [_line(source, None), _line(middle, None)]}
    for viewer in (owner, member):
        for endpoint in ("team", "agents"):
            assert _lines(home, viewer, endpoint=endpoint) == {source: middle, middle: top}
        assert viewer.get(f"{base}/agents/{source}").json()["reportsToAgentId"] == middle
    assert _set(home, changes).json() == {"previous": []}
    assert (StaffRegistry(root).members(), MemberRegistry(root).snapshot()) == before
    assert home["pool"].jobs == {}

    undone = _set(home, response.json()["previous"])
    assert undone.status_code == 200, undone.text
    assert undone.json() == {"previous": changes}
    assert _lines(home, owner) == {}
    assert _lines(home, member) == {}


def test_private_supervisor_is_not_disclosed_to_members(home):
    source = home["agent"]["id"]
    supervisor = _staff(home, "staff-private")
    root, owner, base = home["root"], home["owner"], home["base"]
    MemberRegistry(root).set_audience(Principal(OWNER, app_views.owner_id(root)), "staff-private",
                                      {"mode": "people", "userIds": [], "roleIds": []})
    member = _join(home)
    assert _set(home, [_line(source, supervisor)]).status_code == 200
    assert _lines(home, owner) == {source: supervisor}
    for endpoint in ("team", "agents"):
        assert _lines(home, member, endpoint=endpoint) == {}
    assert "reportsToAgentId" not in member.get(f"{base}/agents/{source}").json()
    assert member.get(f"{base}/agents/{supervisor}").status_code == 404


def test_main_assistant_has_its_own_graph_identity_and_stays_private(home):
    first, main = home["agent"]["id"], home["main"]["id"]
    owner, base = home["owner"], home["base"]
    # Older staff records can use the main assistant's presentation handle.
    specialist = _staff(home, "assistant")
    member = _join(home)
    response = _set(home, [_line(first, main), _line(main, specialist)])
    assert response.status_code == 200, response.text
    assert response.json() == {"previous": [_line(first, None), _line(main, None)]}
    assert _lines(home, owner) == {first: main, main: specialist}
    assert owner.get(f"{base}/agents/{main}").json()["reportsToAgentId"] == specialist
    assert _lines(home, member) == {}
    assert member.get(f"{base}/agents/{main}").status_code == 404
    assert _set(home, [_line(specialist, first)]).status_code == 400
    assert _lines(home, owner) == {first: main, main: specialist}
    assert _set(home, response.json()["previous"]).status_code == 200
    assert _set(home, [_line(first, specialist)]).status_code == 200
    assert _lines(home, member) == {first: specialist}


def test_one_invalid_change_preserves_the_whole_team_graph(home):
    first = home["agent"]["id"]
    second, third, fourth = [_staff(home, f"staff-{word}") for word in ("second", "third", "fourth")]
    assert _set(home, [_line(first, second), _line(second, third)]).status_code == 200
    unchanged = {first: second, second: third}
    for changes, status in (
        ([_line(third, fourth)], 400),                       # a fourth level
        ([_line(third, first)], 400),                        # a cycle
        ([_line(fourth, fourth)], 400),                      # a self link
        ([_line(first, None), _line(first, fourth)], 400),   # duplicate source
        ([_line(first, None), _line(second, "missing")], 404),
        ([{"agentId": first}], 422),                        # explicit null is required to remove
        ([{**_line(first, None), "permissions": ["anything"]}], 422),
    ):
        response = _set(home, changes)
        assert response.status_code == status, response.text
        assert _lines(home, home["owner"]) == unchanged


def test_change_requires_existing_owner_proof(home, monkeypatch):
    url = f"{home['base']}/agent-lines"
    calls = {"changes": []}
    member = _join(home)
    anonymous = TestClient(home["app"], headers=ORIGIN)
    browser_owner = TestClient(home["app"], headers=ORIGIN)
    assert browser_owner.post("/api/auth/sign-in/email", json={"email": "", "password": TOKEN}).status_code == 200
    assert anonymous.put(url, json=calls).status_code == 401
    assert member.put(url, json=calls).status_code == 403
    assert browser_owner.put(url, json=calls, headers={"Origin": "https://elsewhere.example"}).status_code == 403
    assert browser_owner.put(url, json=calls).json() == {"previous": []}
    assert browser_owner.put("/api/app/organizations/unknown/agent-lines", json=calls).status_code == 404
    monkeypatch.delenv("VORTOONE_API_TOKEN")
    assert anonymous.put(url, json=calls).status_code == 403
