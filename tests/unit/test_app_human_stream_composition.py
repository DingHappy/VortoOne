"""Task and direct chats multiplex without sharing their access rules."""

import pytest

from src.web.app_issue_chat import IssueChat
from src.workflow.members.registry import Damaged
from tests.unit.test_app_board import _new
from tests.unit.test_app_issue_chat import _open as _open_issue, _post as _post_issue
from tests.unit.test_app_person_dms import _open as _open_dm, _path as _dm_path, _send as _send_dm
from tests.unit.test_member_agents import ORIGIN, _join, home  # noqa: F401 - pytest fixture


def test_task_audience_revocation_does_not_disrupt_the_same_members_private_dm(home):
    owner, base = home["owner"], home["base"]
    alice = _join(home, "alice@example.test", "Alice")
    bob = _join(home, "bob@example.test", "Bob")
    dm = _open_dm(home, alice, bob.member_id)
    issue = _new(owner, base)
    task = _open_issue(owner, base, issue)
    task_channel = task["channelId"]
    org = base.rsplit("/", 1)[1]
    with alice.websocket_connect(f"/api/ws?orgId={org}", headers=ORIGIN) as socket:
        assert socket.receive_json()["userId"] == alice.member_id
        for channel in (dm, task_channel):
            socket.send_json({"type": "subscribe", "channelId": channel})
            assert socket.receive_json() == {"type": "subscribed", "channelId": channel}

        message = _send_dm(home, alice, dm, "私信")
        event = socket.receive_json()["event"]
        assert (event["channelId"], event["messageId"]) == (dm, message["id"])
        assert bob.post(_dm_path(home, dm, "read"), json={}).status_code == 204
        assert socket.receive_json()["event"] == {
            "type": "channel.read", "channelId": dm, "userId": bob.member_id, "lastReadAt": message["createdAt"]}

        posted = _post_issue(owner, base, task, "任务对话")
        event = socket.receive_json()["event"]
        assert (event["channelId"], event["threadId"], event["messageId"]) == (
            task_channel, task["rootId"], posted["id"])

        owner_id = owner.get("/api/app/me").json()["user"]["id"]
        changed = owner.put(f"{base}/issues/{issue['id']}/audience",
                            json={"mode": "people", "userIds": [owner_id]})
        assert changed.status_code == 200, changed.text
        _post_issue(owner, base, task, "旧订阅不应收到的新任务内容")
        receipt = _send_dm(home, bob, dm, "私信继续送达")
        frames = {frame["channelId"]: frame for frame in (socket.receive_json(), socket.receive_json())}
        assert frames[task_channel] == {"type": "error", "channelId": task_channel, "code": "channel_access_lost"}
        assert frames[dm]["event"]["messageId"] == receipt["id"]

    assert owner.get(_dm_path(home, dm, "messages")).status_code == 404
    assert alice.get(f"{base}/threads/{task['rootId']}").status_code == 404
    assert alice.get(_dm_path(home, dm, "messages")).json()["messages"][-1]["id"] == receipt["id"]


@pytest.mark.parametrize("already_subscribed", [False, True])
def test_task_subscription_history_failure_leaves_private_dm_active(home, monkeypatch, already_subscribed):
    owner, base = home["owner"], home["base"]
    alice = _join(home, "alice@example.test", "Alice")
    bob = _join(home, "bob@example.test", "Bob")
    dm = _open_dm(home, alice, bob.member_id)
    task = _open_issue(owner, base, _new(owner, base))["channelId"]
    org = base.rsplit("/", 1)[1]
    with alice.websocket_connect(f"/api/ws?orgId={org}", headers=ORIGIN) as socket:
        assert socket.receive_json()["userId"] == alice.member_id
        socket.send_json({"type": "subscribe", "channelId": dm})
        assert socket.receive_json() == {"type": "subscribed", "channelId": dm}
        if already_subscribed:
            socket.send_json({"type": "subscribe", "channelId": task})
            assert socket.receive_json() == {"type": "subscribed", "channelId": task}

        def damaged_history(self, actor, channel):
            assert channel == task
            raise Damaged("Synthetic task history read failure")

        with monkeypatch.context() as failure:
            failure.setattr(IssueChat, "can_read", damaged_history)
            socket.send_json({"type": "subscribe", "channelId": task})
            assert socket.receive_json() == {"type": "error", "channelId": task, "code": "unavailable"}
            receipt = _send_dm(home, bob, dm, "私信仍然能送达")
            event = socket.receive_json()["event"]
            assert (event["channelId"], event["messageId"]) == (dm, receipt["id"])

        socket.send_json({"type": "subscribe", "channelId": task})
        assert socket.receive_json() == {"type": "subscribed", "channelId": task}
