"""所有者从界面新建、修改、归档、恢复、彻底删除 agent。

一个新 agent = 一个只属于它的岗位（职责说明就是它的说明，权限默认最小）+ 一个用这个岗位的专员。
两样都走原来的「提议 → 所有者批准」，在同一个所有者请求里完成：界面上按「创建」就是所有者的那一次批准，
审批历史和审计照样留下。模型自己的工具仍然只能提议，碰不到这里。

- **默认权限**：只读、能联网搜索和抓网页，不看私人资料、不碰项目、不能写和执行。要更多由所有者另行提议岗位。
- **谁能对话**：受众存在成员登记处（``MemberRegistry.set_audience``），由内核规则（``identity_groups``）判定。
- **样子**：外观、建立时间、新建时的清单存在 ``AgentProfiles``，不是权限。
- **归档** = 专员暂停、从团队里藏起来，可以恢复；**彻底删除** = 专员撤掉（终态）、岗位停用、受众和单独定的级别清掉。
- 现在只有所有者能做这些（agent 都是所有者的）。
"""
from __future__ import annotations

import re
import uuid
from copy import deepcopy
from typing import Any, Dict, Mapping, Optional

from src.agents.principal import OWNER, Principal
from src.workflow.staff.agent_profiles import SETUP_ROLES, AgentProfiles, check_look
from src.workflow.staff.roles import FULL_GRANTS

# 界面里新建的 agent 默认就是完整能力：和主助理同一套工具和规则（主人 2026-11-22 定，见 roles.FULL_GRANTS）。
# 以前建的只读研究型 agent 不自动升级。
FULL_STEPS, FULL_TIMEOUT = 40, 1800
MAX_NAME, MAX_TITLE, MAX_INSTRUCTIONS = 80, 80, 8000
_HANDLE = re.compile(r"[a-z0-9][a-z0-9_-]{1,31}")
_RESERVED = frozenset({"assistant", "owner", "everyone", "here", "all", "channel", "me", "admin"})
_APP_ID = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}")
MAX_SETUP_APPS = 20


class NotAllowed(PermissionError):
    """不是所有者。"""


def _owner_only(actor: Principal) -> None:
    if actor.kind != OWNER:
        raise NotAllowed("只有所有者能新建和管理 agent")


def _text(value: Any, label: str, limit: int, *, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"{label}必须是文字")
    text = value.strip()
    if required and not text:
        raise ValueError(f"{label}不能为空")
    if len(text) > limit:
        raise ValueError(f"{label}太长了（最多 {limit} 字）")
    return text


def _model(value: Any) -> str:
    text = _text(value, "模型", 160)
    return "" if text.lower() in {"auto", "default"} else text      # 「自动」= 用这个家默认的模型


def _setup(value: Any) -> Optional[Dict[str, Any]]:
    """新建时的清单：从哪个现成角色开始、它怎么介绍自己、开始时的样子、建议用的应用。不认识的现成角色当作从零开始。"""
    if not isinstance(value, Mapping):
        return None
    role = value.get("role")
    setup: Dict[str, Any] = {"role": role if role in SETUP_ROLES else None, "confirmed": []}
    hello = _text(value.get("hello"), "自我介绍", 500)
    if hello:
        setup["hello"] = hello
    starter = value.get("starter")
    if isinstance(starter, str) and _APP_ID.fullmatch(starter):
        setup["starter"] = starter
    apps = value.get("apps")
    if isinstance(apps, Mapping):
        setup["apps"] = {key: level for key, level in list(apps.items())[:MAX_SETUP_APPS]
                         if isinstance(key, str) and _APP_ID.fullmatch(key) and level in ("read", "write")}
    return setup


def _owner_session(root: str) -> str:
    from src.gateway.main_conversation import main_chat_key
    return main_chat_key(root) or "desktop-app"


def _approve(root: str, kind: str, data: Dict[str, Any], reason: str) -> Dict[str, Any]:
    """提议并当场批准（调用方已在所有者写入路由里）。"""
    from src.workflow.staff.approvals import StaffApprovals
    approvals = StaffApprovals(root)
    item = approvals.propose(_owner_session(root), kind, data, reason)
    return approvals.decide(item["id"], True, item["digest"], nonce=item["nonce"], round=item["round"],
                            instance=item["instance"])


def _brief(name: str, title: str, instructions: str) -> str:
    return instructions or f"你是{name}，{title or '一位助手'}。按要求办事，查不到就直说，不要编。"


