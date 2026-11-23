import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

from fastapi.testclient import TestClient

from src.scheduling.notices import record_notice
from src.utils.state_dir import state_dir
from src.workflow.members.registry import MemberRegistry
from tests.unit.test_member_agents import ORIGIN, OWNER_P, TOKEN, _join, home  # noqa: F401 - pytest fixture
from tests.unit.test_team_channels import _create, _override


def _seed(home):
    root = home["root"]
    ledger = state_dir(root) / "logs" / "notices.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    legacy = [{"ts": "2026-09-01T12:00:00Z", "source": "cron:qa", "text": text}
              for text in ("first delivery", "second delivery")]
    ledger.write_text("".join(json.dumps(row) + "\n" for row in legacy), encoding="utf-8")
    owner = home["owner"]
    url = f"{home['base']}/notifications"
    response = owner.get(url, params={"limit": 1})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    page = response.json()
    assert page["counts"] == {"all": 2, "unread": 0, "waiting": 0, "mentions": 0, "reactions": 0}
    assert page["items"][0]["latest"]["text"] == "second delivery"
    later_page = owner.get(url, params={"limit": 1, "cursor": page["nextCursor"]}).json()
    assert later_page["nextCursor"] is None
    assert later_page["items"][0]["latest"]["text"] == "first delivery"
    assert not (state_dir(root) / "logs" / "notice-inbox.json").exists()
    return owner, url, page["items"][0]["id"], later_page["items"][0]["id"]


def test_new_scheduler_notices_have_distinct_ids_and_start_unread(home):
    owner, url, _, _ = _seed(home)
    for _ in range(2):
        record_notice(home["root"], "identical delivery", source="cron:qa")
    items = owner.get(url, params={"filter": "unread"}).json()["items"]
    assert len(items) == 2 and len({item["id"] for item in items}) == 2
    assert {item["latest"]["text"] for item in items} == {"identical delivery"}
    assert owner.get(f"{url}/count").json()["unread"] == 2
    assert owner.post(f"{url}/read-all", json={}).json() == {"ids": [item["id"] for item in items]}
    assert owner.get(f"{url}/count").json()["unread"] == 0


def _everyone_permissions(home):
    roles = home["owner"].get(f"{home['base']}/roles").json()["roles"]
    return next(role["permissions"] for role in roles if role["id"] == "everyone")


def _without_agent_creation(home):
    permissions = _everyone_permissions(home)
    kept = [permission for permission in permissions if permission != "create_agents"]
    assert home["owner"].patch(f"{home['base']}/roles/everyone", json={"permissions": kept}).status_code == 204
    return permissions


def test_member_request_to_create_agent_is_delivered_only_to_the_owner_inbox(home):
    _without_agent_creation(home)
    member = _join(home)
    owner, base = home["owner"], home["base"]
    inbox, ask = f"{base}/notifications", f"{base}/admin-asks"
    before_agents = owner.get(f"{base}/agents").json()
    before_roles = owner.get(f"{base}/roles").json()
    body = {"what": "create_agents"}
    for _ in range(2):
        received = member.post(ask, json=body)
        assert received.status_code == 200 and received.json() == {"sent": 1}

    reply = owner.get(inbox, params={"filter": "unread"}).json()
    item, = reply["items"]
    assert item["kind"] == "admin_ask"
    assert item["actors"] == [{"type": "person", "id": member.member_id}]
    assert item["source"] == {} and item["latest"] == {"reason": "create_agents"}
    assert reply["counts"]["unread"] == 1
    assert member.get(inbox).json()["items"] == []
    assert member.get(f"{inbox}/count").json()["unread"] == 0
    assert owner.get(f"{base}/agents").json() == before_agents
    assert owner.get(f"{base}/roles").json() == before_roles
    assert owner.post(f"{inbox}/{item['id']}/done", json={"done": True}).status_code == 200
    assert owner.get(f"{inbox}/count").json()["unread"] == 0
    assert owner.get(inbox, params={"filter": "done"}).json()["items"][0]["id"] == item["id"]


