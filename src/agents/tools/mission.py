"""Mission 里 agent 用的工具（主人 2026-11-23 定）。

只在 Mission 的一步里装配（见 agents/staff.execute_staff）。计划记在这个 Mission 自己的记录里，
不读也不改任何别的数据、不对外发送，和频道的投票卡一样按读操作，不走确认门。
"""
from __future__ import annotations

from src.agents.tool import Tool
from src.agents.trust import READ


def build_mission_tools(root, task):
    from src.workflow.members.mission_runs import MAX_PLAN_ITEMS, PLAN_STATUSES, runs_for

    async def plan(args):
        try:
            version = runs_for(root).set_plan(task.id, args.get('items'))
        except ValueError as error:
            return f'计划没有记下：{error}'
        return f'计划已更新为第 {version} 版，话题里的人都看得到。'

    return [
        Tool('mission_plan', '列出或更新这个 Mission 的整份计划（每次交完整的一份，会替换上一版）。'
             '每项写清要做什么和状态：todo 还没做、doing 正在做、waiting_you 等人处理、done 做完、dropped 不做了。'
             '全部项目都是 done 或 dropped 时，这个 Mission 就交给所有者收尾。',
             {'items': f'计划项列表，1～{MAX_PLAN_ITEMS} 项：[{{"title": "…", "status": "todo", "note": "可选"}}]'},
             plan, read_only=True, operation_kind=READ, required_args=('items',),
             argument_schema={'items': {'type': 'array', 'minItems': 1, 'maxItems': MAX_PLAN_ITEMS, 'items': {
                 'type': 'object', 'properties': {
                     'title': {'type': 'string', 'maxLength': 200},
                     'status': {'type': 'string', 'enum': list(PLAN_STATUSES)},
                     'note': {'type': 'string', 'maxLength': 300}},
                 'required': ['title', 'status']}}}),
    ]
