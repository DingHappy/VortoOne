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
from src.agents.principal import Principal
from src.utils.state_dir import state_dir

_FILE = "space.json"
_MAX_BYTES = 1024 * 1024
_LOCK = threading.RLock()
_NAME = re.compile(r"^[^\x00-\x1f\x7f]{1,64}$")
_AGENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MAX_GROUPS, MAX_MEMBERS, MAX_INVITATIONS = 64, 500, 200
INVITE_DAYS_DEFAULT, INVITE_DAYS_MAX = 7, 30


class Refused(PermissionError):
    """操作人没有做这件事的权限。"""


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
    }


def _name(value: Any, label: str) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not _NAME.fullmatch(text):
        raise ValueError(f"{label}不能为空，最长 64 个字，不能有控制字符")
    return text


def _post(group_id: str, record: Mapping[str, Any]) -> Post:
    return Post(group_id, frozenset(str(item) for item in record.get("items") or []),
                str(record.get("computer") or "none"), str(record.get("act") or "none"))


class MemberRegistry:
    def __init__(self, root: str):
        self.root = str(root)
        self.directory = state_dir(root) / "members"

    # ── 存取 ──────────────────────────────────────────────────────────────────

    def _load(self) -> Dict[str, Any]:
        path = self.directory / _FILE
        if self.directory.is_symlink():
            raise ValueError("成员登记目录不能是符号链接")
        if not path.exists():
            return _fresh()
        if path.is_symlink() or path.stat().st_size > _MAX_BYTES:
            raise ValueError("成员登记文件无效或过大")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeError, ValueError) as error:
            # 读坏了就停：不回到默认登记，那等于把所有人的身份组悄悄清掉
            raise ValueError("成员登记文件损坏，未恢复默认登记") from error
        if not isinstance(value, dict) or value.get("version") != 1:
            raise ValueError("成员登记文件版本无效")
        for key, kind in (("groups", dict), ("order", list), ("members", dict), ("invitations", dict),
                          ("agent_levels", dict)):
            if not isinstance(value.get(key), kind):
                raise ValueError("成员登记文件结构无效")
        self._check(value)
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
        temporary = self.directory / f".{_FILE}.{uuid.uuid4().hex}.tmp"
        try:
            temporary.write_text(encoded, encoding="utf-8")
            temporary.chmod(0o600)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def _change(self) -> Iterator[Dict[str, Any]]:
        """读—判定—写在一把锁里；跨进程再加文件锁（没有 fcntl 的平台只有进程内的锁）。"""
        with _LOCK:
            handle = None
            try:
                import fcntl
                self.directory.mkdir(parents=True, exist_ok=True)
                handle = open(self.directory / ".lock", "a+")       # noqa: SIM115 —— 锁要持有到写完
                fcntl.flock(handle, fcntl.LOCK_EX)
            except (ImportError, OSError):
                handle = None
            try:
                state = self._load()
                yield state
                self._save(state)
            finally:
                if handle is not None:
                    handle.close()

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

    def act_level_on(self, agent_id: str, principal: Principal) -> str:
        """这个人对这一个 agent 的办事级别。所有者最高；不在册的成员、没有真人发起的，不能对话。"""
        if principal.is_owner:
            return rules.ACT_LEVELS[-1]
        if not principal.is_member:
            return "none"
        state = self.snapshot()
        member = state["members"].get(principal.id)
        if member is None:
            return "none"
        posts = self._posts(state)
        held = member.get("groups") or []
        return rules.act_level_on(state["agent_levels"].get(str(agent_id)) or {}, principal.id,
                                  rules.act_level(held, posts), talks=rules.has("roles.talk", held, posts))

    # ── 身份组 ────────────────────────────────────────────────────────────────

    def create_group(self, actor: Principal, name: str, *, items: Iterable[str] = (), computer: str = "none",
                     act: str = rules.ACT_DEFAULT, before: Optional[str] = None) -> str:
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
                                         "computer": post.computer, "act": post.act}
            state["order"] = order
            return group_id

    @staticmethod
    def _check_levels(state: Mapping[str, Any], held: List[str], posts: Mapping[str, Post], owner: bool, *,
                      computer: Optional[str] = None, act: Optional[str] = None) -> None:
        """身份组上的两条阶梯也不能给出自己没有的：定出去的级别不高于操作人自己按身份组得到的。"""
        if owner:
            return
        if computer is not None and rules.COMPUTER_LEVELS.index(computer) > rules.COMPUTER_LEVELS.index(
                rules.computer_level(held, posts)):
            raise Refused("电脑权限级别不能高于你自己的")
        if act is not None and rules.ACT_LEVELS.index(act) > rules.ACT_LEVELS.index(rules.act_level(held, posts)):
            raise Refused("办事级别不能高于你自己的")

    def change_group(self, actor: Principal, group_id: str, *, name: Optional[str] = None,
                     items: Optional[Iterable[str]] = None, computer: Optional[str] = None,
                     act: Optional[str] = None) -> None:
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
            return wanted

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

    # ── 邀请 ──────────────────────────────────────────────────────────────────

    def invite(self, actor: Principal, *, groups: Iterable[str] = (), days: int = INVITE_DAYS_DEFAULT,
               note: str = "", now: Optional[datetime] = None) -> Tuple[str, str]:
        """发一份邀请，返回（邀请 id，令牌）。令牌只在这里出现一次，登记里只留它的摘要。"""
        if type(days) is not int or not 1 <= days <= INVITE_DAYS_MAX:
            raise ValueError(f"邀请有效期是 1 到 {INVITE_DAYS_MAX} 天")
        moment = now or _now()
        with self._change() as state:
            owner, held = self._actor(state, actor)
            self._drop_expired(state, moment)
            if len(state["invitations"]) >= MAX_INVITATIONS:
                raise ValueError("还没用掉的邀请太多了，先撤回一些")
            try:
                chosen = rules.invite_posts(held, groups, self._posts(state), state["order"], owner=owner)
            except PermissionError as error:
                raise Refused(str(error)) from error
            invitation_id, token = f"i_{uuid.uuid4().hex[:12]}", secrets.token_urlsafe(32)
            state["invitations"][invitation_id] = {
                "digest": _digest(token), "groups": chosen, "by": actor.id, "note": str(note or "")[:200],
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

    def accept_invitation(self, token: str, name: str, *, now: Optional[datetime] = None) -> str:
        """凭令牌加入，返回新成员的 id。邀请用一次就作废；发出后被删掉的身份组自动跳过。"""
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
            invitation = state["invitations"].pop(found)
            member_id = f"m_{uuid.uuid4().hex[:12]}"
            state["members"][member_id] = {
                "name": _name(name, "名字"), "joined": _iso(moment), "invited_by": str(invitation.get("by") or ""),
                "groups": rules.accepted_posts(invitation.get("groups") or [], self._posts(state))}
            return member_id

    # ── 对单个 agent 单独定级别 ───────────────────────────────────────────────

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
            member = self._member(state, member_id)
            posts = self._posts(state)
            levels = state["agent_levels"].get(agent) or {}
            mine = rules.act_level_on(levels, actor.id, rules.act_level(held, posts), owner=owner,
                                      talks=rules.has("roles.talk", held, posts, owner=owner))
            target = member.get("groups") or []
            result = level if level is not None else rules.act_level_on(
                {}, str(member_id), rules.act_level(target, posts), talks=rules.has("roles.talk", target, posts))
            if not rules.may_set_act_level(str(result), actor_level=mine, responsible=responsible, owner=owner,
                                           manages_all=rules.has("roles.manage_all", held, posts, owner=owner)):
                raise Refused("不能定这个级别：要是这个 agent 的负责人或能管理所有 agent，且不高于你自己的级别")
            if level is None:
                levels.pop(str(member_id), None)
            else:
                levels[str(member_id)] = str(level)
            if levels:
                state["agent_levels"][agent] = levels
            else:
                state["agent_levels"].pop(agent, None)
