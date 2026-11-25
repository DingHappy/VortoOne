"""agent 提议改团队背景，所有者采纳才生效（主人 2026-11-24 定）。"""
import asyncio
import time
from types import SimpleNamespace

from src.agents.taint import mark_tainted, reset_taint
from src.agents.tools.context_proposal import build_context_proposal_tool
from src.memory.assistant_profile import load_home_context
from src.workflow.staff.service import StaffService
from src.workflow.tasks.tasks import TaskLedger, TaskRunner
from tests.unit.test_member_agents import ORIGIN, _join, home  # noqa: F401 - pytest fixture


def _task(staff_id, initiator=None):
    dispatch = {"member": staff_id, **({"initiator": {"kind": "member", "id": initiator}} if initiator else {})}
    return SimpleNamespace(id="task-" + str(time.monotonic_ns()), collaboration={"dispatch": dispatch})


def _propose(home, after, *, initiator=None, title="补上客户名单的位置", tainted=False):
    tool = build_context_proposal_tool(home["root"], _task(home["staff"]["id"], initiator))
    reset_taint()
    if tainted:
        mark_tainted()
    try:
        return asyncio.run(tool.handler({"after": after, "title": title, "why": "有人问了两次"}))
    finally:
        reset_taint()


def _list(client, home, **params):
    answer = client.get(f"{home['base']}/proposals", params=params)
    assert answer.status_code == 200, answer.text
    return answer.json()["proposals"]


def test_an_agent_proposes_and_the_owner_accepts_after_a_tweak(home):
    owner = home["owner"]
    before = load_home_context(home["root"])
    said = _propose(home, "我们是一家做咖啡的小店。客户名单在共享盘的「客户」文件夹。")
    assert "等所有者" in said and load_home_context(home["root"]) == before          # 还没变
    [row] = _list(owner, home)
    assert row["target"] == {"type": "org_context"} and row["status"] == "pending" and row["canDecide"] is True
    assert row["before"] == before and "客户名单" in row["after"] and row["stale"] is False
    assert row["proposedByAgentId"] == home["agent"]["id"] and row["decider"] == {"type": "account_owner"}
    assert len(row["evidence"]) == 1                                                  # 没读过外部内容
    assert _list(owner, home, ids=row["id"]) == [row] and _list(owner, home, ids="prop-none") == []

    accepted = owner.post(f"{home['base']}/proposals/{row['id']}/accept",
                          json={"text": "我们是一家做咖啡的小店。客户名单在共享盘。"}, headers=ORIGIN)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted" and accepted.json()["decidedBy"]["userId"]
    assert load_home_context(home["root"]) == "我们是一家做咖啡的小店。客户名单在共享盘。"
    again = owner.post(f"{home['base']}/proposals/{row['id']}/accept", json={}, headers=ORIGIN)
    assert (again.status_code, again.json()["error"]["code"]) == (409, "proposal.decided")


def test_a_proposal_goes_stale_when_the_context_changes_and_can_be_refused(home):
    owner = home["owner"]
    assert owner.patch(f"{home['base']}/settings", json={"context": "现在的背景"}, headers=ORIGIN).status_code == 200
    assert "一样" in _propose(home, "现在的背景")                                       # 和现在一样的不记
    _propose(home, "第一版")
    _propose(home, "第一版")                                                          # 重复的只记一条
    [row] = _list(owner, home)
    assert owner.patch(f"{home['base']}/settings", json={"context": "所有者自己改了"}, headers=ORIGIN).status_code == 200
    [row] = _list(owner, home)
    assert row["stale"] is True
    stale = owner.post(f"{home['base']}/proposals/{row['id']}/accept", json={}, headers=ORIGIN)
    assert (stale.status_code, stale.json()["error"]["code"]) == (409, "proposal.stale")
    assert load_home_context(home["root"]) == "所有者自己改了"
    refused = owner.post(f"{home['base']}/proposals/{row['id']}/refuse", json={}, headers=ORIGIN)
    assert refused.status_code == 200 and refused.json()["status"] == "refused"


def test_members_see_only_what_they_asked_for_and_can_only_ask(home):
    ana, li = _join(home), _join(home, "li@example.com", "小李")
    _propose(home, "安娜想加的一句", initiator=ana.member_id)
    _propose(home, "所有者自己让加的")
    [mine] = _list(ana, home)
    assert mine["requestedBy"]["userId"] == ana.member_id and mine["canDecide"] is False
    assert _list(li, home) == []
    assert ana.post(f"{home['base']}/proposals/{mine['id']}/accept", json={}).status_code == 403
    asked = ana.post(f"{home['base']}/proposals/{mine['id']}/ask", json={})
    assert asked.status_code == 200 and asked.json()["askedAt"]
    assert li.post(f"{home['base']}/proposals/{mine['id']}/ask", json={}).status_code == 404
    owners = {row["id"]: row for row in _list(home["owner"], home)}
    assert len(owners) == 2 and owners[mine["id"]]["askedAt"] == asked.json()["askedAt"]
    assert home["owner"].post(f"{home['base']}/proposals/{mine['id']}/ask", json={}, headers=ORIGIN).status_code == 409


def test_a_proposal_from_a_turn_that_read_outside_content_says_so(home):
    _propose(home, "频道里有人说要加的", tainted=True)
    [row] = _list(home["owner"], home)
    assert len(row["evidence"]) == 2 and "外部内容" in row["evidence"][1]["what"]


def test_every_staff_agent_gets_the_tool(home, monkeypatch):
    import src.agents.staff as staff_module
    seen = {}

    class _Agent:
        execution_report = None

        async def run_turn(self, prompt, mode="build", execution_ref=None):
            return "好"

    def build(root, role, member, **options):
        seen["tools"] = [tool.name for tool in options.get("extra_tools", ())]
        return _Agent()
    monkeypatch.setattr(staff_module, "build_staff_member", build)

    async def execute(task, work, role, member, check):
        return await staff_module.execute_staff(home["root"], task, work, role, member, check)

    async def go():
        service = StaffService(home["root"], TaskRunner(home["root"], None, max_concurrent=1), execute=execute)
        task = service.assign("owner-session", {"member": home["staff"]["id"], "prompt": "看看"})
        for _ in range(200):
            if TaskLedger(home["root"]).load(task.id).status not in {"queued", "running"}:
                break
            await asyncio.sleep(0.02)
    asyncio.run(go())
    assert "propose_team_context" in seen["tools"]
