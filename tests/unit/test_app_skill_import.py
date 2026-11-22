"""Desktop local ZIP uploads remain private and need a separate version review."""

import base64
import io
import stat
import zipfile

import pytest
from fastapi.testclient import TestClient

from src.agents import taint
from src.gateway.agent_session import build_session
from src.web.app_views import main_agent_id
from src.workflow.workbench.personal_skills import PersonalSkills
from src.workflow.workbench.skill_import import MAX_IMPORT_BODY
from tests.unit.test_member_login import ORIGIN, TOKEN, _join, home  # noqa: F401

MARKER = "UPLOAD-FIXTURE-MARKDOWN"
DOCUMENT = f"---\nname: Weekly Report\ndescription: Write a report\n---\n\n{MARKER}\n"


@pytest.fixture(autouse=True)
def general_workspace(monkeypatch):
    monkeypatch.setenv("VORTOONE_WORKSPACE_SCOPE", "general")


def _zip(*files) -> str:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, content in files:
            archive.writestr(path, content)
    return base64.b64encode(data.getvalue()).decode("ascii")


def _upload(home, document=DOCUMENT, **options):
    return home["owner"].post(home["base"] + "/skills/import", json={
        "zipBase64": _zip(("weekly/", ""), ("weekly/SKILL.md", document)), **options,
    })


@pytest.mark.asyncio
async def test_upload_review_and_desktop_toggle_keep_result_as_untrusted_tool_data(home):
    owner, base = home["owner"], home["base"]
    result = _upload(home)
    assert result.status_code == 200, result.text
    skill = result.json()
    skill_id = skill["id"]
    assert skill["ownerUserId"] == owner.get("/api/app/me").json()["user"]["id"]
    assert skill["source"] == "upload" and skill["sourceUrl"] is None and skill["body"] == MARKER
    assert skill["files"] == [] and skill["defaultForNewAgents"] is False
    listed = owner.get(base + "/skills").json()["skills"]
    assert [row["source"] for row in listed] == ["upload"] and MARKER not in str(listed)

    store = PersonalSkills(home["root"])
    pending = store.get(skill_id)
    assert pending["status"] == "needs_review" and not pending["enabled"]
    agent = build_session(home["root"], kind="web", workspace_scope="general")
    assert MARKER not in agent._system("plan")
    assert not (await agent.tools["use_personal_skill"].handler({"id": skill_id}))["ok"]

    activation = f"{base}/agents/{main_agent_id(home['root'])}/skills"
    toggles = {"skills": [{"skillId": skill_id, "enabled": True}]}
    assert owner.put(activation, json=toggles).status_code == 409
    reviewed = owner.get(f"{base}/skills/{skill_id}").json()
    assert reviewed["body"] == MARKER and reviewed["source"] == "upload"
    enabled = owner.put(activation, json=toggles)
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["skills"][0]["enabled"] is True
    assert store.load_instructions(skill_id)["revision"] == pending["revision"]
    assert MARKER not in agent._system("plan")

    tool = agent.tools["use_personal_skill"]
    assert tool.untrusted_source and tool.external_content
    taint.reset_taint()
    try:
        data = await agent._run_tool("use_personal_skill", {"id": skill_id}, "plan", lambda _msg: None)
        assert MARKER in data and taint.is_tainted()
    finally:
        taint.reset_taint()

    changed = owner.patch(f"{base}/skills/{skill_id}", json={"body": "New inspection required"})
    assert changed.status_code == 200 and changed.json()["source"] == "upload"
    assert not store.get(skill_id)["enabled"]
    assert owner.put(activation, json=toggles).status_code == 409
    assert owner.put(activation, json={"skills": [{"skillId": skill_id, "enabled": False}]}).status_code == 200


