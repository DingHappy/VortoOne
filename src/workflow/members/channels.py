"""Team channel membership and authorization, separate from personal conversations."""
from __future__ import annotations

import re
import uuid
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

from src.agents import identity_groups as rules
from src.agents.principal import MEMBER, Principal
from src.gateway.session_store import ChannelNameConflict, channel_metadata, normalize_channel
from src.workflow.members import channel_threads
from src.workflow.members.channel_history import AGENT_AUTHOR, OWNER_KEY, ChannelHistory, _before, _stamp
from src.workflow.members.registry import (
    ADMIN_ASK_COOLDOWN, ADMIN_ASK_TTL, MAX_CHANNEL_AGENT_ASKS, Damaged, MemberRegistry, Missing, Refused,
    _iso, _member_act_level,
)
from src.workflow.members.thread_reads import ThreadReader

TEAM_CHANNEL_ID_PATTERN = r"ch_[a-f0-9]{32}"
CHANNEL_CATEGORY_ID_PATTERN = r"cat_[a-f0-9]{32}"
_ID = re.compile(TEAM_CHANNEL_ID_PATTERN)
_CATEGORY_ID = re.compile(CHANNEL_CATEGORY_ID_PATTERN)
CATEGORY_NAME_MAX = 40
MAX_OVERRIDES = 64
_MAX_AGENT_ASKS_PER_MEMBER = 10


class ChannelCategoryNameConflict(ValueError):
    """A category of the same name already exists in this workspace."""


class ChannelAgentAskConflict(ValueError):
    """An agent is already in the channel or the bounded request inbox is full."""


def is_team_channel(channel_id: str) -> bool:
    return bool(_ID.fullmatch(channel_id))


def _category_name(value):
    name = value.strip() if isinstance(value, str) else ""
    if not 1 <= len(name) <= CATEGORY_NAME_MAX or any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValueError(f"频道分组名称需要 1～{CATEGORY_NAME_MAX} 个字，不能包含控制字符")
    return name


@dataclass(frozen=True)
class Access:
    visible: bool
    joined: bool
    privileged: bool
    permissions: frozenset[str]


