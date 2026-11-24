"""删除频道，以及一批「这里没有这个功能」的接口如实回答（2026-11-23）。

删除频道是真的：所有者打一遍频道名确认，频道和它的内容（消息、话题、话题上的 Mission、投票卡、已读、静音）
一起删掉；频道里还有 agent 在做事时拒绝；删之前写持久审计。其余接口没有对应能力：如实 4xx 或空列表，
不存一份不生效的数据。
"""
import json
import sqlite3
from contextlib import closing

import pytest

from src.utils.state_dir import state_dir
from src.workflow.members.registry import MemberRegistry
from tests.unit.test_member_agents import _join, home  # noqa: F401 - pytest fixture
from tests.unit.test_team_channels import _create


def _history(home):
    return state_dir(home["root"]) / "members/channel-messages.sqlite3"


def _audits(home):
    path = state_dir(home["root"]) / "audit.log"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_the_owner_deletes_a_channel_and_everything_in_it(home):
    owner, base = home["owner"], home["base"]
    channel = _create(home, "to-delete")
    channel_id = channel.rsplit("/", 1)[1]
    keep = _create(home, "keep")
    root = owner.post(channel + "/messages", json={"body": "话题开头"}).json()["id"]
    owner.post(channel + "/messages", json={"body": "回复", "threadId": root, "openThread": True})
    kept = owner.post(keep + "/messages", json={"body": "别的频道"}).json()["id"]
    mission = owner.post(base + "/missions", json={"agentId": home["agent"]["id"], "threadId": root, "goal": "随便"})
    assert mission.status_code == 201, mission.text

    wrong = owner.post(channel + "/delete", json={"name": "to-delet"})
    assert wrong.status_code == 409 and wrong.json()["error"]["code"] == "channel.delete_refused"
    assert owner.get(channel + "/messages").status_code == 200                       # 名字不对：什么都没删
    before = len(_audits(home))

    deleted = owner.post(channel + "/delete", json={"name": "to-delete"})
    assert deleted.status_code == 204, deleted.text
    assert owner.get(channel + "/messages").status_code == 404
    assert owner.get(f"{base}/missions/{mission.json()['id']}").status_code == 404
    assert channel_id not in MemberRegistry(home["root"]).snapshot()["channels"]
    with closing(sqlite3.connect(_history(home))) as conn:
        assert conn.execute("SELECT count(*) FROM messages WHERE channel = ?", (channel_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM mission_drafts").fetchone()[0] == 0          # 话题上的 Mission 跟着删
        assert conn.execute("SELECT count(*) FROM thread_lifecycle WHERE channel = ?", (channel_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM messages WHERE id = ?", (kept,)).fetchone()[0] == 1   # 别的频道不动
    audit = _audits(home)[before:]
    assert [row["operation"] for row in audit] == ["永久删除团队频道"] and audit[0]["channel"] == channel_id
    assert owner.post(channel + "/delete", json={"name": "to-delete"}).status_code == 404   # 已经没了


def test_only_the_owner_deletes_and_busy_channels_are_kept(home, monkeypatch):
    owner = home["owner"]
    member = _join(home)
    channel = _create(home, "shared", people=[member.member_id])
    refused = member.post(channel + "/delete", json={"name": "shared"})
    assert refused.status_code == 403
    from src.web import app_channel_agents
    monkeypatch.setattr(app_channel_agents, "runs", lambda root, channel_id: [{"runId": "task-x"}])
    before = len(_audits(home))
    busy = owner.post(channel + "/delete", json={"name": "shared"})
    assert busy.status_code == 409 and "agent" in busy.json()["error"]["message"]
    assert owner.get(channel + "/messages").status_code == 200
    assert len(_audits(home)) == before                                                 # 被拒的不记「允许删除」


def test_consultations_and_follow_ups_are_empty_for_people_who_can_read(home):
    owner, base = home["owner"], home["base"]
    channel = _create(home, "talk")
    assert owner.get(channel + "/consultations").json() == {"consultations": []}
    assert owner.get(channel + "/consultations/c1").status_code == 404
    assert owner.get(channel + "/follow-ups").json() == {"followUps": []}
    member = _join(home, "ana@example.com", "小王")
    shared = _create(home, "shared-talk", people=[member.member_id])
    assert member.get(shared + "/follow-ups").json() == {"followUps": []}               # 频道成员也拿到空列表
    assert member.get(shared + "/consultations").json() == {"consultations": []}
    outsider = _join(home, "bo@example.com", "小博")
    private = _create(home, "secret", visibility="private")
    for path in ("/follow-ups", "/consultations"):
        assert outsider.get(private + path).status_code in (403, 404), path       # 读不了这个频道：不回空列表


@pytest.mark.parametrize(("path", "body", "status", "code"), [
    ("/messages/m1/answer-now", {}, 409, "message.nothing_waiting"),
    ("/messages/m1/cancel-follow-up", {}, 409, "follow_up.not_pending"),
    ("/messages/m1/correction", {"body": "下次先查日历"}, 409, "message.correction_unavailable"),
    ("/messages/m1/github-answer", {}, 409, "github_event.unavailable"),
])
def test_message_actions_without_the_feature_say_so(home, path, body, status, code):
    response = home["owner"].post(home["base"] + path, json=body)
    assert (response.status_code, response.json()["error"]["code"]) == (status, code)
    member = _join(home)
    assert member.post(home["base"] + path, json=body).status_code == 403


def test_undo_on_a_message_has_nothing_to_undo(home):
    response = home["owner"].post(home["base"] + "/messages/m1/actions/undo", json={})
    assert response.status_code == 200 and response.json() == {"actions": [], "failed": []}


@pytest.fixture
def skill(home, monkeypatch):
    from src.web import app_views
    from src.workflow.workbench.personal_skills import PersonalSkills
    monkeypatch.setenv("VORTOONE_WORKSPACE_SCOPE", "general")
    app_views._SHOWN_SKILLS.clear()
    store = PersonalSkills(str(home["root"]))
    store.save("weekly-report", {"name": "周报", "description": "写周报时用", "instructions": "按项目列出进展。"})
    yield store
    app_views._SHOWN_SKILLS.clear()


@pytest.mark.parametrize(("path", "code"), [
    ("reimport", "skill.no_source"), ("update", "skill.no_source"), ("update/approve", "skill.no_source"),
    ("update/dismiss", "skill.no_source"), ("update/reopen", "skill.no_source"), ("update/undo", "skill.no_source"),
    ("revert", "skill.nothing_to_revert"), ("restore", "skill.not_archived"),
])
def test_skill_source_actions_are_refused_without_touching_the_skill(home, skill, path, code):
    from src.web import app_views
    owner, base = home["owner"], home["base"]
    before = skill.get("weekly-report")
    response = owner.post(f"{base}/skills/weekly-report/{path}", json={})
    assert (response.status_code, response.json()["error"]["code"]) == (409, code)
    assert skill.get("weekly-report") == before
    assert not app_views._SHOWN_SKILLS                      # 没有把这一版记成「主人看过」
    assert owner.post(f"{base}/skills/nope/{path}", json={}).status_code == 404
    assert _join(home).post(f"{base}/skills/weekly-report/{path}", json={}).status_code == 403