def test_owner_cookie_needs_same_origin_and_members_cannot_upload_or_activate(home, monkeypatch):
    base = home["base"]
    body = {"zipBase64": _zip(("SKILL.md", DOCUMENT)), "personal": True}
    member = _join(home)
    assert member.post(base + "/skills/import", json=body).status_code == 403
    activation = f"{base}/agents/{main_agent_id(home['root'])}/skills"
    assert member.put(activation, json={"skills": []}).status_code == 403
    cookie = TestClient(home["app"])
    assert cookie.post("/api/auth/sign-in/email", json={"email": "", "password": TOKEN}).status_code == 200
    assert cookie.post(base + "/skills/import", json=body, headers={"Origin": "http://foreign.test"}).status_code == 403
    assert cookie.post(base + "/skills/import", json={**body, "personal": False}, headers=ORIGIN).status_code == 409
    assert cookie.post(base + "/skills/import", json={"zipBase64": body["zipBase64"]}, headers=ORIGIN).status_code == 409
    accepted = cookie.post(base + "/skills/import", json=body, headers=ORIGIN)
    assert accepted.status_code == 200, accepted.text
    assert member.get(base + "/skills").json() == {"skills": []}
    assert member.get(base + "/skills/" + accepted.json()["id"]).status_code == 403
    assert cookie.put(activation, json={"skills": []}, headers={"Origin": "http://foreign.test"}).status_code == 403
    monkeypatch.delenv("VORTOONE_API_TOKEN")
    local = TestClient(home["app"], headers=ORIGIN)
    assert local.post(base + "/skills/import", json=body).status_code == 403
    assert local.put(activation, json={"skills": []}).status_code == 403


def test_import_refuses_external_sources_and_does_not_replace_an_existing_skill(home):
    owner, base = home["owner"], home["base"]
    existing = owner.post(base + "/skills", json={"name": "Weekly Report", "body": "Owner's draft"})
    assert existing.status_code == 200
    activation = f"{base}/agents/{main_agent_id(home['root'])}/skills"
    assert owner.put(activation, json={"skills": ["weekly-report"]}).status_code == 200
    for body in (
        {"url": "https://example.invalid/skills", "personal": True},
        {"url": "https://example.invalid/skills", "paths": ["weekly"], "personal": True},
        {"zipBase64": _zip(("SKILL.md", DOCUMENT)), "forAgentId": "main", "personal": True},
    ):
        rejected = owner.post(base + "/skills/import", json=body)
        assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "skill.import_unsupported"
    uploaded = _upload(home, personal=True).json()
    assert uploaded["id"] == "weekly-report-2"
    toggles = {"skills": [{"skillId": "weekly-report", "enabled": False},
                          {"skillId": uploaded["id"], "enabled": True}]}
    assert owner.put(activation, json=toggles).status_code == 409
    store = PersonalSkills(home["root"])
    assert store.load_instructions("weekly-report")["instructions"] == "Owner's draft"
    assert not store.get(uploaded["id"])["enabled"]


def _symlink():
    entry = zipfile.ZipInfo("SKILL.md")
    entry.create_system = 3
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    return entry


@pytest.mark.parametrize("zip_base64,status", [
    ("not base64!", 400),
    (_zip(("../SKILL.md", DOCUMENT)), 400),
    (_zip((_symlink(), "another-file")), 400),
    (_zip(("SKILL.md", DOCUMENT), ("reference.md", "extra document")), 400),
    (_zip(("SKILL.md", DOCUMENT.replace("description:", "disable-model-invocation: true\ndescription:")),), 400),
    (_zip(("SKILL.md", DOCUMENT.replace("description:", "hooks: {}\ndescription:")),), 400),
    (_zip(("SKILL.md", DOCUMENT.replace("name:", "name: Another\nname:")),), 400),
    (_zip(("SKILL.md", b"\xff")), 400),
    (_zip(("SKILL.md", DOCUMENT + "x" * 100_000)), 413),
    ("A" * (MAX_IMPORT_BODY + 1), 413),
])
def test_bad_upload_leaves_the_managed_store_untouched(home, zip_base64, status):
    answer = home["owner"].post(home["base"] + "/skills/import", json={"zipBase64": zip_base64})
    assert answer.status_code == status, answer.text
    assert not PersonalSkills(home["root"]).base.exists()
