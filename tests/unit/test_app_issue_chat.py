"""Desktop task conversations reuse human chat under the live board audience."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.web.app_issue_chat import IssueChat
from src.workflow.members.channel_history import ChannelHistory
from src.workflow.members.registry import Damaged, MemberRegistry
from tests.unit.test_app_board import _drop_from_everyone, _new
from tests.unit.test_member_agents import ORIGIN, OWNER_P, TOKEN, _join, home  # noqa: F401 - pytest fixture
from tests.unit.test_team_channels import _create


def _open(client, base, issue):
    response = client.post(f"{base}/issues/{issue['id']}/conversation")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    ref = response.json()
    assert set(ref) == {"channelId", "rootId"}
    return ref


def _message_path(base, ref):
    return f"{base}/channels/{ref['channelId']}/messages"


def _post(client, base, ref, text, **extra):
    response = client.post(_message_path(base, ref), json={
        "body": text, "threadId": ref["rootId"], **extra,
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_desktop_opens_one_durable_task_thread_and_projects_actual_replies(home, monkeypatch):
    owner, base = home["owner"], home["base"]
    member = _join(home)
    issue = _new(owner, base)
    detail = f"{base}/issues/by-number/{issue['number']}"
    assert owner.get(detail).json()["conversation"] is None
    with ThreadPoolExecutor(max_workers=4) as pool:
        refs = list(pool.map(lambda client: _open(client, base, issue), [owner, member, owner, member]))
    ref = refs[0]
    assert refs == [ref] * 4

    timeline = _message_path(base, ref)
    thread = f"{base}/threads/{ref['rootId']}"
    info = member.get(thread).json()
    assert info["channelKind"] == "task" and info["id"] == ref["rootId"]
    assert info["channelId"] == ref["channelId"] and info["participants"] == []
    assert member.get(timeline, params={"threadId": ref["rootId"]}).json() == {"messages": []}
    assert member.get(detail).json()["conversation"] == {
        **ref, "count": 0, "lastAt": None, "participants": [], "working": False,
    }
    monkeypatch.setattr("src.web.routers.app_api._call_channel_agents",
                        lambda *a, **kw: pytest.fail("任务对话不能派活"))
    first = _post(member, base, ref, "任务交流内容 @example-agent", clientId="first")
    second = _post(owner, base, ref, "原话题内回复", replyTo=first["id"])
    assert _post(member, base, ref, first["body"], clientId="first") == first
    assert owner.get(timeline).json() == {"messages": []}
    restored = TestClient(home["app"], headers=owner.headers)
    rows = restored.get(timeline, params={"threadId": ref["rootId"]}).json()["messages"]
    assert [row["id"] for row in rows] == [first["id"], second["id"]]
    assert rows[1]["meta"]["replyTo"] == first["id"]
    summary = {**ref, "count": 2, "lastAt": second["createdAt"],
               "participants": [first["author"], second["author"]], "working": False}
    detail_row = restored.get(detail)
    assert detail_row.headers["cache-control"] == "no-store"
    assert detail_row.json()["conversation"] == summary
    second_card = _new(owner, base, title="未开启对话")
    history_opens = []
    database = ChannelHistory.database

    @contextmanager
    def count_history(self, **kwargs):
        history_opens.append(kwargs)
        with database(self, **kwargs) as conn:
            yield conn

    with monkeypatch.context() as count:
        count.setattr(ChannelHistory, "database", count_history)
        rows = restored.get(f"{base}/issues").json()["issues"]
        assert {row["id"]: row["conversation"] for row in rows} == {
            issue["id"]: summary, second_card["id"]: None}
        assert history_opens == [{}]
    assert member.get(thread).json()["replyCount"] == 2
    assert member.post(thread + "/read").status_code == 204
    assert member.get(thread).json()["unread"] == 0
    assert owner.get(f"{base}/channels/{ref['channelId']}/active-runs").json() == {"runs": []}
    assert home["pool"].jobs == {}
    # Existing task activity has its own meaning and doesn't include synthetic root or chat replies.
    assert len(owner.get(f"{base}/issues/{issue['id']}/activity").json()["activity"]) == 1


def test_task_chat_endpoints_and_subscription_lose_access_with_the_board_card(home):
    owner, base = home["owner"], home["base"]
    member = _join(home)
    other = _join(home, email="other@example.test", name="其他成员")
    MemberRegistry(home["root"]).set_member_groups(OWNER_P, other.member_id, ["admins"])
    owner_id = owner.get("/api/app/me").json()["user"]["id"]
    issue = _new(owner, base, audience={"mode": "people", "userIds": [owner_id]},
                 assignee={"type": "person", "id": member.member_id})
    card = f"{base}/issues/{issue['id']}"
    assert other.post(card + "/conversation").status_code == 404
    ref = _open(owner, base, issue)
    assert other.post(card + "/conversation").status_code == 404
    timeline = _message_path(base, ref)
    channel = f"{base}/channels/{ref['channelId']}"
    thread = f"{base}/threads/{ref['rootId']}"
    org_id = base.rsplit("/", 1)[1]
    with member.websocket_connect(f"/api/ws?orgId={org_id}", headers=ORIGIN) as socket:
        assert socket.receive_json()["userId"] == member.member_id
        socket.send_json({"type": "subscribe", "channelId": ref["channelId"]})
        assert socket.receive_json() == {"type": "subscribed", "channelId": ref["channelId"]}
        sent = _post(owner, base, ref, "负责人能收到")
        assert socket.receive_json()["event"]["message"]["id"] == sent["id"]
        assert owner.patch(card, json={"assignee": {"type": "person", "id": other.member_id},
                                      "columnId": "col-in_progress"}).status_code == 200
        _post(owner, base, ref, "已经换了负责人")
        assert socket.receive_json() == {"type": "error", "code": "channel_access_lost", "channelId": ref["channelId"]}
        socket.send_json({"type": "subscribe", "channelId": ref["channelId"]})
        assert socket.receive_json()["code"] == "channel_access_lost"

    assert _open(other, base, issue) == ref
    assert other.get(thread).status_code == 200
    for path in (timeline, thread, channel + "/members", channel + "/active-runs"):
        assert member.get(path).status_code == 404
    for path, body in ((card + "/conversation", {}), (thread + "/read", {}),
                       (timeline, {"body": "过期的发送", "threadId": ref["rootId"]})):
        assert member.post(path, json=body).status_code == 404
    assert owner.put(card + "/audience", json={"mode": "people", "userIds": [member.member_id]}).status_code == 200
    assert member.get(thread).status_code == 200
    assert owner.patch(card, json={"assignee": None}).status_code == 200
    assert other.get(thread).status_code == 404
    assert owner.put(card + "/audience", json={"mode": "people", "userIds": [owner_id]}).status_code == 200
    assert member.get(thread).status_code == 404
    assert owner.delete(card).status_code == 204
    assert owner.get(thread).status_code == 404
    assert owner.post(card + "/conversation").status_code == 404


def test_task_conversation_does_not_grant_new_channel_or_board_permissions(home):
    owner, base = home["owner"], home["base"]
    member = _join(home)
    issue = _new(owner, base)
    frontend_cross_origin = TestClient(home["app"], headers={"Origin": "https://elsewhere.example"})
    assert frontend_cross_origin.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    start = f"{base}/issues/{issue['id']}/conversation"
    assert owner.post(start, json={"threadId": "ignored", "empty": False}).status_code == 422
    assert owner.get(f"{base}/issues/by-number/{issue['number']}").json()["conversation"] is None
    assert frontend_cross_origin.post(start).status_code == 403
    _drop_from_everyone(home, "edit_wiki")
    assert member.post(start).status_code == 403
    assert TestClient(home["app"]).post(start).status_code == 401
    ref = _open(owner, base, issue)
    assert _open(member, base, issue) == ref
    assert frontend_cross_origin.get(f"{base}/threads/{ref['rootId']}").status_code == 403
    messages = _message_path(base, ref)
    channel = f"{base}/channels/{ref['channelId']}"
    denied = member.post(messages, json={"body": "不可写", "threadId": ref["rootId"]})
    assert denied.status_code == 403
    existing = _create(home, "other-channel")
    existing_post = owner.post(existing + "/messages", json={"body": "外部引用"}).json()
    posted = _post(owner, base, ref, "只在任务中的检索词")
    for body in ({"body": "没有话题"}, {"body": "错话题", "threadId": existing_post["id"]},
                 {"body": "跨会话引用", "threadId": ref["rootId"], "replyTo": existing_post["id"]}):
        assert owner.post(messages, json=body).status_code == 404
    assert owner.post(messages, json={"body": "附件", "threadId": ref["rootId"], "media": {"kind": "gif"}}).status_code == 422
    body = {"body": "附件", "threadId": ref["rootId"]}
    for extra in ({"attachments": ["artifact-id"]}, {"steer": "now"}, {"openThread": True}):
        assert owner.post(messages, json={**body, **extra}).status_code == 422
    assert owner.post(messages, json={**body, "unknown": False}).status_code == 400
    assert owner.post(channel + "/join").status_code == 404
    assert owner.get(channel + "/permissions").status_code == 404
    assert owner.post(channel + "/members", json={"agentId": home["agent"]["id"]}).status_code == 404
    assert owner.post(f"{base}/threads/{ref['rootId']}/archive").status_code == 404
    assert owner.get(f"{base}/messages/{posted['id']}/card").status_code == 404
    assert ref["channelId"] not in owner.get(f"{base}/channels").text
    assert ref["rootId"] not in owner.get(f"{base}/threads").text
    result = owner.get(f"{base}/search", params={"q": "只在任务中的检索词"})
    assert result.status_code == 200 and posted["id"] not in result.text


def test_removed_member_loses_the_existing_task_socket(home):
    owner, base = home["owner"], home["base"]
    member = _join(home)
    issue = _new(owner, base)
    ref = _open(owner, base, issue)
    org_id = base.rsplit("/", 1)[1]
    with member.websocket_connect(f"/api/ws?orgId={org_id}", headers=ORIGIN) as socket:
        assert socket.receive_json()["userId"] == member.member_id
        socket.send_json({"type": "subscribe", "channelId": ref["channelId"]})
        assert socket.receive_json() == {"type": "subscribed", "channelId": ref["channelId"]}
        assert owner.delete(f"{base}/members/{member.member_id}").status_code == 204
        _post(owner, base, ref, "已经移除成员")
        with pytest.raises(WebSocketDisconnect) as disconnected:
            socket.receive_json()
        assert disconnected.value.code == 1008
    assert member.get(_message_path(base, ref), params={"threadId": ref["rootId"]}).status_code == 401


def test_successful_board_writes_survive_optional_chat_summary_failure(home, monkeypatch, caplog):
    owner, base = home["owner"], home["base"]
    owner_id = owner.get("/api/app/me").json()["user"]["id"]
    issue = _new(owner, base, criteria=["需复核"],
                 audience={"mode": "people", "userIds": [owner_id]})
    ref = _open(owner, base, issue)
    _post(owner, base, ref, "已有任务消息")
    card = f"{base}/issues/{issue['id']}"
    detail = f"{base}/issues/by-number/{issue['number']}"
    criterion = f"{card}/criteria/{issue['criteria'][0]['id']}"

    def damaged(*args):
        raise Damaged("synthetic summary failure")

    with monkeypatch.context() as failed_projection:
        failed_projection.setattr(IssueChat, "cards", damaged)
        moved = owner.patch(card, json={"columnId": "col-in_progress"})
        shared = owner.post(card + "/share")
        checked = owner.put(criterion, json={"done": True})
        for response in (moved, shared, checked):
            assert response.status_code == 200
            assert "conversation" not in response.json()
            assert response.headers["cache-control"] == "no-store"
        assert moved.json()["columnId"] == "col-in_progress"
        assert shared.json()["audience"]["mode"] == "everyone"
        assert checked.json()["criteria"][0]["done"]
        for route in (detail, f"{base}/issues"):
            denied = owner.get(route)
            assert denied.status_code == 409
            assert denied.json()["error"]["code"] == "unavailable"

    assert caplog.messages.count("看板已更新，本次响应省略任务对话摘要") == 3
    saved = owner.get(detail).json()
    assert saved["columnId"] == "col-in_progress"
    assert saved["audience"]["mode"] == "everyone"
    assert saved["criteria"][0]["done"]
    assert saved["conversation"]["count"] == 1
