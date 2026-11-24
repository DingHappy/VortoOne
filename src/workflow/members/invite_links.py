"""邀请链接（主人 2026-11-23 定）。

- 所有者开一条链接（可以给身份组、限人数、限期）。拿到链接的人**只能申请**，进门仍要所有者批准。
- 申请不要求登录：名字、邮箱、可选备注和密码。公开接口：限总量，不告诉申请人这个家有谁。
- 邮箱验证码是一个开关（见 join_codes）：
  - 开着且证实了邮箱、申请时设了密码：批准时直接用那份密码摘要建好账号，申请页凭 key 换一次性登录进门；
  - 否则（默认）：批准时生成一份写着他邮箱的邀请链接，由所有者转给本人——
    没证实的邮箱不能直接开账号，不然谁填了同事的邮箱、所有者一批准，他就以同事的身份进来了。
- 记在成员登记里（invite_links、link_requests 两个新键）；旧版本不认识它们，读写时原样保留。
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from src.workflow.members.registry import (INVITE_DAYS_DEFAULT, JoinSuggestionConflict, MemberRegistry, Missing,
                                           Refused, login_name)

TTLS = {"30m": timedelta(minutes=30), "1h": timedelta(hours=1), "1d": timedelta(days=1),
        "7d": timedelta(days=7), "30d": timedelta(days=30), "never": None}
NEVER = timedelta(days=3650)
MAX_USES = (1, 5, 10, 25, 50, 100)
MAX_LINKS = 20
MAX_REQUESTS = 100
REQUEST_DAYS = 14
NAME_MAX, NOTE_MAX = 80, 200


class LinkError(RuntimeError):
    """界面认得的结果：code 是错误码，status 是 HTTP 状态。"""

    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.status = code, status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _text(value: Any, limit: int, what: str, *, required: bool) -> str:
    text = " ".join(value.split()) if isinstance(value, str) else ""
    if (required and not text) or len(text) > limit or (value is not None and not isinstance(value, str)):
        raise LinkError("join.invalid", f"{what}要在 {limit} 字以内" + ("且不能为空" if required else ""), 400)
    if any(ord(ch) < 32 for ch in text):
        raise LinkError("join.invalid", f"{what}不能有控制字符", 400)
    return text


class InviteLinks:
    def __init__(self, registry: MemberRegistry):
        self.registry = registry

    # ── 登记里的两个新键 ──
    @staticmethod
    def _links(state) -> Dict[str, Any]:
        return state.setdefault("invite_links", {})

    @staticmethod
    def _requests(state) -> Dict[str, Any]:
        moment = _iso(_now())
        requests = state.setdefault("link_requests", {})
        for key in [key for key, item in requests.items() if item["expires"] <= moment]:
            del requests[key]                       # 过期没处理的申请：连同密码摘要一起清掉
        return requests

    def _owner(self, state, actor):
        owner, _ = self.registry._actor(state, actor)
        if not owner:
            raise Refused("邀请链接只有所有者能管")

    # ── 所有者：开、改、看、关、续 ──
    def _choices(self, state, actor, choices: Mapping[str, Any]) -> Dict[str, Any]:
        from src.agents.identity_groups import invite_posts
        if not isinstance(choices, Mapping) or set(choices) - {"roleIds", "channelIds", "agentIds", "maxUses", "expiresIn"}:
            raise LinkError("invite.invalid", "邀请链接只接受 roleIds、channelIds、agentIds、maxUses、expiresIn", 400)
        if choices.get("channelIds") or choices.get("agentIds"):
            raise LinkError("invite.unsupported", "批准后直接进频道、配 agent 还不支持：请在对方加入后再设", 400)
        roles = choices.get("roleIds") or []
        if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
            raise LinkError("invite.invalid", "roleIds 要是身份组 id 的列表", 400)
        owner, held = self.registry._actor(state, actor)
        try:
            groups = invite_posts(held, roles, self.registry._posts(state), state["order"], owner=owner)
        except PermissionError as error:
            raise Refused(str(error)) from error
        uses = choices.get("maxUses")
        if uses is not None and uses not in MAX_USES:
            raise LinkError("invite.invalid", "人数上限只能是 1、5、10、25、50、100 或不限", 400)
        ttl = choices.get("expiresIn", "30d")
        if ttl not in TTLS:
            raise LinkError("invite.invalid", "有效期只能是 30m、1h、1d、7d、30d 或 never", 400)
        return {"groups": list(groups), "max_uses": uses, "ttl": ttl}

    @staticmethod
    def _expiry(moment: datetime, ttl: str) -> str:
        span = TTLS[ttl]
        return _iso(moment + (span if span is not None else NEVER))

    def _view(self, state, link_id, link, origin) -> Dict[str, Any]:
        moment = _iso(_now())
        waiting = sum(1 for item in self._requests(state).values() if item["link"] == link_id and item["status"] == "pending")
        names = {key: str(member.get("name") or "") for key, member in state["members"].items()}
        row = {"id": link_id, "url": f"{origin}/invite/{link['token']}", "expiresAt": link["expires"],
               "createdAt": link["created"], "createdBy": names.get(link["by"]) if link["by"] != "owner" else None,
               "createdByUserId": None, "waiting": waiting, "plain": link["plain"], "roleIds": list(link["groups"]),
               "channelIds": [], "agentIds": [], "maxUses": link["max_uses"], "uses": link["uses"]}
        if link["ttl"] == "never":
            row["neverExpires"] = True
        if link["off"] or link["expires"] <= moment:
            row["url"] = None if link["off"] else row["url"]
        return row

    def listing(self, actor, origin: str) -> List[Dict[str, Any]]:
        with self.registry._locked_state() as state:
            self._owner(state, actor)
            moment = _iso(_now())
            return [self._view(state, key, link, origin) for key, link in
                    sorted(self._links(state).items(), key=lambda row: row[1]["created"], reverse=True)
                    if not link["off"] and link["expires"] > moment]

    def create(self, actor, choices: Mapping[str, Any], origin: str) -> Dict[str, Any]:
        """没有选项（或都是默认）：这个家自己的那条链接，续成 30 天；有选项：另开一条。"""
        with self.registry._change() as state:
            self._owner(state, actor)
            picked = self._choices(state, actor, choices or {})
            links, moment = self._links(state), _now()
            plain = not picked["groups"] and picked["max_uses"] is None and picked["ttl"] == "30d"
            if plain:
                for key, link in links.items():
                    if link["plain"] and not link["off"]:
                        link["expires"] = self._expiry(moment, "30d")
                        return self._made(state, key, link, origin)
            if sum(1 for link in links.values() if not link["off"]) >= MAX_LINKS:
                raise LinkError("invite.too_many", "开着的邀请链接太多了，先关掉一些")
            key = f"l_{uuid.uuid4().hex[:12]}"
            links[key] = {"token": secrets.token_urlsafe(24), "by": "owner" if actor.is_owner else actor.id,
                          "created": _iso(moment), "expires": self._expiry(moment, picked["ttl"]),
                          "ttl": picked["ttl"], "plain": plain, "groups": picked["groups"],
                          "max_uses": picked["max_uses"], "uses": 0, "off": None}
            return self._made(state, key, links[key], origin)

    def _made(self, state, key, link, origin):
        view = self._view(state, key, link, origin)
        made = {k: view[k] for k in ("id", "url", "expiresAt", "roleIds", "channelIds", "agentIds", "maxUses")}
        if link["ttl"] == "never":
            made["neverExpires"] = True
        return made

    def edit(self, actor, link_id: str, choices: Mapping[str, Any], origin: str) -> Dict[str, Any]:
        with self.registry._change() as state:
            self._owner(state, actor)
            link = self._links(state).get(link_id)
            if link is None or link["off"]:
                raise Missing("没有这条邀请链接")
            picked = self._choices(state, actor, choices or {})
            link.update(groups=picked["groups"], max_uses=picked["max_uses"], ttl=picked["ttl"],
                        expires=self._expiry(_now(), picked["ttl"]),
                        plain=not picked["groups"] and picked["max_uses"] is None and picked["ttl"] == "30d")
            return self._made(state, link_id, link, origin)

    def turn_off(self, actor, link_id: str) -> None:
        """关掉：谁也不能再凭它申请；经它来的、还没处理的申请一起关掉（没有邮件）。"""
        with self.registry._change() as state:
            self._owner(state, actor)
            link = self._links(state).get(link_id)
            if link is None or link["off"]:
                raise Missing("没有这条邀请链接")
            link["off"] = _iso(_now())
            requests = self._requests(state)
            for key in [key for key, item in requests.items() if item["link"] == link_id and item["status"] == "pending"]:
                del requests[key]

    def renew(self, actor, link_id: str) -> Dict[str, str]:
        with self.registry._change() as state:
            self._owner(state, actor)
            link = self._links(state).get(link_id)
            if link is None or link["off"]:
                raise Missing("没有这条邀请链接")
            link["expires"], link["ttl"] = self._expiry(_now(), "30d"), "30d"
            return {"expiresAt": link["expires"]}

    # ── 公开：凭链接看、申请、等结果 ──
    def _by_token(self, state, token: str) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        for key, link in self._links(state).items():
            if isinstance(token, str) and secrets.compare_digest(link["token"], token):
                return key, link
        return None, None

    def _usable(self, link) -> Tuple[bool, bool]:
        """(开着, 满了)。"""
        open_ = not link["off"] and link["expires"] > _iso(_now())
        full = link["max_uses"] is not None and link["uses"] >= link["max_uses"]
        return open_, full

    def preview(self, token: str, org_name: str) -> Optional[Dict[str, Any]]:
        state = self.registry.snapshot()
        _, link = self._by_token(state, token)
        if link is None:
            return None
        open_, full = self._usable(link)
        shown: Dict[str, Any] = {"orgName": org_name, "email": "", "role": "member", "kind": "link",
                                 "invitedBy": None, "mode": "personal", "agents": []}
        if not open_:
            shown["closed"] = True
        elif full:
            shown["full"] = True
        return shown

    def ask(self, token: str, body: Mapping[str, Any], *, verified: bool) -> Dict[str, Any]:
        """凭链接申请。verified：邮箱已用验证码证实（只有开关开着时才可能）。只有证实了邮箱，
        才收密码、给 key（批准后凭 key 自动登录）；没证实的批准后走邀请链接，由所有者转交。"""
        from src.workflow.members.accounts import MemberAccounts
        if not isinstance(body, Mapping) or set(body) - {"name", "email", "note", "code", "password"}:
            raise LinkError("join.invalid", "申请只接受名字、邮箱、备注、验证码和密码", 400)
        name = _text(body.get("name"), NAME_MAX, "名字", required=True)
        note = _text(body.get("note"), NOTE_MAX, "备注", required=False)
        try:
            email = login_name(body.get("email"))
        except ValueError as error:
            raise LinkError("join.invalid_email", "邮箱格式不对", 400) from error
        sealed = None
        if verified and body.get("password") is not None:
            try:
                sealed = MemberAccounts.seal(body["password"])
            except ValueError as error:
                raise LinkError("join.invalid", str(error), 400) from error
        with self.registry._change() as state:
            link_id, link = self._by_token(state, token)
            if link is None:
                raise Missing("没有这条邀请链接")
            open_, full = self._usable(link)
            if not open_:
                raise LinkError("join.closed", "这条邀请链接已经关掉或过期了")
            if full:
                raise LinkError("join.link_full", "这条邀请链接的名额用完了")
            requests = self._requests(state)
            mine = next((key for key, item in requests.items()
                         if item["link"] == link_id and item["email"] == email and item["status"] == "pending"), None)
            if mine is None and len(requests) >= MAX_REQUESTS:
                raise LinkError("join.full", "等着处理的申请太多了，请稍后再试", 429)
            key = secrets.token_urlsafe(24) if sealed is not None else None
            moment = _now()
            requests[mine or f"r_{uuid.uuid4().hex[:12]}"] = {
                "link": link_id, "name": name, "email": email, "note": note, "verified": bool(verified),
                "sealed": sealed, "key": _digest(key) if key else None, "status": "pending", "reason": None,
                "account": None, "created": _iso(moment), "expires": _iso(moment + timedelta(days=REQUEST_DAYS))}
        # 已经是成员的邮箱也照样回「等着」：申请人从回答里看不出这个家有谁
        shown: Dict[str, Any] = {"status": "pending", "email": email, "emailed": False}
        if key:
            shown["key"] = key
        return shown

    def claim(self, token: str, key: str, org_id: str) -> Optional[Dict[str, Any]]:
        """申请页凭 key 看结果：批准了就换一次性登录。key 对不上任何申请时 None。"""
        from src.workflow.members.accounts import MemberAccounts
        if not isinstance(key, str) or not 16 <= len(key) <= 200:
            return None
        state = self.registry.snapshot()
        link_id, link = self._by_token(state, token)
        if link is None:
            return None
        found = next((item for item in self._requests(state).values()
                      if item["link"] == link_id and item["key"] and secrets.compare_digest(item["key"], _digest(key))), None)
        if found is None:
            return None
        if found["status"] == "pending":
            return {"status": "pending"} if self._usable(link)[0] else {"status": "closed"}
        if found["status"] == "denied":
            return {"status": "denied", "reason": found["reason"]}
        if found["account"]:
            return {"status": "in", "orgId": org_id, "signIn": MemberAccounts(self.registry.root).mint_for_claim(found["account"])}
        return {"status": "closed"}

    # ── 所有者的收件箱：和成员的推荐放在一起 ──
    def inbox(self, actor) -> List[Dict[str, Any]]:
        with self.registry._locked_state() as state:
            self._owner(state, actor)
            links = self._links(state)
            rows = []
            for key, item in sorted(self._requests(state).items(), key=lambda row: row[1]["created"]):
                if item["status"] != "pending":
                    continue
                link = links.get(item["link"]) or {}
                full = link.get("max_uses") is not None and link.get("uses", 0) >= link["max_uses"]
                rows.append({"id": key, "name": item["name"], "email": item["email"], "note": item["note"] or None,
                             "verified": item["verified"], "confirmed": item["verified"], "suggestedBy": None,
                             "link": {"id": item["link"], "createdBy": None, "roleIds": list(link.get("groups") or []),
                                      "channelIds": [], "agentIds": [], "full": bool(full)},
                             "createdAt": item["created"], "expiresAt": item["expires"]})
            return rows

    def is_request(self, request_id: str) -> bool:
        return isinstance(request_id, str) and request_id.startswith("r_")

    def approve(self, actor, request_id: str, origin: str) -> Dict[str, Any]:
        """批准：证实过邮箱且设了密码的直接建账号加入；否则生成写着他邮箱的邀请，由所有者转交。"""
        from src.workflow.members.accounts import MemberAccounts
        accounts = MemberAccounts(self.registry.root)
        with self.registry._change() as state:
            self._owner(state, actor)
            requests = self._requests(state)
            item = requests.get(request_id)
            if item is None or item["status"] != "pending":
                raise JoinSuggestionConflict("join.answered", "申请已处理或过期")
            link = self._links(state).get(item["link"])
            if link is None or link["off"]:
                raise JoinSuggestionConflict("join.answered", "这条邀请链接已经关掉了")
            if link["max_uses"] is not None and link["uses"] >= link["max_uses"]:
                raise JoinSuggestionConflict("join.link_full_approval", "这条链接的名额用完了，先调高人数上限")
            address = item["email"]
            if any(member.get("email") == address for member in state["members"].values()):
                raise JoinSuggestionConflict("join.member", "这个邮箱已经是成员")
            direct = bool(item["verified"] and item["sealed"]) and not accounts.has_email(address)
            _, token = self.registry._issue_invitation(state, actor, address, link["groups"], INVITE_DAYS_DEFAULT,
                                                        "", _now())
            link["uses"] += 1
            item["status"] = "approved"
            if not direct:
                item["sealed"] = None
                return {"outcome": "invited", "url": f"{origin}/invite/{token}", "emailed": False}
            sealed, name = item["sealed"], item["name"]
            item["sealed"] = None
        # 账号和成员各自的登记锁：先建账号，再凭刚开的邀请加入，最后把两边连上（和「接受邀请」同一条路）
        account = accounts.create_sealed(address, name, sealed)
        member = self.registry.accept_invitation(token, name, email=address, account=account)
        accounts.bind_member(account, member)
        with self.registry._change() as state:
            found = self._requests(state).get(request_id)
            if found is not None:
                found["account"] = account
        return {"outcome": "joined"}

    def deny(self, actor, request_id: str) -> None:
        with self.registry._change() as state:
            self._owner(state, actor)
            item = self._requests(state).get(request_id)
            if item is None or item["status"] != "pending":
                raise JoinSuggestionConflict("join.answered", "申请已处理或过期")
            item.update(status="denied", sealed=None)


def links_for(root: str) -> InviteLinks:
    return InviteLinks(MemberRegistry(root))


__all__: Iterable[str] = ("InviteLinks", "LinkError", "links_for")
