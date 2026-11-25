"""Wiki 开放给 agent 读（主人 2026-11-24 定：成员写的内容按资料处理）。

只读得到对全体成员公开、开放给了它的页面；读到的内容标成外部内容。
"""
import asyncio

from src.web.wiki_agent_tools import build_wiki_tools
from src.workflow import wiki
from src.workflow.staff.service import StaffService
from src.workflow.tasks.tasks import TaskLedger, TaskRunner
from tests.unit.test_app_wiki import _page
from tests.unit.test_member_agents import ORIGIN, _join, home  # noqa: F401 - pytest fixture


def _open(home, page, agents):
    answer = home["owner"].put(f"{home['base']}/wiki/bulk/agents", json={"ids": [page["id"]], "agentIds": agents},
                               headers=ORIGIN)
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_pages_open_to_chosen_agents_and_close_again(home):
    owner, base, agent = home["owner"], home["base"], home["agent"]["id"]
    page = _page(owner, base, title="报价规则", body="大单打九折")
    assert page["agentIds"] == [] and wiki.agent_pages(home["root"], agent) == []          # 默认谁都不开放
    assert _open(home, page, [agent]) == {"changed": 1, "failed": []}
    assert owner.get(f"{base}/wiki/pages/{page['slug']}").json()["agentIds"] == [agent]
    [seen] = wiki.agent_pages(home["root"], agent)
    assert seen["title"] == "报价规则" and wiki.agent_page(home["root"], agent, page["slug"])["body"] == "大单打九折"
    assert wiki.agent_pages(home["root"], "another-agent") == []
    assert _open(home, page, None)["changed"] == 1                                        # null：每个 agent
    assert wiki.agent_pages(home["root"], "another-agent")[0]["slug"] == page["slug"]
    assert wiki.agent_pages(home["root"], agent, query="九折") and not wiki.agent_pages(home["root"], agent, query="退款")
    assert _open(home, page, [])["changed"] == 1
    assert wiki.agent_page(home["root"], agent, page["slug"]) is None


def test_narrowing_who_sees_a_page_closes_it_to_agents_too(home):
    owner, base, agent = home["owner"], home["base"], home["agent"]["id"]
    page = _page(owner, base, title="薪资表", body="保密")
    _open(home, page, None)
    owner_id = owner.get("/api/app/me").json()["user"]["id"]
    narrowed = owner.put(f"{base}/wiki/pages/{page['slug']}/audience",
                          json={"audience": {"roleIds": [], "userIds": [owner_id]}}, headers=ORIGIN)
    assert narrowed.status_code == 200, narrowed.text
    assert wiki.agent_pages(home["root"], agent) == []                                    # 只给部分人看了：agent 读不到
    deleted = _page(owner, base, title="旧流程", body="作废")
    _open(home, deleted, None)
    assert owner.post(f"{base}/wiki/bulk/delete", json={"ids": [deleted["id"]]}, headers=ORIGIN).status_code == 200
    assert wiki.agent_pages(home["root"], agent) == []


def test_members_without_wiki_rights_cannot_open_pages(home):
    member = _join(home)
    page = _page(home["owner"], home["base"])
    from src.workflow.members.registry import MemberRegistry
    from tests.unit.test_member_agents import OWNER_P
    MemberRegistry(home["root"]).change_group(OWNER_P, "everyone", items=[])
    answer = member.put(f"{home['base']}/wiki/bulk/agents", json={"ids": [page["id"]], "agentIds": None})
    assert answer.status_code == 403


def test_a_staff_agent_reads_open_pages_as_outside_material(home, monkeypatch):
    import src.agents.staff as staff_module
    page = _page(home["owner"], home["base"], title="报价规则", body="大单打九折")
    closed = _page(home["owner"], home["base"], title="内部备忘", body="不给 agent")
    _open(home, page, [home["agent"]["id"]])
    seen = {}

    class _Agent:
        execution_report = None

        async def run_turn(self, prompt, mode="build", execution_ref=None):
            tools = {tool.name: tool for tool in seen["tools"]}
            seen["search"] = await tools["wiki_search"].handler({"query": "报价"})
            seen["read"] = await tools["wiki_read"].handler({"slug": page["slug"]})
            seen["closed"] = await tools["wiki_read"].handler({"slug": closed["slug"]})
            return "好"

    def build(root, role, member, **options):
        seen["tools"] = list(options.get("extra_tools", ()))
        return _Agent()
    monkeypatch.setattr(staff_module, "build_staff_member", build)
    monkeypatch.setattr(staff_module, "_WIKI_TOOLS", build_wiki_tools)   # 结束时还原成没登记

    async def execute(task, work, role, member, check):
        return await staff_module.execute_staff(home["root"], task, work, role, member, check)

    async def go():
        service = StaffService(home["root"], TaskRunner(home["root"], None, max_concurrent=1), execute=execute)
        task = service.assign("owner-session", {"member": home["staff"]["id"], "prompt": "查报价"})
        for _ in range(250):
            if TaskLedger(home["root"]).load(task.id).status not in {"queued", "running"}:
                return TaskLedger(home["root"]).load(task.id)
            await asyncio.sleep(0.02)
    done = asyncio.run(go())
    assert done.status == "done", done.error
    assert "报价规则" in seen["search"] and seen["search"].untrusted is True
    assert "大单打九折" in seen["read"] and seen["read"].untrusted is True
    assert "没有开放给你" in seen["closed"] and "不给 agent" not in seen["closed"]


def test_the_wiki_tools_are_only_there_once_registered(monkeypatch):
    import src.agents.staff as staff_module
    assert staff_module._WIKI_TOOLS is None                                 # 没登记（不在常驻助理里）就没有这两样
    staff_module.set_wiki_tools(build_wiki_tools)
    try:
        assert staff_module._WIKI_TOOLS is build_wiki_tools
    finally:
        staff_module.set_wiki_tools(None)
