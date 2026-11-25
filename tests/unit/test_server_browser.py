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
    assert ("--password-store=basic" in args) is headless                                 # 没桌面时不等系统钥匙串
    assert "--no-sandbox" not in args                                                     # 97 上实测浏览器自带的沙箱可用


def test_limiting_sites_per_agent_is_refused_honestly_either_way(home, chrome, monkeypatch):
    path = f"{home['base']}/agents/{home['agent']['id']}/browser"
    on = home["owner"].put(path, json={"allowedDomains": ["example.com"]}, headers=ORIGIN)
    assert (on.status_code, on.json()["error"]["code"]) == (409, "browser.sites_unavailable")
    monkeypatch.setenv(server_browser.AGENT_BROWSER_ENV, "0")
    off = home["owner"].put(path, json={"allowedDomains": ["example.com"]}, headers=ORIGIN)
    assert off.json()["error"]["code"] == "computer.unavailable"


# —— 主人 2026-11-24 定的三条放宽：普通点击免确认、邮箱和云控制台只读、指定的内网地址 ——

def test_which_sites_can_be_read_or_acted_on(chrome, monkeypatch):
    monkeypatch.setenv("VORTOONE_BROWSER_BLOCKED_DOMAINS", "secret.example")
    policy = server_browser.ServerSitePolicy({"10.0.0.5"})
    for url in ("https://www.paypal.com/", "https://accounts.google.com/", "https://1password.com/",
                "https://mybank.example/", "https://secret.example/"):
        assert policy.reason(url, "read") and policy.reason(url, "act"), url              # 钱和登录：一律不碰
    for url in ("https://mail.qq.com/", "https://console.aliyun.com/home"):
        assert policy.reason(url, "read") == "" and "只能读" in policy.reason(url, "act"), url
    assert policy.reason("https://10.0.0.5:8443/status", "act") == ""                     # 主人列出的内网主机
    assert "内网" in policy.reason("https://10.0.0.6/", "read")
    assert policy.reason("https://example.com/", "act") == ""
    assert policy.reason("ftp://example.com/", "read") and policy.reason("https://u:p@example.com/", "read")


@pytest.mark.parametrize(("item", "free"), [
    ({"tag": "a", "type": "", "label": "下一页", "editable": False, "inForm": False}, True),
    ({"tag": "button", "type": "", "label": "展开全文", "editable": False, "inForm": False}, True),
    ({"tag": "summary", "type": "", "label": "更多", "editable": False, "inForm": False}, True),
    ({"tag": "button", "type": "", "label": "展开", "editable": False, "inForm": True}, False),   # 表单里的
    ({"tag": "button", "type": "submit", "label": "好", "editable": False, "inForm": False}, False),
    ({"tag": "a", "type": "", "label": "删除这条", "editable": False, "inForm": False}, False),     # 会改东西的
    ({"tag": "button", "type": "", "label": "Buy now", "editable": False, "inForm": False}, False),
    ({"tag": "input", "type": "text", "label": "搜索", "editable": True, "inForm": False}, False),
    ({"tag": "a", "type": "", "label": "下一页", "editable": False}, False),                       # 不知道在不在表单里
])
def test_which_clicks_do_not_ask(item, free):
    assert server_browser.ServerSitePolicy.free_click(item) is free


def test_the_intranet_list_comes_from_the_service_environment(monkeypatch):
    monkeypatch.setenv(server_browser.INTRANET_ENV, " 10.0.0.5, NAS.home ,bad host!, ,::1")
    assert server_browser._intranet_hosts() == frozenset({"10.0.0.5", "nas.home", "::1"})
    monkeypatch.delenv(server_browser.INTRANET_ENV)
    assert server_browser._intranet_hosts() == frozenset()


def test_in_the_tools_plain_clicks_read_and_risky_ones_ask(chrome, tmp_path, monkeypatch):
    monkeypatch.setenv(server_browser.INTRANET_ENV, "10.0.0.5")

    async def go():
        tools, close = server_browser.build_server_browser_tools(str(tmp_path), _task())
        by = {tool.name: tool for tool in tools}
        await _run(by["browser_open"], {"url": "https://example.com/"})
        driver = chrome.drivers[0]
        driver.element = {"x": 1, "y": 2, "tag": "a", "type": "", "label": "下一页", "editable": False, "inForm": False}
        plain = await by["browser_click"].prepare({"ref": "1"})
        assert isinstance(plain, PreparedToolCall) and plain.operation_kind == "read"     # 不用确认
        driver.element = {**driver.element, "tag": "button", "label": "提交订单"}
        risky = await by["browser_click"].prepare({"ref": "1"})
        assert risky.operation_kind == "interact"                                         # 照旧确认
        driver.element = {"x": 1, "y": 2, "tag": "input", "type": "text", "label": "搜索", "editable": True,
                          "inForm": False}
        typed = await by["browser_type"].prepare({"ref": "1", "text": "你好"})
        assert typed.operation_kind == "interact"
        assert (await by["browser_scroll"].prepare({"direction": "down", "amount": 300})).operation_kind == "read"
        await close()

        tools, close = server_browser.build_server_browser_tools(str(tmp_path), _task())
        by = {tool.name: tool for tool in tools}
        await _run(by["browser_open"], {"url": "https://mail.qq.com/"})                     # 邮箱：能打开、能读
        assert "正文" in str(await _run(by["browser_snapshot"], {}))
        assert "只能读" in str(await by["browser_click"].prepare({"ref": "1"}))             # 不能点
        await close()

        tools, close = server_browser.build_server_browser_tools(str(tmp_path), _task())
        by = {tool.name: tool for tool in tools}
        await _run(by["browser_open"], {"url": "https://10.0.0.5/"})                        # 列出的内网主机
        assert chrome.drivers[-1].url == "https://10.0.0.5/"
        await close()
    asyncio.run(go())


def test_listing_a_bank_as_intranet_does_not_open_it(chrome):
    # 内网名单只放行内网主机，不能拿来绕开银行支付、账号与密码管理
    policy = server_browser.ServerSitePolicy({"mybank.lan", "accounts.google.com"})
    assert policy.reason("https://mybank.lan/", "read") and policy.reason("https://accounts.google.com/", "read")
