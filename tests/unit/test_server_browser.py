"""agent 在服务器本机用浏览器（主人 2026-11-24 定：只读加每步确认、只给所有者发起的事）。浏览器用替身。"""
import asyncio
from types import SimpleNamespace

import pytest

from src.agents.tool import PreparedToolCall
from src.agents.tools import server_browser
from src.agents.tools.website_browser import shutdown_browsers
from src.browser.backend import BrowserBackend
from tests.unit.test_browser_control import _FakeBrowser
from tests.unit.test_member_agents import ORIGIN, _join, home  # noqa: F401 - pytest fixture


@pytest.fixture
def chrome(monkeypatch):
    seen = SimpleNamespace(drivers=[], headless=[])

    def backend(*, headless=False):
        seen.headless.append(headless)

        def create(launch):
            driver = _FakeBrowser()
            seen.drivers.append(driver)
            return driver
        return BrowserBackend("chromium_cdp", 1, frozenset({"desktop"}), create)
    monkeypatch.setattr("src.browser.backend.chromium_backend", backend)
    monkeypatch.setattr("src.browser.cdp.find_browser", lambda: "/usr/bin/google-chrome")
    monkeypatch.setattr("src.agents.web_fetch.refusal_reason", lambda host: "内网地址" if host.startswith("10.") else "")
    monkeypatch.setenv(server_browser.AGENT_BROWSER_ENV, "1")
    monkeypatch.delenv("DISPLAY", raising=False)
    yield seen
    asyncio.run(shutdown_browsers())


def _task(**dispatch):
    return SimpleNamespace(id="task-b", collaboration={"dispatch": {"source": "staff", "member": "s1", **dispatch}})


async def _run(tool, args):
    result = await tool.prepare(args)
    return await result.invoke() if isinstance(result, PreparedToolCall) else result


def test_only_work_the_owner_started_gets_the_browser(chrome, tmp_path):
    tools, close = server_browser.build_server_browser_tools(str(tmp_path), _task())
    assert [tool.name for tool in tools] == list(server_browser.SERVER_TOOLS) and close is not None
    for other in (_task(initiator={"kind": "member", "id": "m1"}), _task(channel={"id": "c", "thread": "t", "message": "m"}),
                  _task(mission="mission-1"), _task(routine={"id": "routine-a"})):
        assert server_browser.build_server_browser_tools(str(tmp_path), other) == ([], None)   # 成员、频道、Mission、例行都不给


def test_the_switch_and_a_browser_are_both_needed(chrome, tmp_path, monkeypatch):
    monkeypatch.setenv(server_browser.AGENT_BROWSER_ENV, "0")
    assert server_browser.build_server_browser_tools(str(tmp_path), _task()) == ([], None)
    monkeypatch.setenv(server_browser.AGENT_BROWSER_ENV, "1")
    monkeypatch.setattr("src.browser.cdp.find_browser", lambda: None)
    assert server_browser.build_server_browser_tools(str(tmp_path), _task()) == ([], None)


def test_reading_is_free_acting_asks_every_time_and_blocked_sites_stay_blocked(chrome, tmp_path):
    async def go():
        tools, close = server_browser.build_server_browser_tools(str(tmp_path), _task())
        by = {tool.name: tool for tool in tools}
        for name in ("browser_open", "browser_snapshot", "browser_screenshot"):
            assert by[name].read_only and by[name].untrusted_source                       # 看：读到的是外部内容
        for name in ("browser_click", "browser_type", "browser_scroll"):
            assert not by[name].read_only and by[name].operation_kind == "interact"       # 动：每步都确认
        assert "受限站点" in await _run(by["browser_open"], {"url": "https://www.paypal.com/"})
        assert "内网" in await _run(by["browser_open"], {"url": "https://10.0.0.5/"})
        assert chrome.drivers == []                                                       # 被拒的没开浏览器
        await _run(by["browser_open"], {"url": "https://example.com/docs"})
        assert chrome.headless == [True] and chrome.drivers[0].url == "https://example.com/docs"
        assert "正文" in str(await _run(by["browser_snapshot"], {}))
        await close()
        assert chrome.drivers[0].running is False                                         # 这一步结束就关
    asyncio.run(go())


