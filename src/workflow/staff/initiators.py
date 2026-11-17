"""成员发起的 agent 任务：记下是谁发起的，执行时按他的权限把岗位收窄（ADR-019）。

没有记发起人的任务与原来完全一样（所有者派的活、例行工作）。记了成员的任务：

- 岗位在这一回合里按「角色的权限 ∩ 发起人的权限」收窄。成员没有主人的任何私人来源、没有项目副本，
  操作类别按他对这个 agent 的办事级别放。收窄后还留得下哪些工具，用岗位登记自己的那套校验逐个判定，
  不另写一份工具清单。
- 级别在执行时重新读：任务排队期间成员被移除或被降到「不能对话」，这件事就不再执行。
- 判定都在内核（``agents/principal.py``、``agents/identity_groups.py``），这里只负责把登记读出来接上。
"""
from __future__ import annotations

from copy import deepcopy

from src.agents import identity_groups as rules
from src.agents.principal import MEMBER, Grants, Principal, effective_grants, member_initiator_grants


def record(member_id: str) -> dict:
    """写进任务 dispatch 的发起人记录。"""
    if not isinstance(member_id, str) or not member_id:
        raise ValueError('发起人标识无效')
    return {'kind': MEMBER, 'id': member_id}


def initiator_of(task):
    """这件任务是哪个成员发起的；不是成员发起的返回 None。记录写坏了按「说不清是谁」拒绝，不当成所有者。"""
    value = (task.collaboration.get('dispatch') or {}).get('initiator')
    if value is None:
        return None
    if not isinstance(value, dict) or value.get('kind') != MEMBER or not isinstance(value.get('id'), str) or not value['id']:
        raise ValueError('任务的发起人记录无效，未执行')
    return Principal(MEMBER, value['id'])


def level_on(root, staff_member_id: str, principal: Principal) -> str:
    """这个成员现在对这个 agent 的办事级别。登记读不出来按不能对话。"""
    from src.workflow.members.registry import MemberRegistry
    try:
        return MemberRegistry(root).act_level_on(staff_member_id, principal)
    except (ValueError, OSError):
        return 'none'


def role_for_member(registry, role: dict, level: str) -> dict:
    """岗位在成员回合里的样子：只留他用得了的操作类别和工具。什么都不剩时抛 ValueError。"""
    if role.get('project_binding') or str(role.get('id') or '').startswith('routine-'):
        raise ValueError('这个 agent 连着主人的项目或例行工作，成员不能直接使用')
    held = Grants.of(role.get('grants'))
    allowed = effective_grants(Principal(MEMBER, 'member'), held,
                               member_initiator_grants(held, rules.operations_at(level, held.operations)))
    if not allowed.operations:
        raise ValueError('你对这个 agent 的权限不够，不能让它办事')
    grants = {'operations': [kind for kind in role['grants']['operations'] if kind in allowed.operations],
              'tools': [], 'sees': [], 'web': allowed.web, 'project': False}
    catalog = registry.tool_catalog()
    for name in role['grants']['tools']:
        try:        # 岗位登记的校验就是「这组权限配不配得上这个工具」的唯一判据
            registry.validate_role({**deepcopy(role), 'grants': {**grants, 'tools': [name]}},
                                   builtin=bool(role.get('builtin')), catalog=catalog)
        except ValueError:
            continue
        grants['tools'].append(name)
    return {**deepcopy(role), 'grants': grants}
