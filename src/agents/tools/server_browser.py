"""agent 在服务器本机用浏览器（主人 2026-11-24 定：只给所有者发起的事；同日又定放宽三条，见 ServerSitePolicy）。

服务器上的家没有连接的电脑时，派给 agent 的事也能看网页：在这台服务器上无头地开一个独立浏览器。
- 只在**所有者自己发起的事**里给（私聊、所有者派的活）：成员发起的、频道里的（含 Mission）不给；
  例行工作也不给——它按无人值守档不联网（2026-11-22 定）。
- 看：打开公开网页、读页面、截图，不用确认；读到的内容是外部内容，这一回合是污点态。
- 动：普通链接、展开这类点击和滚动不用确认；输入文字、表单里的、「提交 / 购买 / 支付 / 删除 / 发送」这类按钮
  每次都要人确认（interact 类，任何档位都不免）。
- 邮箱网页版、云控制台只读（能打开、读、截图，不点击不输入）；银行支付、账号中心、密码管理器一律不碰。
- 内网地址照旧拒绝，只放行 ``VORTOONE_AGENT_BROWSER_INTRANET`` 里列出的主机（默认空）。
- 不登录、不登记网站：用一次性的临时配置目录（任务结束就删），没有 Cookie 留下；服务器上没有屏幕，
  遇到登录或敏感表单只能停下。银行、支付、邮箱、账号中心、云控制台和内网地址照旧拒绝（tools/browser.py）。
- 开关：服务环境里 ``VORTOONE_AGENT_BROWSER=1``，并且找得到 Chromium；没有显示器（``DISPLAY`` 为空）时无头运行。
"""
from __future__ import annotations

import ipaddress
import os
import re
from urllib.parse import urlparse

AGENT_BROWSER_ENV = "VORTOONE_AGENT_BROWSER"
INTRANET_ENV = "VORTOONE_AGENT_BROWSER_INTRANET"
_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*")
# 点了会改东西的按钮：不管在不在表单里，都要确认
_RISKY = re.compile(r"删除|移除|注销|购买|下单|支付|付款|提交|确认|发送|发布|转账|订阅|退订|保存|授权|"
                    r"delete|remove|buy|purchase|order|pay|checkout|submit|confirm|send|publish|transfer|"
                    r"subscribe|unsubscribe|save|authori[sz]e|sign ?out|log ?out", re.IGNORECASE)


def _matches(host: str, domains) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def _intranet_hosts() -> frozenset:
    """服务环境里列出的内网主机（主机名或 IP，逗号分隔）。写错的忽略。"""
    found = set()
    for item in (os.getenv(INTRANET_ENV) or "").split(","):
        host = item.strip().lower().rstrip(".")
        if not host:
            continue
        try:
            found.add(str(ipaddress.ip_address(host)))
        except ValueError:
            if _HOST.fullmatch(host):
                found.add(host)
    return frozenset(found)


class ServerSitePolicy:
    """服务器上给 agent 的浏览器能碰哪些站点、哪些点击不用确认（主人 2026-11-24 定放宽三条）。"""
    free_scroll = True

    def __init__(self, intranet=frozenset()):
        self.intranet = frozenset(intranet)

    def reason(self, url: str, action: str = "read") -> str:
        from src.agents.tools import browser
        try:
            parsed = urlparse(url)
        except ValueError:
            return "地址无法解析"
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in ("http", "https") or not host or parsed.username or parsed.password:
            return browser.restricted_reason(url)            # 原样的拒绝理由
        always = browser.PAYMENT_DOMAINS + browser.ACCOUNT_DOMAINS + browser._blocked_domains()[len(browser.BLOCKED_DOMAINS):]
        if _matches(host, always) or any(word in host for word in browser._KEYWORDS):
            return browser.restricted_reason(url) or f"{host} 属于受限站点，浏览器操控不会碰它"
        if _matches(host, browser.MAIL_DOMAINS + browser.CLOUD_CONSOLE_DOMAINS):
            return "" if action == "read" else f"{host} 是邮箱或云控制台：在服务器上只能读，不点击、不输入"
        if host in self.intranet:
            return ""                                        # 主人列出的内网主机
        return browser.restricted_reason(url)

    @staticmethod
    def free_click(item: dict) -> bool:
        """普通链接、展开这类点击不用确认；表单里的、能输入的、会改东西的按钮照旧确认。"""
        if not isinstance(item, dict) or item.get("inForm") is not False or item.get("editable"):
            return False
        if _RISKY.search(str(item.get("label") or "")):
            return False
        tag, kind = item.get("tag"), item.get("type") or ""
        return tag in ("a", "summary") or (tag == "button" and kind in ("", "button"))
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
    tools = [tool for tool in _session_tools(root, holder, None, site_policy=ServerSitePolicy(_intranet_hosts()))
             if tool.name in SERVER_TOOLS]

    async def close():
        await holder.close(shutdown=True)
    return tools, close
