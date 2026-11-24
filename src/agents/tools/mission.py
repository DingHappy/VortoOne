"""Mission 里 agent 用的工具（主人 2026-11-23 定）：计划、请人拍板、交付验收。

只在 Mission 的一步里装配（见 agents/staff.execute_staff）。都记在这个 Mission 自己的记录里，
不读也不改任何别的数据、不对外发送，和频道的投票卡一样按读操作，不走确认门。
"""
from __future__ import annotations

from src.agents.tool import Tool
from src.agents.trust import READ


def build_mission_tools(root, task):
    from src.workflow.members.mission_runs import MAX_CHECKLIST, MAX_PLAN_ITEMS, PLAN_STATUSES, cards_for, runs_for

    async def decision(args):
        try:
            status = cards_for(root).propose_decision(task.id, args)
        except ValueError as error:
            return f'决定没有提出：{error}'
        if status == 'provisional':
            return '已记下，会跟着这一步的回答发在话题里。你可以先按建议接着做；有人改选时下一步会告诉你。'
        return '已记下，会跟着这一步的回答发在话题里。这一步结束后 Mission 会停下，等有人拍板再接着做。'

    async def deliver(args):
        try:
            cards_for(root).propose_delivery(task.id, args)
        except ValueError as error:
            return f'交付没有提出：{error}'
        return '已记下，会跟着这一步的回答发在话题里。这一步结束后等所有者验收：完成，或者提出修改再接着做。'

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
        Tool('mission_decision', '请话题里的人拍板一个问题（2～6 个选项）。blocking 默认 true：这一步后停下等人选；'
             '不急的问题设 false 并给出 recommendation，你就先按建议做，有人改选时下一步会告诉你。一步最多提 3 张卡。',
             {'question': '问题，300 字以内', 'options': '2～6 个选项', 'recommendation': '可选：你建议哪个（要是选项之一）',
              'why': '可选：为什么这么建议', 'impact': '可选：选了会有什么影响', 'blocking': '可选：要不要停下等人，默认 true'},
             decision, read_only=True, operation_kind=READ, required_args=('question', 'options'),
             argument_schema={'question': {'type': 'string', 'maxLength': 300},
                              'options': {'type': 'array', 'minItems': 2, 'maxItems': 6,
                                          'items': {'type': 'string', 'maxLength': 120}},
                              'recommendation': {'type': 'string', 'maxLength': 120},
                              'why': {'type': 'string', 'maxLength': 600}, 'impact': {'type': 'string', 'maxLength': 600},
                              'blocking': {'type': 'boolean'}}),
        Tool('mission_deliver', '做完了：交给所有者验收。写清交付了什么，可以列几条让人逐条核对的检查项。'
             '这一步结束后等验收：完成就收尾，提出修改就带着意见接着做。',
             {'summary': '交付说明，4000 字以内', 'checklist': f'可选：检查项，最多 {MAX_CHECKLIST} 条，每条 200 字以内'},
             deliver, read_only=True, operation_kind=READ, required_args=('summary',),
             argument_schema={'summary': {'type': 'string', 'maxLength': 4000},
                              'checklist': {'type': 'array', 'maxItems': MAX_CHECKLIST,
                                            'items': {'type': 'string', 'maxLength': 200}}}),
    ]
