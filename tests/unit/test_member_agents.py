"""成员和 agent 对话：只看到自己用得了的 agent，私聊里只有自己发起的事，主人的数据对成员回空。"""
from datetime import datetime, timedelta
import json

import pytest
from fastapi.testclient import TestClient

from src.agents.principal import MEMBER, OWNER, Principal
from src.workflow.members import accounts as accounts_module
from src.workflow.members.registry import MemberRegistry
from tests.unit.staff_owner_helpers import proposal_body
from tests.unit.test_staff_contract import staff_setup

TOKEN = "owner-token-for-test"
ORIGIN = {"Origin": "http://testserver"}
PASSWORD = "correct horse battery"
OWNER_P = Principal(OWNER, "owner")


@pytest.fixture
def home(tmp_path, monkeypatch):
    from src.gateway.main_conversation import claim_main_chat
    from src.web import app_bridge
    from src.web.server import app
    monkeypatch.chdir(tmp_path)
    for name in ("VORTOCODE_API_TOKEN", "AUTODEV_API_TOKEN", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VORTOONE_API_TOKEN", TOKEN)
    monkeypatch.setattr(accounts_module, "_SCRYPT", {"n": 2 ** 4, "r": 8, "p": 1})
    monkeypatch.setattr(accounts_module, "_DECOY", accounts_module._sealed("decoy-password"))
    accounts_module._FAILURES.clear()
    accounts_module._RECENT.update(since=0.0, count=0)
    registry, staff, pool, service = staff_setup(tmp_path)
    pool.jobs.clear()
    claim_main_chat(str(tmp_path), "sid-owner")
    monkeypatch.setattr("src.scheduling.staff_runner.get_staff_service", lambda root: service)
    app_bridge.reset_bridges()
    owner = TestClient(app)
    owner.headers["Authorization"] = f"Bearer {TOKEN}"
    org = owner.get("/api/app/me").json()["organizations"][0]["id"]
    base = f"/api/app/organizations/{org}"
    agents = owner.get(f"{base}/agents").json()["agents"]
    agent = next(a for a in agents if a["handle"] == staff["id"])
    main = next(a for a in agents if a["handle"] != staff["id"])
    channel = owner.post(f"{base}/dms", json={"agentId": agent["id"]}).json()["channelId"]
    yield {"root": str(tmp_path), "app": app, "owner": owner, "base": base, "agent": agent, "main": main,
           "channel": channel, "staff": staff, "pool": pool, "service": service}
    accounts_module._FAILURES.clear()


def _join(home, email="ana@example.com", name="小王"):
    made = home["owner"].post(f"{home['base']}/invitations", json={"email": email}, headers=ORIGIN)
    token = made.json()["url"].rsplit("/", 1)[1]
    browser = TestClient(home["app"], headers=ORIGIN)
    assert browser.post("/api/auth/sign-up/email", json={"name": name, "email": email, "password": PASSWORD},
                        headers={"x-invitation-token": token}).status_code == 200
    assert browser.post(f"/api/app/invitations/{token}/accept", json={}).status_code == 200
    browser.member_id = browser.get("/api/app/me").json()["user"]["id"]
    return browser


async def _finish(pool, task_id):
    execute, callbacks = pool.jobs[task_id]
    async with callbacks["admission"]():
        await execute()


# ── 看得到哪些 agent ──────────────────────────────────────────────────────────

def test_a_member_sees_the_agents_they_can_use_and_never_the_main_assistant(home):
    member = _join(home)
    listed = member.get(f"{home['base']}/agents").json()["agents"]
    assert [a["id"] for a in listed] == [home["agent"]["id"]]
    assert listed[0]["seesDetails"] is False
    assert [d["with"]["id"] for d in member.get(f"{home['base']}/dms").json()["dms"]] == [home["agent"]["id"]]
    statuses = member.get(f"{home['base']}/agent-status").json()["agents"]
    assert [row["agentId"] for row in statuses] == [home["agent"]["id"]]
    assert [row["agentId"] for row in member.get(f"{home['base']}/ai-status").json()["agents"]] == [home["agent"]["id"]]
    assert member.post(f"{home['base']}/dms", json={"agentId": home["main"]["id"]}).status_code == 404
    main_channel = home["owner"].get(f"{home['base']}/dms").json()["dms"][0]["channelId"]
    for path in ("messages", "members", "active-runs"):
        assert member.get(f"{home['base']}/channels/{main_channel}/{path}").status_code == 404
    assert member.post(f"{home['base']}/channels/{main_channel}/messages", json={"body": "你好"}).status_code == 404


def test_team_directory_uses_existing_visible_rosters(home):
    ana = _join(home, name="安")
    url = f"{home['base']}/team"
    owner = home["owner"].get(url)
    assert owner.status_code == 200
    assert owner.headers["cache-control"] == "no-store"
    names = {row["id"]: row["name"] for row in owner.json()["agents"]}
    assert names == {agent["id"]: agent["name"] for agent in (home["main"], home["agent"])}
    assert owner.json()["consultations"] == []
    for agent in owner.json()["agents"]:
        assert "channelIds" not in agent and "reportsToAgentId" not in agent and "instructions" not in agent

    data = ana.get(url).json()
    assert [agent["id"] for agent in data["agents"]] == [home["agent"]["id"]]
    assert data["agents"][0]["seesDetails"] is False
    assert data["agents"][0]["capabilities"] == []
    assert {person["name"] for person in data["people"]} == {"我", "安"}
    assert all(set(person) == {"id", "name", "image"} for person in data["people"])
    assert ana.get(url, headers={"Origin": "https://elsewhere.example"}).status_code == 403
    assert TestClient(home["app"]).get(url).status_code == 401
    assert ana.get("/api/app/organizations/other/team").status_code == 404
    moved = home["owner"].post(url + "/channel-move", json={})        # agent 还不会提议搬移频道：如实 409
    assert moved.status_code == 409 and moved.json()["error"]["code"] == "channel_move.unavailable"
    # agent 所在频道的接口已经有了（见 test_app_agent_detail_rest.py）：只接受空列表，坏请求 400
    assert home["owner"].put(f"{home['base']}/agents/{home['agent']['id']}/channels", json={}).status_code == 400

    MemberRegistry(home["root"]).set_act_level_on(OWNER_P, home["staff"]["id"], ana.member_id, "none")
    assert ana.get(url).json()["agents"] == []
    assert home["owner"].get(url).json()["agents"][1]["id"] == home["agent"]["id"]


def test_team_directory_only_shows_the_callers_current_run(home, monkeypatch):
    from types import SimpleNamespace

    from src.workflow.tasks.tasks import TaskLedger

    ana = _join(home)
    lee = _join(home, "lee@example.com", "李")
    url = f"{home['base']}/team"
    ledger = TaskLedger(home["root"])
    staff_handle = home["staff"]["id"]

    def save(status, initiator=None):
        dispatch = {"source": "staff", "member": staff_handle, "delivery": "notify"}
        task = home["service"].collaboration("sid-owner").create("private fixture prompt", staff_handle,
                                                               dispatch=dispatch)
        task.status = status
        if initiator is not None:
            task.collaboration["dispatch"]["initiator"] = initiator
        assert ledger.save(task)
        return task

    def busy(client):
        answer = client.get(url)
        assert answer.status_code == 200, answer.text
        return {agent["handle"]: agent["busy"] for agent in answer.json()["agents"]}

    save("queued", {"kind": "member", "id": lee.member_id})
    save("blocked")
    save("running", {"kind": "invalid", "id": lee.member_id})
    own_run = save("running")
    ana_run = save("running", {"kind": "member", "id": ana.member_id})
    assert busy(home["owner"]) == {"assistant": False, staff_handle: True}
    assert busy(ana) == {staff_handle: True}
    assert busy(lee) == {staff_handle: False}

    own_run.status = ana_run.status = "done"
    ledger.save(own_run)
    ledger.save(ana_run)
    assert busy(ana) == {staff_handle: False}
    monkeypatch.setattr("src.web.routers.app_api.existing_bridge",
                        lambda root: SimpleNamespace(active_runs=lambda: [{"status": "running"}]))
    assert busy(home["owner"]) == {"assistant": True, staff_handle: False}
    assert busy(lee) == {staff_handle: False}

    monkeypatch.delenv("VORTOONE_API_TOKEN")
    assert busy(TestClient(home["app"])) == {"assistant": False, staff_handle: False}


def test_screen_status_checks_live_computer_clearance_and_agent_visibility(home):
    member = _join(home)
    registry = MemberRegistry(home["root"])
    base = home["base"]
    agent = f"{base}/agents/{home['agent']['id']}"
    private_agent = f"{base}/agents/{home['main']['id']}"

    assert member.get(f"{agent}/browser").status_code == 403  # Own browser, but this agent belongs to the owner.
    registry.change_group(OWNER_P, "everyone", computer="all_browsers")
    browser = member.get(f"{agent}/browser")
    assert browser.status_code == 200
    assert browser.headers["cache-control"] == "no-store"
    assert browser.json() == {"allowedDomains": None, "available": False, "reason": "no_machine",
                              "detail": None, "machine": None}
    assert member.get(f"{agent}/browser/handoff").json() == {"handoff": None}
    assert member.get(f"{agent}/desktop").status_code == 403
    assert member.get(f"{private_agent}/browser").status_code == 404
    assert member.get(f"{agent}/browser", headers={"Origin": "https://other.example"}).status_code == 403

    registry.change_group(OWNER_P, "everyone", computer="all_computers")
    desktop = member.get(f"{agent}/desktop")
    assert desktop.status_code == 200
    assert not desktop.json()["available"] and not desktop.json()["canControl"]
    registry.change_group(OWNER_P, "everyone", computer="none")
    denied = member.get(f"{agent}/browser/handoff")
    assert denied.status_code == 403 and denied.headers["cache-control"] == "no-store"
    assert member.get(f"{agent}/desktop").status_code == 403
    assert home["owner"].get(f"{agent}/desktop").status_code == 200


def test_none_on_an_agent_hides_it(home):
    member = _join(home)
    MemberRegistry(home["root"]).set_act_level_on(OWNER_P, home["staff"]["id"], member.member_id, "none")
    assert member.get(f"{home['base']}/agents").json()["agents"] == []
    assert member.get(f"{home['base']}/agents/{home['agent']['id']}").status_code == 404
    assert member.get(f"{home['base']}/channels/{home['channel']}/messages").status_code == 404
    sent = member.post(f"{home['base']}/channels/{home['channel']}/messages", json={"body": "帮我看看"})
    assert sent.status_code == 404 and not home["pool"].jobs


def test_member_policy_requires_the_right_actor_and_a_current_role(home, monkeypatch):
    from src.utils.state_dir import state_dir

    member = _join(home)
    owner = home["owner"]
    url = f"{home['base']}/agents/{home['agent']['id']}/member-policy"
    initial = owner.get(url).json()
    assert initial["actForMembers"] is False and initial["autoApprove"] == []
    assert initial["canSetAutoApprove"] and initial["availableAutoApprove"] == []
    enable = {"roleVersion": initial["roleVersion"], "actForMembers": True}
    assert member.put(url, json=enable).status_code == 403
    assert owner.put(url, json={**enable, "roleVersion": "stale"}).status_code == 400
    assert owner.put(url, json={**enable, "autoApprove": ["write"]}).status_code == 400
    assert owner.put(url, json={**enable, "autoApprove": ["deliver"]}).status_code == 400

    registry = MemberRegistry(home["root"])
    post = registry.create_group(OWNER_P, "代办管理", items=["roles.act_for_members"])
    registry.set_member_groups(OWNER_P, member.member_id, [post])
    assert member.get(url).json()["canSetAutoApprove"] is False
    assert member.put(url, json={**enable, "autoApprove": []}).status_code == 403
    assert member.put(url, json=enable, headers={"Origin": "https://elsewhere.example"}).status_code == 403
    assert member.put(url, json=enable).json()["actForMembers"] is True
    assert owner.put(url, json=enable).status_code == 200
    audit_path = state_dir(home["root"]) / "audit.log"
    events = [json.loads(line) for line in audit_path.read_text().splitlines()]
    changes = [event for event in events if event.get("operation") == "授权修改智能体成员权限"]
    assert len(changes) == 1 and changes[0]["actor_id"] == member.member_id
    assert changes[0]["staff_member"] == home["staff"]["id"]
    assert not changes[0]["previous"]["actForMembers"] and changes[0]["requested"]["actForMembers"]

    monkeypatch.setattr("src.gateway.audit.append_audit", lambda *_args, **_kwargs: False)
    assert owner.put(url, json={**enable, "actForMembers": False}).status_code == 409
    assert owner.get(url).json()["actForMembers"] is True
    main_url = f"{home['base']}/agents/{home['main']['id']}/member-policy"
    assert owner.get(main_url).status_code == 404


def test_owner_can_disable_and_restore_member_conversations_through_role_settings(home):
    member = _join(home)
    roles_url = f"{home['base']}/roles"
    everyone_url = f"{roles_url}/everyone"
    agents_url = f"{home['base']}/agents"
    everyone = next(role for role in member.get(roles_url).json()["roles"] if role["id"] == "everyone")
    assert everyone["actLevel"] == "read" and not everyone["actLevelEditable"]
    assert member.patch(everyone_url, json={"actLevel": "execute"}).status_code == 403
    assert member.get(agents_url).json()["agents"]

    assert home["owner"].patch(everyone_url, json={"actLevel": "none"}).status_code == 204
    assert member.get(agents_url).json()["agents"] == []
    sent = member.post(f"{home['base']}/channels/{home['channel']}/messages", json={"body": "帮我看看"})
    assert sent.status_code == 404 and not home["pool"].jobs
    assert home["owner"].patch(everyone_url, json={"actLevel": "read"}).status_code == 204
    assert [agent["id"] for agent in member.get(agents_url).json()["agents"]] == [home["agent"]["id"]]


def test_per_agent_action_level_controls_only_the_selected_member_and_agent(home):
    target = _join(home)
    other = _join(home, "li@example.com")
    owner, base = home["owner"], home["base"]
    agent_url = f"{base}/agents/{home['agent']['id']}"
    levels_url = f"{agent_url}/member-action-levels"
    target_url = f"{levels_url}/{target.member_id}"
    projected = owner.get(levels_url).json()
    assert projected["maxLevel"] == "execute"
    row = next(m for m in projected["members"] if m["userId"] == target.member_id)
    assert row == {"userId": target.member_id, "name": "小王", "level": None,
                   "roleLevel": "read", "inAudience": True}
    assert owner.get(f"{base}/agents/{home['main']['id']}/member-action-levels").status_code == 404
    assert target.get(levels_url).status_code == 403
    assert target.put(target_url, json={"level": "execute"}).status_code == 403
    assert owner.put(target_url, json={}).status_code == 400
    assert owner.put(target_url, json={"level": False}).status_code == 400
    assert owner.put(f"{levels_url}/m_missing", json={"level": "none"}).status_code == 404

    assert owner.put(target_url, json={"level": "none"}).status_code == 204
    assert target.get(agent_url).status_code == 404
    assert other.get(agent_url).status_code == 200
    registry = MemberRegistry(home["root"])
    assert registry.act_level_on("another-agent", Principal(MEMBER, target.member_id)) == "read"
    assert owner.put(target_url, json={"level": None}).status_code == 204
    assert target.get(agent_url).status_code == 200
    assert owner.get(levels_url).json() == projected

    assert owner.patch(agent_url, json={"audience": {"mode": "people", "userIds": [other.member_id]}}).status_code == 200
    assert owner.put(target_url, json={"level": "read"}).status_code == 204
    row = next(m for m in owner.get(levels_url).json()["members"] if m["userId"] == target.member_id)
    assert row["level"] == "read" and not row["inAudience"]
    assert target.get(agent_url).status_code == 404
    assert owner.delete(agent_url).status_code == 204
    assert owner.get(levels_url).status_code == 404
    assert owner.put(target_url, json={"level": "read"}).status_code == 404


def test_agent_manager_changes_levels_only_up_to_current_access(home):
    manager = _join(home)
    target = _join(home, "li@example.com")
    owner, base = home["owner"], home["base"]
    made = owner.post(f"{base}/roles", json={"name": "智能体管理员", "permissions": ["manage_agents"],
                                           "actLevel": "write"}).json()["id"]
    assert owner.put(f"{base}/members/{manager.member_id}/roles", json={"roleIds": [made]}).status_code == 200
    levels_url = f"{base}/agents/{home['agent']['id']}/member-action-levels"
    target_url = f"{levels_url}/{target.member_id}"
    manager_url = f"{levels_url}/{manager.member_id}"
    assert manager.get(f"{base}/agents").json()["agents"][0]["canManageMemberActionLevels"]
    assert manager.get(levels_url).json()["maxLevel"] == "write"
    assert manager.put(target_url, json={"level": "execute"}).status_code == 403
    assert manager.put(target_url, json={"level": "write"}, headers={"Origin": "https://example.org"}).status_code == 403
    assert manager.put(target_url, json={"level": "write"}).status_code == 204

    assert owner.put(manager_url, json={"level": "read"}).status_code == 204
    assert manager.get(levels_url).json()["maxLevel"] == "read"
    assert manager.put(target_url, json={"level": "write"}).status_code == 403
    assert manager.put(target_url, json={"level": None}).status_code == 204

    assert owner.put(target_url, json={"level": "read"}).status_code == 204
    executors = owner.post(f"{base}/roles", json={"name": "执行组", "actLevel": "execute"}).json()["id"]
    assert owner.put(f"{base}/members/{target.member_id}/roles", json={"roleIds": [executors]}).status_code == 200
    assert manager.put(target_url, json={"level": None}).status_code == 403

    assert owner.put(manager_url, json={"level": "none"}).status_code == 204
    assert manager.get(levels_url).status_code == 404
    assert manager.put(target_url, json={"level": "none"}).status_code == 404


def test_the_agent_page_has_no_owner_numbers(home):
    member = _join(home)
    detail = member.get(f"{home['base']}/agents/{home['agent']['id']}").json()
    assert detail["tokensThisMonth"] == 0 and detail["runsThisMonth"] == 0 and detail["runsByDay"] == []


def _member_routine(home, member):
    owner, base = home["owner"], home["base"]
    path = f"{base}/members/{member.member_id}/roles"
    role = owner.post(f"{base}/roles", json={"name": "排期", "permissions": ["manage_routines"]}).json()["id"]
    assert owner.put(path, json={"roleIds": [role]}).status_code == 200
    body = {"agentId": home["agent"]["id"], "reason": "每天核对资料",
            "definition": {"id": "routine-member-report", "name": "例行核对", "prompt": "核对公开资料",
                           "schedule": "0 9 * * *", "timezone": "UTC"}}
    return path, role, body


def test_member_routine_uses_authenticated_creator_and_stops_when_they_lose_access(home):
    from src.workflow.staff.approvals import StaffApprovals
    from src.workflow.staff.routines import Routines
    from src.workflow.tasks.tasks import TaskLedger

    creator = _join(home)
    other = _join(home, "li@example.com")
    owner, base = home["owner"], home["base"]
    roles_url, role, body = _member_routine(home, creator)
    proposal_url = f"{base}/routines/proposals"
    member_org, = creator.get("/api/app/me").json()["organizations"]
    assert member_org["memberRoutineProposals"]
    assert other.post(proposal_url, json=body).status_code == 403
    assert creator.post(proposal_url, json={**body, "initiator": other.member_id}).status_code == 400
    assert creator.post(proposal_url, json={**body, "agentId": home["main"]["id"]}).status_code == 404

    result = creator.post(proposal_url, json=body)
    assert result.status_code == 201 and result.json()["status"] == "pending"
    assert creator.post(proposal_url, json=body).json() == result.json()
    changed = {**body, "definition": {**body["definition"], "schedule": "at 10:00"}}
    assert creator.post(proposal_url, json=changed).status_code == 400
    assert other.get(proposal_url).json() == {"proposals": []}
    assert owner.get(proposal_url).status_code == 403
    listed, = creator.get(proposal_url).json()["proposals"]
    assert listed == {"id": result.json()["id"], "name": "例行核对", "agentName": home["staff"]["name"],
                      "schedule": "0 9 * * *", "timezone": "UTC", "status": "pending",
                      "createdAt": listed["createdAt"]}
    root = home["root"]
    approvals = StaffApprovals(root)
    pending = approvals.get(result.json()["id"])
    routines = Routines(root)
    assert routines.list() == [] and not home["pool"].jobs
    assert pending["snapshot"]["initiator"] == creator.member_id
    assert any(p["id"] == pending["id"] for p in owner.get("/api/staff").json()["proposals"])
    decision = f"/api/staff/proposals/{pending['id']}/decision"
    assert owner.post(decision, json=proposal_body(pending)).status_code == 200
    assert creator.get(proposal_url).json()["proposals"] == [{**listed, "status": "approved"}]
    assert creator.post(proposal_url, json=body).json() == {**result.json(), "status": "approved"}
    definition = routines.get(body["definition"]["id"])
    assert definition["initiator"] == creator.member_id
    due = datetime.fromisoformat(definition["state"]["cursor"]) + timedelta(days=1)
    started, = routines.dispatch(home["service"], due)
    task = TaskLedger(root).load(started)
    assert task.collaboration["dispatch"]["initiator"] == {"kind": "member", "id": creator.member_id}
    assert task.owner_session == "sid-owner"
    with pytest.raises(ValueError, match="发起人记录不一致"):
        routines.check_dispatch(task.collaboration["dispatch"]["routine"], None)

    assert owner.put(roles_url, json={"roleIds": []}).status_code == 200
    assert creator.get(proposal_url).json()["proposals"][0]["status"] == "approved"
    with pytest.raises(ValueError, match="当前无权"):
        routines.check_task(task)
    display, = owner.get(f"{base}/routines").json()["routines"]
    assert display["pausedReason"] == "creator_no_access" and not display["active"]
    assert routines.dispatch(home["service"], due + timedelta(days=1)) == []
    assert not routines.get(definition["id"])["enabled"]
    assert owner.put(roles_url, json={"roleIds": [role]}).status_code == 200
    display, = owner.get(f"{base}/routines").json()["routines"]
    assert display["pausedReason"] is None and not display["active"]
    assert routines.dispatch(home["service"], due + timedelta(days=1)) == []
    assert owner.delete(f"{base}/members/{creator.member_id}").status_code == 204
    assert creator.get(proposal_url).status_code == 401
    display, = owner.get(f"{base}/routines").json()["routines"]
    assert display["pausedReason"] == "creator_left" and display["createdByUserId"] is None
    assert owner.patch(f"{base}/routines/{definition['id']}", json={"active": True}).status_code == 409


def test_departed_creator_cannot_get_a_pending_routine_approved(home):
    from src.workflow.staff.approvals import StaffApprovals
    from src.workflow.staff.routines import Routines

    creator = _join(home)
    _, _, body = _member_routine(home, creator)
    base = home["base"]
    result = creator.post(f"{base}/routines/proposals", json=body)
    assert result.status_code == 201
    approvals = StaffApprovals(home["root"])
    pending = approvals.get(result.json()["id"])
    owner = home["owner"]
    assert owner.delete(f"{base}/members/{creator.member_id}").status_code == 204
    decision = f"/api/staff/proposals/{pending['id']}/decision"
    refused = owner.post(decision, json=proposal_body(pending))
    assert refused.status_code == 409 and "创建者已离开" in refused.json()["detail"]
    assert owner.post(decision, json=proposal_body(pending, approve=False)).status_code == 200
    assert approvals.get(pending["id"])["status"] == "rejected"
    assert not Routines(home["root"]).list()


def test_members_can_read_their_rejected_routine_proposals_only(home):
    creator, other = _join(home), _join(home, "li@example.com")
    _, _, body = _member_routine(home, creator)
    _, _, other_body = _member_routine(home, other)
    url = f"{home['base']}/routines/proposals"
    result = creator.post(url, json=body)
    unrelated = other.post(url, json=other_body)
    assert result.status_code == unrelated.status_code == 201
    proposal_id = result.json()["id"]
    owner = home["owner"]
    pending = next(p for p in owner.get("/api/staff").json()["proposals"] if p["id"] == proposal_id)
    assert owner.post(f"/api/staff/proposals/{proposal_id}/decision",
                      json=proposal_body(pending, approve=False)).status_code == 200
    mine, = creator.get(url).json()["proposals"]
    theirs, = other.get(url).json()["proposals"]
    assert (mine["id"], mine["status"]) == (proposal_id, "rejected")
    assert (theirs["id"], theirs["status"]) == (unrelated.json()["id"], "pending")


def test_owner_can_review_member_routines_in_the_app_and_members_see_the_decisions(home, monkeypatch):
    from src.web.auth import SESSION_COOKIE
    from src.workflow.staff.approvals import StaffApprovals
    from src.workflow.staff.routines import Routines

    creator = _join(home)
    _, _, body = _member_routine(home, creator)
    base = f"{home['base']}/routines/proposals"
    first = creator.post(base, json=body).json()["id"]
    second_body = {**body, "definition": {**body["definition"], "id": "routine-other-review", "enabled": False}}
    second = creator.post(base, json=second_body).json()["id"]
    approvals = StaffApprovals(home["root"])
    unrelated = approvals.propose("sid-owner", "routine", {
        **body["definition"], "id": "routine-owner-only", "member": home["staff"]["id"],
    }, "所有者原来的提议")
    cookie_owner = TestClient(home["app"], headers=ORIGIN)
    cookie_owner.cookies.set(SESSION_COOKIE, TOKEN)
    assert cookie_owner.get("/api/app/me").json()["organizations"][0]["routineProposalReview"]
    assert creator.get(f"{base}/review").status_code == 403
    listing = cookie_owner.get(f"{base}/review")
    assert listing.status_code == 200 and listing.headers["cache-control"] == "no-store"
    rows = {row["id"]: row for row in listing.json()["proposals"]}
    assert set(rows) == {first, second}
    review = rows[first]
    assert review["submitterName"] == "小王" and review["agentName"] == home["staff"]["name"]
    assert review["prompt"] == body["definition"]["prompt"] and review["reason"] == body["reason"]
    assert review["schedule"] == body["definition"]["schedule"] and review["timezone"] == "UTC"
    assert review["status"] == "pending" and review["enabled"] and not rows[second]["enabled"]
    assert set(review["proof"]) == {"digest", "nonce", "round", "instance"}
    decision = {**review["proof"], "approve": True}
    path = f"{base}/{first}/decision"
    assert creator.post(path, json=decision).status_code == 403
    assert cookie_owner.post(path, json=decision, headers={"Origin": "https://elsewhere.test"}).status_code == 403
    assert cookie_owner.post(path, json={**decision, "nonce": "stale"}).status_code == 409
    assert cookie_owner.post(f"{base}/{unrelated['id']}/decision", json=proposal_body(unrelated)).status_code == 404
    assert not Routines(home["root"]).list() and not home["pool"].jobs

    def interrupted(*args, **kwargs):
        raise OSError("registration interrupted")

    with monkeypatch.context() as fault:
        fault.setattr(Routines, "apply", interrupted)
        assert cookie_owner.post(path, json=decision).status_code == 409
    applying = next(row for row in cookie_owner.get(f"{base}/review").json()["proposals"] if row["id"] == first)
    assert applying["status"] == "applying" and applying["proof"] == review["proof"]
    assert cookie_owner.post(path, json={**applying["proof"], "approve": False}).status_code == 409
    assert cookie_owner.post(path, json=decision).json() == {"id": first, "status": "approved"}
    assert cookie_owner.post(path, json=decision).json() == {"id": first, "status": "approved"}
    assert cookie_owner.post(f"{base}/{second}/decision",
                             json={**rows[second]["proof"], "approve": False}).json() == {
        "id": second, "status": "rejected",
    }
    assert cookie_owner.get(f"{base}/review").json() == {"proposals": []}
    assert {row["id"]: row["status"] for row in creator.get(base).json()["proposals"]} == {
        first: "approved", second: "rejected",
    }
    registered, = Routines(home["root"]).list()
    assert registered["id"] == body["definition"]["id"] and registered["initiator"] == creator.member_id
    assert not home["pool"].jobs


# ── 私聊 ──────────────────────────────────────────────────────────────────────

def _approve_routine_for_takeover(home):
    creator = _join(home)
    roles_url, _, body = _member_routine(home, creator)
    base, owner = home["base"], home["owner"]
    result = creator.post(f"{base}/routines/proposals", json=body)
    assert result.status_code == 201
    pending = next(p for p in owner.get("/api/staff").json()["proposals"] if p["id"] == result.json()["id"])
    assert owner.post(f"/api/staff/proposals/{pending['id']}/decision", json=proposal_body(pending)).status_code == 200
    return creator, roles_url, body["definition"]["id"]


def test_routine_takeover_is_owner_confirmed_and_only_schedules_future_runs(home):
    from src.utils.state_dir import state_dir
    from src.workflow.staff.routines import Routines

    creator, _, routine_id = _approve_routine_for_takeover(home)
    other = _join(home, "li@example.com")
    owner, base, service = home["owner"], home["base"], home["service"]
    url = f"{base}/routines/{routine_id}/take-over"
    routines = Routines(home["root"])
    previous = routines.get(routine_id)
    due = datetime.fromisoformat(previous["state"]["cursor"]) + timedelta(days=1)
    old_id, = routines.dispatch(service, due)
    old = service.collaboration("sid-owner").get(old_id)
    assert owner.get(url).status_code == 409
    assert owner.delete(f"{base}/members/{creator.member_id}").status_code == 204
    assert owner.get(f"{base}/routines").json()["routines"][0]["canTakeOver"]
    assert other.get(url).status_code == 403
    assert other.post(url, json={"digest": "a" * 64}).status_code == 403
    assert owner.post(url, json={}).status_code == 400

    preview = owner.get(url).json()
    assert preview["prompt"] == previous["prompt"] and preview["acceptance"] == previous["acceptance"]
    assert preview["schedule"] == previous["schedule"] and preview["timezone"] == previous["timezone"]
    assert preview["operations"] == previous["binding"]["role"]["grants"]["operations"]
    approved = owner.post(url, json={"digest": preview["digest"]})
    assert approved.status_code == 200
    assert not approved.json()["active"] and approved.json()["awaitingTakeoverResume"]
    assert approved.json()["createdByUserId"] == owner.get("/api/app/me").json()["user"]["id"]
    current = routines.get(routine_id)
    assert "initiator" not in current and current["revision"] == previous["revision"] + 1
    assert current["state"]["task"] == old_id
    assert owner.post(url, json={"digest": preview["digest"]}).status_code == 200
    assert routines.get(routine_id) == current
    audit = [json.loads(line) for line in (state_dir(home["root"]) / "audit.log").read_text().splitlines()]
    takeovers = [entry for entry in audit if entry.get("approval_source") == "routine_takeover"]
    assert len(takeovers) == 1 and takeovers[0]["initiator"] == creator.member_id
    assert takeovers[0]["action_digest"] == preview["digest"]
    assert routines.dispatch(service, due + timedelta(days=1), manual=True) == []
    with pytest.raises(ValueError, match="旧执行不能继续"):
        routines.check_task(old)

    resumed = owner.patch(f"{base}/routines/{routine_id}", json={"active": True})
    assert resumed.status_code == 200 and resumed.json()["active"]
    enabled_at = datetime.fromisoformat(routines.get(routine_id)["state"]["cursor"])
    assert routines.dispatch(service, enabled_at + timedelta(days=1)) == []
    service.collaboration("sid-owner").abort(old_id, old.collaboration["round"], error="旧执行已停止")
    assert routines.dispatch(service, enabled_at) == []
    new_id, = routines.dispatch(service, enabled_at + timedelta(days=1))
    new = service.collaboration("sid-owner").get(new_id)
    assert new_id != old_id and "initiator" not in new.collaboration["dispatch"]
    assert new.collaboration["dispatch"]["routine"]["revision"] == current["revision"] + 1
    assert service.collaboration("sid-owner").get(old_id).collaboration["dispatch"]["initiator"]["id"] == creator.member_id


def test_routine_takeover_rejects_stale_review_and_requires_a_durable_audit(home, monkeypatch):
    from src.workflow.staff.routines import Routines
    creator, roles_url, routine_id = _approve_routine_for_takeover(home)
    owner, base = home["owner"], home["base"]
    routine_url = f"{base}/routines/{routine_id}"
    url = f"{routine_url}/take-over"
    routines = Routines(home["root"])
    assert owner.put(roles_url, json={"roleIds": []}).status_code == 200
    preview = owner.get(url).json()
    assert owner.patch(routine_url, json={"active": False}).status_code == 200
    assert owner.post(url, json={"digest": preview["digest"]}).status_code == 409
    fresh = owner.get(url).json()
    assert fresh["digest"] != preview["digest"]

    def cannot_verify(*args, **kwargs):
        raise OSError("could not read members")

    with monkeypatch.context() as fault:
        fault.setattr(MemberRegistry, "member_act_level_on", cannot_verify)
        assert not owner.get(f"{base}/routines").json()["routines"][0]["canTakeOver"]
        assert owner.post(url, json={"digest": fresh["digest"]}).status_code == 409
    with monkeypatch.context() as fault:
        fault.setattr("src.gateway.audit.append_audit", lambda *args, **kwargs: False)
        assert owner.post(url, json={"digest": fresh["digest"]}).status_code == 409
    assert routines.get(routine_id)["initiator"] == creator.member_id
    assert owner.post(url, json={"digest": fresh["digest"]}).status_code == 200


async def test_a_member_message_becomes_a_task_they_started(home):
    member, pool, service = _join(home), home["pool"], home["service"]
    sent = member.post(f"{home['base']}/channels/{home['channel']}/messages",
                       json={"body": "读一下这份材料", "clientId": "c1"})
    assert sent.status_code == 200, sent.text
    message = sent.json()
    assert message["author"] == {"type": "person", "id": member.member_id} and message["clientId"] == "c1"
    task_id, = pool.jobs
    task = service.collaboration("sid-owner").get(task_id)
    assert task.collaboration["dispatch"]["initiator"] == {"kind": MEMBER, "id": member.member_id}
    assert task.collaboration["dispatch"]["delivery"] == "notify"

    runs = member.get(f"{home['base']}/channels/{home['channel']}/active-runs").json()["runs"]
    assert [r["runId"] for r in runs] == [task_id]
    await _finish(pool, task_id)
    listed = member.get(f"{home['base']}/channels/{home['channel']}/messages").json()["messages"]
    assert [(m["author"]["type"], m["body"]) for m in listed] == [("person", "读一下这份材料"), ("agent", "逐条交回结论")]
    assert listed[0]["author"]["id"] == member.member_id


def test_everyone_sees_only_their_own_side_of_an_agent(home):
    ana, bo = _join(home), _join(home, "bo@example.com", "小博")
    base, channel = home["base"], home["channel"]
    assert home["owner"].post(f"{base}/channels/{channel}/messages", json={"body": "主人的私事"}).status_code == 200
    assert ana.post(f"{base}/channels/{channel}/messages", json={"body": "安娜的事"}).status_code == 200
    assert bo.post(f"{base}/channels/{channel}/messages", json={"body": "小博的事"}).status_code == 200

    def bodies(client):
        return [m["body"] for m in client.get(f"{base}/channels/{channel}/messages").json()["messages"]]
    assert bodies(home["owner"]) == ["主人的私事"]
    assert bodies(ana) == ["安娜的事"]
    assert bodies(bo) == ["小博的事"]
    assert len(ana.get(f"{base}/channels/{channel}/active-runs").json()["runs"]) == 1
    assert len(home["owner"].get(f"{base}/channels/{channel}/active-runs").json()["runs"]) == 1


def test_a_member_cannot_stop_work_yet(home):
    member = _join(home)
    member.post(f"{home['base']}/channels/{home['channel']}/messages", json={"body": "读一下"})
    task_id, = home["pool"].jobs
    assert member.post(f"{home['base']}/runs/{task_id}/cancel", json={}).status_code == 403


def test_a_removed_member_cannot_talk(home):
    member = _join(home)
    MemberRegistry(home["root"]).remove_member(OWNER_P, member.member_id)
    for path in ("agents", "dms", "people", "overview", "agent-status"):
        assert member.get(f"{home['base']}/{path}").status_code in {401, 403}
    assert member.post(f"{home['base']}/channels/{home['channel']}/messages",
                       json={"body": "还在吗"}).status_code in {401, 403}
    assert not home["pool"].jobs


# ── 主人的数据对成员一律回空 ──────────────────────────────────────────────────

def test_owner_data_lists_are_empty_for_members_without_running_their_handlers(home, monkeypatch):
    from src.web import member_auth
    member = _join(home)

    def boom(_org_id):
        raise AssertionError("成员的空回答不该进处理函数")
    monkeypatch.setattr("src.web.routers.app_api._root", boom)
    # approvals 不在这里：替所有者代批的成员要进处理函数（没在代批时是空的，见 test_approval_cover.py）
    paths = ["memories", f"agents/{home['agent']['id']}/memories", "skills", "routines", "notifications", "runs",
             "usage", "artifacts", "active-runs"]
    for path in paths:
        url = f"{home['base']}/{path}"
        answer = member.get(url)
        assert answer.status_code == 200, path
        assert answer.json() == member_auth.empty_answer("GET", url), path
    assert member.get("/api/app/models").json() == {"models": [], "defaultModel": ""}


def test_overview_and_people_for_a_member(home):
    member = _join(home)
    overview = member.get(f"{home['base']}/overview").json()
    assert overview["role"] == "member" and overview["agents"] == 1 and overview["people"] == 2
    people = member.get(f"{home['base']}/people").json()["people"]
    assert [(p["role"], p["name"]) for p in people] == [("owner", "我"), ("member", "小王")]
    assert member.get(f"{home['base']}/settings").status_code == 200
    assert member.get(f"{home['base']}/dashboards/home").status_code == 200


def test_the_owner_sees_everything_as_before(home):
    _join(home)
    owner = home["owner"]
    assert len(owner.get(f"{home['base']}/agents").json()["agents"]) == 2
    assert owner.get(f"{home['base']}/overview").json()["role"] == "owner"
    assert len(owner.get(f"{home['base']}/people").json()["people"]) == 2
