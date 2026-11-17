"""成员和 agent 对话：只看到自己用得了的 agent，私聊里只有自己发起的事，主人的数据对成员回空。"""
import pytest
from fastapi.testclient import TestClient

from src.agents.principal import MEMBER, OWNER, Principal
from src.workflow.members import accounts as accounts_module
from src.workflow.members.registry import MemberRegistry
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


def test_none_on_an_agent_hides_it(home):
    member = _join(home)
    MemberRegistry(home["root"]).set_act_level_on(OWNER_P, home["staff"]["id"], member.member_id, "none")
    assert member.get(f"{home['base']}/agents").json()["agents"] == []
    assert member.get(f"{home['base']}/agents/{home['agent']['id']}").status_code == 404
    assert member.get(f"{home['base']}/channels/{home['channel']}/messages").status_code == 404
    sent = member.post(f"{home['base']}/channels/{home['channel']}/messages", json={"body": "帮我看看"})
    assert sent.status_code == 404 and not home["pool"].jobs


def test_the_agent_page_has_no_owner_numbers(home):
    member = _join(home)
    detail = member.get(f"{home['base']}/agents/{home['agent']['id']}").json()
    assert detail["tokensThisMonth"] == 0 and detail["runsThisMonth"] == 0 and detail["runsByDay"] == []


# ── 私聊 ──────────────────────────────────────────────────────────────────────

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
    paths = ["memories", f"agents/{home['agent']['id']}/memories", "skills", "routines", "notifications", "runs",
             "usage", "approvals", "artifacts", "active-runs", "machines", "channels"]
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
