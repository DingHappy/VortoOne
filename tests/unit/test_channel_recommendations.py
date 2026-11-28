"""Authenticated team channel previews with a deterministic model and no dispatch."""

import json
from types import SimpleNamespace

import pytest

from src.web import app_channel_recommendations
from src.workflow.members.registry import MemberRegistry
from tests.unit.test_member_agents import OWNER_P, _join, home  # noqa: F401 - pytest fixture
from tests.unit.test_team_channels import _create


@pytest.mark.parametrize("tool_calls", [[], [{}]], ids=["fenced-json", "tool-output-refused"])
async def test_default_adapter_uses_one_tool_free_model_call(monkeypatch, tool_calls):
    calls = []
    closed = []
    engineer = {"type": "agent", "id": "a-eng", "name": "工程助手", "duties": "排查构建日志"}
    member = {"type": "person", "id": "p-wang", "name": "小王"}
    draft = "依赖没找到，帮忙列出两步检查。"
    answer = {
        "kind": "agent",
        "reason": "构建日志适合工程助手先分析。",
        "primary": {"type": "agent", "id": "a-eng", "work": "列出两步依赖检查"},
    }

    class FakeClient:
        def __init__(self):
            self.config = SimpleNamespace(max_retries=3)

        async def chat(self, messages, **options):
            assert self.config.max_retries == 0
            assert not options.get("tools")
            calls.append(messages)
            return {"content": f"```json\n{json.dumps(answer)}\n```", "tool_calls": tool_calls}

        async def close(self):
            closed.append(True)

    monkeypatch.setattr("src.llm.providers.default_chat_ready", lambda: True)
    monkeypatch.setattr("src.llm.client.LLMClient", FakeClient)
    result = await app_channel_recommendations.recommend_channel_assignee(draft, [engineer, member])

    [messages] = calls
    assert [message["role"] for message in messages] == ["system", "user"]
    assert json.loads(messages[1]["content"]) == {
        "draft": draft,
        "candidates": [engineer],
        "authoritativeOwnerId": None,
    }
    assert closed == [True]
    if tool_calls:
        assert result["kind"] == "clarify"
        assert "primary" not in result
    else:
        assert result["kind"] == "agent"
        assert result["primary"] == {
            "type": "agent",
            "id": "a-eng",
            "name": "工程助手",
            "work": "列出两步依赖检查",
        }


@pytest.fixture
def preview(home, monkeypatch):
    owner = home["owner"]
    member = _join(home, name="小王")
    channel = _create(home, "recommendation-preview", people=[member.member_id])
    targets = {"person": {"type": "person", "id": member.member_id, "name": "小王"}}
    for key, handle, name in (
        ("engineering", "eng-preview", "工程助手"),
        ("documents", "docs-preview", "文档助手"),
    ):
        made = owner.post(f"{home['base']}/agents", json={"name": name, "handle": handle})
        assert made.status_code == 200, made.text
        agent_id = made.json()["id"]
        assert owner.post(f"{channel}/members", json={"agentId": agent_id}).status_code == 204
        targets[key] = {"type": "agent", "id": agent_id, "name": name}

    def unexpected_dispatch(*args, **kwargs):
        pytest.fail("A recommendation preview must not dispatch a task")

    monkeypatch.setattr(home["service"], "assign", unexpected_dispatch)
    return {
        **home,
        "member": member,
        "team_channel": channel,
        "url": f"{channel}/recommendation",
        "targets": targets,
    }


def _model(monkeypatch, answer):
    requests = []

    async def fake(request):
        requests.append(request)
        return answer

    monkeypatch.setattr(app_channel_recommendations, "_infer_recommendation", fake)
    return requests


def _choice(preview, target, work):
    chosen = preview["targets"][target]
    return {"type": chosen["type"], "id": chosen["id"], "work": work}


@pytest.mark.parametrize(
    ("draft", "kind", "participants", "human_review"),
    [
        (
            "小王，请核对这页会议纪要，把决策和待办分开。",
            "person",
            {"primary": ("person", "核对决策和待办")},
            True,
        ),
        (
            "这段 CI 错误是 ModuleNotFoundError，帮忙从日志列两个定位步骤。",
            "agent",
            {"primary": ("engineering", "从日志列出两个定位步骤")},
            False,
        ),
        (
            "请小王负责审核客户公告，让文档助手先整理一版供人工确认。",
            "collaboration",
            {"primary": ("person", "审核客户公告"), "assistant": ("documents", "整理公告初稿")},
            True,
        ),
        ("帮我把这个处理一下。", "clarify", {}, False),
        ("今天的演示结束了，辛苦大家。", "none", {}, False),
    ],
    ids=["explicit-human", "engineering", "human-review", "ambiguous", "idle"],
)
def test_preview_returns_named_recommendations_without_posting(
    preview,
    monkeypatch,
    draft,
    kind,
    participants,
    human_review,
):
    expected = {
        slot: {**preview["targets"][person], "work": work}
        for slot, (person, work) in participants.items()
    }
    answer = {
        "kind": kind,
        "reason": "依据当前草稿与频道人选。",
        **{slot: _choice(preview, person, work) for slot, (person, work) in participants.items()},
    }
    if "primary" in answer:
        answer["primary"]["name"] = "模型虚构的姓名"
    if "assistant" in answer:
        answer["humanReview"] = False  # A model cannot skip review when it proposes collaboration.
    requests = _model(monkeypatch, answer)
    member = preview["member"]
    history_url = f"{preview['team_channel']}/messages"
    issues_url = f"{preview['base']}/issues"
    before = member.get(history_url).json()["messages"]
    issues_before = member.get(issues_url).json()["issues"]

    response = member.post(preview["url"], json={"text": draft})

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert result["kind"] == kind
    assert result["humanReview"] is human_review
    assert result["reason"] == answer["reason"]
    assert result.get("primary") == expected.get("primary")
    assert result.get("assistant") == expected.get("assistant")
    [model_request] = requests
    candidate_ids = {candidate["id"] for candidate in model_request["candidates"]}
    assert {
        preview["targets"][target]["id"] for target in ("engineering", "documents")
    } <= candidate_ids
    assert (preview["targets"]["person"]["id"] in candidate_ids) == ("小王" in draft)
    assert preview["main"]["id"] not in candidate_ids
    assert preview["agent"]["id"] not in candidate_ids  # Existing specialist outside this channel.
    assert member.get(history_url).json()["messages"] == before
    assert member.get(issues_url).json()["issues"] == issues_before
    assert not preview["pool"].jobs


