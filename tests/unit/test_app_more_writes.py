"""新版界面的第二批写入：主人写技能、删制品、批量处理审批。规矩仍在各自的登记处。"""

import pytest

from tests.unit.test_app_bridge import _until, app_home  # noqa: F401 - pytest fixture
from tests.unit.test_app_writes import _signed_in, home  # noqa: F401 - pytest fixture


@pytest.fixture
def owner(home, monkeypatch):  # noqa: F811
    monkeypatch.setenv("VORTOONE_WORKSPACE_SCOPE", "general")
    _signed_in(home, monkeypatch)
    return home


# ── 技能 ──────────────────────────────────────────────────────────────────────

def test_the_owner_writes_a_skill_and_can_turn_it_on_at_once(owner):
    from src.workflow.workbench.personal_skills import PersonalSkills
    client, base, main = owner["client"], owner["base"], owner["main"]
    made = client.post(f"{base}/skills", json={"name": "Weekly Report", "description": "写周报时用",
                                               "body": "按项目列出进展。"})
    assert made.status_code == 200, made.text
    detail = made.json()
    assert detail["id"] == "weekly-report" and detail["body"] == "按项目列出进展。"
    store = PersonalSkills(str(owner["root"]))
    assert store.get("weekly-report")["enabled"] is False                   # 存下时未启用
    # 正文是主人自己写的，算核对过：可以直接启用
    on = client.put(f"{base}/agents/{main}/skills", json={"skills": ["weekly-report"]})
    assert on.status_code == 200 and store.get("weekly-report")["enabled"] is True
    # 同名再建一条：换个 id，不覆盖
    again = client.post(f"{base}/skills", json={"name": "Weekly Report", "description": "", "body": "另一份"})
    assert again.json()["id"] == "weekly-report-2"


def test_a_chinese_name_gets_a_stable_id(owner):
    client, base = owner["client"], owner["base"]
    made = client.post(f"{base}/skills", json={"name": "周报", "description": "", "body": "列出进展"}).json()
    assert made["id"].startswith("skill-") and made["name"] == "周报"


def test_editing_keeps_an_enabled_skill_enabled_at_the_new_version(owner):
    from src.workflow.workbench.personal_skills import PersonalSkills
    client, base, main = owner["client"], owner["base"], owner["main"]
    client.post(f"{base}/skills", json={"name": "report", "description": "", "body": "旧正文"})
    client.put(f"{base}/agents/{main}/skills", json={"skills": ["report"]})
    store = PersonalSkills(str(owner["root"]))
    before = store.get("report")["revision"]
    changed = client.patch(f"{base}/skills/report", json={"body": "新正文", "description": "新说明"})
    assert changed.status_code == 200 and changed.json()["body"] == "新正文"
    now = store.get("report")
    assert now["revision"] != before and now["enabled"] is True
    assert store.load_instructions("report")["instructions"] == "新正文"


def test_editing_a_disabled_skill_leaves_it_off(owner):
    from src.workflow.workbench.personal_skills import PersonalSkills
    client, base = owner["client"], owner["base"]
    client.post(f"{base}/skills", json={"name": "report", "description": "", "body": "旧正文"})
    client.patch(f"{base}/skills/report", json={"body": "新正文"})
    assert PersonalSkills(str(owner["root"])).get("report")["enabled"] is False


def test_a_deleted_skill_is_gone_and_cannot_load(owner):
    from src.workflow.workbench.personal_skills import PersonalSkills
    client, base, main = owner["client"], owner["base"], owner["main"]
    client.post(f"{base}/skills", json={"name": "report", "description": "", "body": "正文"})
    client.put(f"{base}/agents/{main}/skills", json={"skills": ["report"]})
    assert client.delete(f"{base}/skills/report").status_code == 204
    assert client.get(f"{base}/skills").json() == {"skills": []}
    with pytest.raises(FileNotFoundError):
        PersonalSkills(str(owner["root"])).load_instructions("report")
    assert client.delete(f"{base}/skills/report").status_code == 404
    assert client.patch(f"{base}/skills/report", json={"body": "x"}).status_code == 404
    # 删掉后可以用同一个名字重建
    assert client.post(f"{base}/skills", json={"name": "report", "description": "", "body": "重来"}).json()["id"] == "report"


@pytest.mark.parametrize("body", [
    {"name": "", "description": "", "body": "x"},
    {"name": "a", "description": "", "body": ""},
    {"name": "a", "description": "", "body": "x", "defaultForNewAgents": True},
    {"name": "a", "description": "", "body": "x", "sourceUrl": "https://example.com"},
    {"name": "a", "description": "", "body": "x" * 70000},
])
def test_bad_skills_are_refused(owner, body):
    answer = owner["client"].post(f"{owner['base']}/skills", json=body)
    assert answer.status_code == 400
    assert owner["client"].get(f"{owner['base']}/skills").json() == {"skills": []}


