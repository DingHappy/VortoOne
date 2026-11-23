"""频道里的 agent 能发的投票和选择卡（主人 2026-11-22 定）。

只在团队频道交给它的事里装配（见 agents/staff.execute_staff）。卡片先记下，跟着这件事的回答一起发在话题里；
它不读也不改任何别的数据、不对外发送，和 ask_person 一样按读操作，不走确认门。
"""
from __future__ import annotations

from src.agents.tool import Tool
from src.agents.trust import READ


def build_channel_card_tools(root, task):
    from src.workflow.members.channel_cards import ChannelCards
    from src.workflow.members.registry import MemberRegistry
    where = task.collaboration['dispatch']['channel']
    staff = task.collaboration['dispatch']['member']
    cards = ChannelCards(MemberRegistry(root).directory)

    def create(kind, content, hours=None):
        try:
            cards.create(kind, channel=where['id'], thread=where['thread'], task=task.id, staff=staff,
                         content=content, hours=hours)
        except ValueError as error:
            return f'没有发出：{error}'
        return '已准备好，会跟着你这一轮的回答一起发在频道话题里。回答里不用再把选项抄一遍。'

    async def poll(args):
        hours = args.get('hours', 24)
        return create('poll', {'question': args.get('question'), 'options': args.get('options'),
                               'multiple': args.get('multiple', False)}, hours if type(hours) is int else -1)

    async def choices(args):
        return create('choices', {'questions': args.get('questions')})

    return [
        Tool('channel_poll', '在频道里发起一个投票，让频道里的人选。适合大家各自表态的事；只需要一个人拍板时用 channel_choices。',
             {'question': '问题，300 字以内', 'options': '2～10 个选项', 'multiple': '可选：能不能多选，默认不能',
              'hours': '可选：多少小时后截止，1～168，默认 24'}, poll,
             read_only=True, operation_kind=READ, required_args=('question', 'options'),
             argument_schema={'question': {'type': 'string', 'maxLength': 300},
                              'options': {'type': 'array', 'items': {'type': 'string', 'maxLength': 80},
                                          'minItems': 2, 'maxItems': 10},
                              'multiple': {'type': 'boolean'}, 'hours': {'type': 'integer', 'minimum': 1, 'maximum': 168}}),
        Tool('channel_choices', '给频道里的人一张选择卡：1～4 个问题，每个 2～6 个选项（另有「其他答案」可写）。'
             '第一个人答了就算，答案会作为那个人的消息交回给你，接着做。',
             {'questions': '问题列表：[{"question": "…", "options": [{"label": "…", "description": "可选"}]}]'}, choices,
             read_only=True, operation_kind=READ, required_args=('questions',),
             argument_schema={'questions': {'type': 'array', 'minItems': 1, 'maxItems': 4, 'items': {
                 'type': 'object', 'properties': {
                     'question': {'type': 'string', 'maxLength': 300},
                     'options': {'type': 'array', 'minItems': 2, 'maxItems': 6, 'items': {
                         'type': 'object', 'properties': {'label': {'type': 'string', 'maxLength': 80},
                                                          'description': {'type': 'string', 'maxLength': 200}},
                         'required': ['label']}}},
                 'required': ['question', 'options']}}}),
    ]