def test_the_agent_page_says_whether_the_server_browser_is_on(home, chrome, monkeypatch):
    owner, agent = home["owner"], home["agent"]["id"]
    shown = owner.get(f"{home['base']}/agents/{agent}/browser", headers=ORIGIN).json()
    assert shown["available"] is True and shown["reason"] is None and "每步都要确认" in shown["detail"]
    assert owner.get(f"{home['base']}/agents/{home['main']['id']}/browser", headers=ORIGIN).json()["available"] is False
    monkeypatch.setenv(server_browser.AGENT_BROWSER_ENV, "0")
    off = owner.get(f"{home['base']}/agents/{agent}/browser", headers=ORIGIN).json()
    assert (off["available"], off["reason"]) == (False, "no_machine")


def test_a_staff_run_the_owner_started_gets_it_and_closes_it(home, chrome, monkeypatch):
    import src.agents.staff as staff_module
    from src.workflow.staff.service import StaffService
    from src.workflow.tasks.tasks import TaskLedger, TaskRunner
    seen = {}

    class _Agent:
        execution_report = None

        async def run_turn(self, prompt, mode="build", execution_ref=None):
            tools = {tool.name: tool for tool in seen["tools"]}
            await _run(tools["browser_open"], {"url": "https://example.com/"})
            return "好"

    def build(root, role, member, **options):
        seen["tools"] = list(options.get("extra_tools", ()))
        return _Agent()
    monkeypatch.setattr(staff_module, "build_staff_member", build)

    async def execute(task, work, role, member, check):
        return await staff_module.execute_staff(home["root"], task, work, role, member, check)

    async def go():
        service = StaffService(home["root"], TaskRunner(home["root"], None, max_concurrent=1), execute=execute)
        task = service.assign("owner-session", {"member": home["staff"]["id"], "prompt": "查网页"})
        for _ in range(250):
            if TaskLedger(home["root"]).load(task.id).status not in {"queued", "running"}:
                return TaskLedger(home["root"]).load(task.id)
            await asyncio.sleep(0.02)
    done = asyncio.run(go())
    assert done.status == "done", done.error
    assert "browser_open" in [tool.name for tool in seen["tools"]]
    assert chrome.drivers and chrome.drivers[0].running is False                         # 跑完就关了


@pytest.mark.parametrize("headless", [True, False])
def test_headless_launch_keeps_the_browsers_own_sandbox(tmp_path, monkeypatch, headless):
    from src.browser.cdp import CdpBrowser
    launched = []

    async def capture(*args, **kwargs):
        launched.append(args)
        raise RuntimeError("stop before starting a real browser")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    with pytest.raises(RuntimeError):
        asyncio.run(CdpBrowser("/usr/bin/google-chrome", tmp_path / "profile", headless=headless).start())
    [args] = launched
    assert ("--headless=new" in args) is headless
    assert "--no-sandbox" not in args                                                     # 97 上实测浏览器自带的沙箱可用


def test_limiting_sites_per_agent_is_refused_honestly_either_way(home, chrome, monkeypatch):
    path = f"{home['base']}/agents/{home['agent']['id']}/browser"
    on = home["owner"].put(path, json={"allowedDomains": ["example.com"]}, headers=ORIGIN)
    assert (on.status_code, on.json()["error"]["code"]) == (409, "browser.sites_unavailable")
    monkeypatch.setenv(server_browser.AGENT_BROWSER_ENV, "0")
    off = home["owner"].put(path, json={"allowedDomains": ["example.com"]}, headers=ORIGIN)
    assert off.json()["error"]["code"] == "computer.unavailable"