def test_member_request_checks_identity_scope_and_support_before_writing(home):
    owner, base = home["owner"], home["base"]
    member = _join(home)
    ask = f"{base}/admin-asks"
    body = {"what": "create_agents"}
    assert member.post(ask, json=body).status_code == 403  # the default role still advertises creation
    assert owner.post(ask, json=body).status_code == 403
    assert TestClient(home["app"]).post(ask, json=body).status_code == 401
    permissions = _without_agent_creation(home)
    assert member.post(ask, json=body, headers={"Origin": "https://other.example"}).status_code == 403
    assert member.post(ask, json={**body, "userId": "someone-else"}).status_code == 422
    assert member.post(ask, json={"what": "webhook", "provider": "github"}).status_code == 422
    assert owner.get(f"{base}/notifications").json()["items"] == []
    assert member.post(ask, json=body).status_code == 200
    pending = owner.get(f"{base}/notifications").json()["items"]
    assert len(pending) == 1
    assert owner.patch(f"{base}/roles/everyone", json={"permissions": permissions}).status_code == 204
    assert member.post(ask, json=body).status_code == 403
    assert owner.get(f"{base}/notifications").json()["items"] == pending
    MemberRegistry(home["root"]).remove_member(OWNER_P, member.member_id)
    assert member.post(ask, json=body).status_code == 403
    assert owner.get(f"{base}/notifications").json()["items"] == []


def test_agent_creation_request_can_be_renewed_and_expires(home, monkeypatch):
    owner, base = home["owner"], home["base"]
    _without_agent_creation(home)
    member = _join(home)
    now = datetime(2026, 11, 23, tzinfo=timezone.utc)
    monkeypatch.setattr("src.workflow.members.registry._now", lambda: now)
    ask, inbox = f"{base}/admin-asks", f"{base}/notifications"
    body = {"what": "create_agents"}
    assert member.post(ask, json=body).status_code == 200
    first, = owner.get(inbox).json()["items"]
    now += timedelta(hours=23, minutes=59)
    assert member.post(ask, json=body).status_code == 200
    assert owner.get(inbox).json()["items"] == [first]
    now += timedelta(minutes=1)
    assert member.post(ask, json=body).status_code == 200
    replacement, = owner.get(inbox).json()["items"]
    assert replacement["id"] != first["id"]
    now += timedelta(days=7)
    assert owner.get(inbox).json()["items"] == []


def test_member_channel_agent_ask_reaches_only_owner_and_closes_on_enrollment(home):
    owner, base = home["owner"], home["base"]
    member = _join(home)
    other = _join(home, email="other@example.com")
    inbox, ask = f"{base}/notifications", f"{base}/admin-asks"
    channel = _create(home, "agent-work")
    channel_id = channel.rsplit("/", 1)[1]
    agent_id = home["agent"]["id"]
    body = {"what": "channel_agent", "channelId": channel_id, "agentId": agent_id}
    assert member.post(f"{channel}/join").status_code == 204
    assert other.post(f"{channel}/join").status_code == 204
    state_before = MemberRegistry(home["root"]).snapshot()["channels"]

    for client in (member, member, other):
        received = client.post(ask, json=body)
        assert received.status_code == 200 and received.json() == {"sent": 1}
    reply = owner.get(inbox, params={"filter": "unread"}).json()
    assert len(reply["items"]) == reply["counts"]["unread"] == 2
    assert {item["actors"][0]["id"] for item in reply["items"]} == {member.member_id, other.member_id}
    for item in reply["items"]:
        assert item["kind"] == "admin_ask"
        assert item["source"] == {"channelId": channel_id, "agentId": agent_id, "label": "agent-work"}
        assert item["latest"] == {"reason": "channel_agent"}
    assert member.get(inbox).json()["items"] == []
    assert MemberRegistry(home["root"]).snapshot()["channels"] == state_before
    assert not home["pool"].jobs
    assert member.post(f"{channel}/members", json={"agentId": agent_id}).status_code == 403

    assert owner.post(f"{channel}/members", json={"agentId": agent_id}).status_code == 204
    assert owner.get(inbox).json()["items"] == []
    assert member.post(ask, json=body).status_code == 409
    assert owner.delete(f"{channel}/members/agents/{agent_id}").status_code == 204
    assert owner.get(inbox).json()["items"] == []


