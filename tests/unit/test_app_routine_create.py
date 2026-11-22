"""界面新建例行工作：先成为待核对的提议，主人按摘要确认后才登记；排期的解析与说明。"""

from datetime import datetime, timezone

import pytest

from src.web import app_schedule
from tests.unit.test_member_agents import _join, home  # noqa: F401 - pytest fixture

NOW = datetime(2026, 11, 22, 3, 0, tzinfo=timezone.utc)


def _new(home, **extra):
    body = {"name": "晨报", "agentId": home["agent"]["id"], "prompt": "整理昨天的动态",
            "trigger": {"type": "schedule", "phrase": "每天早上9点", "timezone": "Asia/Shanghai"}, **extra}
    return home["owner"].post(f"{home['base']}/routines", json=body)


def _review(home):
    answer = home["owner"].get(f"{home['base']}/routines/proposals/review")
    assert answer.status_code == 200, answer.text
    return answer.json()["proposals"]


def _decide(home, proposal, approve):
    body = {"approve": approve, **proposal["proof"]}
    return home["owner"].post(f"{home['base']}/routines/proposals/{proposal['id']}/decision", json=body)


def test_a_new_routine_waits_for_the_owners_review_then_runs(home):
    from src.workflow.staff.routines import Routines
    made = _new(home)
    assert made.status_code == 200, made.text
    row = made.json()
    assert row["state"] == "waiting_person" and row["active"] is False and row["canManage"] is False
    assert row["trigger"]["phrase"] == "0 9 * * *" and row["agentId"] == home["agent"]["id"]
    assert Routines(home["root"]).list() == []                              # 还没登记
    listed = home["owner"].get(f"{home['base']}/routines").json()["routines"]
    assert [r["id"] for r in listed] == [row["id"]]
    runs = f"{home['base']}/routines/{row['id']}/runs"
    assert home["owner"].get(runs).json() == {"runs": []}
    assert _join(home).get(runs).status_code == 403

    [proposal] = _review(home)
    assert proposal["name"] == "晨报" and proposal["schedule"] == "0 9 * * *" and proposal["timezone"] == "Asia/Shanghai"
    assert proposal["submitterName"] is None and "界面上新建" in proposal["reason"]
    decided = _decide(home, proposal, True)
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "approved"
    [saved] = Routines(home["root"]).list()
    assert saved["id"] == row["id"] and saved["schedule"] == "0 9 * * *" and saved["enabled"] is True
    listed = home["owner"].get(f"{home['base']}/routines").json()["routines"]
    assert [(r["id"], r["state"]) for r in listed] == [(row["id"], "active")]
    assert _review(home) == []


def test_refusing_it_leaves_nothing(home):
    from src.workflow.staff.routines import Routines
    _new(home)
    [proposal] = _review(home)
    assert _decide(home, proposal, False).json()["status"] == "rejected"
    assert Routines(home["root"]).list() == []
    assert home["owner"].get(f"{home['base']}/routines").json()["routines"] == []


def test_the_assistants_own_proposals_stay_out_of_this_page(home):
    from src.workflow.staff.approvals import StaffApprovals
    from src.workflow.staff.routines import Routines
    _new(home)
    theirs = StaffApprovals(home["root"]).propose("sid-owner", "routine", {
        "id": "routine-assistant-one", "name": "助理提的", "member": home["staff"]["id"], "prompt": "x",
        "schedule": "0 9 * * *", "timezone": "UTC"}, "助理提议")
    assert [p["name"] for p in _review(home)] == ["晨报"]
    assert "routine-assistant-one" not in {r["id"] for r in home["owner"].get(f"{home['base']}/routines").json()["routines"]}
    proof = {key: theirs[key] for key in ("digest", "nonce", "round", "instance")}
    answer = _decide(home, {"id": theirs["id"], "proof": proof}, True)
    assert answer.status_code == 404 and Routines(home["root"]).list() == []


def test_a_stale_proof_is_refused(home):
    _new(home)
    [proposal] = _review(home)
    stale = {**proposal, "proof": {**proposal["proof"], "digest": "0" * 64}}
    assert _decide(home, stale, True).status_code == 409


@pytest.mark.parametrize("extra, code", [
    ({"trigger": {"type": "event", "event": {"kind": "issue.created"}}}, "routine.trigger_unsupported"),
    ({"trigger": {"type": "schedule", "phrase": "随便什么时候"}}, "unparsed"),
    ({"trigger": {"type": "schedule", "rule": {"freq": "daily", "interval": 2, "timezone": "UTC"}}}, "unsupported"),
    ({"target": {"type": "channel", "channelId": "x"}}, "invalid"),
    ({"prompt": ""}, "invalid"),
    ({"report": "sometimes"}, "invalid"),
    ({"access": "act", "secret": 1}, "invalid"),
])
def test_routines_this_home_cannot_run_are_refused(home, extra, code):
    answer = _new(home, **extra)
    assert answer.status_code == 400 and answer.json()["error"]["code"] == code, answer.text
    assert _review(home) == []


def test_the_main_assistant_is_not_a_routine_worker(home):
    answer = _new(home, agentId=home["main"]["id"])
    assert answer.status_code == 400 and "专员" in answer.json()["error"]["message"]