def create(root: str, actor: Principal, *, name: Any, handle: Any, title: Any = "", instructions: Any = "",
           model: Any = "", look: Any = None, setup: Any = None, audience: Any = None) -> str:
    """新建一个 agent，返回它的专员 id（就是它的 handle）。"""
    from src.workflow.members.profile_preferences import check_agent_handle
    from src.workflow.members.registry import MemberRegistry
    from src.workflow.staff.owner_confirmation import owner_route
    from src.workflow.staff.registry import StaffRegistry
    from src.workflow.tasks.tasks import TASK_STATE_LOCK
    _owner_only(actor)
    name = _text(name, "名字", MAX_NAME, required=True)
    title = _text(title, "角色", MAX_TITLE)
    instructions = _text(instructions, "说明", MAX_INSTRUCTIONS)
    if not isinstance(handle, str) or not _HANDLE.fullmatch(handle) or handle in _RESERVED:
        raise ValueError("handle 只能是 2～32 个小写字母、数字、- 或 _，且不能是保留的名字")
    registry = StaffRegistry(root)
    checked_look = check_look(look)
    checked_setup = _setup(setup)
    if audience is not None:                          # 先核对，免得 agent 建好了受众却写不进去
        from src.agents.identity_groups import check_audience
        current = MemberRegistry(root).snapshot()
        check_audience(audience, current["members"], current["groups"])
    role_id = f"agent-{handle}-{uuid.uuid4().hex[:6]}"
    with TASK_STATE_LOCK, owner_route(root):
        check_agent_handle(root, handle)
        if handle in registry.reserved_member_handles():
            raise ValueError("这个 handle 已经有 agent 用了（包括删掉的），换一个")
        _approve(root, "role", {"id": role_id, "title": title or name, "brief": _brief(name, title, instructions),
                                "grants": deepcopy(FULL_GRANTS), "engine": ["vortocode"], "max_steps": FULL_STEPS,
                                "timeout_seconds": FULL_TIMEOUT}, "所有者在界面里新建 agent：它自己的岗位")
        _approve(root, "member", {"id": handle, "name": name, "role": role_id, "engine": "vortocode",
                                  "model": _model(model)}, "所有者在界面里新建 agent")
    AgentProfiles(root).create(handle, look=checked_look, setup=checked_setup)
    if audience is not None:
        MemberRegistry(root).set_audience(actor, handle, audience)
    return handle


def update(root: str, actor: Principal, staff_id: str, change: Mapping[str, Any]) -> None:
    """改名字、角色、说明、模型、外观、受众。改说明和角色是给它的岗位提一个新版本，改名字和模型是给专员提一个新版本，都当场批准。"""
    from src.workflow.members.registry import MemberRegistry
    from src.workflow.staff.owner_confirmation import owner_route
    from src.workflow.staff.registry import StaffRegistry
    _owner_only(actor)
    registry = StaffRegistry(root)
    member = registry.member(staff_id)
    if member["status"] == "retired":
        raise ValueError("这个 agent 已经删除")
    if change.get("audience") is not None:            # 先核对，免得别的改了受众却写不进去
        from src.agents.identity_groups import check_audience
        current = MemberRegistry(root).snapshot()
        check_audience(change["audience"], current["members"], current["groups"])
    if "appearance" in change:
        check_look(change["appearance"])
    role = registry.role(member["role"])
    with owner_route(root):
        if "instructions" in change or "role" in change:
            if role.get("builtin"):
                raise ValueError("用内置岗位的 agent 不能在这里改说明和角色")
            data = {key: role[key] for key in ("id", "title", "brief", "grants", "engine", "max_steps",
                                               "timeout_seconds", "revision")}
            if "role" in change:
                data["title"] = _text(change["role"], "角色", MAX_TITLE) or member["name"]
            if "instructions" in change:
                data["brief"] = _brief(member["name"], data["title"], _text(change["instructions"], "说明",
                                                                            MAX_INSTRUCTIONS))
            if data["title"] != role["title"] or data["brief"] != role["brief"]:
                _approve(root, "role", data, "所有者在界面里改 agent 的角色或说明")
                member = registry.member(staff_id)
        if "name" in change or "model" in change:
            data = {key: member[key] for key in ("id", "name", "role", "engine", "model", "limits", "revision")}
            if "name" in change:
                data["name"] = _text(change["name"], "名字", MAX_NAME, required=True)
            if "model" in change:
                data["model"] = _model(change["model"])
            if data["name"] != member["name"] or data["model"] != member["model"]:
                _approve(root, "member", data, "所有者在界面里改 agent 的名字或模型")
    if "appearance" in change:
        AgentProfiles(root).update(staff_id, appearance=check_look(change["appearance"]))
    if "audience" in change and change["audience"] is not None:
        MemberRegistry(root).set_audience(actor, staff_id, change["audience"])