def test_member_channel_agent_ask_obeys_current_channel_and_agent_access(home):
    owner, base = home["owner"], home["base"]
    member = _join(home)
    channel = _create(home, "team-work")
    private = _create(home, "private-work", visibility="private")
    channel_id = channel.rsplit("/", 1)[1]
    agent_id = home["agent"]["id"]
    inbox, ask = f"{base}/notifications", f"{base}/admin-asks"
    body = {"what": "channel_agent", "channelId": channel_id, "agentId": agent_id}

    assert owner.post(ask, json=body).status_code == 403
    assert TestClient(home["app"]).post(ask, json=body).status_code == 401
    assert member.post(ask, json={**body, "channelId": private.rsplit("/", 1)[1]}).status_code == 404
    assert member.post(ask, json=body).status_code == 403
    assert member.post(f"{channel}/join").status_code == 204
    assert member.post(ask, json={**body, "agentId": home["main"]["id"]}).status_code == 404
    assert member.post(ask, json={**body, "agentId": "agent-unknown"}).status_code == 404
    assert member.post(ask, json={**body, "by": "someone"}).status_code == 422
    assert member.post(ask, json=body, headers={"Origin": "https://other.example"}).status_code == 403
    assert _override(owner, channel, "person", member.member_id, deny=["post"]).status_code == 200
    assert member.post(ask, json=body).status_code == 403
    assert _override(owner, channel, "person", member.member_id, deny=["talk_to_agents"]).status_code == 200
    assert member.post(ask, json=body).status_code == 403
    assert owner.get(inbox).json()["items"] == []

    assert _override(owner, channel, "person", member.member_id, allow=["talk_to_agents"]).status_code == 200
    assert member.post(ask, json=body).status_code == 200
    assert len(owner.get(inbox).json()["items"]) == 1
    assert owner.delete(channel).status_code == 204
    assert member.post(ask, json=body).status_code == 404
    assert owner.get(inbox).json()["items"] == []
    assert owner.post(f"{channel}/unarchive").status_code == 204
    assert owner.get(inbox).json()["items"] == []
    assert member.post(ask, json=body).status_code == 200
    agent_url = f"{base}/agents/{agent_id}"
    restricted = owner.patch(agent_url, json={"audience": {"mode": "people", "userIds": []}})
    assert restricted.status_code == 200, restricted.text
    assert member.post(ask, json=body).status_code == 404
    assert owner.get(inbox).json()["items"] == []
    assert owner.patch(agent_url, json={"audience": {"mode": "everyone"}}).status_code == 200
    MemberRegistry(home["root"]).remove_member(OWNER_P, member.member_id)
    assert MemberRegistry(home["root"]).snapshot()["channel_agent_asks"] == {}
    assert member.post(ask, json=body).status_code == 403
    assert owner.get(inbox).json()["items"] == []


