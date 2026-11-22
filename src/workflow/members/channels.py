"""Team channel membership and authorization, separate from personal conversations."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from src.agents import identity_groups as rules
from src.gateway.session_store import ChannelNameConflict, channel_metadata, normalize_channel
from src.workflow.members.channel_history import OWNER_KEY, ChannelHistory, _stamp
from src.workflow.members.registry import Damaged, MemberRegistry, Missing, Refused

TEAM_CHANNEL_ID_PATTERN = r"ch_[a-f0-9]{32}"
_ID = re.compile(TEAM_CHANNEL_ID_PATTERN)
MAX_OVERRIDES = 64


def is_team_channel(channel_id: str) -> bool:
    return bool(_ID.fullmatch(channel_id))


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

    def _describe(self, channel, access):
        flags = access.permissions
        return {"id": channel["id"], "name": channel["title"], "topic": channel["channel"]["topic"],
                "visibility": "public" if channel["kind"] == "public" else "private",
                "audienceRoleIds": channel["audience_roles"], "department": channel["kind"] == "department",
                "isMember": access.joined,
                "canRead": access.joined, "seenAsAdmin": access.privileged and not access.joined,
                "createdAt": channel["created"], "createdByUserId": self.history.person_id(channel["creator"]),
                "can": {"post": access.joined and "post" in flags,
                        "callAgents": access.joined and "talk_to_agents" in flags,
                        "manage": "manage_channels" in flags}}

    def list(self, actor, *, archived=False):
        with self.registry._locked_state() as state, self.history.database() as conn:
            MemberRegistry._actor(state, actor)
            result = []
            for channel in state["channels"].values():
                if bool(channel["archived"]) != archived:
                    continue
                access = self._access(state, actor, channel)
                if not access.visible or (archived and "manage_channels" not in access.permissions):
                    continue
                row = self._describe(channel, access)
                if archived:
                    row["archivedAt"] = channel["archived"]
                elif access.joined:
                    person_key = self._actor_key(actor)
                    latest, unread, read_at = self.history.summary(conn, channel["id"], person_key, channel["joined"][person_key])
                    row.update(unreadCount=unread, lastReadAt=read_at, lastMessage=None)
                    if latest:
                        person = state["members"].get(latest["author"], {})
                        row["lastMessage"] = {"id": latest["id"], "authorType": "person",
                                              "authorId": self.history.person_id(latest["author"]),
                                              "authorName": self.owner_name if latest["author"] == OWNER_KEY else person.get("name"),
                                              "preview": " ".join(latest["body"].split())[:140], "createdAt": latest["created"]}
                result.append(row)
            return sorted(result, key=lambda item: item["archivedAt"], reverse=True) if archived else result

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

    def create(self, actor, name, topic, visibility, roles, initial_people):
        title, metadata = normalize_channel(name, topic, name_limit=80)
        with self.registry._change() as state:
            owner, held = MemberRegistry._actor(state, actor)
            if not rules.has("chat.create_channel", held, MemberRegistry._posts(state), owner=owner):
                raise Refused("没有创建频道的权限")
            self._unique_name(state, actor, title)
            kind, audience = self._audience(state, visibility, roles)
            stamp = _stamp(datetime.now(timezone.utc))
            channel_id = "ch_" + uuid.uuid4().hex
            person = self._actor_key(actor)
            channel = {"id": channel_id, "title": title, "channel": metadata, "kind": kind,
                       "audience_roles": audience, "creator": person, "created": stamp, "archived": None,
                       "joined": {}, "overrides": []}
            self._add_person(state, channel, person, stamp)
            for person in initial_people:
                self._add_person(state, channel, self._person_key(person), stamp)
            state["channels"][channel_id] = channel
            return self._describe(channel, self._access(state, actor, channel))

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
            return self._describe(channel, access) if access.visible else None

    def card(self, actor, channel_id):
        state = self.registry.snapshot()
        channel, access = self._find(state, actor, channel_id)
        return {**self._describe(channel, access), "archived": bool(channel["archived"]),
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

    def subscribe(self, actor, channel_id):
        with self.registry._locked_state() as state:
            self._find(state, actor, channel_id, archived=False, joined=True)
            return self.history.latest_sequence(channel_id)

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

    def message_details(self, actor, message_id, *, thread=False):
        with self.registry._locked_state() as state, self.history.database() as conn:
            row = self.history.find_by_id(conn, message_id)
            try:
                channel, _ = self._find(state, actor, row["channel"], archived=False, joined=True)
            except Refused as error:
                raise Missing("没有这条消息") from error
            if thread:
                if row["thread"] is not None:
                    raise Missing("没有这个线程")
                return self.history.thread_summary(conn, row["channel"], row, channel["title"])
            name = self.owner_name if row["author"] == OWNER_KEY else state["members"].get(row["author"], {}).get("name", "")
            return {"id": row["id"], "channelId": row["channel"], "threadId": row["thread"],
                    "kind": "channel", "name": channel["title"], "author": name}

    def threads(self, actor, channel_id, *, tab, limit):
        with self.registry._locked_state() as state:
            channel, _ = self._find(state, actor, channel_id, archived=False, joined=True)
            return self.history.threads(channel_id, channel["title"], limit=min(max(limit, 1), 100)) if tab in ("all", "open") else []

    def read(self, actor, channel_id):
        with self.registry._locked_state() as state:
            self._find(state, actor, channel_id, archived=False, joined=True)
            self.history.mark_read(channel_id, self._actor_key(actor))

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
        for rule in channel["overrides"]:
            if (not isinstance(rule, dict) or not isinstance(rule.get("target"), dict)
                    or rule["target"].get("type") not in ("person", "role")
                    or not isinstance(rule["target"].get("id"), str)
                    or any(not isinstance(rule.get(key), list) or any(
                        not isinstance(item, str) or item not in rules.CHANNEL_PERMISSIONS for item in rule[key])
                        for key in ("allow", "deny"))):
                raise Damaged("频道权限例外无效")