class TeamChannels:
    def __init__(self, root: str, owner_id: str, owner_name: str = "我"):
        self.registry = MemberRegistry(root)
        self.history = ChannelHistory(self.registry.directory, owner_id)
        from src.workflow.members.channel_cards import ChannelCards
        self.cards = ChannelCards(self.registry.directory)
        self.history.cards = self.cards
        self.owner_id = owner_id
        self.owner_name = owner_name

    @staticmethod
    def _actor_key(actor):
        # The owner's API id depends on the installation path. Channel history survives a home move.
        return OWNER_KEY if actor.is_owner else actor.id

    def _person_key(self, person):
        return OWNER_KEY if person == self.owner_id else person

    @staticmethod
    def _access(state, actor, channel):
        owner, held = MemberRegistry._actor(state, actor)
        posts = MemberRegistry._posts(state)
        privileged = owner or rules.has(rules.ADMIN, held, posts)
        person = TeamChannels._actor_key(actor)
        joined = person in channel["joined"]
        visible = rules.channel_visible(channel["kind"], channel["audience_roles"], held,
                                        joined=joined, privileged=privileged)
        permissions = rules.channel_permissions(person, held, posts, channel["overrides"], owner=owner,
                                                creator=joined and channel["creator"] == person)
        return Access(visible, visible and joined, privileged, permissions)

    def _find(self, state, actor, channel_id, *, archived=None, joined=False, permission=None):
        channel = state["channels"].get(channel_id)
        if channel is None or (archived is not None and bool(channel["archived"]) != archived):
            raise Missing("没有这个频道")
        access = self._access(state, actor, channel)
        if not access.visible:
            raise Missing("没有这个频道")
        if joined and not access.joined:
            raise Refused("加入频道后才能读取或参与")
        if permission and permission not in access.permissions:
            raise Refused("没有执行此操作的频道权限")
        return channel, access

    def _describe(self, channel, access, position):
        flags = access.permissions
        return {"id": channel["id"], "name": channel["title"], "topic": channel["channel"]["topic"],
                "visibility": "public" if channel["kind"] == "public" else "private",
                "audienceRoleIds": channel["audience_roles"], "department": channel["kind"] == "department",
                "categoryId": channel.get("category_id"), "position": position,
                "isMember": access.joined,
                "canRead": access.joined, "seenAsAdmin": access.privileged and not access.joined,
                "createdAt": channel["created"], "createdByUserId": self.history.person_id(channel["creator"]),
                "can": {"post": access.joined and "post" in flags,
                        "callAgents": access.joined and "talk_to_agents" in flags,
                        "manage": "manage_channels" in flags}}

    @staticmethod
    def _ordered_channels(state, archived):
        channels = [channel for channel in state["channels"].values() if bool(channel["archived"]) == archived]
        return sorted(channels, key=lambda channel: channel["archived"], reverse=True) if archived else channels

    def _position(self, state, actor, channel):
        archived = bool(channel["archived"])
        category_id = channel.get("category_id")
        position = 0
        for sibling in self._ordered_channels(state, archived):
            if sibling is channel:
                break
            if sibling.get("category_id") == category_id:
                access = self._access(state, actor, sibling)
                if access.visible and (not archived or "manage_channels" in access.permissions):
                    position += 1
        return position

    def list(self, actor, *, archived=False):
        with self.registry._locked_state() as state, self.history.database() as conn:
            MemberRegistry._actor(state, actor)
            person_key = self._actor_key(actor)
            muted = set() if archived else self.history.muted_channels(conn, person_key)
            result = []
            positions = Counter()
            for channel in self._ordered_channels(state, archived):
                access = self._access(state, actor, channel)
                if not access.visible or (archived and "manage_channels" not in access.permissions):
                    continue
                category_id = channel.get("category_id")
                row = self._describe(channel, access, positions[category_id])
                positions[category_id] += 1
                if archived:
                    row["archivedAt"] = channel["archived"]
                elif access.joined:
                    latest, unread, read_at = self.history.summary(conn, channel["id"], person_key, channel["joined"][person_key])
                    row.update(unreadCount=unread, lastReadAt=read_at, lastMessage=None, muted=channel["id"] in muted)
                    if latest:
                        person = state["members"].get(latest["author"], {})
                        row["lastMessage"] = {"id": latest["id"], "authorType": "person",
                                              "authorId": self.history.person_id(latest["author"]),
                                              "authorName": self.owner_name if latest["author"] == OWNER_KEY else person.get("name"),
                                              "preview": " ".join(latest["body"].split())[:140], "createdAt": latest["created"]}
                result.append(row)
            return result

    @staticmethod
    def _can_manage_categories(state, actor):
        owner, held = MemberRegistry._actor(state, actor)
        return rules.has("chat.manage_channels", held, MemberRegistry._posts(state), owner=owner)

    def _require_category_management(self, state, actor):
        if not self._can_manage_categories(state, actor):
            raise Refused("没有管理频道分组的权限")

    @staticmethod
    def _find_category(state, category_id):
        for index, category in enumerate(state["channel_categories"]):
            if category["id"] == category_id:
                return index, category
        raise Missing("没有这个频道分组")

    @staticmethod
    def _unique_category_name(state, name, excluding=None):
        if any(row["name"].casefold() == name.casefold() and row["id"] != excluding
               for row in state["channel_categories"]):
            raise ChannelCategoryNameConflict("已有同名频道分组，请换一个名称")

    def _visible_category_ids(self, state, actor):
        return {channel["category_id"] for channel in state["channels"].values()
                if channel.get("category_id") is not None and not channel["archived"]
                and self._access(state, actor, channel).visible}

    def _check_channel_category(self, state, actor, category_id):
        if category_id is None:
            return
        self._find_category(state, category_id)
        if not self._can_manage_categories(state, actor) and category_id not in self._visible_category_ids(state, actor):
            raise Missing("没有这个频道分组")

    def list_categories(self, actor):
        state = self.registry.snapshot()
        categories = state["channel_categories"]
        if not self._can_manage_categories(state, actor):
            visible = self._visible_category_ids(state, actor)
            categories = [category for category in categories if category["id"] in visible]
        return [{**category, "position": index} for index, category in enumerate(categories)]

    @staticmethod
    def _move_channel(state, channel, category_id, before_channel_id=None):
        channel["category_id"] = category_id
        channels = state["channels"]
        channel_id = channel["id"]
        channels.pop(channel_id)
        if before_channel_id is None:
            channels[channel_id] = channel
        else:
            rows = list(channels.items())
            rows.insert(list(channels).index(before_channel_id), (channel_id, channel))
            state["channels"] = dict(rows)

    def create_category(self, actor, name, channel_ids=()):
        name = _category_name(name)
        with self.registry._change() as state:
            self._require_category_management(state, actor)
            self._unique_category_name(state, name)
            selected = [self._find(state, actor, channel_id, archived=False, permission="manage_channels")[0]
                        for channel_id in dict.fromkeys(channel_ids)]
            category = {"id": "cat_" + uuid.uuid4().hex, "name": name}
            categories = state["channel_categories"]
            categories.append(category)
            for channel in selected:
                self._move_channel(state, channel, category["id"])
            return {**category, "position": len(categories) - 1}

    def rename_category(self, actor, category_id, name):
        name = _category_name(name)
        with self.registry._change() as state:
            self._require_category_management(state, actor)
            position, category = self._find_category(state, category_id)
            self._unique_category_name(state, name, excluding=category_id)
            category["name"] = name
            return {**category, "position": position}

    def delete_category(self, actor, category_id):
        with self.registry._change() as state:
            self._require_category_management(state, actor)
            position, _ = self._find_category(state, category_id)
            # Detach archived channels too so restoring one cannot leave it in a missing group.
            for channel in list(state["channels"].values()):
                if channel.get("category_id") == category_id:
                    self._move_channel(state, channel, None)
            del state["channel_categories"][position]

    def place_category(self, actor, category_id, before_category_id):
        with self.registry._change() as state:
            self._require_category_management(state, actor)
            position, category = self._find_category(state, category_id)
            if before_category_id == category_id:
                return
            categories = state["channel_categories"]
            before = len(categories) if before_category_id is None else self._find_category(state, before_category_id)[0]
            categories.pop(position)
            if position < before:
                before -= 1
            categories.insert(before, category)

    def place_channel(self, actor, channel_id, category_id, before_channel_id=None):
        with self.registry._change() as state:
            channel, _ = self._find(state, actor, channel_id, archived=False, permission="manage_channels")
            self._check_channel_category(state, actor, category_id)
            if before_channel_id is not None:
                before, _ = self._find(state, actor, before_channel_id, archived=False)
                if before.get("category_id") != category_id:
                    raise ValueError("作为位置参照的频道不在指定分组")
                if before is channel:
                    return
            self._move_channel(state, channel, category_id, before_channel_id)

    def _unique_name(self, state, actor, title, excluding=None):
        if any(row["title"].casefold() == title.casefold() and row["id"] != excluding
               and self._access(state, actor, row).visible
               for row in state["channels"].values()):
            raise ChannelNameConflict("已有同名频道，请换一个名称")

    @staticmethod
    def _audience(state, visibility, roles):
        if visibility not in ("public", "private"):
            raise ValueError("频道可见范围无效")
        if visibility == "public" and roles:
            raise ValueError("公开频道不能绑定身份组")
        if any(role == rules.EVERYONE or role not in state["groups"] for role in roles):
            raise ValueError("部门频道只能绑定现有身份组，不能绑定所有人")
        kind = "department" if roles else visibility
        return kind, list(dict.fromkeys(roles))

    def _person_groups(self, state, person):
        if person == OWNER_KEY:
            return [], True
        held = MemberRegistry._member(state, person).get("groups") or []
        return held, rules.has(rules.ADMIN, held, MemberRegistry._posts(state))

    def _add_person(self, state, channel, person, joined_at):
        held, privileged = self._person_groups(state, person)
        if channel["kind"] == "department" and not rules.channel_visible(
                "department", channel["audience_roles"], held, joined=False, privileged=privileged):
            raise Refused("只能添加持有此频道所需身份组的成员")
        channel["joined"].setdefault(person, joined_at)

    def create(self, actor, name, topic, visibility, roles, initial_people, *, category_id=None):
        title, metadata = normalize_channel(name, topic, name_limit=80)
        with self.registry._change() as state:
            owner, held = MemberRegistry._actor(state, actor)
            if not rules.has("chat.create_channel", held, MemberRegistry._posts(state), owner=owner):
                raise Refused("没有创建频道的权限")
            self._check_channel_category(state, actor, category_id)
            self._unique_name(state, actor, title)
            kind, audience = self._audience(state, visibility, roles)
            stamp = _stamp(datetime.now(timezone.utc))
            channel_id = "ch_" + uuid.uuid4().hex
            person = self._actor_key(actor)
            channel = {"id": channel_id, "title": title, "channel": metadata, "kind": kind,
                       "audience_roles": audience, "creator": person, "created": stamp, "archived": None,
                       "joined": {}, "overrides": [], "category_id": category_id}
            self._add_person(state, channel, person, stamp)
            for person in initial_people:
                self._add_person(state, channel, self._person_key(person), stamp)
            state["channels"][channel_id] = channel
            return self._describe(channel, self._access(state, actor, channel), self._position(state, actor, channel))

    def update(self, actor, channel_id, change):
        with self.registry._change() as state:
            channel, _ = self._find(state, actor, channel_id, archived=False, permission="manage_channels")
            title, metadata = normalize_channel(change.get("name", channel["title"]),
                                                change.get("topic", channel["channel"]["topic"]), name_limit=80)
            self._unique_name(state, actor, title, excluding=channel_id)
            if "visibility" in change or "audienceRoleIds" in change:
                visibility = change.get("visibility", "public" if channel["kind"] == "public" else "private")
                roles = change.get("audienceRoleIds", [] if visibility == "public" else channel["audience_roles"])
                kind, audience = self._audience(state, visibility, roles)
                channel.update(kind=kind, audience_roles=audience)
                if kind == "department":
                    for person in list(channel["joined"]):
                        held, privileged = self._person_groups(state, person)
                        if not rules.channel_visible(kind, audience, held, joined=False, privileged=privileged):
                            del channel["joined"][person]
            channel.update(title=title, channel=metadata)
            access = self._access(state, actor, channel)
            # Someone can restrict a channel enough to lose their own access. Return only a receipt.
            return self._describe(channel, access, self._position(state, actor, channel)) if access.visible else None

    def card(self, actor, channel_id, *, agents=None):
        """agents：调用方认得、还在团队里的 agent（专员 id → 界面资料），已删掉的不算。"""
        state = self.registry.snapshot()
        channel, access = self._find(state, actor, channel_id)
        count = sum(1 for staff in channel.get("agents") or {} if staff in (agents or {}))
        return {**self._describe(channel, access, self._position(state, actor, channel)), "archived": bool(channel["archived"]),
                "people": len(channel["joined"]), "agents": count}

    # ── 频道里的 agent（主人 2026-11-22 定：只有所有者能放进、拿出；第一期不做不 @ 自动回答）──
    # 记在频道记录的 "agents" 里：专员 id → 加入时间。哪些 agent 能进由调用方判定（这里不读专员登记）。

    @staticmethod
    def _require_owner(state, actor):
        owner, _ = MemberRegistry._actor(state, actor)
        if not owner:
            raise Refused("只有所有者能把 agent 放进或移出频道")

    def _agent_ask_channel(self, state, actor, channel_id, staff_id):
        channel, access = self._find(state, actor, channel_id, archived=False, joined=True, permission="post")
        if "talk_to_agents" not in access.permissions:
            raise Refused("你不能在此频道呼叫 agent")
        _, held = MemberRegistry._actor(state, actor)
        if _member_act_level(state, staff_id, actor.id, held, MemberRegistry._posts(state)) == "none":
            raise Missing("没有这个 agent")
        return channel

    def request_agent(self, actor: Principal, channel_id: str, staff_id: str) -> None:
        """Request the owner's decision without changing the channel roster."""
        from src.workflow.members.registry import _now

        if not actor.is_member:
            raise Refused("只有在册成员能提交频道角色申请")
        with self.registry._locked_state() as state:
            channel = self._agent_ask_channel(state, actor, channel_id, staff_id)
            if staff_id in (channel.get("agents") or {}):
                raise ChannelAgentAskConflict("这个 agent 已在频道中")
            now = _now()
            oldest = _iso(now - ADMIN_ASK_TTL)
            asks = {}
            for key, item in state["channel_agent_asks"].items():
                if item["created"] <= oldest:
                    continue
                if (item["by"], item["channel"], item["staff"]) == (actor.id, channel_id, staff_id):
                    if now - datetime.fromisoformat(item["created"]) < ADMIN_ASK_COOLDOWN:
                        return
                    continue
                asks[key] = item
            if (len(asks) >= MAX_CHANNEL_AGENT_ASKS
                    or sum(item["by"] == actor.id for item in asks.values()) >= _MAX_AGENT_ASKS_PER_MEMBER):
                raise ChannelAgentAskConflict("待处理的频道角色申请已满")
            asks[uuid.uuid4().hex] = {"by": actor.id, "channel": channel_id, "staff": staff_id, "created": _iso(now)}
            state["channel_agent_asks"] = asks
            self.registry._save(state)

    def agent_requests(self) -> list[tuple[str, dict[str, str], str]]:
        from src.workflow.members.registry import _now

        state = self.registry.snapshot()
        oldest = _iso(_now() - ADMIN_ASK_TTL)
        rows = []
        for key, item in state["channel_agent_asks"].items():
            if item["created"] <= oldest:
                continue
            actor = Principal(MEMBER, item["by"])
            try:
                channel = self._agent_ask_channel(state, actor, item["channel"], item["staff"])
            except (Missing, Refused):
                continue
            if item["staff"] not in (channel.get("agents") or {}):
                rows.append((key, item, channel["title"]))
        return rows

    @staticmethod
    def _enroll_agent(state, channel, staff_id, stamp):
        channel.setdefault("agents", {}).setdefault(staff_id, stamp)
        state["channel_agent_asks"] = {key: item for key, item in state["channel_agent_asks"].items()
                                       if (item["channel"], item["staff"]) != (channel["id"], staff_id)}

    def set_agent(self, actor, channel_id, staff_id, joined):
        with self.registry._change() as state:
            self._require_owner(state, actor)
            channel, _ = self._find(state, actor, channel_id, archived=False)
            agents = channel.setdefault("agents", {})
            if joined:
                self._enroll_agent(state, channel, staff_id, _stamp(datetime.now(timezone.utc)))
            elif staff_id not in agents:
                raise Missing("这个 agent 不在这个频道里")
            else:
                del agents[staff_id]

    def agents_here(self, actor, channel_id):
        """在频道里的 agent（专员 id）。和名册一样，加入了频道的人才看得到。"""
        state = self.registry.snapshot()
        channel, _ = self._find(state, actor, channel_id, archived=False, joined=True)
        return set(channel.get("agents") or {})

    def can_call_agents(self, actor, channel_id):
        state = self.registry.snapshot()
        _, access = self._find(state, actor, channel_id, archived=False, joined=True)
        return "talk_to_agents" in access.permissions

    def post_as_agent(self, channel_id, staff_id, agent_id, text, *, thread, client, cards_of=None):
        """agent 交回的话。发之前再核对一次：频道还在、没归档、agent 还在里面；不满足就不发（抛 Missing）。
        client 让同一件事的回答只发一次（重试、重启后补发都落到同一条）。"""
        with self.registry._locked_state() as state:
            channel = state["channels"].get(channel_id)
            if channel is None or channel["archived"] or staff_id not in (channel.get("agents") or {}):
                raise Missing("agent 已不在这个频道里，没有发出回答")
            message = self.history.append(channel_id, AGENT_AUTHOR + agent_id, text, client, thread, None, False)
            if cards_of is not None:                # 这件事发的投票、选择卡：和回答同时出现，不留空档
                self.cards.attach(cards_of, message["id"])
                message = self.history.messages(channel_id, None, thread=thread, after=message["sequence"] - 1,
                                                limit=1)[0]
            return message

    # ── agent 在频道里发的投票和选择卡 ──
    def _card(self, state, actor, card_id, *, permission=None):
        card = self.cards.get(card_id)
        if card is None or card["message"] is None:              # 还没跟着回答发出来的卡片谁也看不到
            raise Missing("没有这张卡片")
        channel, access = self._find(state, actor, card["channel"], joined=True, permission=permission)
        return card, channel, access

    def card_view(self, actor, card_id):
        state = self.registry.snapshot()
        card, channel, _ = self._card(state, actor, card_id)
        people = sum(1 for _ in channel["joined"])
        return {**card, "people": people, "votes": self.cards.votes(card_id) if card["kind"] == "poll" else [],
                "answer": self.cards.answer(card_id) if card["kind"] == "choices" else None}

    def vote(self, actor, card_id, options):
        """替换这个人的选择；空列表是收回。截止以后不能再投。"""
        with self.registry._locked_state() as state:
            card, channel, _ = self._card(state, actor, card_id, permission="post")
            if card["kind"] != "poll":
                raise Missing("没有这个投票")
            if channel["archived"] or card["closes"] <= _stamp(datetime.now(timezone.utc)):
                from src.workflow.members.channel_cards import CardConflict
                raise CardConflict("poll.closed", "投票已经截止")
            content = card["content"]
            if (not isinstance(options, list) or any(type(o) is not int or not 0 <= o < len(content["options"])
                                                     for o in options) or len(set(options)) != len(options)
                    or (not content["multiple"] and len(options) > 1)):
                raise ValueError("投票的选项无效")
            self.cards.set_votes(card_id, self._actor_key(actor), sorted(options))

    def answer_choices(self, actor, card_id, picks, others, client):
        """先答的算。答案作为这个人的一条消息发在话题里，返回 (卡片, 消息)；调用方再把它交给 agent。"""
        with self.registry._locked_state() as state:
            card, channel, _ = self._card(state, actor, card_id, permission="post")
            if card["kind"] != "choices" or channel["archived"]:
                raise Missing("没有这张选择卡")
            _, access = self._find(state, actor, card["channel"], joined=True)
            if "talk_to_agents" not in access.permissions:
                raise Refused("你在这个频道里没有叫 agent 办事的权限")
            questions = card["content"]["questions"]
            if (not isinstance(picks, list) or len(picks) != len(questions)
                    or any(type(p) is not int or not 0 <= p <= len(q["options"]) for p, q in zip(picks, questions))):
                raise ValueError("选择卡的答案无效")
            written = others if isinstance(others, list) else []
            if len(written) > len(questions) or any(o is not None and not isinstance(o, str) for o in written):
                raise ValueError("选择卡的答案无效")
            written = [*written, *[None] * (len(questions) - len(written))]
            lines = []
            for index, (pick, question) in enumerate(zip(picks, questions)):
                if pick == len(question["options"]):
                    text = " ".join(str(written[index] or "").split())[:1000]
                    if not text:
                        raise ValueError("写下的答案不能为空")
                    written[index] = text
                    lines.append(f"{question['question']}：{text}")
                else:
                    written[index] = None
                    lines.append(f"{question['question']}：{chr(65 + pick)}. {question['options'][pick]['label']}")
            if not self.cards.claim_answer(card_id, self._actor_key(actor), picks,
                                           written if any(written) else None):
                from src.workflow.members.channel_cards import CardConflict
                raise CardConflict("choices.answered", "这张选择卡已经有人答过了")
            message = self.history.append(card["channel"], self._actor_key(actor), "\n".join(lines), client,
                                          card["thread"], None, False)
            self.cards.answer_message(card_id, message["id"])
            return card, message

    def require_owner(self, actor):
        self._require_owner(self.registry.snapshot(), actor)

    def has_agent(self, actor, channel_id, staff_id):
        state = self.registry.snapshot()
        self._require_owner(state, actor)
        channel, _ = self._find(state, actor, channel_id, archived=False)
        return staff_id in (channel.get("agents") or {})

    def agent_channels(self, actor, staff_id):
        """这个 agent 所在的、没归档的频道（只给所有者看）。"""
        state = self.registry.snapshot()
        self._require_owner(state, actor)
        return [{"id": channel["id"], "name": channel["title"], "autoReply": False}
                for channel in self._ordered_channels(state, False) if staff_id in (channel.get("agents") or {})]

    def replace_agent_channels(self, actor, staff_id, channel_ids):
        """整组替换：先核对全部频道，有一个不对就什么都不改。"""
        with self.registry._change() as state:
            self._require_owner(state, actor)
            wanted = set()
            for channel_id in channel_ids:
                self._find(state, actor, channel_id, archived=False)
                wanted.add(channel_id)
            stamp = _stamp(datetime.now(timezone.utc))
            for channel in state["channels"].values():
                if channel["archived"]:
                    continue
                agents = channel.get("agents") or {}
                if channel["id"] in wanted:
                    self._enroll_agent(state, channel, staff_id, stamp)
                elif staff_id in agents:
                    del agents[staff_id]

    def archive(self, actor, channel_id, archived):
        with self.registry._change() as state:
            channel, _ = self._find(state, actor, channel_id, permission="manage_channels")
            if bool(channel["archived"]) != archived:
                channel["archived"] = _stamp(datetime.now(timezone.utc)) if archived else None
                if archived:
                    state["channel_agent_asks"] = {key: item for key, item in state["channel_agent_asks"].items()
                                                   if item["channel"] != channel_id}

    def join(self, actor, channel_id, joining):
        with self.registry._change() as state:
            channel, access = self._find(state, actor, channel_id, archived=False)
            person = self._actor_key(actor)
            if joining:
                self._add_person(state, channel, person, _stamp(datetime.now(timezone.utc)))
            elif access.joined:
                channel["joined"].pop(person)

    def set_person(self, actor, channel_id, person, joined):
        with self.registry._change() as state:
            channel, _ = self._find(state, actor, channel_id, archived=False, permission="manage_channels")
            person = self._person_key(person)
            if joined:
                self._add_person(state, channel, person, _stamp(datetime.now(timezone.utc)))
            else:
                channel["joined"].pop(person, None)

    def roster(self, actor, channel_id, *, agents=None):
        state = self.registry.snapshot()
        channel, _ = self._find(state, actor, channel_id, archived=False, joined=True)
        people = self._people(state, channel)
        for staff, joined_at in (channel.get("agents") or {}).items():
            agent = (agents or {}).get(staff)
            if agent is not None:                   # 已删掉、调用方不认得的 agent 不列出
                people.append({"type": "agent", "id": agent["id"], "name": agent["name"], "handle": agent["handle"],
                               "autoReply": False, "joinedAt": joined_at})
        return people

    def _people(self, state, channel):
        people = []
        for person, joined_at in channel["joined"].items():
            name = self.owner_name if person == OWNER_KEY else MemberRegistry._member(state, person)["name"]
            people.append({"type": "person", "id": self.history.person_id(person), "name": name, "handle": None,
                           "autoReply": None, "joinedAt": joined_at})
        return people

    def overrides(self, actor, channel_id):
        state = self.registry.snapshot()
        channel, _ = self._find(state, actor, channel_id, archived=False, permission="manage_channels")
        return [self._describe_override(rule) for rule in channel["overrides"]]

    def _describe_override(self, rule):
        if rule["target"]["type"] == "person":
            return {**rule, "target": {"type": "person", "id": self.history.person_id(rule["target"]["id"])}}
        return rule

    def set_override(self, actor, channel_id, target, allow, deny):
        allowed, denied = set(allow), set(deny)
        if allowed & denied or not (allowed | denied) <= rules.CHANNEL_PERMISSIONS.keys():
            raise ValueError("频道例外只能设置发言、唤起角色或管理频道，每项只能选一种状态")
        with self.registry._change() as state:
            channel, access = self._find(state, actor, channel_id, archived=False, permission="manage_channels")
            if target["type"] == "role":
                if target["id"] not in state["groups"]:
                    raise Missing("没有这个身份组")
            else:
                target = {**target, "id": self._person_key(target["id"])}
                self._person_groups(state, target["id"])
            rest = [item for item in channel["overrides"] if item["target"] != target]
            old = next((item for item in channel["overrides"] if item["target"] == target), {"allow": [], "deny": []})
            changed = (set(old["allow"]) ^ allowed) | (set(old["deny"]) ^ denied)
            if not changed <= access.permissions:
                raise Refused("只能修改自己在这个频道持有的权限")
            if not (allowed or denied):
                channel["overrides"] = rest
                return None
            if len(rest) >= MAX_OVERRIDES:
                raise ValueError("频道权限例外已到上限")
            result = {"target": target, "allow": sorted(allowed), "deny": sorted(denied)}
            channel["overrides"] = rest + [result]
            return self._describe_override(result)

    def search(self, actor, needle, *, channel_ids=None, after="", before="", oldest=False, limit=20):
        """调用人读得到的团队频道（已加入、未归档）里含这段文字的消息。after/before 是带时区的 ISO 时间。"""
        after, before = (_before(value) if value else "" for value in (after, before))
        with self.registry._locked_state() as state, self.history.database() as conn:
            MemberRegistry._actor(state, actor)
            readable = {key: channel for key, channel in state["channels"].items()
                        if not channel["archived"] and self._access(state, actor, channel).joined
                        and (channel_ids is None or key in channel_ids)}
            rows = self.history.search(conn, list(readable), needle, after=after, before=before, oldest=oldest,
                                       limit=limit)
            hits = []
            for row in rows:
                author = row["author"]
                name = self.owner_name if author == OWNER_KEY else state["members"].get(author, {}).get("name", "")
                title = None
                if row["thread"]:
                    try:
                        root = self.history.find_by_id(conn, row["thread"])
                    except Missing:
                        root = None
                    title = (root["body"].strip().splitlines() or [""])[0][:80] if root is not None else ""
                hits.append({"message": self.history.message(row, self._actor_key(actor)),
                             "channelName": readable[row["channel"]]["title"], "authorName": name,
                             "threadTitle": title})
            return hits

    def can_read(self, actor, channel_id):
        state = self.registry.snapshot()
        try:
            self._find(state, actor, channel_id, archived=False, joined=True)
        except (Missing, Refused):
            return False
        return True

    @staticmethod
    def readable_public_names(state, actor):
        """Current channel names a Wiki reader can follow from saved public messages."""
        return {key: channel["title"] for key, channel in state["channels"].items()
                if channel["kind"] == "public" and not channel["archived"]
                and TeamChannels._access(state, actor, channel).joined}

    @contextmanager
    def public_message(self, actor, channel_id, message_id):
        """Verify a shareable source and keep channel access stable through the caller's save."""
        with self.registry._locked_state() as state:
            try:
                channel, _ = self._find(state, actor, channel_id, archived=False, joined=True)
            except Refused as error:
                raise Missing("没有这个来源消息") from error
            if channel["kind"] != "public":
                raise Refused("目前只能从加入的公开团队频道摘录消息")
            with self.history.database() as conn:
                message = self.history.find(conn, channel_id, message_id)
                yield {"channelId": channel_id, "messageId": message["id"],
                       "excerpt": " ".join(message["body"].split())[:200], "at": message["created"]}

    def subscribe(self, actor, channel_id):
        with self.registry._locked_state() as state, self.history.database() as conn:
            self._find(state, actor, channel_id, archived=False, joined=True)
            return (self.history.latest_sequence(conn, channel_id), self.history.reaction_cursor(conn, channel_id),
                    self.history.thread_cursor(conn, channel_id))

    def reaction_changes(self, actor, channel_id, after):
        with self.registry._locked_state() as state, self.history.database() as conn:
            self._find(state, actor, channel_id, archived=False, joined=True)
            return self.history.reaction_changes(conn, channel_id, after)
    def thread_changes(self, actor, channel_id, after):
        with self.registry._locked_state() as state, self.history.database() as conn:
            self._find(state, actor, channel_id, archived=False, joined=True)
            return self.history.thread_changes(conn, channel_id, after)

    def messages(self, actor, channel_id, *, before="", limit=100, thread=None, after=None):
        with self.registry._locked_state() as state:
            self._find(state, actor, channel_id, archived=False, joined=True)
            return self.history.messages(channel_id, self._actor_key(actor), before=before, thread=thread, after=after,
                                         limit=min(max(limit, 1), 200))

    def post(self, actor, channel_id, text, *, client_id=None, thread=None, reply_to=None, opened=False, media=None):
        with self.registry._locked_state() as state:
            self._find(state, actor, channel_id, archived=False, joined=True, permission="post")
            return self.history.append(channel_id, self._actor_key(actor), text, client_id, thread, reply_to, opened,
                                       media)

    def check_thread_voice(self, actor, channel_id, thread, reply_to, client, existing_clip):
        with self.registry._locked_state() as state:
            self._find(state, actor, channel_id, archived=False, joined=True, permission="post")
            self.history.check_thread_voice(channel_id, self._actor_key(actor), thread, reply_to, client, existing_clip)

    def _message_access(self, state, conn, actor, message_id):
        row = self.history.find_by_id(conn, message_id)
        try:
            channel, access = self._find(state, actor, row["channel"], archived=False, joined=True)
        except Refused as error:
            raise Missing("没有这条消息") from error
        return row, channel, access

    def _can_manage_thread(self, actor, access, author):
        return "post" in access.permissions and (author == self._actor_key(actor) or "manage_channels" in access.permissions)

    def message_details(self, actor, message_id, *, thread=False):
        with self.registry._locked_state() as state, self.history.database() as conn:
            row, channel, access = self._message_access(state, conn, actor, message_id)
            if thread:
                if row["thread"] is not None:
                    raise Missing("没有这个线程")
                return self.history.thread_summary(conn, row["channel"], row, channel["title"],
                                                   reader=self._thread_reader(actor, channel),
                                                   can_manage=self._can_manage_thread(actor, access, row["author"]))
            name = self.owner_name if row["author"] == OWNER_KEY else state["members"].get(row["author"], {}).get("name", "")
            return {"id": row["id"], "channelId": row["channel"], "threadId": row["thread"],
                    "kind": "channel", "name": channel["title"], "author": name}

    def message_reactions(self, actor, message_id, *, reaction=None, enabled=True):
        with self.registry._locked_state() as state:
            with self.history.database() as conn:
                row, _, access = self._message_access(state, conn, actor, message_id)
                if reaction is None:
                    return self.history.reactions(conn, [row["id"]]).get(row["id"], [])
            if "post" not in access.permissions:
                raise Refused("没有发送频道表情的权限")
            return self.history.set_reaction(row["id"], self._actor_key(actor), reaction, enabled)

    def threads(self, actor, channel_id, *, tab, limit):
        with self.registry._locked_state() as state:
            channel, access = self._find(state, actor, channel_id, archived=False, joined=True)
            if tab not in ("all", "open", "closed", "archived"):
                return []
            return self.history.thread_page(
                {channel_id: channel["title"]}, tab=tab, limit=min(max(limit, 1), 100),
                reader_for=lambda _channel: self._thread_reader(actor, channel),
                can_manage=lambda _channel, author: self._can_manage_thread(actor, access, author),
            )["threads"]

    def organization_threads(self, actor, *, tab, channel_id, agent_id, needs_you, mine, query, cursor, limit):
        with self.registry._locked_state() as state:
            MemberRegistry._actor(state, actor)
            if tab not in ("all", "open", "working", "missions", "closed", "archived"):
                raise ValueError("话题列表分类无效")
            # Team channel history contains conversations between people.
            if tab in ("working", "missions") or agent_id or needs_you:
                return {"threads": [], "nextCursor": None}
            channels = state["channels"]
            if channel_id:
                channels = {channel_id: channels[channel_id]} if channel_id in channels else {}
            titles, accesses = {}, {}
            for cid, channel in channels.items():
                if channel["archived"]:
                    continue
                access = self._access(state, actor, channel)
                if access.joined:
                    titles[cid], accesses[cid] = channel["title"], access
            return self.history.thread_page(
                titles, tab=tab, limit=min(max(limit, 1), 100), cursor=cursor, query=query,
                person=self._actor_key(actor) if mine else "",
                reader_for=lambda cid: self._thread_reader(actor, channels[cid]),
                can_manage=lambda channel, author: self._can_manage_thread(actor, accesses[channel], author),
            )

    def change_thread(self, actor, thread_id, action, *, title=None):
        with self.registry._locked_state() as state:
            with self.history.database() as conn:
                row, channel, access = self._message_access(state, conn, actor, thread_id)
                if row["thread"] is not None or not self.history.has_thread_replies(conn, channel["id"], thread_id):
                    raise Missing("没有这个话题")
                if not self._can_manage_thread(actor, access, row["author"]):
                    raise Refused("没有管理这个话题的权限")
            with self.history.database(write=True) as conn:
                if action == "title":
                    normalized = " ".join(title.split()) if title else ""
                    if len(normalized) > 200 or any(ord(char) < 32 for char in normalized):
                        raise ValueError("话题名称不能超过 200 字或包含控制字符")
                    channel_threads.rename(conn, channel["id"], thread_id, normalized or None)
                else:
                    channel_threads.apply(conn, channel["id"], thread_id, self._actor_key(actor), action,
                                          _stamp(datetime.now(timezone.utc)))
                return self.history.thread_summary(conn, channel["id"], row, channel["title"],
                                                   reader=self._thread_reader(actor, channel), can_manage=True)

    def _thread_reader(self, actor, channel):
        person = self._actor_key(actor)
        return ThreadReader(person, channel["joined"][person])

    def mark_thread_read(self, actor, thread_id):
        with self.registry._locked_state() as state:
            with self.history.database() as conn:
                row, channel, _ = self._message_access(state, conn, actor, thread_id)
                if row["thread"] is not None:
                    raise Missing("没有这个话题")
            self.history.mark_thread_read(channel["id"], thread_id, self._actor_key(actor))

    def set_thread_follow(self, actor, thread_id, following):
        with self.registry._locked_state() as state:
            with self.history.database() as conn:
                try:
                    row, channel, _ = self._message_access(state, conn, actor, thread_id)
                except Missing as error:
                    raise Missing("没有这个话题") from error
                if row["thread"] is not None or not self.history.has_thread_replies(conn, channel["id"], thread_id):
                    raise Missing("没有这个话题")
            self.history.set_thread_follow(thread_id, self._actor_key(actor), following)
            return {"threadId": thread_id, "channelId": channel["id"], "following": following}

    def unread_thread_count(self, actor):
        with self.registry._locked_state() as state:
            MemberRegistry._actor(state, actor)
            readers = {cid: self._thread_reader(actor, channel) for cid, channel in state["channels"].items()
                       if not channel["archived"] and self._access(state, actor, channel).joined}
            return self.history.unread_thread_count(readers)

    def read(self, actor, channel_id):
        with self.registry._locked_state() as state:
            self._find(state, actor, channel_id, archived=False, joined=True)
            self.history.mark_read(channel_id, self._actor_key(actor))

    def set_muted(self, actor, channel_id, muted):
        with self.registry._locked_state() as state:
            self._find(state, actor, channel_id, archived=False, joined=True)
            self.history.set_muted(channel_id, self._actor_key(actor), muted)

    def pins(self, actor, channel_id):
        with self.registry._locked_state() as state:
            self._find(state, actor, channel_id, archived=False, joined=True)
            return self.history.pins(channel_id, self._actor_key(actor))

    def set_pin(self, actor, channel_id, message_id, pinned):
        with self.registry._locked_state() as state:
            self._find(state, actor, channel_id, archived=False, joined=True, permission="post")
            return self.history.set_pin(channel_id, self._actor_key(actor), message_id, pinned)


