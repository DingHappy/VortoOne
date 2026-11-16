"""回合的发起人，以及按发起人封顶的权限判定（ADR-019）。

内核原先只有一个主人。成员进来之后要补的是「这一回合是谁发起的」：它由装配会话的宿主申报，
内核只使用，回合内不变。本模块是纯数据和纯函数——不查名册、不读配置、不导入上层；发起人有哪些
权限、角色登记了什么、所有者对这个角色放行了什么，都由上层查好传进来。

规矩：

- 没申报发起人的回合按 ``system``（没有真人发起）处理，走现有的无人值守规则：新入口漏了申报只会更严。
- 发起人是所有者时这一关恒通过，所以单人时的行为与没有本模块时一致。
- 成员回合的有效权限 = 角色的权限 ∩ 发起人的权限；角色打开「代成员办事」时只用角色的。
- 成员回合里写及以上默认要问人。所有者可以按角色放行，但只限写与执行；对外发送、不可逆、交互
  始终要问，污点回合里放行失效。
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import FrozenSet, Iterable, Optional

OWNER = "owner"
MEMBER = "member"
SYSTEM = "system"
_KINDS = frozenset({OWNER, MEMBER, SYSTEM})

# 与 trust.py 的操作类别同名；这里不导入它，免得判定函数的含义跟着别处的表变
READ, WRITE, EXECUTE = "read", "write", "execute"
ALLOWABLE = frozenset({WRITE, EXECUTE})      # 所有者能对成员放行的只有这两类


@dataclass(frozen=True)
class Principal:
    kind: str = SYSTEM
    id: str = ""
    home: str = ""

    def __post_init__(self) -> None:
        if self.kind not in _KINDS:
            raise ValueError(f"未知的发起人类别：{self.kind!r}")
        if self.kind != SYSTEM and not self.id:
            raise ValueError("真人发起的回合必须写明是谁")

    @property
    def is_owner(self) -> bool:
        return self.kind == OWNER

    @property
    def is_member(self) -> bool:
        return self.kind == MEMBER


NOBODY = Principal()

_current: contextvars.ContextVar[Principal] = contextvars.ContextVar("vortoone_principal", default=NOBODY)


def current_principal() -> Principal:
    return _current.get()


def set_principal(principal: Principal) -> contextvars.Token:
    """回合开始时由宿主调用一次；返回的 token 交给 ``reset_principal`` 在回合结束时还原。"""
    if not isinstance(principal, Principal):
        raise TypeError("发起人必须是 Principal")
    return _current.set(principal)


def reset_principal(token: contextvars.Token) -> None:
    _current.reset(token)


def _set(values: Optional[Iterable[str]]) -> FrozenSet[str]:
    return frozenset(str(value) for value in values or ())


@dataclass(frozen=True)
class Grants:
    """一组权限，和专员岗位登记的 grants 同一套词：操作类别、工具、看得到的来源、出网、项目。"""

    operations: FrozenSet[str] = field(default_factory=frozenset)
    tools: FrozenSet[str] = field(default_factory=frozenset)
    sees: FrozenSet[str] = field(default_factory=frozenset)
    web: bool = False
    project: bool = False

    @classmethod
    def of(cls, value: Optional[dict]) -> "Grants":
        """从登记里的 grants 字典读。缺的字段一律按「没有」：读不全只会更严。"""
        value = value if isinstance(value, dict) else {}
        return cls(_set(value.get("operations")), _set(value.get("tools")), _set(value.get("sees")),
                   value.get("web") is True, value.get("project") is True)

    def meet(self, other: "Grants") -> "Grants":
        return Grants(self.operations & other.operations, self.tools & other.tools, self.sees & other.sees,
                      self.web and other.web, self.project and other.project)


def effective_grants(principal: Principal, role: Grants, initiator: Optional[Grants] = None, *,
                     acts_for_members: bool = False) -> Grants:
    """这一回合里角色实际能用的权限。

    所有者：就是角色的权限。成员：与他自己的权限取交集；该角色打开了「代成员办事」才只用角色的。
    没有真人发起（system）：这里不放宽也不收紧，仍是角色的权限——无人值守的限制在能力档案里。
    成员却没给出他的权限：按什么都没有。
    """
    if not principal.is_member or acts_for_members:
        return role
    return role.meet(initiator or Grants())


def may_address_main_assistant(principal: Principal) -> bool:
    """主助理连着主人的个人记忆、个人事项和电脑，属于个人空间：只有所有者和系统回合能到它。"""
    return not principal.is_member


def may_write_owner_memory(principal: Principal) -> bool:
    """成员的发言不能写进主人的个人记忆。"""
    return not principal.is_member


def pre_authorized_for(principal: Principal, kind: str, *, owner_pre_authorized: bool,
                       member_allowed: Iterable[str] = (), tainted: bool = False) -> bool:
    """这次操作在发起人这一维上算不算已经授权免问。结果交给 ``gate.decide`` 的 ``pre_authorized``。

    - 所有者、系统回合：原样用现有的预授权（档位、始终允许、--yes），本函数不改变它。
    - 成员：主人给自己的预授权不借给他。只认所有者对这个角色登记的「成员放行」，且只对写与执行
      生效；读由有效权限直接决定，不经这里。污点回合一律不算（decide 里污点本来也一票否决，
      这里再挡一次，是为了调用方单独用本函数时也不出错）。
    """
    if not principal.is_member:
        return owner_pre_authorized
    if tainted or kind not in ALLOWABLE:
        return False
    return kind in _set(member_allowed)


def may_approve(approver: Principal, initiator: Principal, *, holds_approval: bool = False) -> bool:
    """approver 的批准对 initiator 发起的这次操作作不作数。

    所有者的批准总是作数。成员的批准只有在他持有对应范围的审批权限时才作数——包括他批自己发起的
    操作。系统不能批准任何东西。
    """
    if approver.is_owner:
        return True
    return approver.is_member and holds_approval
