"""agent 提议改团队背景的工具（主人 2026-11-24 定）。

只记一条待决定的提议，团队背景不会变，要所有者看过前后对比采纳才生效；所以和 Mission 的卡片一样按读操作，
不走确认门。读过外部内容的那一轮提的，提议上会注明，所有者决定时看得到。
"""
from __future__ import annotations

from src.agents.tool import Tool
from src.agents.trust import READ


def build_context_proposal_tool(root, task):
    from src.memory.assistant_profile import MAX_CONTEXT
    from src.workflow.members.context_proposals import MAX_TITLE, MAX_WHY

    async def propose(args):
        from src.agents.taint import is_tainted
        from src.workflow.members.context_proposals import ContextProposals, ProposalError
        from src.workflow.staff.initiators import initiator_of

        dispatch = task.collaboration.get('dispatch') or {}
        try:
            actor = initiator_of(task)
            item = ContextProposals(root).propose(
                by=str(dispatch.get('member') or ''), initiator=actor.id if actor is not None else None, task=task.id,
                title=args.get('title'), why=args.get('why'), after=args.get('after'), tainted=is_tainted())
        except (ValueError, ProposalError) as error:
            return f'提议没有记下：{error}'
        return (f'已记下提议「{item["title"]}」，等所有者看过前后对比再决定；团队背景现在没有变。'
                '不要说已经改好了。')

    return Tool('propose_team_context',
                '提议修改团队背景（每个 agent 每次对话最前面都会读到的那段话）。要交完整的一份新版本，不是只写改动；'
                '团队背景不会马上变，所有者看过前后对比、采纳以后才生效。只在有人明确希望把某件事写进团队背景时用。',
                {'after': f'完整的新团队背景，{MAX_CONTEXT} 字以内', 'title': f'这次改了什么，{MAX_TITLE} 字以内',
                 'why': f'可选：为什么要改，{MAX_WHY} 字以内'},
                propose, read_only=True, operation_kind=READ, required_args=('after', 'title'),
                argument_schema={'after': {'type': 'string', 'maxLength': MAX_CONTEXT},
                                 'title': {'type': 'string', 'maxLength': MAX_TITLE},
                                 'why': {'type': 'string', 'maxLength': MAX_WHY}})
