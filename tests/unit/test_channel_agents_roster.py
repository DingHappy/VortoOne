"""agent 进团队频道：名册（主人 2026-11-22 定）。

- 只有所有者能把 agent 放进 / 拿出频道；
- 主助理和用本机 CLI 的 agent 第一期不进频道；
- 不 @ 也回答（autoReply）第一期不做，如实拒绝，不假装保存。
"""
from fastapi.testclient import TestClient

from src.workflow.members.registry import MemberRegistry
from tests.unit.test_member_agents import OWNER_P, TOKEN, _join, home  # noqa: F401 - pytest fixture
from tests.unit.test_team_channels import _create


def _roster(client, channel):
    answer = client.get(f"{channel}/roster")
    assert answer.status_code == 200, answer.text
    return answer.json()["members"]


def _agents(client, channel):
    return [(m["id"], m["autoReply"]) for m in _roster(client, channel) if m["type"] == "agent"]


def test_the_owner_adds_and_removes_an_agent_and_everyone_in_the_channel_sees_it(home):
    owner, member = home["owner"], _join(home)
    channel = _create(home, "ops", people=[member.member_id])
    agent = home["agent"]["id"]
    added = owner.post(f"{channel}/members", json={"agentId": agent})
    assert added.status_code == 204, added.text
    assert owner.post(f"{channel}/members", json={"agentId": agent}).status_code == 204        # 再加一次不重复
    assert _agents(owner, channel) == [(agent, False)] and _agents(member, channel) == [(agent, False)]
    [row] = [m for m in _roster(member, channel) if m["type"] == "agent"]
    assert row["name"] == home["agent"]["name"] and row["handle"] == home["staff"]["id"] and row["joinedAt"]
    assert {"type": "agent", "id": agent} in owner.get(f"{channel}/members").json()["members"]
    assert owner.get(f"{channel}/card").json()["agents"] == 1
    listed = owner.get(f"{home['base']}/agents/{agent}/channels").json()["channels"]
    assert listed == [{"id": channel.rsplit("/", 1)[1], "name": "ops", "autoReply": False}]
    assert owner.delete(f"{channel}/members/agents/{agent}").status_code == 204
    assert _agents(owner, channel) == [] and owner.get(f"{channel}/card").json()["agents"] == 0
    assert owner.get(f"{home['base']}/agents/{agent}/channels").json() == {"channels": []}


def test_only_the_owner_may_bring_an_agent_in_or_take_it_out(home):
    owner, member = home["owner"], _join(home)
    channel = _create(home, "ops", people=[member.member_id])
    agent = home["agent"]["id"]
    managers = MemberRegistry(home["root"]).create_group(OWNER_P, "频道管理", items=["chat.manage_channels"])
    MemberRegistry(home["root"]).set_member_groups(OWNER_P, member.member_id, [managers])
    assert member.post(f"{channel}/members", json={"agentId": agent}).status_code == 403
    assert owner.post(f"{channel}/members", json={"agentId": agent}).status_code == 204
    assert member.delete(f"{channel}/members/agents/{agent}").status_code == 403
    assert member.patch(f"{channel}/members/agents/{agent}", json={"autoReply": False}).status_code == 403
    assert member.put(f"{home['base']}/agents/{agent}/channels", json={"channels": []}).status_code in {401, 403}
    assert _agents(owner, channel) == [(agent, False)]


def test_the_main_assistant_and_unknown_agents_stay_out(home):
    owner = home["owner"]
    channel = _create(home, "ops")
    main = owner.post(f"{channel}/members", json={"agentId": home["main"]["id"]})
    assert main.status_code == 409 and main.json()["error"]["code"] == "agent_channels.unsupported"
    assert owner.post(f"{channel}/members", json={"agentId": "agent-nobody"}).status_code == 404
    assert owner.post(f"{channel}/members", json={"agentId": home["agent"]["id"], "userId": "x"}).status_code == 422
    assert _agents(owner, channel) == []


