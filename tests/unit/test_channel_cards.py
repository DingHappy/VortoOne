"""频道里 agent 发的投票和选择卡（主人 2026-11-22 定：等 agent 进了团队频道再做）。

真实派发、执行和频道记录；假模型这次真的调用 agent 的工具。
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from tests.unit.test_channel_agents_answer import _agent_replies, _client, _say, _tasks, _until, team  # noqa: F401
from tests.unit.test_member_agents import _join, home  # noqa: F401 - pytest fixture


@pytest.fixture
def cards(team, monkeypatch):
    from src.agents.agent_loop import MainAgent
    plan = []

    async def run_turn(self, prompt, mode="build", **kwargs):
        team.seen.agents.append(self)
        team.seen.prompts.append(prompt)
        results = []
        for name, args in plan.pop(0) if plan else []:
            results.append(await self._run_tool(name, args, mode, lambda _text: None))
        team.seen.results = results
        return "看卡片"
    monkeypatch.setattr(MainAgent, "run_turn", run_turn)
    team.plan = plan
    return team


async def _card(case, tools):
    case.plan.append(tools)
    async with _client(case) as owner:
        posted = await _say(owner, case, "@helper 大家决定一下")
        [reply] = await _until(lambda: _agent_replies(case, case.owner, posted["id"]) or None)
    return posted, reply


POLL = ("channel_poll", {"question": "周五团建去哪", "options": ["爬山", "看电影", "吃火锅"], "hours": 2})
CHOICES = ("channel_choices", {"questions": [{"question": "用哪个方案", "options": [{"label": "甲"}, {"label": "乙"}]}]})


async def test_a_poll_arrives_with_the_answer_and_people_in_the_channel_vote(cards):
    posted, reply = await _card(cards, [POLL])
    [ref] = reply["meta"]["artifacts"]
    member, base = cards.member, cards.base
    artifact = member.get(f"{base}/artifacts/{ref['id']}")
    assert artifact.status_code == 200, artifact.text
    assert artifact.headers["cache-control"] == "no-store"
    assert artifact.json()["content"] == {"kind": "poll", "question": "周五团建去哪",
                                          "options": ["爬山", "看电影", "吃火锅"], "multiple": False}
    poll = member.get(f"{base}/polls/{ref['id']}").json()
    assert poll["people"] == 2 and poll["votes"] == [] and poll["closedAt"] is None
    assert member.put(f"{base}/polls/{ref['id']}/vote", json={"options": [2]}).json()["votes"] == [
        {"userId": member.member_id, "option": 2}]
    assert member.put(f"{base}/polls/{ref['id']}/vote", json={"options": [0]}).json()["votes"] == [
        {"userId": member.member_id, "option": 0}]                               # 改票是替换
    assert member.put(f"{base}/polls/{ref['id']}/vote", json={"options": [0, 1]}).status_code == 400   # 单选
    assert member.put(f"{base}/polls/{ref['id']}/vote", json={"options": [7]}).status_code == 400
    owner_votes = cards.owner.put(f"{base}/polls/{ref['id']}/vote", json={"options": [1]}).json()["votes"]
    assert len(owner_votes) == 2
    assert member.put(f"{base}/polls/{ref['id']}/vote", json={"options": []}).json()["votes"] == [
        v for v in owner_votes if v["userId"] != member.member_id]               # 空列表是收回


async def test_outsiders_and_strangers_see_nothing(cards):
    _, reply = await _card(cards, [POLL])
    card = reply["meta"]["artifacts"][0]["id"]
    outsider = _join(cards.__dict__, "bo@example.com", "小博")
    base = cards.base
    for path in (f"/artifacts/{card}", f"/polls/{card}"):
        assert outsider.get(base + path).status_code in {403, 404}          # 没加入频道：看不到内容
        assert TestClient(cards.app).get(base + path).status_code in {401, 403}
    assert outsider.put(f"{base}/polls/{card}/vote", json={"options": [0]}).status_code in {403, 404}
    assert cards.member.get(f"{base}/polls/{card}").json()["votes"] == []
    assert cards.member.get(f"{base}/choices/{card}").status_code == 404     # 投票不是选择卡
    assert cards.owner.get(f"{base}/artifacts/{card}", params={"version": 2}).status_code == 404


async def test_a_closed_poll_takes_no_votes(cards):
    _, reply = await _card(cards, [POLL])
    card = reply["meta"]["artifacts"][0]["id"]
    from src.web.app_channels import channels
    store = channels(cards.root).cards
    with store.database(write=True) as conn:
        conn.execute("UPDATE cards SET closes = '2000-01-01T00:00:00.000Z' WHERE id = ?", (card,))
    refused = cards.member.put(f"{cards.base}/polls/{card}/vote", json={"options": [0]})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "poll.closed"
    assert cards.member.get(f"{cards.base}/polls/{card}").json()["closedAt"]


async def test_a_card_not_yet_sent_is_invisible(cards):
    from src.web.app_channels import channels
    store = channels(cards.root).cards
    card = store.create("poll", channel=cards.channel_id, thread="x", task="task-x", staff="helper",
                        content={"question": "q", "options": ["a", "b"]}, hours=1)
    assert cards.owner.get(f"{cards.base}/artifacts/{card}").status_code == 404
    assert cards.owner.get(f"{cards.base}/polls/{card}").status_code == 404


async def test_the_first_answer_to_a_choice_card_goes_back_to_the_agent_as_that_persons_message(cards):
    posted, reply = await _card(cards, [CHOICES])
    card = reply["meta"]["artifacts"][0]["id"]
    base = cards.base
    assert cards.member.get(f"{base}/choices/{card}").json() == {"id": card, "answer": None}
    async with _client(cards, cards.member) as member:
        answered = await member.post(f"{base}/choices/{card}/answer",
                                     json={"picks": [2], "others": ["都不好，用丙"], "clientId": "c-1"})
        assert answered.status_code == 200, answered.text
        body = answered.json()
        assert body["choices"]["answer"]["userId"] == cards.member.member_id
        assert body["choices"]["answer"]["others"] == ["都不好，用丙"]
        await _until(lambda: len(_tasks(cards)) == 2)
        again = await member.post(f"{base}/choices/{card}/answer", json={"picks": [0], "clientId": "c-2"})
        assert again.status_code == 409 and again.json()["error"]["code"] == "choices.answered"
    rows = cards.owner.get(f"{cards.channel}/messages", params={"threadId": posted["id"]}).json()["messages"]
    mine = next(row for row in rows if row["id"] == body["messageId"])
    assert mine["author"] == {"type": "person", "id": cards.member.member_id} and "都不好，用丙" in mine["body"]
    await _until(lambda: len(cards.seen.prompts) == 2)
    assert "都不好，用丙" in cards.seen.prompts[-1]
    second = next(t for t in _tasks(cards) if "都不好，用丙" in t.prompt)
    assert second.collaboration["dispatch"]["initiator"] == {"kind": "member", "id": cards.member.member_id}


async def test_answering_needs_the_right_to_call_agents(cards):
    from tests.unit.test_team_channels import _override
    _, reply = await _card(cards, [CHOICES])
    card = reply["meta"]["artifacts"][0]["id"]
    assert _override(cards.owner, cards.channel, "person", cards.member.member_id, deny=["talk_to_agents"]).status_code == 200
    refused = cards.member.post(f"{cards.base}/choices/{card}/answer", json={"picks": [0], "clientId": "c"})
    assert refused.status_code == 403
    assert cards.member.get(f"{cards.base}/choices/{card}").json()["answer"] is None


async def test_bad_cards_are_refused_to_the_agent_and_not_sent(cards):
    tools = [("channel_poll", {"question": "q", "options": ["只有一个"]}),
             ("channel_poll", {"question": "q", "options": ["a", "b"], "hours": 999}),
             ("channel_choices", {"questions": []})]
    _, reply = await _card(cards, tools)
    assert "artifacts" not in (reply.get("meta") or {})
    assert len(cards.seen.results) == 3 and not any("已准备好" in str(result) for result in cards.seen.results)


async def test_only_channel_work_gets_the_card_tools(cards):
    dm = cards.owner.post(f"{cards.base}/dms", json={"agentId": cards.agent}).json()["channelId"]
    async with _client(cards) as owner:
        await owner.post(f"{cards.base}/channels/{dm}/messages", json={"body": "私聊里的事"})
        await _until(lambda: cards.seen.agents)
    assert "channel_poll" not in cards.seen.agents[-1].tools
    await asyncio.sleep(0)
