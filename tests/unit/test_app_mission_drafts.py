"""Mission drafts retain existing owner, channel, and task execution boundaries."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from src.utils.state_dir import state_dir
from src.workflow.members.registry import MemberRegistry
from src.workflow.staff.registry import StaffRegistry
from src.workflow.tasks.tasks import TaskLedger
from tests.unit.test_member_agents import ORIGIN, OWNER_P, TOKEN, _join, home  # noqa: F401 - pytest fixture
from tests.unit.test_team_channels import _create


def _thread(owner, channel):
    messages = channel + "/messages"
    root = owner.post(messages, json={"body": "频道讨论"})
    assert root.status_code == 200, root.text
    thread_id = root.json()["id"]
    reply = owner.post(messages, json={"body": "继续讨论", "threadId": thread_id, "openThread": True})
    assert reply.status_code == 200, reply.text
    return thread_id, reply.json()["id"]


def _input(home, thread_id, **fields):
    return {"agentId": home["agent"]["id"], "threadId": thread_id, "goal": "核对测试结果", **fields}


def _draft(home, thread_id, **fields):
    response = home["owner"].post(home["base"] + "/missions", json=_input(home, thread_id, **fields))
    assert response.status_code == 201, response.text
    return response.json()


def test_owner_creates_and_reads_real_draft_without_dispatch_or_channel_post(home, monkeypatch):
    owner, base = home["owner"], home["base"]
    channel = _create(home, "draft-thread")
    thread_id, _ = _thread(owner, channel)
    me = owner.get("/api/app/me").json()["user"]
    ledger = TaskLedger(home["root"])
    assert ledger.list() == []
    messages = owner.get(channel + "/messages", params={"threadId": thread_id}).json()
    monkeypatch.setattr(home["service"], "assign", lambda *args, **kwargs: pytest.fail("草案不派活"))

    initial = owner.get(base + "/missions")
    assert initial.json() == {"missions": [], "counts": {"active": 0, "waitingYou": 0, "closed": 0}}
    absent = owner.get(f"{base}/threads/{thread_id}/mission")
    assert absent.status_code == 404 and absent.json()["error"]["code"] == "mission.not_found"
    draft = _draft(home, thread_id, goal="  核对测试\n结果  ", title="  测试草案 ")
    assert (draft["channelId"], draft["threadId"], draft["agentId"], draft["ownerId"]) == (
        channel.rsplit("/", 1)[-1], thread_id, home["agent"]["id"], me["id"])
    assert (draft["title"], draft["goal"], draft["role"], draft["status"]) == (
        "测试草案", "核对测试\n结果", "owner", "proposed")
    assert draft["commandApproval"] == "always" and draft["executor"] == "agent" and draft["machine"] is None
    assert not (draft["canAct"] or draft["canManage"] or draft["resumeTasks"] or draft["runningTasks"])
    assert draft["plan"] == {"version": 0, "done": 0, "total": 0, "doing": 0, "waiting": 0, "items": []}
    assert draft["startedAt"] is None and draft["closedAt"] is None
    route = f"{base}/missions/{draft['id']}"
    for path in (route, f"{base}/threads/{thread_id}/mission"):
        result = owner.get(path)
        assert result.status_code == 200 and result.json() == draft
        assert result.headers["cache-control"] == "no-store"

    assert owner.get(route + "/plan").json() == {"missionId": draft["id"], "current": None, "history": []}
    summary = owner.get(route + "/summary").json()
    assert summary == {"missionId": draft["id"], "text": draft["goal"],
                       "plan": {"done": 0, "total": 0, "doing": 0, "waiting": 0}, "running": 0,
                       "pendingDecisions": 0, "provisionalDecisions": 0, "pendingReviews": 0}
    assert owner.get(route + "/participants").json() == {
        "owner": {"userId": me["id"], "name": me["name"]}, "participants": []}
    assert owner.post(route + "/start", json={}).status_code == 404
    assert home["pool"].jobs == {} and ledger.list() == []
    assert owner.get(channel + "/messages", params={"threadId": thread_id}).json() == messages

    history = state_dir(home["root"]) / "members/channel-messages.sqlite3"
    with closing(sqlite3.connect(history)) as conn:
        saved = conn.execute("SELECT owner, staff_id, staff_revision, role_id, role_revision, revision"
                             " FROM mission_drafts WHERE id = ?", (draft["id"],)).fetchone()
    role = StaffRegistry(home["root"]).role(home["staff"]["role"])
    assert saved == ("owner", home["staff"]["id"], home["staff"]["revision"], role["id"], role["revision"], 1)


def test_missions_require_owner_proof_even_for_a_channel_admin(home, monkeypatch):
    owner, base = home["owner"], home["base"]
    member = _join(home)
    registry = MemberRegistry(home["root"])
    admin = registry.create_group(OWNER_P, "管理组", items=["admin"])
    registry.set_member_groups(OWNER_P, member.member_id, [admin])
    channel = _create(home, "shared-mission")
    assert member.post(channel + "/join").status_code == 204
    thread_id, _ = _thread(owner, channel)
    draft = _draft(home, thread_id)
    route = f"{base}/missions/{draft['id']}"
    reads = [base + "/missions", route, route + "/plan", route + "/summary", route + "/participants",
             f"{base}/threads/{thread_id}/mission"]
    assert member.get(f"{base}/threads/{thread_id}").status_code == 200
    for path in reads:
        assert member.get(path).status_code == 403
    assert member.post(base + "/missions", json=_input(home, thread_id)).status_code == 403
    assert TestClient(home["app"]).get(route).status_code == 401
    assert owner.get(f"/api/app/organizations/another-home/missions/{draft['id']}").status_code == 404

    browser = TestClient(home["app"], headers=ORIGIN)
    assert browser.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    assert browser.get(route).status_code == 200
    assert browser.post(base + "/missions", json=_input(home, thread_id)).status_code == 201
    hostile = {"Origin": "https://elsewhere.example"}
    assert browser.get(route, headers=hostile).status_code == 403
    assert browser.post(base + "/missions", json=_input(home, thread_id), headers=hostile).status_code == 403
    monkeypatch.delenv("VORTOONE_API_TOKEN")
    assert browser.get(route).status_code == 403
    assert browser.get(base + "/missions").json() == {
        "missions": [], "counts": {"active": 0, "waitingYou": 0, "closed": 0}}
    assert browser.post(base + "/missions", json=_input(home, thread_id)).status_code == 403


def test_archiving_a_thread_rejects_a_stale_draft_submission_until_unarchive(home, monkeypatch):
    owner, base = home["owner"], home["base"]
    channel = _create(home, "stale-mission")
    thread_id, _ = _thread(owner, channel)
    thread = f"{base}/threads/{thread_id}"
    assert owner.get(thread).json()["state"] == "open"
    stale = _input(home, thread_id)
    assert owner.post(thread + "/archive", json={}).json()["state"] == "archived"
    history = owner.get(channel + "/messages", params={"threadId": thread_id}).json()

    with monkeypatch.context() as archived:
        archived.setattr(home["service"], "assignment", lambda *args, **kwargs: pytest.fail("先检查话题状态"))
        rejected = owner.post(base + "/missions", json=stale)
    assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "thread.archived"
    assert owner.get(thread + "/mission").status_code == 404
    assert owner.get(channel + "/messages", params={"threadId": thread_id}).json() == history
    assert owner.post(thread + "/unarchive", json={}).json()["state"] == "open"
    draft = _draft(home, thread_id)
    assert draft["threadId"] == thread_id
    assert owner.get(channel + "/messages", params={"threadId": thread_id}).json() == history
    assert home["pool"].jobs == {} and TaskLedger(home["root"]).list() == []


def test_archived_drafts_still_read_and_replay_only_with_source_access(home):
    owner, base = home["owner"], home["base"]
    member = _join(home)
    channel = _create(home, "private-draft", visibility="private", people=[member.member_id])
    thread_id, _ = _thread(owner, channel)
    thread = f"{base}/threads/{thread_id}"
    draft = _draft(home, thread_id)
    assert owner.post(thread + "/archive", json={}).json()["state"] == "archived"

    path = base + "/missions"
    draft_path = f"{path}/{draft['id']}"
    assert owner.get(draft_path).json() == draft
    assert owner.get(thread + "/mission").json() == draft
    assert _draft(home, thread_id) == draft
    conflict = owner.post(path, json=_input(home, thread_id, goal="其他目标"))
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "mission.thread_in_use"
    assert member.post(path, json=_input(home, thread_id)).status_code == 403
    assert owner.post(channel + "/leave", json={}).status_code == 204
    missing_source = owner.post(path, json=_input(home, thread_id))
    missing_thread = owner.post(path, json=_input(home, "unknown-thread"))
    assert missing_source.status_code == missing_thread.status_code == 404
    assert missing_source.json() == missing_thread.json()
    assert owner.get(draft_path).status_code == 404


def test_draft_filters_count_visible_matches_before_status_and_limit(home):
    owner, base = home["owner"], home["base"]
    url = base + "/missions"
    visible = _create(home, "visible-drafts")
    archived = _create(home, "archived-drafts")
    visible_thread, _ = _thread(owner, visible)
    other_thread, _ = _thread(owner, visible)
    archived_thread, _ = _thread(owner, archived)
    wanted = _draft(home, visible_thread, title="Straße")
    later = _draft(home, other_thread, title="第二份")
    hidden = _draft(home, archived_thread, title="Straße")
    assert owner.delete(archived).status_code == 204

    limited = owner.get(url, params={"status": "open", "limit": 1}).json()
    assert limited == {"missions": [later], "counts": {"active": 2, "waitingYou": 0, "closed": 0}}
    assert owner.get(url, params={"q": "STRASSE", "status": "proposed"}).json() == {
        "missions": [wanted], "counts": {"active": 1, "waitingYou": 0, "closed": 0}}
    for status in ("active", "waiting_you", "closed", "paused"):
        assert owner.get(url, params={"status": status, "agentId": home["agent"]["id"]}).json() == {
            "missions": [], "counts": {"active": 2, "waitingYou": 0, "closed": 0}}
    for filters in ({"agentId": home["main"]["id"]}, {"channelId": hidden["channelId"]}, {"q": "%"}):
        assert owner.get(url, params=filters).json() == {
            "missions": [], "counts": {"active": 0, "waitingYou": 0, "closed": 0}}

    hidden_route = f"{url}/{hidden['id']}"
    for path in (hidden_route, hidden_route + "/plan", hidden_route + "/summary", hidden_route + "/participants",
                 f"{base}/threads/{archived_thread}/mission"):
        assert owner.get(path).status_code == 404
    assert owner.post(url, json=_input(home, archived_thread, title="Straße")).status_code == 404
    assert owner.post(archived + "/unarchive").status_code == 204
    assert owner.post(archived + "/leave").status_code == 204
    assert owner.get(hidden_route).status_code == 404
    assert owner.post(archived + "/join").status_code == 204
    assert owner.get(hidden_route).json() == hidden


def test_bad_bodies_and_stale_staff_cannot_create_new_drafts(home):
    owner = home["owner"]
    url = home["base"] + "/missions"
    channel = _create(home, "draft-input")
    thread_id, reply_id = _thread(owner, channel)
    body = _input(home, thread_id)
    for change, code in (
        ({"threadId": None, "channelId": channel.rsplit("/", 1)[-1]}, "mission.thread_required"),
        ({"channelId": channel.rsplit("/", 1)[-1]}, "mission.invalid_location"),
        ({"folder": "code"}, "mission.unsupported"),
        ({"executor": "codex"}, "mission.unsupported"),
        ({"commandApproval": "never"}, "mission.unsupported"),
        ({"commandApproval": "low_risk_auto"}, "mission.unsupported"),
        ({"agentId": home["main"]["id"]}, "mission.unsupported"),
        ({"goal": " \n "}, "mission.invalid_text"),
        ({"title": " "}, "mission.invalid_text"),
    ):
        result = owner.post(url, json=body | change)
        assert result.status_code == 400 and result.json()["error"]["code"] == code
    for change in ({"threadId": reply_id}, {"threadId": "missing-thread"}, {"agentId": "removed-agent"}):
        assert owner.post(url, json=body | change).status_code == 404
    assert owner.post(url, json=body | {"goal": "x" * 2001}).status_code == 422
    assert owner.post(url, json=body | {"status": "active"}).status_code == 422
    for filters in ({"limit": 0}, {"limit": 101}, {"status": "other"}, {"q": "x" * 201}):
        assert owner.get(url, params=filters).status_code == 422

    registry = StaffRegistry(home["root"])
    staff = home["staff"]
    paused = registry.set_member_status(staff["id"], "paused", staff["revision"])
    refused = owner.post(url, json=body)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "mission.agent_unavailable"
    registry.set_member_status(paused["id"], "active", paused["revision"])
    registry.set_role_enabled(staff["role"], False)
    assert owner.post(url, json=body).status_code == 409
    assert owner.get(url).json()["missions"] == []


def test_old_history_reads_and_concurrent_draft_retries(home):
    owner, app = home["owner"], home["app"]
    url = home["base"] + "/missions"
    channel = _create(home, "draft-race")
    first, _ = _thread(owner, channel)
    second, _ = _thread(owner, channel)
    history = state_dir(home["root"]) / "members/channel-messages.sqlite3"
    with closing(sqlite3.connect(history)) as conn, conn:
        conn.execute("DROP TABLE mission_drafts")
    assert owner.get(url).json()["missions"] == []

    def submit(body):
        return TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}).post(url, json=body)

    with ThreadPoolExecutor(max_workers=4) as pool:
        same = list(pool.map(submit, [_input(home, first)] * 4))
        competing = list(pool.map(submit, [_input(home, second, title="第一"), _input(home, second, title="第二")]))
    assert [row.status_code for row in same] == [201] * 4
    assert all(row.json() == same[0].json() for row in same)
    assert sorted(row.status_code for row in competing) == [201, 409]
    assert next(row for row in competing if row.status_code == 409).json()["error"]["code"] == "mission.thread_in_use"
    assert owner.get(url).json()["counts"]["active"] == 2
    assert owner.get(f"{home['base']}/threads/{first}").json()["rootText"] == "频道讨论"


def test_draft_inspection_has_no_executions_or_files_and_cannot_change_approval(home):
    owner, base = home["owner"], home["base"]
    channel = _create(home, "draft-inspection")
    thread_id, _ = _thread(owner, channel)
    other_thread, _ = _thread(owner, channel)

    assert owner.get(f"{base}/threads/{thread_id}/tasks").status_code == 404
    draft = _draft(home, thread_id)
    mission_path = f"{base}/missions/{draft['id']}"
    paths = {
        mission_path + "/files": {"files": [], "editedOnMachine": []},
        mission_path + "/settings": {
            "missionId": draft["id"],
            "rules": [],
            "stats": {"auto": 0, "asked": 0, "blocked": 0},
            "agentDefault": {"commandApproval": "always", "canSet": False, "ownerId": draft["ownerId"]},
        },
        f"{base}/threads/{thread_id}/tasks": {"tasks": []},
    }

    outsider = TestClient(home["app"])
    member = _join(home)
    assert member.post(channel + "/join").status_code == 204
    assert member.get(f"{base}/threads/{thread_id}").status_code == 200
    browser = TestClient(home["app"], headers=ORIGIN)
    assert browser.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    for path, data in paths.items():
        result = owner.get(path)
        assert result.status_code == 200 and result.json() == data
        assert result.headers["cache-control"] == "no-store"
        assert outsider.get(path).status_code == 401
        assert member.get(path).status_code == 403
        assert browser.get(path).json() == data
        assert browser.get(path, headers={"Origin": "https://elsewhere.example"}).status_code == 403
        assert owner.get(path.replace(base, "/api/app/organizations/another-home")).status_code == 404

    for path in (f"{base}/missions/missing/files", f"{base}/missions/missing/settings",
                 f"{base}/threads/{other_thread}/tasks"):
        result = owner.get(path)
        assert result.status_code == 404 and result.json()["error"]["code"] == "mission.not_found"
    assert owner.put(mission_path + "/command-approval", json={"commandApproval": "never"}).status_code == 404
    assert owner.get(mission_path).json() == draft
    assert not home["pool"].jobs
    assert TaskLedger(home["root"]).list() == []

    assert owner.delete(channel).status_code == 204
    for path in paths:
        response = owner.get(path)
        assert response.status_code == 404 and response.json()["error"]["code"] == "mission.not_found"