def test_channel_agent_requests_respect_capacity_and_current_workspace_roles(home, monkeypatch):
    monkeypatch.setattr("src.workflow.members.channels._MAX_AGENT_ASKS_PER_MEMBER", 1)
    owner, base = home["owner"], home["base"]
    member = _join(home)
    permissions = _everyone_permissions(home)
    channels = [_create(home, name) for name in ("first-agent-request", "second-agent-request")]
    for channel in channels:
        assert member.post(f"{channel}/join").status_code == 204
    inbox, ask = f"{base}/notifications", f"{base}/admin-asks"
    agent_id = home["agent"]["id"]
    first, second = [{"what": "channel_agent", "channelId": channel.rsplit("/", 1)[1], "agentId": agent_id}
                     for channel in channels]

    submitted = member.post(ask, json=first)
    assert submitted.status_code == 200 and submitted.json() == {"sent": 1}
    [pending] = owner.get(inbox).json()["items"]
    duplicate = member.post(ask, json=first)
    assert duplicate.status_code == 200 and duplicate.json() == {"sent": 1}
    full = member.post(ask, json=second)
    assert full.status_code == 409 and full.json()["error"]["code"] == "channel_agent_ask.conflict"
    assert owner.get(inbox).json()["items"] == [pending]

    restricted = [permission for permission in permissions if permission != "talk_to_agents"]
    assert owner.patch(f"{base}/roles/everyone", json={"permissions": restricted}).status_code == 204
    denied = member.post(ask, json=first)
    assert denied.status_code == 404 and denied.json()["error"]["code"] == "not_found"
    assert owner.get(inbox).json()["items"] == []
    assert owner.patch(f"{base}/roles/everyone", json={"permissions": permissions}).status_code == 204
    assert owner.get(inbox).json()["items"] == [pending]

    assert owner.post(f"{channels[0]}/members", json={"agentId": agent_id}).status_code == 204
    assert owner.get(inbox).json()["items"] == []
    submitted = member.post(ask, json=second)
    assert submitted.status_code == 200 and submitted.json() == {"sent": 1}
    [replacement] = owner.get(inbox).json()["items"]
    assert replacement["source"] == {"channelId": second["channelId"], "agentId": agent_id,
                                     "label": "second-agent-request"}


def test_member_channel_agent_request_renewal_and_expiration(home, monkeypatch):
    owner, base = home["owner"], home["base"]
    member = _join(home)
    channel = _create(home, "agent-expiry")
    assert member.post(f"{channel}/join").status_code == 204
    now = datetime(2026, 11, 23, tzinfo=timezone.utc)
    monkeypatch.setattr("src.workflow.members.registry._now", lambda: now)
    inbox, ask = f"{base}/notifications", f"{base}/admin-asks"
    body = {"what": "channel_agent", "channelId": channel.rsplit("/", 1)[1], "agentId": home["agent"]["id"]}
    assert member.post(ask, json=body).status_code == 200
    [first] = owner.get(inbox).json()["items"]
    now += timedelta(hours=23, minutes=59)
    assert member.post(ask, json=body).status_code == 200
    assert owner.get(inbox).json()["items"] == [first]
    now += timedelta(minutes=1)
    assert member.post(ask, json=body).status_code == 200
    [second] = owner.get(inbox).json()["items"]
    assert second["id"] != first["id"]
    now += timedelta(days=7)
    assert owner.get(inbox).json()["items"] == []


def test_notice_actions_update_inbox_and_preserve_the_original_ledger(home):
    owner, url, added, older = _seed(home)
    read, done, snooze = (f"{url}/{added}/{action}" for action in ("read", "done", "snooze"))
    ledger = state_dir(home["root"]) / "logs" / "notices.jsonl"
    original = ledger.read_bytes()

    unread = owner.post(read, json={"read": False})
    assert unread.status_code == 200 and unread.json()["readAt"] is None
    assert [row["id"] for row in owner.get(url, params={"filter": "unread"}).json()["items"]] == [added]
    assert owner.get(f"{url}/count").json() == {"unread": 1, "waiting": 0}
    now = datetime.now(timezone.utc)
    snoozed = owner.post(snooze, json={"until": (now + timedelta(hours=1)).isoformat()})
    assert snoozed.status_code == 200 and snoozed.json()["snoozedUntil"].endswith("Z")
    assert owner.get(f"{url}/count").json()["unread"] == 0
    assert owner.post(f"{url}/done-all", json={"filter": "all"}).json() == {"ids": [older]}
    assert owner.get(url, params={"filter": "all"}).json()["items"] == []
    assert owner.post(snooze, json={"until": (now - timedelta(hours=1)).isoformat()}).status_code == 200
    assert owner.get(f"{url}/count").json()["unread"] == 1

    assert owner.post(f"{url}/read-all", json={"filter": "mentions"}).json() == {"ids": []}
    assert owner.get(f"{url}/count").json()["unread"] == 1
    assert owner.post(f"{url}/read-all", json={"filter": "unread"}).json() == {"ids": [added]}
    assert owner.post(f"{url}/read-all", json={}).json() == {"ids": []}
    assert owner.post(snooze, json={"until": None}).json()["snoozedUntil"] is None

    owner.post(read, json={"read": False}).raise_for_status()
    finished = owner.post(done, json={"done": True}).json()
    assert finished["doneAt"] and finished["readAt"]
    assert owner.post(done, json={"done": True}).json() == finished
    done_page = owner.get(url, params={"filter": "done"}).json()
    assert {row["id"] for row in done_page["items"]} == {added, older}
    assert done_page["counts"]["all"] == 0
    assert owner.post(done, json={"done": False}).json()["doneAt"] is None
    fresh_client = TestClient(home["app"], headers={"Authorization": f"Bearer {TOKEN}"})
    assert [row["id"] for row in fresh_client.get(url).json()["items"]] == [added]
    assert ledger.read_bytes() == original