@pytest.mark.parametrize(
    "draft", ["@eng-preview 排查构建失败。", "＠工程助手 排查构建失败。", "/help"]
)
def test_preview_rejects_explicit_addressing_and_commands_before_the_model(
    preview, monkeypatch, draft
):
    requests = _model(monkeypatch, {"kind": "none", "reason": "意外调用。"})
    response = preview["member"].post(preview["url"], json={"text": draft})
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "invalid"
    assert requests == []


def test_preview_does_not_offer_an_agent_the_member_cannot_call(preview, monkeypatch):
    member = preview["member"]
    MemberRegistry(preview["root"]).set_act_level_on(
        OWNER_P, "eng-preview", member.member_id, "none"
    )
    requests = _model(
        monkeypatch,
        {
            "kind": "agent",
            "reason": "工程任务。",
            "primary": _choice(preview, "engineering", "分析失败日志"),
        },
    )

    response = member.post(preview["url"], json={"text": "帮忙分析一下这段构建失败的日志。"})

    assert response.status_code == 200, response.text
    assert response.json()["kind"] in {"none", "clarify"}
    assert response.json().get("primary") is None
    assert response.json().get("assistant") is None
    [model_request] = requests
    ids = {candidate["id"] for candidate in model_request["candidates"]}
    assert preview["targets"]["engineering"]["id"] not in ids
    assert preview["targets"]["documents"]["id"] in ids


def test_preview_never_consults_the_model_for_someone_outside_the_channel(preview, monkeypatch):
    outsider = _join(preview, email="outsider@example.com", name="小林")
    requests = _model(monkeypatch, {"kind": "none", "reason": "意外调用。"})
    response = outsider.post(preview["url"], json={"text": "帮忙分析一下构建失败。"})
    assert response.status_code == 403, response.text
    assert requests == []


def test_preview_does_not_invent_a_duty_for_a_channel_member(preview, monkeypatch):
    requests = _model(
        monkeypatch,
        {
            "kind": "person",
            "reason": "猜测小王是这个项目负责人。",
            "primary": _choice(preview, "person", "核对上线申请"),
        },
    )

    response = preview["member"].post(preview["url"], json={"text": "有人负责核对上线申请吗？"})

    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "clarify"
    assert response.json().get("primary") is None
    assert len(requests) == 1


@pytest.mark.parametrize("model_kind,expected_kind", [("person", "person"), ("agent", "clarify")])
def test_a_visible_open_issue_preserves_its_human_assignee(
    preview, monkeypatch, model_kind, expected_kind
):
    target = preview["targets"]["person"]
    issue = preview["owner"].post(
        f"{preview['base']}/issues",
        json={
            "title": "核对上线检查步骤",
            "assignee": {"type": target["type"], "id": target["id"]},
        },
    )
    assert issue.status_code == 200, issue.text
    requests = _model(
        monkeypatch,
        {
            "kind": model_kind,
            "reason": "已有工单负责人。",
            "primary": _choice(
                preview, "person" if model_kind == "person" else "engineering", "核对上线检查步骤"
            ),
        },
    )
    number = issue.json()["number"]
    draft = f"请核对工单 #{number} 记录的上线检查步骤。"

    response = preview["member"].post(preview["url"], json={"text": draft})

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["kind"] == expected_kind
    if expected_kind == "person":
        assert result["primary"] == {**target, "work": "核对上线检查步骤"}
        assert result["humanReview"] is True
    else:
        assert result.get("primary") is None
        assert result.get("assistant") is None
    [model_request] = requests
    assert target["id"] in {candidate["id"] for candidate in model_request["candidates"]}
    assert not preview["pool"].jobs
    stored = preview["member"].get(f"{preview['base']}/issues/by-number/{number}")
    assert stored.status_code == 200, stored.text
    assert stored.json()["assignee"] == {"type": "person", "id": target["id"]}
