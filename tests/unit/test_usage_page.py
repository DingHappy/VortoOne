"""用量页（主人 2026-11-23 定）：按所有者给模型填的价格折成美元，按人归账，可给成员设每月上限。"""
import json
from datetime import date

import pytest

from src.llm import usage_ledger as ledger
from src.utils.state_dir import state_dir
from src.workflow.staff.budgets import StaffBudgets
from src.workflow.tasks.tasks import TaskLedger
from tests.unit.test_member_agents import _join, home  # noqa: F401 - pytest fixture

PRICED, FREE = "m-priced", "m-unpriced"


@pytest.fixture
def priced(home, monkeypatch, tmp_path):
    monkeypatch.setenv("VORTOONE_USAGE_DIR", str(tmp_path / "usage"))
    path = state_dir(home["root"]) / "model_credentials.json"
    path.write_text(json.dumps({"credentials": [{"id": "cred-1", "status": "active", "models": [
        {"id": PRICED, "prices": {"input": 1.0, "output": 2.0}}, {"id": FREE}]}], "default": None}))
    return home


def _member_task(case, member):
    sent = member.post(f"{case['base']}/channels/{case['channel']}/messages", json={"body": "帮我看看"})
    assert sent.status_code == 200, sent.text
    [task] = [t for t in TaskLedger(case["root"]).list(limit=20)
              if ((t.collaboration or {}).get("dispatch") or {}).get("initiator", {}).get("id") == member.member_id]
    return task


def _spend(case, task, model, prompt, completion):
    instance = StaffBudgets(case["root"]).instance
    ledger.record(prompt, completion, source=f"staff:{instance}:-:{case['staff']['id']}:{task.id}",
                  model=model, today=date.today())


def test_costs_follow_registered_prices_and_land_on_the_right_person(priced):
    owner, base = priced["owner"], priced["base"]
    member = _join(priced)
    ledger.record(1_000_000, 500_000, source="对话", model=PRICED, today=date.today())     # 所有者：1 + 1 = 2 美元
    ledger.record(3_000_000, 0, source="对话", model=FREE, today=date.today())             # 没价格：只记 token
    task = _member_task(priced, member)
    _spend(priced, task, PRICED, 500_000, 0)                                              # 成员：0.5 美元
    report = owner.get(f"{base}/usage/report").json()
    assert report["totalUsd"] == pytest.approx(2.5)
    main = next(row for row in report["agents"] if row["agentId"] == priced["main"]["id"])
    helper = next(row for row in report["agents"] if row["agentId"] == priced["agent"]["id"])
    assert main["costUsd"] == pytest.approx(2.0) and main["inputTokens"] == 4_000_000      # 没价格的照样记 token
    [kind] = report["byKind"]
    assert kind["partialCalls"] == 1                                                    # 缺价格的那笔标出来，不当免费
    assert helper["costUsd"] == pytest.approx(0.5)
    people = {row["userId"]: row for row in owner.get(f"{base}/usage/people").json()["people"]}
    owner_id = next(uid for uid, row in people.items() if row["owner"])
    assert people[owner_id]["costUsd"] == pytest.approx(2.0)
    assert people[member.member_id]["costUsd"] == pytest.approx(0.5) and people[member.member_id]["runs"] == 1
    mine = owner.get(f"{base}/usage/people/{member.member_id}").json()
    assert mine["money"] is True and mine["total"] == pytest.approx(0.5)
    assert [row["agentId"] for row in mine["agents"]] == [priced["agent"]["id"]]
    listed = {row["agentId"]: row for row in owner.get(f"{base}/usage").json()["usage"]}
    assert listed[priced["main"]["id"]]["costUsd"] == pytest.approx(2.0)                   # 原来的占位 0 换成真数
    agent = owner.get(f"{base}/agents/{priced['agent']['id']}/usage").json()
    assert agent["tokens"] == 500_000 and agent["sources"][0]["costUsd"] == pytest.approx(0.5)
    assert owner.get(f"{base}/usage/plans").json()["accounts"] == []


