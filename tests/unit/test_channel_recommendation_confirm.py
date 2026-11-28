"""Confirmed channel drafts use the real routes, board, and staff task ledger."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from src.web import app_channel_agents
from src.workflow.staff.service import StaffService
from src.workflow.tasks.tasks import TaskLedger
from tests.unit.test_channel_agents_answer import _until
from tests.unit.test_channel_recommendations import _choice, _model
from tests.unit.test_channel_recommendations import preview as seeded_preview  # noqa: F401
from tests.unit.test_member_agents import _finish, _join, home  # noqa: F401 - dependency of preview
from tests.unit.test_team_channels import _create


_AGENT_RESULT = "合成公告草稿：周五更新支付页，原有入口继续可用。"
_CI_RESULT = "1. 核对 CI 运行环境和依赖安装。\n2. 检查锁文件中的包版本。"


@pytest.fixture
def preview(seeded_preview):
    initiator = _join(seeded_preview, email="initiator@example.com", name="小李")
    reply = seeded_preview["owner"].post(
        f"{seeded_preview['team_channel']}/members", json={"userId": initiator.member_id}
    )
    assert reply.status_code == 204, reply.text
    return {**seeded_preview, "initiator": initiator}


def _preview_token(preview, monkeypatch, kind):
    if kind == "person":
        text = "小王，请核对这页会议纪要，把决策和待办分开。"
        primary = _choice(preview, "person", "核对决策和待办")
    elif kind == "agent":
        text = "这段 CI 错误是 ModuleNotFoundError，帮忙从日志列两个定位步骤。"
        primary = _choice(preview, "engineering", "列出两个定位步骤")
    else:
        text = "请小王负责审核客户公告，让文档助手先整理一版供人工确认。"
        primary = _choice(preview, "person", "审核客户公告")
    answer = {"kind": kind, "reason": "依据草稿指定接手人。", "primary": primary}
    if kind == "collaboration":
        answer["assistant"] = _choice(preview, "documents", "整理公告初稿")
    _model(monkeypatch, answer)
    response = preview["initiator"].post(preview["url"], json={"text": text})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["kind"] == kind
    token = result["confirmationToken"]
    assert isinstance(token, str) and token
    assert not preview["pool"].jobs

    async def unexpected_model(_request):
        pytest.fail("Confirmation must use the exact preview without calling the model again")

    monkeypatch.setattr(
        "src.web.app_channel_recommendations._infer_recommendation", unexpected_model
    )
    return token


def _confirm(preview, token, *, caller=None, channel=None):
    client = caller if caller is not None else preview["initiator"]
    return client.post(
        f"{channel or preview['team_channel']}/recommendation/confirm",
        json={"confirmationToken": token},
    )


def _issues(preview):
    response = preview["member"].get(f"{preview['base']}/issues")
    assert response.status_code == 200, response.text
    return response.json()["issues"]


def _history(preview):
    response = preview["member"].get(f"{preview['team_channel']}/messages")
    assert response.status_code == 200, response.text
    return response.json()["messages"]


def _enable_fake_task_execution(preview, monkeypatch, text):
    service = preview["service"]
    seen = []

    async def execute(task, work, role, member, check):
        check()
        seen.append((task.id, member["id"]))
        return text

    # Preview fixture rejects all assignments. Enable the real service only after obtaining the token.
    monkeypatch.setattr(service, "assign", StaffService.assign.__get__(service))
    monkeypatch.setattr(service, "execute", execute)
    monkeypatch.setattr(
        service, "on_update", lambda task: app_channel_agents.on_update(preview["root"], task)
    )
    return seen


@pytest.mark.parametrize("kind", ["none", "clarify"])
def test_nondispatchable_previews_have_no_confirmation_token(preview, monkeypatch, kind):
    _model(monkeypatch, {"kind": kind, "reason": "条件不足以派发。"})
    result = preview["initiator"].post(preview["url"], json={"text": "还有一点没说清楚。"})
    assert result.status_code == 200, result.text
    assert result.json()["kind"] == kind
    assert "confirmationToken" not in result.json()
    assert _issues(preview) == []
    assert not preview["pool"].jobs


def test_confirming_a_person_creates_one_assigned_issue(preview, monkeypatch):
    token = _preview_token(preview, monkeypatch, "person")
    assert _issues(preview) == []
    before = _history(preview)

    first = _confirm(preview, token)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "assigned"
    again = _confirm(preview, token)
    assert again.status_code == 200, again.text
    assert again.json() == first.json()

    [issue] = _issues(preview)
    assert issue["id"] == first.json()["issueId"]
    assert issue["number"] == first.json()["issueNumber"]
    assert issue["assignee"] == {"type": "person", "id": preview["member"].member_id}
    assert issue["createdBy"] == {"type": "person", "id": preview["initiator"].member_id}
    assert "核对" in issue["title"] + issue["description"]
    assert _history(preview) == before
    assert not preview["pool"].jobs


def test_concurrent_confirmations_create_the_same_issue(preview, monkeypatch):
    token = _preview_token(preview, monkeypatch, "person")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(lambda _: _confirm(preview, token), range(2))
    assert first.status_code == second.status_code == 200, (first.text, second.text)
    assert first.json() == second.json()
    assert len(_issues(preview)) == 1


async def test_confirming_an_agent_queues_one_private_task(preview, monkeypatch):
    token = _preview_token(preview, monkeypatch, "agent")
    seen = _enable_fake_task_execution(preview, monkeypatch, _CI_RESULT)
    before = _history(preview)

    first = _confirm(preview, token)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "agent_started"
    task_id = first.json()["taskId"]
    replay = _confirm(preview, token)
    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()
    assert list(preview["pool"].jobs) == [task_id]
    assert _issues(preview) == []

    await _finish(preview["pool"], task_id)
    app_channel_agents.sweep(preview["root"])
    task = TaskLedger(preview["root"]).load(task_id)
    assert task.status == "done"
    assert task.result == _CI_RESULT
    assert task.collaboration["dispatch"]["channel"]["privateDraft"] is True
    assert task.collaboration["tainted"] is True
    assert seen == [(task_id, "eng-preview")]
    completed = _confirm(preview, token)
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "agent_completed"
    assert completed.json()["taskId"] == task_id
    assert completed.json()["result"] == task.result
    other_actor = _confirm(preview, token, caller=preview["owner"])
    assert other_actor.status_code == 404, other_actor.text
    assert task.result not in other_actor.text
    assert _issues(preview) == []
    assert _history(preview) == before


async def test_collaboration_creates_the_reviewer_issue_only_after_the_draft_finishes(
    preview, monkeypatch
):
    observer = _join(preview, email="observer@example.com", name="小周")
    invited = preview["owner"].post(
        f"{preview['team_channel']}/members", json={"userId": observer.member_id}
    )
    assert invited.status_code == 204, invited.text
    token = _preview_token(preview, monkeypatch, "collaboration")
    seen = _enable_fake_task_execution(preview, monkeypatch, _AGENT_RESULT)
    before = _history(preview)
    assert _issues(preview) == []

    response = _confirm(preview, token)
    assert response.status_code == 200, response.text
    pending = response.json()
    assert pending["status"] == "awaiting_draft"
    assert pending.get("issueId") is None
    task_id = pending["taskId"]
    assert list(preview["pool"].jobs) == [task_id]
    replay = _confirm(preview, token)
    assert replay.status_code == 200, replay.text
    assert replay.json() == pending
    assert _issues(preview) == []
    assert seen == []

    await _finish(preview["pool"], task_id)
    task = TaskLedger(preview["root"]).load(task_id)
    assert task.status == "done" and task.result == _AGENT_RESULT
    assert seen == [(task_id, "docs-preview")]
    # Observe the terminal hook via a read, before a confirmation replay or startup recovery can deliver it.
    [issue] = await _until(lambda: _issues(preview))

    response = _confirm(preview, token)
    assert response.status_code == 200, response.text
    ready = response.json()
    assert ready["status"] == "ready_for_review"
    assert ready["taskId"] == task_id
    assert issue["id"] == ready["issueId"]
    assert issue["number"] == ready["issueNumber"]
    assert issue["status"] == "in_review"
    assert issue["assignee"] == {"type": "person", "id": preview["member"].member_id}
    assert issue["createdBy"] == {"type": "person", "id": preview["initiator"].member_id}
    assert _AGENT_RESULT in issue["description"]
    observer_view = observer.get(f"{preview['base']}/issues/by-number/{issue['number']}")
    assert observer_view.status_code == 404, observer_view.text
    assert _AGENT_RESULT not in observer_view.text
    app_channel_agents.sweep(preview["root"])
    again = _confirm(preview, token)
    assert again.status_code == 200, again.text
    assert again.json() == ready
    assert _issues(preview) == [issue]
    assert list(preview["pool"].jobs) == [task_id]
    assert _history(preview) == before


async def test_a_failed_agent_draft_does_not_issue_a_review_request(preview, monkeypatch):
    token = _preview_token(preview, monkeypatch, "collaboration")
    seen = _enable_fake_task_execution(preview, monkeypatch, "")
    response = _confirm(preview, token)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "awaiting_draft"
    task_id = response.json()["taskId"]

    await _finish(preview["pool"], task_id)
    app_channel_agents.sweep(preview["root"])
    assert TaskLedger(preview["root"]).load(task_id).status == "failed"
    assert seen == [(task_id, "docs-preview")]

    failed = _confirm(preview, token)
    assert failed.status_code == 200, failed.text
    assert failed.json()["status"] == "agent_failed"
    assert failed.json().get("issueId") is None
    assert _issues(preview) == []
    assert _history(preview) == []
    assert list(preview["pool"].jobs) == [task_id]


def test_confirmation_is_bound_to_its_initiator_and_channel(preview, monkeypatch):
    token = _preview_token(preview, monkeypatch, "person")
    other_channel = _create(
        preview, "different-confirmation-channel", people=[preview["initiator"].member_id]
    )

    wrong_person = _confirm(preview, token, caller=preview["owner"])
    wrong_channel = _confirm(preview, token, channel=other_channel)
    assert wrong_person.status_code == 404, wrong_person.text
    assert wrong_channel.status_code == 404, wrong_channel.text
    assert _issues(preview) == []
    assert not preview["pool"].jobs

    correct = _confirm(preview, token)
    assert correct.status_code == 200, correct.text
    assert correct.json()["status"] == "assigned"
    assert len(_issues(preview)) == 1


@pytest.mark.parametrize("change,status", [("removed-agent", 409), ("archived-channel", 404)])
def test_confirmation_rechecks_channel_and_agent_eligibility(preview, monkeypatch, change, status):
    token = _preview_token(preview, monkeypatch, "agent")
    owner = preview["owner"]
    if change == "removed-agent":
        agent = preview["targets"]["engineering"]["id"]
        changed = owner.delete(f"{preview['team_channel']}/members/agents/{agent}")
    else:
        changed = owner.delete(preview["team_channel"])
    assert changed.status_code == 204, changed.text
    refused = _confirm(preview, token)
    assert refused.status_code == status, refused.text
    if change == "removed-agent":
        assert refused.json()["error"]["code"] == "recommendation.changed"
    assert _issues(preview) == []
    assert not preview["pool"].jobs
