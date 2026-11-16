"""新版界面读取的记忆、例行工作、技能与制品：都来自现有登记，读不出来时如实报错。"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("VORTOONE_API_TOKEN", "VORTOCODE_API_TOKEN", "AUTODEV_API_TOKEN", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    from src.web.server import app
    client = TestClient(app)
    org = client.get("/api/app/me").json()["organizations"][0]["id"]
    base = f"/api/app/organizations/{org}"
    main = client.get(f"{base}/agents").json()["agents"][0]["id"]
    return {"root": tmp_path, "client": client, "base": base, "main": main}


def _member(root):
    from src.workflow.staff.registry import StaffRegistry
    registry = StaffRegistry(str(root))
    role = registry.register_role({"id": "plain-reader", "title": "核对", "brief": "核对任务中的文字",
        "grants": {"operations": ["read"], "tools": [], "sees": [], "web": False, "project": False},
        "engine": ["vortocode"], "max_steps": 4, "timeout_seconds": 60})
    return registry.register_member({"id": "staff-reader", "name": "核对专员", "role": role["id"],
                                     "engine": "vortocode"})


def test_everything_is_empty_in_a_new_home(home):
    client, base, main = home["client"], home["base"], home["main"]
    assert client.get(f"{base}/agents/{main}/memories").json() == {"memories": []}
    assert client.get(f"{base}/memories").json() == {"memories": []}
    assert client.get(f"{base}/routines").json() == {"routines": []}
    assert client.get(f"{base}/skills").json() == {"skills": []}
    assert client.get(f"{base}/agents/{main}/skills").json() == {"skills": []}
    assert client.get(f"{base}/artifacts").json() == {"artifacts": [], "nextCursor": None, "counts": {}}
    assert client.get(f"{base}/proposals").json() == {"proposals": []}
    assert client.get(f"{base}/artifacts/nope").status_code == 404
    assert client.get(f"{base}/agents/unknown/memories").status_code == 404


def test_memories_are_the_main_assistants_long_term_ones(home):
    from src.memory import assistant_profile
    from src.memory.conversation_db import SessionStore
    path = assistant_profile._db_path(str(home["root"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    SessionStore(str(path)).add_memory("__longterm__", "fact", "主人周三不开会")
    client, base, main = home["client"], home["base"], home["main"]
    listed = client.get(f"{base}/agents/{main}/memories").json()["memories"]
    assert [(m["text"], m["agentId"], m["kind"]) for m in listed] == [("主人周三不开会", main, "fact")]

    member = _member(home["root"])
    agent = next(a for a in client.get(f"{base}/agents").json()["agents"] if a["handle"] == member["id"])
    assert client.get(f"{base}/agents/{agent['id']}/memories").json() == {"memories": []}


def test_routines_come_from_the_staff_registry(home):
    from src.workflow.staff.routines import Routines
    member = _member(home["root"])
    routines = Routines(str(home["root"]))
    routines.register({"id": "routine-report", "name": "例行核对", "member": member["id"],
                       "prompt": "核对公开数字", "schedule": "at 09:00", "timezone": "UTC", "delivery": "notify"})
    client, base = home["client"], home["base"]
    agent = next(a for a in client.get(f"{base}/agents").json()["agents"] if a["handle"] == member["id"])
    listed = client.get(f"{base}/routines").json()["routines"]
    assert len(listed) == 1
    routine = listed[0]
    assert (routine["id"], routine["name"], routine["agentId"], routine["prompt"]) == (
        "routine-report", "例行核对", agent["id"], "核对公开数字")
    assert routine["active"] is True and routine["state"] == "active"
    assert routine["trigger"] == {"type": "schedule", "phrase": "at 09:00", "rule": None, "cron": "0 9 * * *",
                                  "timezone": "UTC", "description": "at 09:00"}
    assert routine["nextRunAt"].endswith("T09:00:00.000Z")
    assert routine["canManage"] is False and routine["recentRuns"] == []      # 这里只读，不编造运行记录


def test_a_damaged_routine_registry_is_reported_not_shown_as_empty(home):
    from src.workflow.staff.registry import StaffRegistry
    registry = StaffRegistry(str(home["root"]))
    registry.directory.mkdir(parents=True, exist_ok=True)
    (registry.directory / "routines.json").write_text('{"version": 1, "revision": 0}', encoding="utf-8")
    response = home["client"].get(f"{home['base']}/routines")
    assert response.status_code == 409 and response.json()["error"]["code"] == "unavailable"


def test_schedule_expressions_are_shown_as_written():
    from src.web.app_views import _trigger
    once = _trigger("once 2026-12-01T08:00:00+08:00", "Asia/Shanghai")
    assert once == {"type": "once", "at": "2026-12-01T00:00:00.000Z", "timezone": "Asia/Shanghai",
                    "description": "once 2026-12-01T08:00:00+08:00"}
    assert _trigger("every 2h", "UTC")["cron"] == "" and _trigger("every 2h", "UTC")["description"] == "every 2h"
    assert _trigger("30 8 * * 1", "UTC")["cron"] == "30 8 * * 1"


def test_personal_skills_belong_to_the_main_assistant(home, monkeypatch):
    from src.workflow.workbench.personal_skills import PersonalSkills
    monkeypatch.setenv("VORTOONE_WORKSPACE_SCOPE", "general")
    store = PersonalSkills(str(home["root"]))
    saved = store.save("weekly-report", {"name": "周报", "description": "写周报时用",
                                         "instructions": "按项目列出进展。"})
    client, base, main = home["client"], home["base"], home["main"]
    listed = client.get(f"{base}/skills").json()["skills"]
    assert [(s["id"], s["name"], s["description"], s["source"]) for s in listed] == [
        ("weekly-report", "周报", "写周报时用", "manual")]
    mine = client.get(f"{base}/agents/{main}/skills").json()["skills"]
    assert mine[0]["enabled"] is False                         # 主人还没核对启用
    store.set_enabled("weekly-report", enabled=True, revision=saved["revision"])
    assert client.get(f"{base}/agents/{main}/skills").json()["skills"][0]["enabled"] is True

    member = _member(home["root"])
    agent = next(a for a in client.get(f"{base}/agents").json()["agents"] if a["handle"] == member["id"])
    assert client.get(f"{base}/agents/{agent['id']}/skills").json() == {"skills": []}

    monkeypatch.setenv("VORTOONE_WORKSPACE_SCOPE", "project")     # 个人技能只在私人助理范围可见
    assert client.get(f"{base}/skills").json() == {"skills": []}


def test_artifacts_are_listed_and_served_as_untrusted_pages(home):
    from src.agents.tools.artifacts import ArtifactStore
    store = ArtifactStore(str(home["root"]))
    first = store.publish("周报看板", "<h1>v1</h1>")
    store.publish("周报看板", "<h1>v2</h1>", first["id"])
    client, base = home["client"], home["base"]
    page = client.get(f"{base}/artifacts").json()
    assert page["counts"] == {"html": 1} and page["nextCursor"] is None
    listed = page["artifacts"][0]
    assert (listed["id"], listed["title"], listed["version"], listed["untrusted"]) == (
        first["id"], "周报看板", 2, True)
    assert listed["canEdit"] is False and listed["canDelete"] is False

    current = client.get(f"{base}/artifacts/{first['id']}").json()
    assert current["content"] == {"kind": "html", "html": "<h1>v2</h1>"} and current["versioned"] is True
    older = client.get(f"{base}/artifacts/{first['id']}", params={"version": 1}).json()
    assert older["content"]["html"] == "<h1>v1</h1>" and older["shownVersion"] == 1

    assert client.get(f"{base}/artifacts", params={"q": "不存在"}).json()["artifacts"] == []
    assert client.get(f"{base}/artifacts", params={"kinds": "table,diff"}).json()["artifacts"] == []
    assert client.get(f"{base}/artifacts/..%2F..%2Fetc").status_code == 404


def test_the_app_api_is_served_in_the_private_assistant_scope(home, monkeypatch):
    """常驻助理与 Desktop 都以私人助理范围运行；这套接口不碰项目文件、Shell 或 Git，在那里必须可用。"""
    from src.gateway.workspace_scope import http_workspace_requirement
    monkeypatch.setenv("VORTOONE_WORKSPACE_SCOPE", "general")
    client, base, main = home["client"], home["base"], home["main"]
    assert client.get("/api/instance").status_code == 200
    assert client.get("/api/app/me").status_code == 200
    channel = client.get(f"{base}/dms").json()["dms"][0]["channelId"]
    for path in ("agents", "routines", "skills", "artifacts", f"agents/{main}/memories",
                 f"channels/{channel}/messages"):
        assert client.get(f"{base}/{path}").status_code == 200, path
    assert http_workspace_requirement("/api/git/status", "general") == "project"     # 项目入口照旧被挡
    assert http_workspace_requirement("/api/application", "general") == "scratch"    # 前缀不误放行相近路径
