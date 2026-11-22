"""Team channel membership and authorization, separate from personal conversations."""
from __future__ import annotations

import re
import uuid
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

from src.agents import identity_groups as rules
from src.gateway.session_store import ChannelNameConflict, channel_metadata, normalize_channel
from src.workflow.members import channel_threads
from src.workflow.members.channel_history import OWNER_KEY, ChannelHistory, _stamp
from src.workflow.members.registry import Damaged, MemberRegistry, Missing, Refused
from src.workflow.members.thread_reads import ThreadReader

TEAM_CHANNEL_ID_PATTERN = r"ch_[a-f0-9]{32}"
CHANNEL_CATEGORY_ID_PATTERN = r"cat_[a-f0-9]{32}"
_ID = re.compile(TEAM_CHANNEL_ID_PATTERN)
_CATEGORY_ID = re.compile(CHANNEL_CATEGORY_ID_PATTERN)
CATEGORY_NAME_MAX = 40
MAX_OVERRIDES = 64


class ChannelCategoryNameConflict(ValueError):
    """A category of the same name already exists in this workspace."""


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

    def card(self, actor, channel_id):
        state = self.registry.snapshot()
        channel, access = self._find(state, actor, channel_id)
        return {**self._describe(channel, access, self._position(state, actor, channel)), "archived": bool(channel["archived"]),
                "people": len(channel["joined"]), "agents": 0}

    def archive(self, actor, channel_id, archived):
        with self.registry._change() as state:
            channel, _ = self._find(state, actor, channel_id, permission="manage_channels")
            if bool(channel["archived"]) != archived:
                channel["archived"] = _stamp(datetime.now(timezone.utc)) if archived else None

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

    def roster(self, actor, channel_id):
        state = self.registry.snapshot()
        channel, _ = self._find(state, actor, channel_id, archived=False, joined=True)
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
                or (channel.get("archived") is not None and not isinstance(channel["archived"], str))):
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