def test_a_member_over_a_blocking_limit_cannot_start_more_work(priced):
    owner, base = priced["owner"], priced["base"]
    member = _join(priced)
    task = _member_task(priced, member)
    _spend(priced, task, PRICED, 1_000_000, 0)                                            # 1 美元
    saved = owner.put(f"{base}/usage/limits", json={"defaultUsd": 0.5, "onLimit": "block"})
    assert saved.status_code == 200 and saved.json()["defaultUsd"] == 0.5
    refused = member.post(f"{base}/channels/{priced['channel']}/messages", json={"body": "再帮我看看"})
    assert refused.status_code >= 400 and "上限" in refused.text
    view = member.get(f"{base}/usage/people/{member.member_id}").json()
    assert view["money"] is False and view["limit"]["state"] == "at" and "monthUsd" not in view["limit"]
    assert view["total"] == pytest.approx(200)                                            # 占上限的百分比
    asked = member.post(f"{base}/usage/limits/request", json={})
    assert asked.status_code == 200 and asked.json()["requestedAt"]
    row = next(r for r in owner.get(f"{base}/usage/people").json()["people"] if r["userId"] == member.member_id)
    assert row["requested"] is True and row["limitFrom"] == "default" and row["limitUsd"] == 0.5
    assert owner.post(f"{base}/usage/people/{member.member_id}/raise", json={"limitUsd": 5}).status_code == 204
    row = next(r for r in owner.get(f"{base}/usage/people").json()["people"] if r["userId"] == member.member_id)
    assert row["limitFrom"] == "raised" and row["limitUsd"] == 5 and row["requested"] is False
    again = member.post(f"{base}/channels/{priced['channel']}/messages", json={"body": "现在可以了吗"})
    assert again.status_code == 200, again.text


def test_notify_only_does_not_block_and_the_owner_has_no_limit(priced):
    owner, base = priced["owner"], priced["base"]
    member = _join(priced)
    task = _member_task(priced, member)
    _spend(priced, task, PRICED, 1_000_000, 0)
    owner.put(f"{base}/usage/limits", json={"defaultUsd": 0.5, "onLimit": "notify"})
    assert member.post(f"{base}/channels/{priced['channel']}/messages", json={"body": "继续"}).status_code == 200
    ledger.record(10_000_000, 0, source="对话", model=PRICED, today=date.today())
    me = next(r for r in owner.get(f"{base}/usage/people").json()["people"] if r["owner"])
    assert me["limitFrom"] == "owner" and me["state"] == "none"


def test_limits_validation_and_who_sees_what(priced):
    owner, base = priced["owner"], priced["base"]
    member = _join(priced)
    for bad in ({"defaultUsd": -1}, {"onLimit": "maybe"}, {"people": {"nobody": 1}}, {"extra": 1}):
        assert owner.put(f"{base}/usage/limits", json=bad).status_code == 400, bad
    assert owner.put(f"{base}/usage/limits", json={"people": {member.member_id: 3}}).json()["people"] == {
        member.member_id: 3.0}
    for path in ("/usage/report", "/usage/people", "/usage/limits", "/usage/plans",
                 f"/agents/{priced['agent']['id']}/usage", f"/agents/{priced['agent']['id']}/usage-queue"):
        assert member.get(base + path).status_code == 403, path
    other = _join(priced, "li@example.com", "小李")
    assert member.get(f"{base}/usage/people/{other.member_id}").status_code == 403         # 只能看自己
    assert member.put(f"{base}/usage/limits", json={"defaultUsd": 100}).status_code == 403
    assert member.post(f"{base}/usage/people/{member.member_id}/raise", json={"limitUsd": 100}).status_code == 403
    assert owner.post(f"{base}/usage/limits/request", json={}).status_code == 409


def test_composio_and_an_empty_queue(priced):
    owner, base = priced["owner"], priced["base"]
    assert owner.get(f"{base}/usage/composio").json()["calls"] == 0
    queue = owner.get(f"{base}/agents/{priced['agent']['id']}/usage-queue").json()
    assert queue["rows"] == [] and queue["reason"] is None
    assert owner.post(f"{base}/agents/{priced['agent']['id']}/usage-queue/cancel").json() == {"canceled": 0, "skipped": 0}
    assert owner.get(f"{base}/agents/nope/usage-queue").status_code == 404
