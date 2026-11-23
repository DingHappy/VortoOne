"""真实 WebSocket 上的成员隔离、任务结果推送、重连和撤销；执行器沿用离线 staff fixture。"""
import asyncio
from functools import partial
import json

import pytest
from starlette.websockets import WebSocket, WebSocketDisconnect

from src.web import app_member_realtime
from src.workflow.members.registry import MemberRegistry
from tests.unit.test_member_agents import OWNER_P, _finish, _join, home  # noqa: F401


@pytest.fixture(autouse=True)
def fast_updates(monkeypatch):
    monkeypatch.setattr(app_member_realtime, "serve", partial(app_member_realtime.serve, interval=0.01))


def url(home):
    return "/api/ws?orgId=" + home["base"].rsplit("/", 1)[1]


def subscribe(socket, channel):
    socket.send_json({"type": "subscribe", "channelId": channel})
    assert socket.receive_json() == {"type": "subscribed", "channelId": channel}


def until(socket, predicate):
    frames = []
    for _ in range(30):
        frame = socket.receive_json()
        frames.append(frame)
        if predicate(frame):
            return frames
    pytest.fail("没有收到预期的实时帧")


def test_result_arrives_without_refresh_and_reconnect_replays_only_my_conversation(home):
    member, other = _join(home), _join(home, "other@example.com", "小李")
    endpoint = f"{home['base']}/channels/{home['channel']}/messages"
    home["owner"].post(endpoint, json={"body": "主人的私人任务"}, headers={"Origin": "http://testserver"})
    other.post(endpoint, json={"body": "别人的私人任务"})
    with member.websocket_connect(url(home)) as socket:
        assert socket.receive_json()["userId"] == member.member_id
        subscribe(socket, home["channel"])
        sent = member.post(endpoint, json={"body": "我的材料"}).json()
        mine = next(t for t in home["pool"].jobs
                    if home["service"].collaboration("sid-owner").get(t).prompt == "我的材料")
        frames = until(socket, lambda f: f.get("event", {}).get("type") == "agent.run")
        asyncio.run(_finish(home["pool"], mine))
        frames += until(socket, lambda f: f.get("event", {}).get("type") == "run.finished")
        events = [f["event"] for f in frames if f["type"] == "event"]
        messages = [e["message"] for e in events if e["type"] == "message.created"]
        assert [m["body"] for m in messages] == ["我的材料", "逐条交回结论"]
        assert messages[0]["id"] == sent["id"]
        assert messages[0]["author"]["id"] == member.member_id
        assert {e["runId"] for e in events if "runId" in e} == {mine}
        assert next(e for e in events if e["type"] == "run.finished")["status"] == "done"
    with member.websocket_connect(url(home)) as socket:
        socket.receive_json()
        subscribe(socket, home["channel"])
        frames = until(socket, lambda f: f.get("event", {}).get("message", {}).get("agentRunId") == mine)
        assert [f["event"]["message"]["body"] for f in frames if f.get("event", {}).get("type") == "message.created"] == [
            "我的材料", "逐条交回结论"]


def test_main_assistant_and_unknown_channels_are_never_subscribed(home):
    from src.web import app_views as views
    member = _join(home)
    with member.websocket_connect(url(home)) as socket:
        socket.receive_json()
        for channel in [views.dm_channel_id(home["main"]["id"]), "unknown"]:
            socket.send_json({"type": "subscribe", "channelId": channel, "userId": "owner"})
            assert socket.receive_json() == {"type": "error", "code": "not_found", "channelId": channel}
        subscribe(socket, home["channel"])
        socket.send_json({"type": "unsubscribe", "channelId": home["channel"]})
        assert socket.receive_json() == {"type": "unsubscribed", "channelId": home["channel"]}
        member.post(f"{home['base']}/channels/{home['channel']}/messages", json={"body": "已取消订阅"})
        socket.send_json({"type": "ping"})
        assert socket.receive_json() == {"type": "pong"}


