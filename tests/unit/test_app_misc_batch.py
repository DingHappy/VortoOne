"""一批零散接口（2026-11-24）：频道卡片上回复、停下后说原因、沙箱设置照实读取，其余没有对应能力的如实回答。"""
import asyncio

import pytest

from tests.unit.test_channel_agents_answer import _agent_replies, _client, _say, _until, team  # noqa: F401
from tests.unit.test_channel_cards import CHOICES, _card, cards  # noqa: F401
from tests.unit.test_member_agents import _join, home  # noqa: F401 - pytest fixture
from src.workflow.tasks.tasks import TaskLedger


async def test_a_reply_on_a_card_goes_to_the_agent_that_made_it(cards):
    posted, reply = await _card(cards, [CHOICES])
    card = reply["meta"]["artifacts"][0]["id"]
    before = len(cards.seen.prompts)
    async with _client(cards, cards.member) as member:
        sent = await member.post(f"{cards.base}/artifacts/{card}/reply", json={"text": "两个都行，先做甲", "clientId": "c-1"})
        assert sent.status_code == 200, sent.text
        await _until(lambda: len(cards.seen.prompts) > before)
    assert "两个都行，先做甲" in cards.seen.prompts[-1]
    rows = cards.owner.get(f"{cards.channel}/messages", params={"threadId": posted["id"]}).json()["messages"]
    mine = next(row for row in rows if row["id"] == sent.json()["id"])
    assert mine["author"] == {"type": "person", "id": cards.member.member_id} and mine["body"] == "两个都行，先做甲"
    assert cards.owner.post(f"{cards.base}/artifacts/not-a-card/reply", json={"text": "x", "clientId": "c"}).status_code == 404
    assert cards.member.post(f"{cards.base}/artifacts/not-a-card/reply", json={"text": "x", "clientId": "c"}).status_code == 403
    outsider = _join(cards.__dict__, "bo@example.com", "小博")
    assert outsider.post(f"{cards.base}/artifacts/{card}/reply", json={"text": "x", "clientId": "c"}).status_code in (403, 404)


async def test_a_member_without_the_right_to_call_agents_cannot_reply(cards):
    from tests.unit.test_team_channels import _override
    _, reply = await _card(cards, [CHOICES])
    card = reply["meta"]["artifacts"][0]["id"]
    assert _override(cards.owner, cards.channel, "person", cards.member.member_id, deny=["talk_to_agents"]).status_code == 200
    refused = cards.member.post(f"{cards.base}/artifacts/{card}/reply", json={"text": "x", "clientId": "c"})
    assert refused.status_code == 403


async def test_the_stop_reason_reaches_the_agent_next_time(team):
    team.seen.release = asyncio.Event()
    dm = team.owner.post(f"{team.base}/dms", json={"agentId": team.agent}).json()["channelId"]
    async with _client(team) as owner:
        assert (await owner.post(f"{team.base}/channels/{dm}/messages", json={"body": "帮我写周报"})).status_code == 200
        [task] = await _until(lambda: [t for t in TaskLedger(team.root).list(limit=10) if t.status == "running"] or None)
        early = await owner.post(f"{team.base}/runs/{task.id}/stop-reason", json={"reason": "先别写"})
        assert early.status_code == 409                                         # 还没停下
        assert (await owner.post(f"{team.base}/runs/{task.id}/cancel", json={})).status_code == 204
        team.seen.release.set()
        await _until(lambda: TaskLedger(team.root).load(task.id).status == "cancelled")
        said = await owner.post(f"{team.base}/runs/{task.id}/stop-reason", json={"reason": "周报改到周五再写"})
        assert said.status_code == 204, said.text
        again = await owner.post(f"{team.base}/runs/{task.id}/stop-reason", json={"reason": "再说一次"})
        assert again.status_code == 409                                         # 只能说一次
        team.seen.release = None
        assert (await owner.post(f"{team.base}/channels/{dm}/messages", json={"body": "那先帮我查日程"})).status_code == 200
        await _until(lambda: len(team.seen.prompts) >= 2)
    assert "周报改到周五再写" in team.seen.prompts[-1]
    assert team.member.post(f"{team.base}/runs/{task.id}/stop-reason", json={"reason": "x"}).status_code == 403
    assert team.owner.post(f"{team.base}/runs/task-missing/stop-reason", json={"reason": "x"}).status_code == 404


@pytest.mark.parametrize(("method", "path", "body", "status", "code"), [
    ("post", "/artifacts/a1/keep", None, 409, "artifact.keep_unavailable"),
    ("put", "/artifacts/a1/sharing", {"mode": "everyone"}, 409, "artifact.sharing_unavailable"),
    ("post", "/calendar/events/e1/rsvp", {"status": "accepted"}, 404, "calendar.invitation_not_found"),
    ("post", "/calendar/proposals/p1/confirm", {}, 404, "calendar.proposal_not_found"),
    ("post", "/calendar/proposals/p1/dismiss", {}, 404, "calendar.proposal_not_found"),
    ("post", "/reminders", {"message": "喝水", "at": "2026-11-25T09:00:00Z"}, 409, "reminder.unavailable"),
    ("delete", "/reminders/r1", None, 404, "reminder.not_found"),
    ("post", "/team-templates/t1/use", {}, 404, "team_template.not_found"),
    ("post", "/runs/task-x/device-wait", {"action": "retry"}, 404, "run.not_waiting"),
])
def test_features_we_do_not_have_say_so(home, method, path, body, status, code):
    response = getattr(home["owner"], method)(home["base"] + path, **({"json": body} if body is not None else {}))
    assert (response.status_code, response.json()["error"]["code"]) == (status, code)


def test_empty_lists_and_sandbox_settings_are_honest(home, monkeypatch):
    owner, base = home["owner"], home["base"]
    assert owner.get(f"{base}/reminders").json() == {"reminders": []}
    assert owner.get(f"{base}/team-templates").json() == {"templates": []}
    monkeypatch.delenv("VORTOONE_ENABLE_SHELL", raising=False)
    current = owner.get(f"{base}/sandbox-settings").json()
    assert current == {"enabled": False, "execTimeoutSeconds": 300, "maxTimeoutSeconds": 300, "requireApproval": True}
    assert owner.put(f"{base}/sandbox-settings", json={"requireApproval": True}).json() == current
    refused = owner.put(f"{base}/sandbox-settings", json={"enabled": True})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "sandbox.settings_fixed"
    assert owner.put(f"{base}/sandbox-settings", json={"requireApproval": False}).status_code == 409   # 不另开免确认
    member = _join(home)
    for method, path in (("get", "/sandbox-settings"), ("put", "/sandbox-settings"), ("get", "/reminders")):
        response = getattr(member, method)(base + path, **({"json": {}} if method == "put" else {}))
        assert response.status_code == 403, path