def _status(root: str, staff_id: str, status: str) -> None:
    from src.workflow.staff.registry import StaffRegistry
    registry = StaffRegistry(root)
    member = registry.member(staff_id)
    if member["status"] != status:
        registry.set_member_status(staff_id, status, member["revision"])


def archive(root: str, actor: Principal, staff_id: str) -> None:
    """归档：不再接活、从团队里藏起来；资料都留着，可以恢复。"""
    _owner_only(actor)
    from src.workflow.staff.registry import StaffRegistry
    if StaffRegistry(root).member(staff_id)["status"] == "retired":
        raise ValueError("这个 agent 已经删除")
    _status(root, staff_id, "paused")
    AgentProfiles(root).update(staff_id, archived=True)


def unarchive(root: str, actor: Principal, staff_id: str) -> None:
    _owner_only(actor)
    if not AgentProfiles(root).get(staff_id).get("archived"):
        raise ValueError("这个 agent 没有归档")
    _status(root, staff_id, "active")
    AgentProfiles(root).update(staff_id, archived=False)


def purge(root: str, actor: Principal, staff_id: str) -> None:
    """彻底删除：专员撤掉（不能恢复）、它自己的岗位停用、谁能和它对话与单独定的级别清掉。聊天和任务记录留着。"""
    from src.workflow.members.registry import MemberRegistry
    from src.workflow.staff.registry import StaffRegistry
    _owner_only(actor)
    registry = StaffRegistry(root)
    member = registry.member(staff_id)
    if member["status"] != "retired":
        registry.set_member_status(staff_id, "retired", member["revision"])
    role = registry.role(member["role"])
    if not role.get("builtin") and role["id"].startswith(f"agent-{staff_id}-") and role["enabled"]:
        registry.set_role_enabled(role["id"], False)
    MemberRegistry(root).forget_agent(actor, staff_id)
    AgentProfiles(root).update(staff_id, archived=False, deleted=True)


def member_settings_agent(root: str, staff_id: str) -> dict:
    from src.workflow.staff.registry import StaffRegistry

    try:
        staff = StaffRegistry(root).member(staff_id)
    except ValueError as error:
        raise LookupError("没有这个智能体") from error
    profile = AgentProfiles(root).get(staff_id)
    if staff["status"] == "retired" or profile.get("archived") or profile.get("deleted"):
        raise LookupError("没有这个智能体")
    return staff


def set_member_action_level(root: str, actor: Principal, staff_id: str, member_id: str, level: str | None) -> None:
    from src.workflow.members.registry import MemberRegistry
    from src.workflow.tasks.tasks import TASK_STATE_LOCK

    # The task lock must always precede the member registry lock, including during approval.
    with TASK_STATE_LOCK:
        member_settings_agent(root, staff_id)
        MemberRegistry(root).set_act_level_on(actor, staff_id, member_id, level)


def member_policy(root: str, actor: Principal, staff_id: str, change: dict | None = None) -> dict:
    from src.agents.principal import ALLOWABLE
    from src.workflow.members.registry import MemberRegistry
    from src.workflow.staff.initiators import available_member_auto_approval, member_policy_supported, role_version
    from src.workflow.staff.registry import StaffRegistry
    from src.workflow.tasks.tasks import TASK_STATE_LOCK

    if change is not None:
        if (not set(change) <= {"roleVersion", "actForMembers", "autoApprove"}
                or not isinstance(change.get("roleVersion"), str) or len(change) < 2):
            raise ValueError("缺少当前岗位版本或权限设置")
        if "actForMembers" in change and type(change["actForMembers"]) is not bool:
            raise ValueError("代成员办事必须是开或关")
        if "autoApprove" in change:
            kinds = change["autoApprove"]
            if not isinstance(kinds, list) or any(not isinstance(kind, str) or kind not in ALLOWABLE for kind in kinds):
                raise ValueError("成员放行只支持写入和执行")
            change = {**change, "autoApprove": sorted(set(kinds))}
    with TASK_STATE_LOCK:
        staff = member_settings_agent(root, staff_id)
        registry = StaffRegistry(root)
        role = registry.role(staff["role"])
        if not member_policy_supported(role, staff):
            raise LookupError("这个智能体没有成员权限设置")
        eligible = available_member_auto_approval(registry, role)
        members = MemberRegistry(root)
        if change is None:
            return members.agent_policy(actor, staff_id, role, role_version(role), eligible)
        return members.change_agent_policy(actor, staff_id, role, role_version(role), eligible, change)


def confirm_step(root: str, actor: Principal, staff_id: str, step: str) -> None:
    _owner_only(actor)
    from src.workflow.staff.registry import StaffRegistry
    StaffRegistry(root).member(staff_id)
    AgentProfiles(root).confirm_step(staff_id, step)