@pytest.mark.parametrize("change", ["sign-out", "removed", "none", "reset"])
def test_existing_socket_closes_when_session_or_access_is_revoked(home, change):
    from src.web import member_auth
    from src.workflow.members.accounts import MemberAccounts
    member = _join(home)
    with member.websocket_connect(url(home)) as socket:
        socket.receive_json()
        subscribe(socket, home["channel"])
        if change == "sign-out":
            assert member.post("/api/app/device-accounts/sign-out", json={}).status_code == 200
        elif change == "removed":
            assert home["owner"].delete(f"{home['base']}/members/{member.member_id}",
                                       headers={"Origin": "http://testserver"}).status_code == 204
        elif change == "none":
            MemberRegistry(home["root"]).set_act_level_on(OWNER_P, home["staff"]["id"], member.member_id, "none")
        else:
            accounts = MemberAccounts(home["root"])
            found = accounts.session(member.cookies.get(member_auth.MEMBER_COOKIE))
            token, _ = accounts.reset_link(found["id"])
            accounts.reset_password(token, "replacement-password")
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == 1008


@pytest.mark.parametrize("sign_out", [False, True])
async def test_pending_receive_survives_permission_checks_until_delivery_or_revocation(home, monkeypatch, sign_out):
    member = _join(home)
    incoming, outgoing = asyncio.Queue(), asyncio.Queue()
    frame_received, permission_checked = asyncio.Event(), asyncio.Event()
    release_frame, receive_finished = asyncio.Event(), asyncio.Event()

    async def receive():
        frame = await incoming.get()
        if frame["type"] == "websocket.receive":
            frame_received.set()
            try:
                await release_frame.wait()
            finally:
                receive_finished.set()
        return frame

    headers = [(key.lower(), value) for key, value in member.build_request("GET", "/api/ws").headers.raw]
    socket = WebSocket({"type": "websocket", "path": "/api/ws", "headers": headers},
                       receive=receive, send=outgoing.put)
    identity = app_member_realtime.identity
    bound = identity(socket, home["root"])
    assert bound is not None and bound[0] == member.member_id

    def observe_identity(socket, root):
        found = identity(socket, root)
        if frame_received.is_set():
            permission_checked.set()
        return found

    monkeypatch.setattr(app_member_realtime, "identity", observe_identity)
    incoming.put_nowait({"type": "websocket.connect"})
    serving = asyncio.create_task(app_member_realtime.serve(socket, home["root"], bound))
    try:
        assert (await asyncio.wait_for(outgoing.get(), timeout=3))["type"] == "websocket.accept"
        assert (await asyncio.wait_for(outgoing.get(), timeout=3))["type"] == "websocket.send"
        incoming.put_nowait({"type": "websocket.receive",
                             "text": json.dumps({"type": "subscribe", "channelId": "unknown"})})
        await asyncio.wait_for(permission_checked.wait(), timeout=3)
        assert not receive_finished.is_set()
        if sign_out:
            response = await asyncio.to_thread(member.post, "/api/app/device-accounts/sign-out", json={})
            assert response.status_code == 200
            closed = await asyncio.wait_for(outgoing.get(), timeout=3)
            assert (closed["type"], closed["code"]) == ("websocket.close", 1008)
        else:
            release_frame.set()
            sent = await asyncio.wait_for(outgoing.get(), timeout=3)
            assert sent["type"] == "websocket.send"
            assert json.loads(sent["text"]) == {"type": "error", "code": "not_found", "channelId": "unknown"}
            incoming.put_nowait({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(serving, timeout=3)
        assert receive_finished.is_set()
        assert outgoing.empty()
    finally:
        serving.cancel()
        await asyncio.gather(serving, return_exceptions=True)


@pytest.mark.parametrize("origin", [None, "https://elsewhere.example"])
def test_member_handshake_needs_the_services_own_origin(home, origin):
    member = _join(home)
    member.headers.pop("Origin")
    if origin:
        member.headers["Origin"] = origin
    with pytest.raises(WebSocketDisconnect) as error:
        with member.websocket_connect(url(home)):
            pass
    assert error.value.code == 1008


def test_member_session_does_not_unlock_owner_runtime_socket_or_wrong_workspace(home):
    member = _join(home)
    for path in ["/ws", "/api/ws?orgId=other"]:
        with pytest.raises(WebSocketDisconnect) as error:
            with member.websocket_connect(path):
                pass
        assert error.value.code == 1008