def test_unsupported_skill_edits_are_refused(owner):
    client, base = owner["client"], owner["base"]
    client.post(f"{base}/skills", json={"name": "report", "description": "", "body": "正文"})
    for patch in ({"locked": True}, {"autoUpdate": True}, {"agentsImprove": True}, {"defaultForNewAgents": True}):
        assert client.patch(f"{base}/skills/report", json=patch).status_code == 400


def test_agents_improving_skills_by_themselves_stays_off(owner):
    client, base = owner["client"], owner["base"]
    assert client.put(f"{base}/skill-settings", json={"agentsImprove": False}).json() == {"agentsImprove": False}
    assert client.put(f"{base}/skill-settings", json={"agentsImprove": True}).status_code == 400


def test_skill_writes_need_an_authenticated_owner_connection(home, monkeypatch):  # noqa: F811
    monkeypatch.setenv("VORTOONE_WORKSPACE_SCOPE", "general")
    client, base = home["client"], home["base"]
    assert client.post(f"{base}/skills", json={"name": "a", "description": "", "body": "x"}).status_code == 403
    assert client.delete(f"{base}/skills/a").status_code == 403
    assert client.put(f"{base}/skill-settings", json={"agentsImprove": False}).status_code == 403


def test_skills_are_only_managed_in_the_personal_assistant_scope(home, monkeypatch):  # noqa: F811
    _signed_in(home, monkeypatch)
    monkeypatch.delenv("VORTOONE_WORKSPACE_SCOPE", raising=False)
    answer = home["client"].post(f"{home['base']}/skills", json={"name": "a", "description": "", "body": "x"})
    assert answer.status_code in (400, 409)


# ── 制品 ──────────────────────────────────────────────────────────────────────

def test_the_owner_deletes_an_artifact(owner):
    from src.agents.tools.artifacts import ArtifactStore
    store = ArtifactStore(str(owner["root"]))
    made = store.publish("周报", "<p>hi</p>")
    client, base = owner["client"], owner["base"]
    listed = client.get(f"{base}/artifacts").json()["artifacts"]
    assert listed[0]["canDelete"] is True
    assert client.delete(f"{base}/artifacts/{made['id']}").status_code == 204
    assert client.get(f"{base}/artifacts").json()["artifacts"] == []
    assert client.delete(f"{base}/artifacts/{made['id']}").status_code == 404


def test_without_an_owner_connection_artifacts_cannot_be_deleted(home):  # noqa: F811
    from src.agents.tools.artifacts import ArtifactStore
    made = ArtifactStore(str(home["root"])).publish("周报", "<p>hi</p>")
    client, base = home["client"], home["base"]
    assert client.get(f"{base}/artifacts").json()["artifacts"][0]["canDelete"] is False
    assert client.delete(f"{base}/artifacts/{made['id']}").status_code == 403


# ── 批量处理审批 ─────────────────────────────────────────────────────────────

async def test_decide_many_decides_each_listed_approval_once(app_home):  # noqa: F811
    http, base, channel, socket, agent = (app_home[k] for k in ("http", "base", "channel", "socket", "agent"))
    await http.post(f"{base}/channels/{channel}/messages", json={"body": "删文件"})
    await _until(lambda: socket.events("approval.created"))
    pending = socket.events("approval.created")[0]["approval"]["id"]
    answer = await http.post(f"{base}/approvals/decide", json={"ids": [pending, "gone"], "decision": "refuse"})
    assert answer.status_code == 200
    result = answer.json()
    assert [a["id"] for a in result["approvals"]] == [pending] and result["skipped"] == ["gone"]
    await _until(lambda: socket.events("run.finished"))
    assert agent.confirmed is False


async def test_decide_many_with_only_routine_touches_nothing(app_home):  # noqa: F811
    http, base, channel, socket, agent = (app_home[k] for k in ("http", "base", "channel", "socket", "agent"))
    await http.post(f"{base}/channels/{channel}/messages", json={"body": "删文件"})
    await _until(lambda: socket.events("approval.created"))
    pending = socket.events("approval.created")[0]["approval"]["id"]
    answer = (await http.post(f"{base}/approvals/decide",
                              json={"ids": [pending], "decision": "approve", "only": "routine"})).json()
    assert answer == {"approvals": [], "skipped": [pending]} and agent.confirmed is None
    assert (await http.post(f"{base}/approvals/decide", json={"ids": [], "decision": "approve"})).status_code == 422
    assert (await http.post(f"{base}/approvals/decide",
                            json={"ids": [pending], "decision": "always"})).status_code == 422
    assert agent.confirmed is None
