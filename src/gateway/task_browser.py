"""Local Desktop opt-in for a dedicated browser in one owner turn."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import stat
from typing import TYPE_CHECKING

from src.gateway.computer_browser_preview import browser_previews

if TYPE_CHECKING:
    from src.gateway.computer_file_authority import OwnerTurn

_MARKER = "task-browser.enabled"
_native_home: Path | None = None


def register_native_home(root) -> None:
    """Called after the app's private startup frame has been validated."""
    global _native_home
    _native_home = Path(root).resolve()


def _revision(path: Path) -> tuple[int, int, int] | None:
    try:
        info = path.lstat()
        owner = path.parent.stat().st_uid
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_size or info.st_uid != owner:
        return None
    return info.st_dev, info.st_ino, info.st_ctime_ns


@dataclass(frozen=True)
class TaskBrowserGrant:
    marker: Path
    revision: tuple[int, int, int]
    owner_epoch: object
    desktop_directory: Path | None = None

    def current(self) -> bool:
        return (self.owner_epoch is browser_previews.owner_epoch()
                and _revision(self.marker) == self.revision)


def task_browser_grant(root: str) -> TaskBrowserGrant | None:
    from src.gateway.workspace_scope import GENERAL, current_workspace_scope
    if (_native_home is None or Path(root).resolve() != _native_home
            or current_workspace_scope() != GENERAL):
        return None
    marker = _native_home.parent / _MARKER
    revision = _revision(marker)
    if revision is None:
        return None
    # The trusted native launcher puts the backend in app-data/home and
    # the connector in its sibling computer-connections directory.
    desktop = (_native_home.with_name("computer-connections")
               if os.name == "posix" and _native_home.name == "home" else None)
    return TaskBrowserGrant(marker, revision, browser_previews.owner_epoch(), desktop)


@asynccontextmanager
async def task_browser_tools(agent, owner: OwnerTurn | None):
    gate = getattr(agent, "confirm_gate", None)
    if owner is None or (owner.task_browser is None and owner.paired_browser_epoch is None) or gate is None:
        yield
        return

    tools = []
    close = None
    grant = owner.task_browser
    if grant is not None and grant.current() and "browser_open" not in agent.tools:
        from src.agents.tools.website_browser import build_task_browser
        tools, close = build_task_browser(owner.root, gate, grant.current, desktop_directory=grant.desktop_directory)
    if (owner.file_identity is not None and owner.paired_browser_epoch is browser_previews.owner_epoch()
            and "browser_paired_handoff" not in agent.tools):
        from src.gateway.computer_connections import connections
        from src.gateway.connected_computers import ConnectedComputers, ComputerUnavailable

        try:
            devices = await asyncio.to_thread(ConnectedComputers(owner.root).list)
        except (ComputerUnavailable, OSError):
            devices = []
        if any(row["active"] and connections.online(owner.root, row["connector_id"]) for row in devices):
            tools.append(_paired_handoff_tool(owner, gate, owner.file_identity))
    agent.add_tools(tools)
    try:
        yield
    finally:
        agent.remove_tools(tool.name for tool in tools)
        if close is not None:
            await close()


def _paired_handoff_tool(owner: OwnerTurn, confirm, identity: tuple[str, str, str]):
    from src.agents.confirmation_context import presenting_website
    from src.agents.tool import PreparedToolCall, Tool
    from src.agents.tools.browser import restricted_reason
    from src.agents.trust import INTERACT
    from src.browser.connections import site_origin
    from src.gateway.computer_protocol import origin_url
    from src.gateway.paired_browser_task import PairedBrowserTask

    asked = False

    async def prepare(args):
        nonlocal asked
        if asked or browser_previews.owner_epoch() is not owner.paired_browser_epoch:
            return "此回合已经请求过人工核对，或所有者会话已失效。"
        try:
            origin = site_origin(origin_url(args.get("origin")))
            if await asyncio.to_thread(restricted_reason, origin):
                return "请使用可核对的公开 HTTPS 站点源地址。"
        except ValueError:
            return "请使用可核对的公开 HTTPS 站点源地址。"
        asked = True
        task = PairedBrowserTask(owner.root, origin, owner.paired_browser_epoch, identity)
        details = {"version": 1, "action": "login", "origin": origin,
                   "account_label": "本回合的专用窗口", "device_mode": "desktop"}

        async def finish():
            return f"所有者已确认继续，配对电脑的专用窗口已通过 {origin} 的站点核对。"

        return PreparedToolCall(
            finish, f"请在一台配对电脑上人工打开并核对 {origin}。在接管卡选电脑，在那台电脑上打开专用窗口。",
            INTERACT, denied_result="所有者未完成配对电脑的网页核对。",
            approval_context=lambda: presenting_website(details, paired=task),
        )

    return Tool.prepared(
        "browser_paired_handoff", "仅当所有者要求在配对电脑上人工处理或核对网站时使用。"
        "等待用户选择电脑、在专用窗口完成操作并确认。请提供精确的 HTTPS 站点源地址。",
        {"origin": "公开 HTTPS 源地址，例如 https://example.com"}, prepare,
        operation_kind=INTERACT, required_args=("origin",), serial=True, confirm=confirm,
    )
