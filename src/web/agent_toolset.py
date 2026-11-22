"""完整能力的 agent 用的工具：和主要聊天里的主助理同一份装配（主人 2026-11-22 定，ADR-021）。

装配参数照抄 ``routers/realtime.py`` 给主要聊天建会话的那几项——工作区范围、能力档案、授权档位、
本机文件读取、项目 Hook、Desktop 监管、审计：规则都在内核里判定。不一样的只有两点：

- 「问谁」：agent 在后台干活，确认改走任务的审批卡；
- 连接的电脑的文件工具不给：它们的授权是「主人在主要聊天里的这一回合」，确认要更强的主人证明
  （HTTPS 或回环），任务审批卡给不了这两样。
"""
from __future__ import annotations

import os

# 主助理会话自己带的两样（绑在那个临时会话对象上），agent 自己的会话另有
_SESSION_OWN = frozenset({"update_plan", "request_build"})
AUDIT_SESSION = "staff:"


def main_assistant_toolset(root: str, confirm, agent_id: str):
    from src.gateway import agent_session
    from src.gateway.trust_setting import get_trust_level
    from src.gateway.workspace_scope import GENERAL, current_workspace_scope

    scope = current_workspace_scope()
    sidecar = os.getenv("VORTOONE_DESKTOP_SIDECAR") == "1"
    desktop_trusted = sidecar and scope != GENERAL
    profile = "local" if desktop_trusted else "external"
    audit = {"session": AUDIT_SESSION + agent_id, "mode": "build"}

    def trust_level():
        chosen = get_trust_level(root)
        if sidecar:
            from src.agents.trust import resolve
            return resolve(chosen, "external")          # 同 build_session：Desktop 监管下写、执行、外发不能预授权
        return chosen

    def on_tool(name, args, result):
        from src.gateway.audit import record_tool_audit
        record_tool_audit(root, **audit, name=name, args=args, result=result)

    def on_decision(operation, decision, tainted):
        from src.agents.confirmation_context import approval_context
        from src.gateway.audit import record_decision_audit
        if (approval_context() or {}).get("decision_source") == "user":
            return                                      # 人在审批卡上的决定由审批卡自己记（staff/actions）
        record_decision_audit(root, **audit, operation=operation, decision=decision, tainted=tainted)

    agent = agent_session.build_session(root, kind="web", confirm=confirm, can_ask_human=True,
                                        capability_profile=profile, trust_level=trust_level, workspace_scope=scope,
                                        allow_local_files=sidecar, task_owner=None, on_decision=on_decision)
    tools = [tool for tool in agent.tools.values() if tool.name not in _SESSION_OWN]
    session = {"hook_system": agent_session.load_project_hook_system(root), "supervised": sidecar,
               "on_tool": on_tool, "on_decision": on_decision}
    return tools, trust_level, profile, session
