"""Recommendation fallback with the real API paths and a fake Decisions transport."""

import json
from contextlib import asynccontextmanager
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.llm.client import ProviderHTTPError
from src.web import app_channel_decisions, app_channel_recommendations
from tests.unit.test_channel_recommendations import preview  # noqa: F401 - pytest fixture
from tests.unit.test_member_agents import home  # noqa: F401 - a dependency of preview


_TEST_KEY = "sk-test-decisions-preview-only-not-a-real-key"


def _outage():
    raise app_channel_recommendations.RecommendationPrimaryUnavailable("测试主模型不可用")


def _forbid_relay(monkeypatch):
    def unexpected_relay(*args, **kwargs):
        pytest.fail("This preview must not contact the Decisions relay")

    monkeypatch.setattr(app_channel_decisions.aiohttp, "ClientSession", unexpected_relay)


def _settings_url(preview):
    return f"{preview['base']}/credentials/decisions-backup"


def _configure_backup(preview):
    response = preview["owner"].put(_settings_url(preview), json={"apiKey": _TEST_KEY})
    assert response.status_code == 200, response.text
    assert response.json() == {"configured": True}


def _mock_decisions(monkeypatch, kind, participants, *, status=200, confidence=0.95):
    sent = []

    @asynccontextmanager
    async def post(url, *, headers, allow_redirects, **options):
        assert url == "https://api.openai.com/v1/decisions"
        assert headers["Authorization"] == f"Bearer {_TEST_KEY}"
        assert allow_redirects is False
        payload = options["json"]
        assert _TEST_KEY not in payload["input"]
        request = json.loads(payload["input"])
        sent.append(request)
        assert payload["model"] == "gpt-6-luna"
        refs = {candidate["name"]: candidate["ref"] for candidate in request["candidates"]}
        choices = {
            "kind": kind,
            "human_lead": refs["小王"] if "person" in participants else "background",
            "agent": refs.get(participants.get("agent", ""), "none"),
        }
        for question in payload["questions"]:
            assert choices[question["name"]] in {choice["value"] for choice in question["choices"]}

        answer = {
            "answers": [
                {"type": "choice", "name": name, "choice": choice, "confidence": confidence}
                for name, choice in choices.items()
            ]
        }
        body = BytesIO(json.dumps(answer).encode("utf-8"))

        async def read(size):
            return body.read(size)

        yield SimpleNamespace(status=status, content=SimpleNamespace(read=read))

    @asynccontextmanager
    async def session(*, trust_env, **options):
        assert trust_env is False
        yield SimpleNamespace(post=post)

    monkeypatch.setattr(app_channel_decisions.aiohttp, "ClientSession", session)
    return sent


@pytest.mark.parametrize("backup", [False, True], ids=["primary", "backup"])
@pytest.mark.parametrize(
    ("draft", "kind", "primary", "assistant", "human_review"),
    [
        (
            "小王，请核对这页会议纪要，把决策和待办分开。",
            "person",
            "person",
            None,
            True,
        ),
        (
            "这段 CI 错误是 ModuleNotFoundError，帮忙从日志列两个定位步骤。",
            "agent",
            "engineering",
            None,
            False,
        ),
        (
            "请小王负责审核客户公告，让文档助手先整理一版供人工确认。",
            "collaboration",
            "person",
            "documents",
            True,
        ),
    ],
    ids=["human", "agent", "collaboration"],
)
def test_decisions_fallback_preserves_the_existing_chinese_cases(
    preview,
    monkeypatch,
    backup,
    draft,
    kind,
    primary,
    assistant,
    human_review,
):
    member = preview["member"]
    targets = preview["targets"]
    requests = []

    async def model(request):
        requests.append(request)
        if backup:
            _outage()
        result = {"kind": kind, "humanReview": human_review, "reason": "依据草稿安排工作。"}
        for slot, key in (("primary", primary), ("assistant", assistant)):
            if key:
                result[slot] = {**targets[key], "work": "完成草稿中要求的步骤"}
        return result

    monkeypatch.setattr(app_channel_recommendations, "_infer_recommendation", model)
    if backup:
        participants = {
            targets[key]["type"]: targets[key]["name"] for key in (primary, assistant) if key
        }
        sent = _mock_decisions(monkeypatch, kind, participants)
        _configure_backup(preview)
    else:
        _forbid_relay(monkeypatch)
        assert preview["owner"].get(_settings_url(preview)).json() == {"configured": False}

    history_url = f"{preview['team_channel']}/messages"
    issues_url = f"{preview['base']}/issues"
    messages = member.get(history_url).json()["messages"]
    issues = member.get(issues_url).json()["issues"]
    response = member.post(preview["url"], json={"text": draft})

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert result["kind"] == kind
    assert result["humanReview"] is human_review
    for slot, key in (("primary", primary), ("assistant", assistant)):
        participant = result.get(slot)
        if key:
            assert {field: participant[field] for field in ("type", "id", "name")} == targets[key]
            assert participant["work"].strip()
        else:
            assert participant is None
    [primary_request] = requests
    assert primary_request["draft"] == draft
    if backup:
        [relayed] = sent
        assert relayed["draft"] == draft
        assert all("id" not in candidate for candidate in relayed["candidates"])
        serialized = json.dumps(relayed)
        assert all(candidate["id"] not in serialized for candidate in primary_request["candidates"])
    assert member.get(history_url).json()["messages"] == messages
    assert member.get(issues_url).json()["issues"] == issues
    assert not preview["pool"].jobs