def check_channels(state):
    """Reject partial or malformed stored policy before serving any channel."""
    categories = state["channel_categories"]
    if not isinstance(categories, list):
        raise Damaged("频道分组记录无效")
    category_ids, category_names = set(), set()
    for category in categories:
        if (not isinstance(category, dict) or set(category) != {"id", "name"}
                or not isinstance(category["id"], str) or not _CATEGORY_ID.fullmatch(category["id"])
                or _category_name(category["name"]) != category["name"] or category["id"] in category_ids
                or category["name"].casefold() in category_names):
            raise Damaged("频道分组记录无效")
        category_ids.add(category["id"])
        category_names.add(category["name"].casefold())

    for channel_id, channel in state["channels"].items():
        if (not is_team_channel(channel_id) or not isinstance(channel, dict) or channel.get("id") != channel_id
                or not isinstance(channel.get("title"), str) or channel_metadata(channel.get("channel")) is None
                or channel.get("kind") not in ("public", "private", "department")
                or not isinstance(channel.get("audience_roles"), list)
                or any(not isinstance(role, str) or role not in state["groups"] for role in channel["audience_roles"])
                or (channel["audience_roles"] and channel["kind"] != "department")
                or not isinstance(channel.get("joined"), dict) or not isinstance(channel.get("overrides"), list)
                or any(not isinstance(joined_at, str) for joined_at in channel["joined"].values())
                or not isinstance(channel.get("creator"), str) or not isinstance(channel.get("created"), str)
                or (channel.get("archived") is not None and not isinstance(channel["archived"], str))
                or not isinstance(channel.get("agents", {}), dict)
                or any(not isinstance(key, str) or not isinstance(value, str)
                       for key, value in channel.get("agents", {}).items())):
            raise Damaged("频道权限记录无效")
        category_id = channel.get("category_id")
        if category_id is not None and (not isinstance(category_id, str) or category_id not in category_ids):
            raise Damaged("频道分组引用无效")
        for rule in channel["overrides"]:
            if (not isinstance(rule, dict) or not isinstance(rule.get("target"), dict)
                    or rule["target"].get("type") not in ("person", "role")
                    or not isinstance(rule["target"].get("id"), str)
                    or any(not isinstance(rule.get(key), list) or any(
                        not isinstance(item, str) or item not in rules.CHANNEL_PERMISSIONS for item in rule[key])
                        for key in ("allow", "deny"))):
                raise Damaged("频道权限例外无效")
