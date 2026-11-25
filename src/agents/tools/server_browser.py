"""agent 在服务器本机用浏览器（主人 2026-11-24 定：只读加每步确认、只给所有者发起的事）。

服务器上的家没有连接的电脑时，派给 agent 的事也能看网页：在这台服务器上无头地开一个独立浏览器。
- 只在**所有者自己发起的事**里给（私聊、所有者派的活）：成员发起的、频道里的（含 Mission）不给；
  例行工作也不给——它按无人值守档不联网（2026-11-22 定）。
- 看：打开公开网页、读页面、截图，不用确认；读到的内容是外部内容，这一回合是污点态。
- 动：点击、输入、滚动，**每一步都要人确认**（interact 类，任何档位都不免）。
- 不登录、不登记网站：用一次性的临时配置目录（任务结束就删），没有 Cookie 留下；服务器上没有屏幕，
  遇到登录或敏感表单只能停下。银行、支付、邮箱、账号中心、云控制台和内网地址照旧拒绝（tools/browser.py）。
- 开关：服务环境里 ``VORTOONE_AGENT_BROWSER=1``，并且找得到 Chromium；没有显示器（``DISPLAY`` 为空）时无头运行。
"""
from __future__ import annotations

import os

AGENT_BROWSER_ENV = "VORTOONE_AGENT_BROWSER"
# 服务器上给的几样：看三样，动三样（每步确认）。登记网站、登录、关闭（任务结束自动关）不给。
SERVER_TOOLS = ("browser_open", "browser_snapshot", "browser_screenshot", "browser_click", "browser_type",
                "browser_scroll")


def server_browser_enabled() -> bool:
    if os.getenv(AGENT_BROWSER_ENV) != "1":
        return False
    from src.browser.cdp import find_browser
    return find_browser() is not None


def eligible(task) -> bool:
    from src.agents.tools.team_mail import owner_started
    return owner_started(task) and not (task.collaboration.get("dispatch") or {}).get("routine")


def build_server_browser_tools(root, task):
    """（工具, 收尾）。不给的时候是 ([], None)。收尾在这一步结束时关掉浏览器、删临时目录。"""
    if not eligible(task) or not server_browser_enabled():
        return [], None
    from src.agents.tools.website_browser import BrowserSession, _session_tools
    from src.browser.backend import chromium_backend
    from src.browser.connections import BrowserConnections

    holder = BrowserSession(BrowserConnections(root), chromium_backend(headless=not os.getenv("DISPLAY")))
    tools = [tool for tool in _session_tools(root, holder, None) if tool.name in SERVER_TOOLS]

    async def close():
        await holder.close(shutdown=True)
    return tools, close