@pytest.mark.parametrize("kind", ["clarify", "none"])
def test_configured_backup_is_not_used_for_an_intentional_primary_result(
    preview, monkeypatch, kind
):
    async def model(request):
        return {"kind": kind, "reason": "先明确这件事的目标。", "humanReview": False}

    monkeypatch.setattr(app_channel_recommendations, "_infer_recommendation", model)
    _forbid_relay(monkeypatch)
    _configure_backup(preview)
    response = preview["member"].post(preview["url"], json={"text": "这个还得再看看。"})
    assert response.status_code == 200, response.text
    assert response.json()["kind"] == kind
    assert not preview["pool"].jobs


@pytest.mark.parametrize("cleared", [False, True], ids=["never-set", "cleared"])
def test_primary_timeout_without_backup_does_not_relay(preview, monkeypatch, cleared):
    async def model(request):
        raise TimeoutError("synthetic primary timeout")

    monkeypatch.setattr(app_channel_recommendations, "_infer_recommendation", model)
    _forbid_relay(monkeypatch)
    if cleared:
        _configure_backup(preview)
        assert preview["owner"].put(_settings_url(preview), json={"apiKey": None}).json() == {
            "configured": False
        }
    response = preview["member"].post(preview["url"], json={"text": "列出两个定位步骤。"})
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "unavailable"
    assert not preview["pool"].jobs


@pytest.mark.parametrize(
    ("status", "confidence", "expected_status"),
    [(503, 0.95, 503), (200, "unknown", 200)],
    ids=["decisions-http-failure", "malformed-confidence"],
)
def test_failed_or_invalid_backup_never_recommends_an_assignee(
    preview,
    monkeypatch,
    status,
    confidence,
    expected_status,
):
    async def model(request):
        _outage()

    monkeypatch.setattr(app_channel_recommendations, "_infer_recommendation", model)
    sent = _mock_decisions(
        monkeypatch, "agent", {"agent": "工程助手"}, status=status, confidence=confidence
    )
    _configure_backup(preview)
    response = preview["member"].post(preview["url"], json={"text": "排查构建日志。"})
    assert response.status_code == expected_status, response.text
    assert _TEST_KEY not in response.text
    result = response.json()
    if status == 200:
        assert result["kind"] == "clarify"
    else:
        assert result["error"]["code"] == "unavailable"
    assert "primary" not in result
    assert "assistant" not in result
    assert len(sent) == 1
    assert not preview["pool"].jobs


@pytest.mark.parametrize("status", [401, 429])
def test_primary_authorization_and_rate_limits_do_not_relay_the_draft(preview, monkeypatch, status):
    async def model(request):
        raise ProviderHTTPError(status, "synthetic primary response")

    monkeypatch.setattr(app_channel_recommendations, "_infer_recommendation", model)
    _forbid_relay(monkeypatch)
    _configure_backup(preview)
    response = preview["member"].post(preview["url"], json={"text": "列出两个定位步骤。"})
    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "unavailable"
    assert not preview["pool"].jobs


def test_only_an_authenticated_owner_can_read_or_change_backup_configuration(preview, monkeypatch):
    _forbid_relay(monkeypatch)
    url = _settings_url(preview)
    owner = preview["owner"]
    member = preview["member"]
    unauthenticated = TestClient(preview["app"])
    assert owner.get(url).json() == {"configured": False}
    _configure_backup(preview)
    assert owner.get(url).json() == {"configured": True}
    for caller in (member, unauthenticated):
        assert caller.get(url).status_code in {401, 403}
        assert caller.put(url, json={"apiKey": None}).status_code in {401, 403}
    assert owner.get(url).json() == {"configured": True}
    cleared = owner.put(url, json={"apiKey": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json() == {"configured": False}
    assert owner.get(url).json() == {"configured": False}
