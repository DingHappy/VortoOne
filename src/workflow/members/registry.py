"""成员登记处（HOME_AND_MEMBERS_DESIGN 7.2、7.3，ADR-019）。

这里只管存和取：成员、身份组及其先后、邀请、对单个 agent 单独给人定的办事级别，都在状态目录下
一个文件里。**能不能做**一律问内核的 ``agents/identity_groups.py``，本模块不自己写规则——每个写操作
先读出当前登记、交给内核判定、通过了才落盘，判定和落盘在同一把锁里。

操作人用 ``Principal`` 表示：所有者恒通过内核那一关；成员按他持有的身份组判定；没有真人发起的
（system）以及登记处不认识的成员，任何写操作都拒绝。

登记处不管登录：接受邀请只产生一条成员记录，凭什么证明「我是这个成员」由入口另行处理。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import uuid
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple

from src.agents import identity_groups as rules
from src.agents.identity_groups import ADMIN, ADMINS, EVERYONE, Post
from src.agents.principal import ALLOWABLE, MEMBER, OWNER, Principal
from src.utils.state_dir import ensure_state_gitignore, state_dir

_FILE = "space.json"
_MAX_BYTES = 1024 * 1024
_LOCK = threading.RLock()
_NAME = re.compile(r"^[^\x00-\x1f\x7f]{1,64}$")
_EMAIL = re.compile(r"^[^@\s\x00-\x1f\x7f]{1,64}@[^@\s\x00-\x1f\x7f]{1,190}$")
_AGENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MEMBER_ID = re.compile(r"m_[a-f0-9]{12}")
MAX_GROUPS, MAX_MEMBERS, MAX_INVITATIONS = 64, 500, 200
INVITE_DAYS_DEFAULT, INVITE_DAYS_MAX = 7, 30


class Refused(PermissionError):
    """操作人没有做这件事的权限。"""


class Damaged(ValueError):
    """登记文件读不出来或内容不合规矩。停下等人处理，不回到默认登记。"""


class Missing(LookupError):
    """说的那个成员、身份组或邀请不存在（或邀请已失效）。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _fresh() -> Dict[str, Any]:
    return {
        "version": 1,
        "groups": {
            ADMINS: {"name": "管理员", "items": [ADMIN], "computer": rules.COMPUTER_LEVELS[-1],
                     "act": rules.ACT_LEVELS[-1]},
            EVERYONE: {"name": "所有人", "items": sorted(rules.EVERYONE_DEFAULT),
                       "computer": rules.COMPUTER_DEFAULT, "act": rules.ACT_DEFAULT},
        },
        "order": [ADMINS, EVERYONE],
        "members": {},
        "invitations": {},
        "agent_levels": {},
        "plans_shared": {},
        "agent_audiences": {},
        "agent_policies": {},
        "channels": {},
    }


def _name(value: Any, label: str) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not _NAME.fullmatch(text):
        raise ValueError(f"{label}不能为空，最长 64 个字，不能有控制字符")
    return text


def login_name(value: Any) -> str:
    """登录用的邮箱：去空白、转小写。登记处不验证它收不收得到信，只当登录名。"""
    text = value.strip().lower() if isinstance(value, str) else ""
    if not _EMAIL.fullmatch(text):
        raise ValueError("邮箱格式不对")
    return text


