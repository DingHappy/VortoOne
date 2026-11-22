"""助理提的开通单（新 agent 连同例行工作）以团队卡片出现在主要聊天里；「创建」按这一版批准整张开通单。"""
from src.web import app_views as views
from src.workflow.staff.approvals import StaffApprovals
from src.workflow.staff.registry import StaffRegistry
from src.workflow.staff.routines import Routines
from tests.unit.test_agent_proposal_cards import _cards
from tests.unit.test_member_agents import _join, home  # noqa: F401 - pytest fixture

BUNDLE = {"member": {"id": "staff-brief", "name": "晨报员", "role": "reader", "engine": "vortocode"},
          "routines": [{"id": "routine-morning", "name": "晨报", "member": "staff-brief",
                        "prompt": "整理昨天的进展", "schedule": "0 9 * * *", "timezone": "UTC"}]}


def _propose(home):
    return StaffApprovals(home["root"]).propose("sid-owner", "bundle", BUNDLE, "主人要每天一份晨报",
                                                origin="assistant")


def test_the_team_card_shows_what_will_be_registered_and_creates_it(home):
    owner, base = home["owner"], home["base"]
    proposal = _propose(home)
    message, = _cards(owner, home)
    card = message["meta"]["actionCard"]
    assert message["id"] == views.team_proposal_card_id(proposal["id"]) and "1 项例行工作" in message["body"]
    assert card["kind"] == "team_proposal" and card["state"] == "pending"
    agent, = card["params"]["agents"]
    assert agent["name"] == "晨报员" and agent["handle"] == "staff-brief"
    assert [(r["name"], r["prompt"], r["cron"]) for r in agent["routines"]] == [("晨报", "整理昨天的进展", "0 9 * * *")]

    route = f"{base}/team-proposals/{message['id']}"
    assert owner.patch(f"{route}/agents/staff-brief", json={"name": "改名"}).status_code == 409
    made = owner.post(f"{route}/create", json={})
    assert made.status_code == 200, made.text
    done = made.json()["card"]
    assert done["state"] == "created" and done["created"]["agentIds"] == [views.member_agent_id(home["root"], "staff-brief")]
    assert StaffRegistry(home["root"]).member("staff-brief")["name"] == "晨报员"
    assert [r["id"] for r in Routines(home["root"]).list()] == ["routine-morning"]
    assert owner.post(f"{route}/create", json={}).status_code == 409
    assert owner.post(f"{route}/undo", json={}).status_code == 409
    assert _cards(owner, home)[0]["meta"]["actionCard"]["state"] == "created"


def test_deny_and_access(home):
    owner, base = home["owner"], home["base"]
    _propose(home)
    message, = _cards(owner, home)
    route = f"{base}/team-proposals/{message['id']}"
    assert _join(home).post(f"{route}/create", json={}).status_code == 403
    # 团队卡片的 id 不能拿去当新建 agent 卡片用，反过来也一样
    assert owner.get(f"{base}/agent-proposals/{message['id']}").status_code == 404
    denied = owner.post(f"{route}/deny", json={})
    assert denied.status_code == 200 and denied.json()["card"]["state"] == "denied"
    assert "staff-brief" not in {m["id"] for m in StaffRegistry(home["root"]).members()}
    assert Routines(home["root"]).list() == []
    assert owner.post(f"{base}/team-proposals/not-a-card/create", json={}).status_code == 404