def test_notice_routes_refuse_other_actors_and_invalid_inputs(home):
    owner, url, added, _ = _seed(home)
    member = _join(home)
    assert member.get(url).json()["items"] == []
    assert member.get(f"{url}/count").json() == {"unread": 0, "waiting": 0}

    anonymous = TestClient(home["app"])
    assert anonymous.get(url).status_code == 401
    for tail, body in (("read-all", {}), ("done-all", {}), (f"{added}/done", {"done": True}),
                       (f"{added}/read", {"read": False}), (f"{added}/snooze", {"until": None})):
        assert member.post(f"{url}/{tail}", json=body).status_code == 403
        assert anonymous.post(f"{url}/{tail}", json=body).status_code == 401

    browser = TestClient(home["app"])
    assert browser.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    assert browser.post(f"{url}/{added}/read", json={"read": False}, headers={"Origin": "https://other.test"}).status_code == 403
    assert browser.post(f"{url}/{added}/read", json={"read": False}, headers=ORIGIN).status_code == 200
    assert owner.post(f"{url}/missing/read", json={"read": True}).status_code == 404
    assert owner.post(f"{url}/missing/done", json={"done": True}).status_code == 404
    assert owner.post(f"{url}/missing/snooze", json={"until": None}).status_code == 404
    assert owner.post(f"{url}/{added}/read", json={"read": "false"}).status_code == 422
    assert owner.post(f"{url}/{added}/snooze", json={"until": "tomorrow"}).status_code == 400
    assert owner.post(f"{url}/read-all", json={"filter": "wrong"}).status_code == 422
    assert owner.get(url, params={"cursor": -1}).status_code == 422
    assert owner.post(f"{url}/done-all".replace(home["base"], "/api/app/organizations/other"),
                      json={}).status_code == 404
    assert owner.get(f"{url}/count").json()["unread"] == 1


def test_inbox_state_errors_are_visible_and_never_overwrite_the_bad_file(home):
    owner, url, added, _ = _seed(home)
    path = state_dir(home["root"]) / "logs" / "notice-inbox.json"
    path.write_text("broken", encoding="utf-8")
    assert owner.get(url).status_code == 409
    assert owner.get(f"{url}/count").status_code == 409
    assert owner.post(f"{url}/{added}/read", json={"read": False}).status_code == 409
    assert path.read_text(encoding="utf-8") == "broken"


def test_concurrent_inbox_writes_keep_changes_to_both_notices(home):
    owner, url, first, second = _seed(home)
    start = Barrier(2)

    def mark(notice_id):
        client = TestClient(home["app"], headers={"Authorization": f"Bearer {TOKEN}"})
        start.wait(timeout=5)
        return client.post(f"{url}/{notice_id}/read", json={"read": False})

    with ThreadPoolExecutor(max_workers=2) as workers:
        responses = list(workers.map(mark, (first, second)))
    assert [response.status_code for response in responses] == [200, 200]
    assert owner.get(f"{url}/count").json()["unread"] == 2