def test_an_agent_on_its_owners_cli_stays_out(home):
    from src.workflow.staff.registry import StaffRegistry
    state = StaffRegistry(home["root"])
    member = state.member(home["staff"]["id"])
    members = [{**m, "engine": "codex"} if m["id"] == member["id"] else m for m in state.members()]
    state._write("members.json", {"version": 1, "members": members})
    refused = home["owner"].post(f"{_create(home, 'ops')}/members", json={"agentId": home["agent"]["id"]})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "agent_channels.unsupported"


def test_answering_without_a_mention_is_refused_honestly(home):
    owner = home["owner"]
    channel = _create(home, "ops")
    agent = home["agent"]["id"]
    assert owner.post(f"{channel}/members", json={"agentId": agent}).status_code == 204
    on = owner.patch(f"{channel}/members/agents/{agent}", json={"autoReply": True})
    assert on.status_code == 409 and on.json()["error"]["code"] == "agent_channels.auto_reply_unavailable"
    assert owner.patch(f"{channel}/members/agents/{agent}", json={"autoReply": False}).status_code == 204
    assert _agents(owner, channel) == [(agent, False)]
    outside = _create(home, "elsewhere")
    assert owner.patch(f"{outside}/members/agents/{agent}", json={"autoReply": False}).status_code == 404


def test_the_agent_page_replaces_its_channels_all_or_nothing(home):
    owner = home["owner"]
    first, second = _create(home, "one"), _create(home, "two")
    ids = [c.rsplit("/", 1)[1] for c in (first, second)]
    url = f"{home['base']}/agents/{home['agent']['id']}/channels"
    saved = owner.put(url, json={"channels": [{"channelId": ids[0]}, {"channelId": ids[1], "autoReply": False}]})
    assert saved.status_code == 200, saved.text
    assert sorted(c["name"] for c in saved.json()["channels"]) == ["one", "two"]
    refused = owner.put(url, json={"channels": [{"channelId": ids[0], "autoReply": True}]})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "agent_channels.auto_reply_unavailable"
    missing = owner.put(url, json={"channels": [{"channelId": ids[0]}, {"channelId": "ch_" + "0" * 32}]})
    assert missing.status_code == 404
    assert sorted(c["name"] for c in owner.get(url).json()["channels"]) == ["one", "two"]   # 失败的两次什么都没改
    assert owner.put(url, json={"channels": [{"channelId": ids[1]}]}).json()["channels"][0]["name"] == "two"
    assert _agents(owner, first) == []
    main = owner.put(f"{home['base']}/agents/{home['main']['id']}/channels", json={"channels": [{"channelId": ids[0]}]})
    assert main.status_code == 409
    assert owner.put(f"{home['base']}/agents/{home['main']['id']}/channels", json={"channels": []}).status_code == 200


def test_archived_channels_take_no_agents_and_a_deleted_agent_leaves_every_list(home):
    owner = home["owner"]
    channel = _create(home, "ops")
    agent = home["agent"]["id"]
    assert owner.post(f"{channel}/members", json={"agentId": agent}).status_code == 204
    archived = _create(home, "old")
    assert owner.delete(archived).status_code == 204
    assert owner.post(f"{archived}/members", json={"agentId": agent}).status_code == 404
    removed = owner.delete(f"{home['base']}/agents/{agent}")
    assert removed.status_code in {200, 204}, removed.text
    assert _agents(owner, channel) == [] and owner.get(f"{channel}/card").json()["agents"] == 0


def test_another_site_cannot_borrow_the_owners_cookie(home):
    from src.web.auth import SESSION_COOKIE
    channel = _create(home, "ops")
    borrowed = TestClient(home["app"])
    borrowed.cookies.set(SESSION_COOKIE, TOKEN)
    refused = borrowed.post(f"{channel}/members", json={"agentId": home["agent"]["id"]},
                            headers={"Origin": "https://evil.example"})
    assert refused.status_code in {401, 403}
    assert _agents(home["owner"], channel) == []
