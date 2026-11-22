"""完整能力的 agent 用的工具：和主要聊天里的主助理同一份装配（主人 2026-11-22 定）。

装配参数照抄 ``routers/realtime.py`` 给主要聊天建会话的那几项——工作区范围、能力档案、授权档位、
本机文件读取、连接的电脑——所以主助理在这台服务上能用什么，agent 就能用什么，规则也一样：
确认门、污点和档位都在内核里判定。不一样的只有「问谁」：agent 在后台干活，确认改走任务的审批卡。
"""
from __future__ import annotations

import os

# 主助理会话自己带的两样（绑在那个临时会话对象上），agent 自己的会话另有
_SESSION_OWN = frozenset({"update_plan", "request_build"})


def main_assistant_toolset(root: str, confirm):
    from src.gateway.agent_session import build_session
    from src.gateway.trust_setting import get_trust_level
    from src.gateway.workspace_scope import GENERAL, current_workspace_scope
    from src.web.auth import get_api_token

    scope = current_workspace_scope()
    sidecar = os.getenv("VORTOONE_DESKTOP_SIDECAR") == "1"
    desktop_trusted = sidecar and scope != GENERAL
    profile = "local" if desktop_trusted else "external"

    def trust_level():
        return get_trust_level(root)

    agent = build_session(root, kind="web", confirm=confirm, can_ask_human=True,
                          capability_profile=profile, trust_level=trust_level, workspace_scope=scope,
                          allow_local_files=sidecar, task_owner=None)
    tools = [tool for tool in agent.tools.values() if tool.name not in _SESSION_OWN]
    if not desktop_trusted and get_api_token():
        from src.gateway.computer_file_tools import computer_file_tools
        tools += computer_file_tools(root)
    return tools, trust_level, profile