def _look(value: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """身份组的展示信息（图标、颜色之类）：登记处只存不解释，只收短字符串和布尔值。"""
    look: Dict[str, Any] = {}
    for key, item in (value or {}).items():
        if not isinstance(key, str) or not re.fullmatch(r"[a-z_]{1,24}", key):
            raise ValueError("展示信息的字段名无效")
        if not (isinstance(item, bool) or (isinstance(item, str) and len(item) <= 32)):
            raise ValueError("展示信息只能是短文字或开关")
        look[key] = item
    if len(look) > 8:
        raise ValueError("展示信息太多了")
    return look


def _post(group_id: str, record: Mapping[str, Any]) -> Post:
    return Post(group_id, frozenset(str(item) for item in record.get("items") or []),
                str(record.get("computer") or "none"), str(record.get("act") or "none"))


def _group_act_level(held: List[str], posts: Mapping[str, Post]) -> str:
    return rules.act_level(held, posts) if rules.has("roles.talk", held, posts) else "none"


def _member_act_level(state: Mapping[str, Any], agent: str, person: str, held: List[str],
                      posts: Mapping[str, Post]) -> str:
    return rules.act_level_on(state["agent_levels"].get(agent) or {}, person, _group_act_level(held, posts),
                              audience=state["agent_audiences"].get(agent), held=held)


class MemberRegistry:
    def __init__(self, root: str):
        self.root = str(root)
        self.directory = state_dir(root) / "members"

    # ── 存取 ──────────────────────────────────────────────────────────────────

    def _load(self) -> Dict[str, Any]:
        path = self.directory / _FILE
        if self.directory.is_symlink():
            raise Damaged("成员登记目录不能是符号链接")
        if not path.exists():
            return _fresh()
        if path.is_symlink() or path.stat().st_size > _MAX_BYTES:
            raise Damaged("成员登记文件无效或过大")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeError, ValueError) as error:
            # 读坏了就停：不回到默认登记，那等于把所有人的身份组悄悄清掉
            raise Damaged("成员登记文件损坏，未恢复默认登记") from error
        if not isinstance(value, dict) or value.get("version") != 1:
            raise Damaged("成员登记文件版本无效")
        for key, kind in (("groups", dict), ("order", list), ("members", dict), ("invitations", dict),
                          ("agent_levels", dict)):
            if not isinstance(value.get(key), kind):
                raise Damaged("成员登记文件结构无效")
        value.setdefault("plans_shared", {})          # 早于这一项的登记文件没有它：一个都没打开
        value.setdefault("agent_audiences", {})       # 早于这一项的：没设受众，按所有人
        value.setdefault("agent_policies", {})
        value.setdefault("channels", {})
        if any(not isinstance(value[key], dict) for key in ("plans_shared", "agent_audiences", "agent_policies", "channels")):
            raise Damaged("成员登记文件结构无效")
        try:
            self._check(value)
        except Damaged:
            raise
        except ValueError as error:
            raise Damaged(f"成员登记文件内容不合规矩：{error}") from error
        return value

    @staticmethod
    def _check(state: Mapping[str, Any]) -> None:
        """落盘前和读出后都过一遍：身份组各自合规、顺序表与身份组一一对应、「所有人」在最后。"""
        groups, order = state["groups"], state["order"]
        for group_id, record in groups.items():
            rules.check_post(_post(group_id, record))
        rules.check_order(order)
        if set(order) != set(groups) or ADMINS not in groups:
            raise ValueError("身份组与顺序表对不上")
        for agent, policy in state["agent_policies"].items():
            if (not _AGENT.fullmatch(agent) or not isinstance(policy, dict)
                    or not isinstance(policy.get("roleVersion"), str) or type(policy.get("actForMembers")) is not bool
                    or not isinstance(policy.get("autoApprove"), list)
                    or any(not isinstance(kind, str) or kind not in ALLOWABLE for kind in policy["autoApprove"])):
                raise ValueError("智能体成员权限设置无效")
        if state["channels"]:
            from src.workflow.members.channels import check_channels
            check_channels(state)

    def _save(self, state: Dict[str, Any]) -> None:
        self._check(state)
        if self.directory.is_symlink():
            raise ValueError("成员登记目录不能是符号链接")
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / _FILE
        if path.is_symlink():
            raise ValueError("成员登记文件不能是符号链接")
        encoded = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
        if len(encoded.encode("utf-8")) > _MAX_BYTES:
            raise ValueError("成员登记文件过大，未写入")
        ensure_state_gitignore(self.root)
        temporary = self.directory / f".{_FILE}.{uuid.uuid4().hex}.tmp"
        try:
            fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(encoded)
                file.flush()
                os.fsync(file.fileno())
            temporary.replace(path)
            if hasattr(os, "O_DIRECTORY"):
                dir_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def _locked_state(self) -> Iterator[Dict[str, Any]]:
        """Keep membership and authorization stable while committing a change."""
        with _LOCK:
            try:
                import fcntl
            except ImportError:
                yield self._load()
                return
            if self.directory.is_symlink():
                raise Damaged("成员登记目录不能是符号链接")
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / ".lock"
            fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "a+b") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                yield self._load()

    @contextmanager
    def _change(self) -> Iterator[Dict[str, Any]]:
        """读、判定与保存使用同一把锁。"""
        with self._locked_state() as state:
            yield state
            self._save(state)

    # ── 判定用的小工具 ────────────────────────────────────────────────────────

    @staticmethod
    def _posts(state: Mapping[str, Any]) -> Dict[str, Post]:
        return {group_id: _post(group_id, record) for group_id, record in state["groups"].items()}

    @staticmethod
    def _actor(state: Mapping[str, Any], actor: Principal) -> Tuple[bool, List[str]]:
        """操作人是不是所有者、持有哪些身份组。不是所有者也不是在册成员的，一律拒绝。"""
        if actor.is_owner:
            return True, []
        member = state["members"].get(actor.id) if actor.is_member else None
        if member is None:
            raise Refused("只有所有者和在册成员能改成员登记")
        return False, list(member.get("groups") or [])

    @staticmethod
    def _member(state: Mapping[str, Any], member_id: str) -> Dict[str, Any]:
        member = state["members"].get(str(member_id))
        if member is None:
            raise Missing("没有这个成员")
        return member

    # ── 读 ────────────────────────────────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        """当前登记的副本（邀请只带摘要，没有令牌本身）。"""
        with _LOCK:
            return deepcopy(self._load())

    def standing(self, member_id: str) -> Dict[str, Any]:
        """一个成员现在的权限：权限项集合、电脑权限级别、按身份组得到的办事级别。不在册的抛 Missing。"""
        state = self.snapshot()
        held = self._member(state, member_id).get("groups") or []
        posts = self._posts(state)
        return {"groups": [group for group in held if group in posts],
                "permissions": rules.permissions_of(held, posts),
                "computer": rules.computer_level(held, posts),
                "act": rules.act_level(held, posts)}

    # ── 用个人订阅的 agent 回不回应别人 ─────────────────────────────────────────
    # 主人 2026-11-16 定（参照 agentes-chat ADR 0059 §5）：用本机 Claude Code / Codex（所有者的个人订阅）的
    # agent 默认只回应所有者；所有者可以对单个 agent 打开「也回应别人」。只有所有者能开关——订阅是他的。

    def plan_shared(self, agent_id: str) -> bool:
        return str(agent_id) in self.snapshot()["plans_shared"]

    def share_plan(self, actor: Principal, agent_id: str, shared: bool, *, now: Optional[datetime] = None) -> None:
        """打开或关上。调用方负责先让所有者看过条款提示，并记审计。"""
        agent = str(agent_id)
        if not _AGENT.fullmatch(agent):
            raise ValueError("agent 标识无效")
        if not actor.is_owner:
            raise Refused("只有所有者能决定自己的订阅回不回应别人")
        with self._change() as state:
            if shared:
                state["plans_shared"][agent] = {"at": _iso(now or _now())}
            else:
                state["plans_shared"].pop(agent, None)

    def act_level_on(self, agent_id: str, principal: Principal) -> str:
        """这个人对这一个 agent 的办事级别。所有者最高；不在册的成员、没有真人发起的，不能对话。"""
        if principal.is_owner:
            return rules.ACT_LEVELS[-1]
        if not principal.is_member:
            return "none"
        try:
            return self.member_act_level_on(agent_id, principal.id)
        except Missing:
            return "none"

    def member_act_level_on(self, agent_id: str, member_id: str, *, required_permission: Optional[str] = None,
                            requires_shared_plan: bool = False) -> str:
        """在同一份快照核对成员、所需的权限项和对智能体的办事级别。不在册的抛 Missing。"""
        state = self.snapshot()
        held = self._member(state, member_id).get("groups") or []
        posts = self._posts(state)
        if required_permission is not None and not rules.has(required_permission, held, posts):
            return "none"
        if requires_shared_plan and str(agent_id) not in state["plans_shared"]:
            return "none"
        return _member_act_level(state, str(agent_id), member_id, held, posts)

    # ── 身份组 ────────────────────────────────────────────────────────────────

    def create_group(self, actor: Principal, name: str, *, items: Iterable[str] = (), computer: str = "none",
                     act: str = rules.ACT_DEFAULT, before: Optional[str] = None,
                     look: Optional[Mapping[str, Any]] = None) -> str:
        """新建身份组，放在 before 之前（默认放在「所有人」之前，也就是自定义身份组的最后）。"""
        with self._change() as state:
            owner, held = self._actor(state, actor)
            if len(state["groups"]) >= MAX_GROUPS:
                raise ValueError("身份组太多了")
            group_id = f"g_{uuid.uuid4().hex[:12]}"
            wanted = frozenset(str(item) for item in items)
            post = Post(group_id, wanted, str(computer), str(act))
            rules.check_post(post)
            anchor = before or EVERYONE
            if anchor not in state["order"]:
                raise Missing("没有这个身份组")
            order = list(state["order"])
            order.insert(order.index(anchor), group_id)
            posts = self._posts(state)
            if not rules.may_change_post(held, group_id, posts, order, before=frozenset(), after=wanted, owner=owner):
                raise Refused("不能新建这个身份组：位置要排在你之后，权限项要是你自己有的")
            self._check_levels(state, held, posts, owner, computer=post.computer, act=post.act)
            state["groups"][group_id] = {"name": _name(name, "身份组名字"), "items": sorted(wanted),
                                         "computer": post.computer, "act": post.act, "look": _look(look)}
            state["order"] = order
            return group_id

    @staticmethod
    def _check_levels(state: Mapping[str, Any], held: List[str], posts: Mapping[str, Post], owner: bool, *,
                      computer: Optional[str] = None, act: Optional[str] = None) -> None:
        """身份组上的两条阶梯也不能给出自己没有的：定出去的级别不高于操作人自己按身份组得到的。"""
        if owner:
            return
        if computer is not None and not rules.computer_level_at_most(computer, rules.computer_level(held, posts)):
            raise Refused("电脑权限级别不能高于你自己的")
        if act is not None and rules.ACT_LEVELS.index(act) > rules.ACT_LEVELS.index(rules.act_level(held, posts)):
            raise Refused("办事级别不能高于你自己的")

    def change_group(self, actor: Principal, group_id: str, *, name: Optional[str] = None,
                     items: Optional[Iterable[str]] = None, computer: Optional[str] = None,
                     act: Optional[str] = None, look: Optional[Mapping[str, Any]] = None) -> None:
        """改一个身份组；没给的字段不动。look 给了就并进现有的展示信息。"""
        with self._change() as state:
            owner, held = self._actor(state, actor)
            record = state["groups"].get(str(group_id))
            if record is None:
                raise Missing("没有这个身份组")
            posts = self._posts(state)
            current = posts[group_id]
            wanted = current.items if items is None else frozenset(str(item) for item in items)
            if group_id == ADMINS and (items is not None or computer is not None or act is not None):
                raise ValueError("「管理员」身份组的权限是固定的，只能改名字")
            changed = Post(group_id, wanted, current.computer if computer is None else str(computer),
                           current.act if act is None else str(act))
            rules.check_post(changed)
            if not rules.may_change_post(held, group_id, posts, state["order"], before=current.items, after=wanted,
                                         owner=owner):
                raise Refused("不能改这个身份组：它要排在你之后，改动的权限项要是你自己有的")
            self._check_levels(state, held, posts, owner,
                               computer=None if computer is None else changed.computer,
                               act=None if act is None else changed.act)
            if name is not None:
                record["name"] = _name(name, "身份组名字")
            if look is not None:
                record["look"] = _look({**(record.get("look") or {}), **look})
            record.update(items=sorted(wanted), computer=changed.computer, act=changed.act)

    def delete_group(self, actor: Principal, group_id: str) -> None:
        """删除身份组。持有它的成员随之失去它；带着它的邀请在接受时自动跳过它。"""
        with self._change() as state:
            owner, held = self._actor(state, actor)
            if group_id in (EVERYONE, ADMINS):
                raise ValueError("内置的身份组不能删除")
            if group_id not in state["groups"]:
                raise Missing("没有这个身份组")
            posts = self._posts(state)
            if not rules.may_change_post(held, group_id, posts, state["order"], before=posts[group_id].items,
                                         after=frozenset(), owner=owner):
                raise Refused("不能删除这个身份组")
            del state["groups"][group_id]
            state["order"] = [item for item in state["order"] if item != group_id]
            for member in state["members"].values():
                member["groups"] = [item for item in member.get("groups") or [] if item != group_id]
            for channel in state["channels"].values():
                channel["audience_roles"] = [role for role in channel["audience_roles"] if role != group_id]
                channel["overrides"] = [item for item in channel["overrides"]
                                        if item["target"] != {"type": "role", "id": group_id}]
            for member_id in state["members"]:
                self._prune_department_membership(state, member_id)

    def reorder_groups(self, actor: Principal, order: Iterable[str]) -> None:
        with self._change() as state:
            owner, held = self._actor(state, actor)
            wanted = [str(item) for item in order]
            if not rules.may_reorder(held, state["order"], wanted, self._posts(state), owner=owner):
                raise Refused("不能这样调整先后：只能动排在你之后的身份组，「所有人」留在最后")
            state["order"] = wanted

    # ── 成员 ──────────────────────────────────────────────────────────────────

    def set_member_groups(self, actor: Principal, member_id: str, groups: Iterable[str]) -> List[str]:
        """把一个成员的身份组改成给定的这些。加上的和拿掉的每一个，操作人都得授予得了。"""
        with self._change() as state:
            owner, held = self._actor(state, actor)
            member = self._member(state, member_id)
            posts = self._posts(state)
            wanted = list(dict.fromkeys(str(group) for group in groups if group != EVERYONE))
            current = [group for group in member.get("groups") or [] if group in posts]
            for group in set(wanted) ^ set(current):
                if not rules.may_grant(held, group, posts, state["order"], target_held=current, owner=owner):
                    raise Refused(f"不能授予或收回身份组 {group}")
            member["groups"] = wanted
            self._prune_department_membership(state, str(member_id))
            return wanted

    @classmethod
    def _prune_department_membership(cls, state, member_id):
        held = state["members"][member_id].get("groups") or []
        privileged = rules.has(ADMIN, held, cls._posts(state))
        for channel in state["channels"].values():
            if channel["kind"] == "department" and not rules.channel_visible(
                    "department", channel["audience_roles"], held, joined=False, privileged=privileged):
                channel["joined"].pop(member_id, None)

    def remove_member(self, actor: Principal, member_id: str) -> None:
        """把成员移出空间；在各个 agent 上单独给他定的级别一并清掉。成员也可以自己退出。"""
        with self._change() as state:
            owner, held = self._actor(state, actor)
            member = self._member(state, member_id)
            leaving = actor.is_member and actor.id == str(member_id)
            if not leaving and not rules.may_remove_member(held, member.get("groups") or [], self._posts(state),
                                                           state["order"], owner=owner):
                raise Refused("不能移除这个成员")
            del state["members"][str(member_id)]
            for agent_id in list(state["agent_levels"]):
                state["agent_levels"][agent_id].pop(str(member_id), None)
                if not state["agent_levels"][agent_id]:
                    del state["agent_levels"][agent_id]
            for audience in state["agent_audiences"].values():
                if str(member_id) in audience.get("userIds", []):
                    audience["userIds"].remove(str(member_id))
            for channel in state["channels"].values():
                channel["joined"].pop(str(member_id), None)
                channel["overrides"] = [item for item in channel["overrides"]
                                        if item["target"] != {"type": "person", "id": str(member_id)}]

    # ── 邀请 ──────────────────────────────────────────────────────────────────

    def invite(self, actor: Principal, *, groups: Iterable[str] = (), days: int = INVITE_DAYS_DEFAULT,
               note: str = "", email: str = "", now: Optional[datetime] = None) -> Tuple[str, str]:
        """发一份邀请，返回（邀请 id，令牌）。令牌只在这里出现一次，登记里只留它的摘要。

        写了 email 的邀请只有那个邮箱的账号能接受；同一个邮箱只留最新的一份邀请。"""
        address = login_name(email) if email else ""
        if type(days) is not int or not 1 <= days <= INVITE_DAYS_MAX:
            raise ValueError(f"邀请有效期是 1 到 {INVITE_DAYS_MAX} 天")
        moment = now or _now()
        with self._change() as state:
            owner, held = self._actor(state, actor)
            self._drop_expired(state, moment)
            if address:
                if any(member.get("email") == address for member in state["members"].values()):
                    raise ValueError("这个邮箱已经是成员了")
                for stale in [key for key, item in state["invitations"].items() if item.get("email") == address]:
                    del state["invitations"][stale]
            if len(state["invitations"]) >= MAX_INVITATIONS:
                raise ValueError("还没用掉的邀请太多了，先撤回一些")
            try:
                chosen = rules.invite_posts(held, groups, self._posts(state), state["order"], owner=owner)
            except PermissionError as error:
                raise Refused(str(error)) from error
            invitation_id, token = f"i_{uuid.uuid4().hex[:12]}", secrets.token_urlsafe(32)
            state["invitations"][invitation_id] = {
                "digest": _digest(token), "groups": chosen, "by": actor.id, "note": str(note or "")[:200],
                "email": address, "by_kind": actor.kind,
                "created": _iso(moment), "expires": _iso(moment + timedelta(days=days))}
            return invitation_id, token

    @staticmethod
    def _drop_expired(state: Dict[str, Any], moment: datetime) -> None:
        stamp = _iso(moment)
        for invitation_id in [key for key, item in state["invitations"].items()
                              if str(item.get("expires") or "") <= stamp]:
            del state["invitations"][invitation_id]

    def revoke_invitation(self, actor: Principal, invitation_id: str) -> None:
        """撤回邀请：发出的人自己，或有「管理成员」的人。"""
        with self._change() as state:
            owner, held = self._actor(state, actor)
            invitation = state["invitations"].get(str(invitation_id))
            if invitation is None:
                raise Missing("没有这份邀请")
            mine = actor.is_member and invitation.get("by") == actor.id
            if not (owner or mine or rules.has("space.manage_members", held, self._posts(state))):
                raise Refused("不能撤回别人发的邀请")
            del state["invitations"][str(invitation_id)]

    @staticmethod
    def _invited_groups(state: Mapping[str, Any], invitation: Mapping[str, Any]) -> List[str]:
        posts = MemberRegistry._posts(state)
        invited = rules.accepted_posts(invitation.get("groups") or [], posts)
        issuer = invitation.get("by")
        kind = invitation.get("by_kind")
        if kind is None and isinstance(issuer, str) and issuer:
            # Version-one invitations used the generated member id or the owner's UI id.
            kind = MEMBER if _MEMBER_ID.fullmatch(issuer) else OWNER
        if kind == OWNER:
            return invited
        member = state["members"].get(issuer) if kind == MEMBER and isinstance(issuer, str) else None
        if member is not None:
            try:
                return rules.invite_posts(member.get("groups") or [], invited, posts, state["order"])
            except PermissionError:
                pass
        raise Missing("邀请权限已变化，请让有权限的人重新发送")

    def invitation(self, token: str, *, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
        """凭令牌看一份还有效的邀请（不消耗它）。没有或已过期返回 None。"""
        stamp, digest = _iso(now or _now()), _digest(str(token or ""))
        state = self.snapshot()
        for invitation_id, item in state["invitations"].items():
            if str(item.get("expires") or "") > stamp and secrets.compare_digest(str(item.get("digest") or ""), digest):
                try:
                    groups = self._invited_groups(state, item)
                except Missing:
                    return None
                return {**item, "groups": groups, "id": invitation_id}
        return None

    def manages(self, actor: Principal, member_id: str) -> bool:
        """操作人管不管得了这个成员（重置他的密码、移除他）：所有者，或有「管理成员」且排在他之前。"""
        state = self.snapshot()
        member = state["members"].get(str(member_id))
        if member is None:
            raise Missing("没有这个成员")
        try:
            owner, held = self._actor(state, actor)
        except Refused:
            return False
        return rules.may_remove_member(held, member.get("groups") or [], self._posts(state), state["order"],
                                       owner=owner)

    def accept_invitation(self, token: str, name: str, *, email: str = "", account: str = "",
                          now: Optional[datetime] = None) -> str:
        """凭令牌加入，返回新成员的 id。邀请用一次就作废；发出后被删掉的身份组自动跳过。

        邀请写了邮箱的，接受的人必须是那个邮箱（email 由入口从已登录的账号取，不取请求里的）。"""
        moment = now or _now()
        digest = _digest(str(token or ""))
        with self._change() as state:
            self._drop_expired(state, moment)
            found = next((key for key, item in state["invitations"].items()
                          if secrets.compare_digest(str(item.get("digest") or ""), digest)), None)
            if found is None:
                raise Missing("邀请不存在或已失效")
            if len(state["members"]) >= MAX_MEMBERS:
                raise ValueError("成员数已到上限")
            wanted = str(state["invitations"][found].get("email") or "")
            if wanted and wanted != str(email or ""):
                raise Refused("这份邀请是发给另一个邮箱的")
            groups = self._invited_groups(state, state["invitations"][found])
            invitation = state["invitations"].pop(found)
            member_id = f"m_{uuid.uuid4().hex[:12]}"
            state["members"][member_id] = {
                "name": _name(name, "名字"), "joined": _iso(moment), "invited_by": str(invitation.get("by") or ""),
                "email": str(email or ""), "account": str(account or ""),
                "groups": groups}
            return member_id

    # ── 对单个 agent 单独定级别 ───────────────────────────────────────────────

    def audience(self, agent_id: str) -> Dict[str, Any]:
        """这个 agent 的受众；没设过是所有人。"""
        found = self.snapshot()["agent_audiences"].get(str(agent_id))
        return dict(found) if isinstance(found, dict) else {"mode": "everyone", "userIds": [], "roleIds": []}

    def set_audience(self, actor: Principal, agent_id: str, audience: Mapping[str, Any]) -> Dict[str, Any]:
        """定谁能和这个 agent 对话。现在只有所有者能定（agent 都是所有者的）。"""
        agent = str(agent_id)
        if not _AGENT.fullmatch(agent):
            raise ValueError("agent 标识无效")
        with self._change() as state:
            owner, _held = self._actor(state, actor)
            if not owner:
                raise Refused("只有所有者能定谁能和 agent 对话")
            checked = rules.check_audience(audience, state["members"], state["groups"])
            if checked["mode"] == "everyone":
                state["agent_audiences"].pop(agent, None)
            else:
                state["agent_audiences"][agent] = checked
            return dict(checked)

    def forget_agent(self, actor: Principal, agent_id: str) -> None:
        """agent 彻底删除时清掉受众、成员权限和订阅共享设置。"""
        agent = str(agent_id)
        with self._change() as state:
            owner, _held = self._actor(state, actor)
            if not owner:
                raise Refused("只有所有者能删除 agent")
            for key in ("agent_audiences", "agent_levels", "plans_shared", "agent_policies"):
                state[key].pop(agent, None)

    @staticmethod
    def _agent_policy(state, agent, role_version):
        entry = state["agent_policies"].get(agent, {})
        if entry.get("roleVersion") != role_version:
            return {"roleVersion": role_version, "actForMembers": False, "autoApprove": []}
        return dict(entry)

    def member_execution_policy(self, agent, principal, role_version, *, requires_shared_plan=False):
        """Read membership, audience, subscription sharing, and policy from one snapshot."""
        state = self.snapshot()
        member = state["members"].get(principal.id) if principal.is_member else None
        if member is None or (requires_shared_plan and agent not in state["plans_shared"]):
            return "none", False, frozenset()
        level = _member_act_level(state, agent, principal.id, member.get("groups") or [], self._posts(state))
        policy = self._agent_policy(state, agent, role_version)
        return level, policy["actForMembers"], frozenset(policy["autoApprove"])

    @staticmethod
    def _policy_access(state, actor, agent, role):
        owner, held = MemberRegistry._actor(state, actor)
        posts = MemberRegistry._posts(state)
        level = _member_act_level(state, agent, actor.id, held, posts) if not owner else "execute"
        grants = role["grants"]
        can_delegate = rules.may_set_act_for_members(
            level, grants["operations"], holds_permission=rules.has("roles.act_for_members", held, posts),
            private=bool(grants["sees"] or grants["project"]), owner=owner)
        if not can_delegate:
            raise Refused("需要代成员办事权限，且智能体的岗位权限不能超出你自己的权限")
        return owner

    def agent_policy(self, actor, agent, role, role_version, approval_kinds):
        state = self.snapshot()
        owner = self._policy_access(state, actor, agent, role)
        return {**self._agent_policy(state, agent, role_version), "canSetAutoApprove": owner,
                "availableAutoApprove": sorted(approval_kinds) if owner else []}

    def change_agent_policy(self, actor, agent, role, role_version, approval_kinds, change):
        from src.gateway.audit import append_audit

        if not _AGENT.fullmatch(agent):
            raise ValueError("agent 标识无效")
        with self._change() as state:
            owner = self._policy_access(state, actor, agent, role)
            if change.get("roleVersion") != role_version:
                raise ValueError("岗位已变化，请重新读取权限设置")
            if "autoApprove" in change and not owner:
                raise Refused("只有所有者能设置成员放行")
            previous = self._agent_policy(state, agent, role_version)
            result = {**previous, **change}
            if "autoApprove" in change and not set(change["autoApprove"]) <= approval_kinds:
                raise ValueError("只能放行岗位内可用于成员的写入或执行工具")
            if result != previous:
                if not append_audit(self.root, {"category": "event", "operation": "授权修改智能体成员权限",
                                    "actor_kind": actor.kind, "actor_id": actor.id, "staff_member": agent,
                                    "previous": previous, "requested": result}, durable=True):
                    raise OSError("权限变更审计无法保存，未修改权限")
                if result["actForMembers"] or result["autoApprove"]:
                    state["agent_policies"][agent] = result
                else:
                    state["agent_policies"].pop(agent, None)
            return {**result, "canSetAutoApprove": owner,
                    "availableAutoApprove": sorted(approval_kinds) if owner else []}

    @staticmethod
    def _act_level_ceiling(state: Mapping[str, Any], agent: str, actor: Principal, owner: bool, held: List[str],
                           posts: Mapping[str, Post], *, responsible: bool = False) -> str:
        if owner:
            return rules.ACT_LEVELS[-1]
        mine = _member_act_level(state, agent, actor.id, held, posts)
        if mine == "none" or not rules.may_set_act_level(
                "none", actor_level=mine, responsible=responsible, manages_all=rules.has("roles.manage_all", held, posts)):
            raise Refused("只有所有者、能使用此智能体的负责人或智能体管理员能管理办事级别")
        return mine

    def agent_member_action_levels(self, actor: Principal, agent: str) -> Dict[str, Any]:
        """给管理界面的名单、已设的级别和角色默认值，使用同一份登记快照。"""
        state = self.snapshot()
        owner, held = self._actor(state, actor)
        posts = self._posts(state)
        ceiling = self._act_level_ceiling(state, agent, actor, owner, held, posts)
        levels = state["agent_levels"].get(agent) or {}
        audience = state["agent_audiences"].get(agent)
        members = []
        for member_id, member in state["members"].items():
            groups = member.get("groups") or []
            members.append({"userId": member_id, "name": member["name"], "level": levels.get(member_id),
                            "roleLevel": _group_act_level(groups, posts),
                            "inAudience": rules.in_audience(audience, member_id, groups)})
        return {"maxLevel": ceiling, "members": members}

    def set_act_level_on(self, actor: Principal, agent_id: str, member_id: str, level: Optional[str], *,
                         responsible: bool = False) -> None:
        """在一个 agent 上给某个成员单独定办事级别；level 为 None 是撤掉单独定的、回到按身份组算。

        responsible 由调用方查好传入：操作人是不是这个 agent 的负责人。撤掉也按同一条规矩——
        撤掉之后对方回到身份组的级别，那一级同样不能高于操作人自己的。"""
        agent = str(agent_id)
        if not _AGENT.fullmatch(agent):
            raise ValueError("agent 标识无效")
        with self._change() as state:
            owner, held = self._actor(state, actor)
            posts = self._posts(state)
            mine = self._act_level_ceiling(state, agent, actor, owner, held, posts, responsible=responsible)
            member = self._member(state, member_id)
            result = level if level is not None else _group_act_level(member.get("groups") or [], posts)
            if not rules.act_level_at_most(result, mine):
                raise Refused("设定或恢复的办事级别不能高于你自己的级别")
            levels = state["agent_levels"].get(agent) or {}
            if level is None:
                levels.pop(str(member_id), None)
            else:
                levels[str(member_id)] = str(level)
            if levels:
                state["agent_levels"][agent] = levels
            else:
                state["agent_levels"].pop(agent, None)
