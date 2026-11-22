"""成员发起的 agent 任务：记下是谁发起的，执行时按他的权限把岗位收窄（ADR-019）。

没有记发起人的任务沿用所有者权限。记了成员的任务（包括他创建的例行工作）：

- 岗位默认按「角色的权限 ∩ 发起人的权限」收窄。代成员办事仅扩大受众内成员可请求的操作。
  成员没有主人的任何私人来源、没有项目副本，
  操作类别按他对这个 agent 的办事级别放。收窄后还留得下哪些工具，用岗位登记自己的那套校验逐个判定，
  不另写一份工具清单。
- 任务开始时读取权限并冻结该轮的操作类别。运行和等待审批时继续核对成员当前权限。
- 判定都在内核（``agents/principal.py``、``agents/identity_groups.py``），这里只负责把登记读出来接上。
"""
from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy

from src.agents import identity_groups as rules
from src.agents.principal import ALLOWABLE, MEMBER, Grants, Principal, effective_grants, member_initiator_grants


class MemberAccessChanged(ValueError):
    pass


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


def plan_allows(root, staff_member: dict) -> bool:
    """Personal CLI subscriptions are shared only when the owner opted in."""
    from src.workflow.staff.roles import CLI_ENGINES
    if staff_member.get('engine') not in CLI_ENGINES:
        return True
    from src.workflow.members.registry import MemberRegistry
    try:
        return MemberRegistry(root).plan_shared(staff_member.get('id', ''))
    except (ValueError, OSError):
        return False


def role_version(role: dict) -> str:
    return f"{role['id']}:{role['revision']}"


def member_authority(root, role, staff_member, principal, *, scheduled=False):
    from src.workflow.members.registry import MemberRegistry
    from src.workflow.staff.roles import CLI_ENGINES

    try:
        level, delegated, auto_approve = MemberRegistry(root).member_execution_policy(
            staff_member['id'], principal, role_version(role), requires_shared_plan=staff_member['engine'] in CLI_ENGINES)
    except (ValueError, OSError):
        return 'none', False, frozenset()
    if scheduled or staff_member['engine'] in CLI_ENGINES:
        return level, False, frozenset()
    # Delegation can request higher operations, but only a member's own level can use standing approval.
    return level, delegated, auto_approve & rules.operations_at(level, role['grants']['operations'])


def require_member_operations(root, task, role: dict, staff_member: dict, operations: Iterable[str], *,
                              preapproved: Iterable[str] = ()) -> None:
    """The recorded initiator must still be allowed to use the requested operations."""
    principal = initiator_of(task)
    if principal is None:
        return
    scheduled = bool(task.collaboration['dispatch'].get('routine'))
    if scheduled:
        from src.workflow.staff.routines import Routines
        try:
            Routines(root).check_task(task)
        except (OSError, ValueError) as error:
            raise MemberAccessChanged(str(error)) from error
    level, delegated, approval = member_authority(root, role, staff_member, principal, scheduled=scheduled)
    required = frozenset(operations)
    if (not required or not required <= _member_grants(role, level, acts_for_members=delegated).operations
            or not frozenset(preapproved) <= approval):
        raise MemberAccessChanged('发起人的办事权限已变化，请重新发起任务')


def member_role_supported(role: dict) -> bool:
    grants = role.get('grants')
    return (isinstance(grants, dict) and grants.get('project') is False
            and not role.get('project_binding') and not role['id'].startswith('routine-'))


def member_policy_supported(role: dict, staff: dict) -> bool:
    from src.workflow.staff.roles import CLI_ENGINES
    engine = staff.get('engine')
    return isinstance(engine, str) and engine not in CLI_ENGINES and member_role_supported(role)


def _member_grants(role: dict, level: str, *, acts_for_members: bool) -> Grants:
    held = Grants.of(role.get('grants'))
    if not member_role_supported(role):
        return Grants()
    baseline = member_initiator_grants(held, rules.operations_at(level, held.operations))
    allowed = effective_grants(Principal(MEMBER, 'member'), held, baseline,
                               acts_for_members=acts_for_members and level in rules.ACT_LEVELS[1:])
    # Delegation never exposes private sources or project access.
    return allowed.meet(member_initiator_grants(held, held.operations))


def role_for_member(registry, role: dict, level: str, *, acts_for_members: bool = False) -> dict:
    """岗位在成员回合里的样子：只留他用得了的操作类别和工具。什么都不剩时抛 ValueError。"""
    if not member_role_supported(role):
        raise ValueError('这个 agent 连着主人的项目或例行工作，成员不能直接使用')
    allowed = _member_grants(role, level, acts_for_members=acts_for_members)
    if not allowed.operations:
        raise ValueError('你对这个 agent 的权限不够，不能让它办事')
    from src.workflow.staff.roles import is_full
    tools: list[str] = []
    full = is_full(role)
    operations = role['grants']['operations']
    if full:        # 成员回合里没有完整能力：退回登记工具表，按成员能用的操作类别和来源逐个核对（主人 2026-11-22 定）
        operations = [kind for kind in operations if kind not in {'deliver', 'interact', 'irreversible'}]
    grants = {'operations': [kind for kind in operations if kind in allowed.operations],
              'tools': tools, 'sees': [], 'web': allowed.web, 'project': False}
    catalog = registry.tool_catalog()
    for name in (sorted(catalog) if full else role['grants']['tools']):
        try:        # 岗位登记的校验就是「这组权限配不配得上这个工具」的唯一判据
            registry.validate_role({**deepcopy(role), 'grants': {**grants, 'tools': [name]}},
                                   builtin=bool(role.get('builtin')), catalog=catalog)
        except ValueError:
            continue
        tools.append(name)
    return {**deepcopy(role), 'grants': grants}


def available_member_auto_approval(registry, role):
    """Only offer operation kinds backed by tools that a team member can actually use."""
    allowed = role_for_member(registry, role, 'execute')
    catalog = registry.tool_catalog()
    return frozenset(
        kind for name in allowed['grants']['tools'] if not catalog[name].always_confirm
        for kind in (catalog[name].operation_kind, *catalog[name].possible_operations) if kind in ALLOWABLE
    )
