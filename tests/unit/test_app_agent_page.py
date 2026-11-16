"""新版界面的角色资料页与通知：来自现有登记，只读。"""

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
    return {"root": tmp_path, "client": client, "base": base,
            "main": client.get(f"{base}/agents").json()["agents"][0]["id"]}


def _member(home, tools):
    from src.workflow.staff.registry import StaffRegistry
    registry = StaffRegistry(str(home["root"]))
    role = registry.register_role({"id": "researcher", "title": "研究", "brief": "查公开资料",
        "grants": {"operations": ["read"], "tools": tools, "sees": [], "web": bool(tools), "project": False},
        "engine": ["vortocode"], "max_steps": 4, "timeout_seconds": 60})
    member = registry.register_member({"id": "staff-research", "name": "研究专员", "role": role["id"],
                                       "engine": "vortocode", "model": "m-small"})
    agents = home["client"].get(f"{home['base']}/agents").json()["agents"]
    return next(a for a in agents if a["handle"] == member["id"])["id"]


def test_the_agent_page_reads_all_answer(home):
    client, base, main = home["client"], home["base"], home["main"]
    for path in ("brain", "access", "computer", "repositories", "holds", "skills", "memories", "scopes"):
        assert client.get(f"{base}/agents/{main}/{path}").status_code == 200, path
        assert client.get(f"{base}/agents/unknown/{path}").status_code == 404, path
    for path in ("connectors", "connectors/custom", "credentials", "mail/accounts", "calendar", "usage", "runs"):
        assert client.get(f"{base}/{path}").status_code == 200, path
    assert client.get("/api/app/connectors/catalog").json() == {"connectors": [], "builtinScopes": []}


def test_brain_is_read_only_and_follows_the_model_credential(home, monkeypatch):
    client, base = home["client"], home["base"]
    agent = _member(home, [])
    brain = client.get(f"{base}/agents/{agent}/brain").json()
    assert (brain["brain"], brain["model"], brain["canEdit"]) == ("model", "m-small", False)
    assert client.get(f"{base}/agents/{agent}").json()["instructions"] == "查公开资料"     # 岗位职责
    assert brain["options"] == [{"kind": "model", "available": False, "models": [], "defaultModel": None}]
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    assert client.get(f"{base}/agents/{agent}/brain").json()["options"][0]["available"] is True
    assert client.get(f"{base}/agents/{home['main']}/brain").json()["model"] is None


def test_access_lists_what_the_members_role_grants(home):
    client, base = home["client"], home["base"]
    agent = _member(home, ["web_search", "web_fetch"])
    access = client.get(f"{base}/agents/{agent}/access").json()
    assert access["all"] is False and access["connectors"] == []
    scope, = access["builtins"]
    assert scope["state"] == "granted" and "研究" in scope["label"]
    assert [(t["name"], t["readOnly"], t["sensitive"]) for t in scope["tools"]] == [
        ("web_search", True, False), ("web_fetch", True, False)]
    # 主助理的工具不在登记表里：不列，也不说成「全部允许」。
    assert client.get(f"{base}/agents/{home['main']}/access").json() == {
        "all": False, "builtins": [], "connectors": []}


def test_notices_show_as_read_history_without_a_stuck_badge(home):
    from src.scheduling.notices import record_notice
    client, base = home["client"], home["base"]
    assert client.get(f"{base}/notifications").json()["items"] == []
    record_notice(str(home["root"]), "早报已生成", source="cron")
    record_notice(str(home["root"]), "巡视：有一封要回的邮件", source="heartbeat")
    page = client.get(f"{base}/notifications").json()
    assert [n["latest"]["text"] for n in page["items"]] == ["巡视：有一封要回的邮件", "早报已生成"]
    assert all(n["readAt"] and not n["waiting"] for n in page["items"])
    assert page["counts"] == {"all": 2, "mentions": 0, "waiting": 0, "unread": 0, "reactions": 0}
    assert len({n["id"] for n in page["items"]}) == 2
    assert client.get(f"{base}/notifications").json()["items"][0]["id"] == page["items"][0]["id"]
    for name in ("waiting", "unread", "mentions"):
        assert client.get(f"{base}/notifications", params={"filter": name}).json()["items"] == []
    assert client.get(f"{base}/notifications/count").json() == {"unread": 0, "waiting": 0}
