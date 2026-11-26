"""Personal Codex creation and chatting through the normal Desktop API."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.agents import external_workers
from src.agents.staff import execute_staff
from tests.unit.test_app_staff import _finish, home  # noqa: F401 - reuse the authenticated Desktop fixture


@pytest.fixture
def local_client(home):
    return TestClient(home["client"].app, base_url="http://127.0.0.1", client=("127.0.0.1", 49152),
                      headers={"Authorization": home["client"].headers["Authorization"]})


async def test_personal_codex_answers_in_its_private_chat_without_a_workspace_key(home, local_client, monkeypatch):
    client = local_client
    base, pool, service = (home[key] for key in ("base", "pool", "service"))
    monkeypatch.setattr("src.web.app_views.model_connected", lambda: False)
    monkeypatch.setattr(external_workers, "personal_codex_status", lambda: "ready")
    received = []

    async def fake_codex(prompt, cwd, *, write, **kwargs):
        assert not write and list(Path(cwd).iterdir()) == []
        assert kwargs["execution_ref"].backend_id == "codex"
        received.append(prompt)
        return external_workers.WorkerRun("codex", final_text="收到来自 Codex 的回复")

    monkeypatch.setattr(external_workers, "run_personal_codex", fake_codex)
    service.execute = lambda task, work, role, member, check: execute_staff(
        service.root, task, work, role, member, check)

    status = client.get(f"{base}/personal-codex-agent")
    assert status.status_code == 200 and status.json()["status"] == "ready"
    assert status.headers["cache-control"] == "no-store"
    created = client.post(f"{base}/personal-codex-agent", json={"name": "我的 Codex", "handle": "codex-mine"})
    assert created.status_code == 200, created.text
    agent = created.json()
    assert agent["personalCodex"] is True
    assert agent["audienceMode"] == "owner" and agent["brain"]["kind"] == "codex"

    ai_status = client.get(f"{base}/ai-status").json()
    assert ai_status["any"] is True and ai_status["apiKey"] is False
    assert {"agentId": agent["id"], "state": "ready"} in ai_status["agents"]
    channel = client.post(f"{base}/dms", json={"agentId": agent["id"]}).json()["channelId"]
    sent = client.post(f"{base}/channels/{channel}/messages", json={"body": "回复一个确认词"})
    assert sent.status_code == 200, sent.text
    task_id, = pool.jobs
    await _finish(pool, task_id)
    assert len(received) == 1 and "回复一个确认词" in received[0]
    history = client.get(f"{base}/channels/{channel}/messages").json()["messages"]
    assert [message["body"] for message in history] == ["回复一个确认词", "收到来自 Codex 的回复"]


def test_personal_codex_creation_does_not_fall_back_or_accept_a_path(home, local_client, monkeypatch):
    client, base = local_client, home["base"]
    monkeypatch.setattr(external_workers, "personal_codex_status", lambda: "signed_out")
    before = client.get(f"{base}/agents").json()
    refused = client.post(f"{base}/personal-codex-agent", json={"name": "Codex", "handle": "signed-out"})
    assert refused.status_code == 422 and "ChatGPT" in refused.json()["error"]["message"]
    monkeypatch.setattr(external_workers, "personal_codex_status", lambda: "ready")
    refused = client.post(f"{base}/personal-codex-agent",
                          json={"name": "Codex", "handle": "arbitrary", "project": "/unregistered"})
    assert refused.status_code == 422
    assert client.get(f"{base}/agents").json() == before


def test_remote_owner_cannot_connect_the_hosts_codex_login(home, local_client, monkeypatch):
    def unexpected_probe():
        raise AssertionError("remote requests cannot inspect the Codex login")

    monkeypatch.setattr(external_workers, "personal_codex_status", unexpected_probe)
    path = f"{home['base']}/personal-codex-agent"
    assert home["client"].get(path).status_code == 403
    relayed = {"X-Forwarded-For": "192.0.2.5"}
    assert local_client.get(path, headers=relayed).status_code == 403
    assert local_client.post(path, headers=relayed, json={"name": "Codex", "handle": "remote"}).status_code == 403


@pytest.mark.usefixtures("isolated_personal_codex_git")
async def test_personal_codex_touches_only_the_approved_project_copy(home, local_client, monkeypatch, tmp_path):
    import json
    import subprocess

    def git(path, *args):
        return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()

    project = tmp_path / "approved-project"
    project.mkdir()
    git(project, "init", "-q")
    git(project, "config", "user.name", "Fixture")
    git(project, "config", "user.email", "fixture@example.invalid")
    (project / "app.txt").write_text("original\n")
    git(project, "add", "app.txt")
    git(project, "commit", "-qm", "Fixture")
    registry = tmp_path / "projects.json"
    registry.write_text(json.dumps({"projects": [{"name": "Example", "repo_root": str(project), "port": 8910}]}))
    monkeypatch.setenv("VORTOONE_RESIDENT_PROJECTS", str(registry))
    monkeypatch.setattr(external_workers, "personal_codex_status", lambda: "ready")

    async def fake_codex(prompt, cwd, *, write, **kwargs):
        copy = Path(cwd)
        assert write and copy != project and "更新项目样例" in prompt
        assert (copy / "app.txt").read_text() == "original\n"
        (copy / "app.txt").write_text("updated\n")
        return external_workers.WorkerRun("codex", final_text="已修改样例")

    def unexpected_verify(*_args):
        raise AssertionError("host verification would execute the newly edited code")

    monkeypatch.setattr(external_workers, "run_personal_codex", fake_codex)
    monkeypatch.setattr("src.agents.team_delivery._verify_command", unexpected_verify)
    base, pool, service = (home[key] for key in ("base", "pool", "service"))
    service.execute = lambda task, work, role, member, check: execute_staff(
        service.root, task, work, role, member, check)

    assert local_client.get(f"{base}/personal-codex-agent").json()["projects"] == ["Example"]
    created = local_client.post(f"{base}/personal-codex-agent",
                                json={"name": "Project Codex", "handle": "codex-project", "project": "Example"})
    assert created.status_code == 200, created.text
    channel = local_client.post(f"{base}/dms", json={"agentId": created.json()["id"]}).json()["channelId"]
    sent = local_client.post(f"{base}/channels/{channel}/messages", json={"body": "更新项目样例"})
    assert sent.status_code == 200, sent.text
    task_id, = pool.jobs
    await _finish(pool, task_id)
    task = service.collaboration("sid-owner").get(task_id)
    delivery = task.collaboration["delivery"]
    assert delivery["verified"] is None and delivery["files"] == ["app.txt"]
    assert git(project, "show", f"{delivery['branch']}:app.txt") == "updated"
    assert (project / "app.txt").read_text() == "original\n"
