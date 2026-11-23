"""自定义连接器的工具（登记与调用见 workflow/workbench/custom_connectors）。

只给所有者分类保存过的工具。只读（对方也标了只读）按读；其余一律按不可撤销每次问人。
返回内容是外部不可信内容，摄入即污点。每次调用都重新核对登记，断开、改分类立刻生效。
"""
from __future__ import annotations

import hashlib
import json
import re

PREFIX = "mcp__cc"
MAX_ARGS = 8000             # 审批卡上要看得全：太长就不发，不截断了让人批


def public_name(server_id: str, name: str) -> str:
    """模型看到的名字：只含字母数字和 _-、不超过 64 个字符，不同工具不会撞名。"""
    suffix = hashlib.sha256(f"{server_id}:{name}".encode()).hexdigest()[:8]
    return f"{PREFIX}{server_id[4:12]}__{re.sub(r'[^A-Za-z0-9_-]', '_', name)[:34]}_{suffix}"


def build_custom_connector_tools(root: str, confirm) -> list:
    from src.workflow.workbench import custom_connectors
    return [_build(root, confirm, server, tool) for server, tool in custom_connectors.exposed(root)]


def _build(root, confirm, server, spec):
    from copy import deepcopy
    from src.agents.tool import PreparedToolCall, Tool, ToolResult
    from src.agents.trust import IRREVERSIBLE, READ
    from src.workflow.workbench import custom_connectors

    reads = spec["access"] == "read"
    kind = READ if reads else IRREVERSIBLE

    async def prepare(args):
        from src.agents.principal import current_principal
        if current_principal().is_member:
            return ToolResult("连接器用的是所有者的账号，成员发起的事不能用它。", "blocked")
        text = json.dumps(args, ensure_ascii=False, sort_keys=True, indent=2)
        if len(text) > MAX_ARGS:
            return ToolResult("参数太长，审批卡上看不全，没有调用。", "blocked")
        frozen = deepcopy(args)

        async def invoke():
            from src.agents.mcp_media import render_result
            from src.agents.tool_runtime import check_tool_execution
            try:
                result = await custom_connectors.call(root, server["id"], spec, frozen, check_tool_execution)
            except custom_connectors.ConnectorError as error:
                return ToolResult(f"连接器「{server['label']}」没有完成：{error}", "failed")
            if not result.success:
                body = json.dumps(result.output, ensure_ascii=False)[:4000] if result.output else result.error
                return ToolResult(f"连接器「{server['label']}」报告失败：{body}", "failed")
            return render_result(result.output, result.structured_content)
        from urllib.parse import urlsplit
        message = (f"用连接器「{server['label']}」（{urlsplit(server['serverUrl']).hostname}）"
                   f"调用「{spec['name']}」？\n参数：{text}")
        return PreparedToolCall(invoke, message, kind)

    schema = {"type": "object", **deepcopy(spec["schema"])}
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    hints = {str(name): str((value.get("description") or value.get("type") or "") if isinstance(value, dict) else "")
             for name, value in props.items()}
    return Tool.prepared(public_name(server["id"], spec["name"]),
                         f"[连接器：{server['label']}] {spec['description'] or spec['name']}", hints, prepare,
                         operation_kind=kind, read_only=reads, always_confirm=not reads, confirm=confirm,
                         untrusted_source=True, external_content=True, input_schema=schema)