def test_a_rule_and_a_one_off(home):
    rule = {"freq": "weekly", "byWeekday": ["MO", "WE"], "times": ["18:30"], "timezone": "Asia/Shanghai"}
    assert _new(home, trigger={"type": "schedule", "rule": rule}).json()["trigger"]["phrase"] == "30 18 * * 1,3"
    once = _new(home, name="提醒", trigger={"type": "once", "at": "2099-01-01T09:00:00+08:00"}).json()
    assert once["trigger"]["type"] == "once"


def test_members_cannot_create_routines_here(home):
    member = _join(home)
    assert member.post(f"{home['base']}/routines", json={"agentId": home["agent"]["id"], "prompt": "x",
                                                         "trigger": {"type": "schedule", "phrase": "每天9点"}}
                       ).status_code == 403


def test_parse_endpoints(home):
    base, owner = home["base"], home["owner"]
    parsed = owner.post(f"{base}/routines/parse-schedule", json={"phrase": "工作日 9:30", "timezone": "Asia/Shanghai"})
    assert parsed.json() == {"ok": True, "cron": "30 9 * * 1,2,3,4,5", "description": "工作日 09:30",
                             "timezone": "Asia/Shanghai"}
    assert owner.post(f"{base}/routines/parse-schedule", json={"phrase": "看心情"}).json()["ok"] is False
    rule = owner.post(f"{base}/routines/parse-rule", json={"phrase": "每月1号 8点", "timezone": "UTC"}).json()
    assert rule["ok"] and rule["rule"]["freq"] == "monthly" and rule["cron"] == "0 8 1 * *" and rule["nextRuns"]
    described = owner.post(f"{base}/routines/describe-rule", json={"rule": rule["rule"]}).json()
    assert described["description"] == "每月 1 日 08:00"
    bad = owner.post(f"{base}/routines/describe-rule", json={"rule": {"freq": "daily", "times": ["9:00", "18:30"],
                                                                     "timezone": "UTC"}}).json()
    assert bad == {"ok": False, "error": bad["error"], "code": "unsupported"}


# ── 说法与规则（不经 HTTP）──────────────────────────────────────────────────

@pytest.mark.parametrize("phrase, cron, description", [
    ("每天早上9点", "0 9 * * *", "每天 09:00"),
    ("每天下午3:15", "15 15 * * *", "每天 15:15"),
    ("工作日 9:30", "30 9 * * 1,2,3,4,5", "工作日 09:30"),
    ("每周一三 18点", "0 18 * * 1,3", "每周一、三 18:00"),
    ("周末10点半", "30 10 * * 6,0", "每周六、日 10:30"),
    ("每月15号", "0 9 15 * *", "每月 15 日 09:00"),
    ("每小时", "0 * * * *", "每小时的第 0 分"),
    ("every weekday at 9am", "0 9 * * 1,2,3,4,5", "工作日 09:00"),
    ("every monday at 6:30 pm", "30 18 * * 1", "每周一 18:30"),
    ("daily at 7", "0 7 * * *", "每天 07:00"),
    ("every day at 12am", "0 0 * * *", "每天 00:00"),
    ("晚上8点 每天", "0 20 * * *", "每天 20:00"),
])
def test_phrases(phrase, cron, description):
    info = app_schedule.parse_phrase(phrase, "Asia/Shanghai", now=NOW)
    assert (info["cron"], info["description"]) == (cron, description)
    assert info["nextRuns"]


def test_tomorrow_is_a_one_off_in_the_homes_timezone():
    info = app_schedule.parse_phrase("明天上午10点", "Asia/Shanghai", now=NOW)
    assert info["cron"] == "once 2026-11-23T10:00:00+08:00" and info["nextRuns"] == ["2026-11-23T02:00:00Z"]


@pytest.mark.parametrize("rule, code", [
    ({"freq": "daily", "interval": 2, "timezone": "UTC"}, "unsupported"),
    ({"freq": "daily", "times": ["09:00", "18:30"], "timezone": "UTC"}, "unsupported"),
    ({"freq": "monthly", "byMonthDay": [1], "bySetPos": 2, "timezone": "UTC"}, "unsupported"),
    ({"freq": "daily", "until": "2027-01-01", "timezone": "UTC"}, "unsupported"),
    ({"freq": "daily", "count": 3, "timezone": "UTC"}, "unsupported"),
    ({"freq": "weekly", "timezone": "UTC"}, "invalid"),
    ({"freq": "daily", "times": ["25:00"], "timezone": "UTC"}, "invalid"),
    ({"freq": "daily", "timezone": "Mars/Base"}, "invalid"),
    ({"freq": "once", "start": "2000-01-01", "times": ["09:00"], "timezone": "UTC"}, "past"),
])
def test_rules_cron_cannot_hold_are_refused(rule, code):
    with pytest.raises(app_schedule.ScheduleError) as caught:
        app_schedule.rule_info(rule, now=NOW)
    assert caught.value.code == code


def test_a_rule_survives_the_round_trip():
    rule = {"freq": "yearly", "byMonth": [3], "byMonthDay": [8], "times": ["10:00"], "timezone": "UTC"}
    cron = app_schedule.rule_info(rule, now=NOW)["cron"]
    assert cron == "0 10 8 3 *" and app_schedule.schedule_view(cron) == {
        "freq": "yearly", "times": ["10:00"], "byMonthDay": [8], "byMonth": [3]}
