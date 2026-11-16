"""新版 Desktop 界面接口（/api/app/*、/api/ws）第一批：断行为，不看源码。"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("VORTOONE_API_TOKEN", "VORTOCODE_API_TOKEN", "AUTODEV_API_TOKEN", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    from src.web import app_bridge
    app_bridge.reset_bridges()
    yield tmp_path
    app_bridge.reset_bridges()


@pytest.fixture
def client(home):
    from src.web.server import app
    return TestClient(app)


def _org(client):
    return client.get("/api/app/me").json()["organizations"][0]["id"]


def _seed_main_chat(root, transcript):
    from src.gateway.main_conversation import claim_main_chat
    from src.gateway.session_store import save_session
    key = claim_main_chat(str(root), "sid-desktopmain01")
    save_session(str(root), key, transcript, [], None)


def test_owner_lands_in_one_home_with_the_main_assistant(client):
    me = client.get("/api/app/me").json()
    assert [org["role"] for org in me["organizations"]] == ["owner"]
    assert "onboarding" not in me["organizations"][0]  # 否则界面会把主人送去引导页
    org = me["organizations"][0]["id"]

    agents = client.get(f"/api/app/organizations/{org}/agents").json()["agents"]
    assert [(a["handle"], a["canTalk"]) for a in agents] == [("assistant", True)]
    dms = client.get(f"/api/app/organizations/{org}/dms").json()["dms"]
    assert dms[0]["with"]["id"] == agents[0]["id"] and dms[0]["lastMessage"] is None
    opened = client.post(f"/api/app/organizations/{org}/dms", json={"agentId": agents[0]["id"]}).json()
    assert opened["channelId"] == dms[0]["channelId"]


def test_ids_are_stable_across_requests(client):
    first, second = client.get("/api/app/me").json(), client.get("/api/app/me").json()
    assert first == second


def test_another_space_is_not_served(client):
    for path in ("agents", "dms", "overview", "notifications/count", "me/sidebar"):
        response = client.get(f"/api/app/organizations/someone-else/{path}")
        assert response.status_code == 404, path
        assert response.json()["error"]["code"] == "not_found"


def test_main_chat_history_becomes_messages(client, home):
    _seed_main_chat(home, [
        {"role": "user", "text": "早", "rid": "r1", "at": "2026-11-10T01:00:00+00:00"},
        {"role": "assistant", "text": "早上好", "rid": "r1", "at": "2026-11-10T01:00:02+00:00"},
        {"role": "user", "text": "今天有什么事", "rid": "r2", "at": "2026-11-10T01:05:00+00:00"},
    ])
    org = _org(client)
    me = client.get("/api/app/me").json()["user"]["id"]
    dm = client.get(f"/api/app/organizations/{org}/dms").json()["dms"][0]
    assert dm["lastMessage"]["body"] == "今天有什么事"

    url = f"/api/app/organizations/{org}/channels/{dm['channelId']}/messages"
    messages = client.get(url).json()["messages"]
    assert [m["body"] for m in messages] == ["早", "早上好", "今天有什么事"]
    assert [m["author"]["type"] for m in messages] == ["person", "agent", "person"]
    assert messages[0]["author"]["id"] == me and messages[1]["author"]["id"] == dm["with"]["id"]
    assert len({m["id"] for m in messages}) == 3
    assert messages[1]["createdAt"] == "2026-11-10T01:00:02.000Z"

    older = client.get(url, params={"before": messages[2]["createdAt"]}).json()["messages"]
    assert [m["body"] for m in older] == ["早", "早上好"]
    assert client.get(url, params={"threadId": "t"}).json() == {"messages": []}


def test_message_ids_survive_trimming_and_repeats(home):
    from src.web import app_views as views
    rows = [{"role": "user", "text": "好", "rid": "", "at": "2026-11-10T01:00:00+00:00"} for _ in range(3)]
    rows.append({"role": "assistant", "text": "收到", "rid": "", "at": "2026-11-10T01:00:01+00:00"})
    _seed_main_chat(home, rows)
    full = views.main_messages(str(home))
    assert len({m["id"] for m in full}) == 4  # 同一秒、同样的话也各有各的 id

    _seed_main_chat(home, rows)  # 已登记，保持原会话
    from src.gateway.session_store import save_session
    save_session(str(home), "sid-desktopmain01", rows[3:], [], None)
    assert views.main_messages(str(home))[0]["id"] == full[3]["id"]


def test_unknown_conversation_is_not_found(client):
    org = _org(client)
    for path in ("messages", "members", "threads"):
        assert client.get(f"/api/app/organizations/{org}/channels/nope/{path}").status_code == 404


def test_writes_that_are_not_wired_yet_do_not_pretend_to_succeed(client):
    org = _org(client)
    channel = client.get(f"/api/app/organizations/{org}/dms").json()["dms"][0]["channelId"]
    response = client.post(f"/api/app/organizations/{org}/channels/{channel}/read", json={})
    assert response.status_code in {404, 405}


def test_staff_members_are_listed_and_paused_ones_are_not_talkable(client, home, monkeypatch):
    from src.web import app_views as views
    monkeypatch.setattr(views, "_staff", lambda root: {
        "roles": [{"id": "web-researcher", "title": "研究员"}],
        "members": [{"id": "ann", "name": "安", "role": "web-researcher", "model": "m", "status": "paused"},
                    {"id": "old", "name": "旧", "role": "web-researcher", "status": "retired"}]})
    org = _org(client)
    agents = client.get(f"/api/app/organizations/{org}/agents").json()["agents"]
    assert [(a["name"], a["role"], a["canTalk"]) for a in agents[1:]] == [("安", "研究员", False)]
    statuses = {s["agentId"]: s["presence"] for s in
                client.get(f"/api/app/organizations/{org}/agent-status").json()["agents"]}
    assert statuses[agents[1]["id"]] == "offline" and statuses[agents[0]["id"]] == "online"
    opened = client.post(f"/api/app/organizations/{org}/dms", json={"agentId": agents[1]["id"]})
    assert opened.status_code == 200 and opened.json()["dm"]["with"]["name"] == "安"
    assert client.post(f"/api/app/organizations/{org}/dms", json={"agentId": "unknown"}).status_code == 404


def test_ai_status_follows_the_model_credential(client, monkeypatch):
    org = _org(client)
    assert client.get(f"/api/app/organizations/{org}/ai-status").json()["any"] is False
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    status = client.get(f"/api/app/organizations/{org}/ai-status").json()
    assert status["any"] is True and status["agents"][0]["state"] == "ready"


def test_token_gates_everything_but_the_sign_in_surface(client, monkeypatch):
    org = _org(client)
    monkeypatch.setenv("VORTOONE_API_TOKEN", "owner-token-for-test")
    client.cookies.clear()
    assert client.get("/api/app/auth-config").status_code == 200
    assert client.get("/api/instance").json()["signIn"]["email"] is True
    assert client.get("/api/app/me").status_code == 401
    assert client.get(f"/api/app/organizations/{org}/agents").status_code == 401
    with pytest.raises(Exception):
        with client.websocket_connect(f"/api/ws?orgId={org}"):
            pass

    wrong = client.post("/api/auth/sign-in/email", json={"email": "a@b.c", "password": "nope"})
    assert wrong.status_code == 401 and "set-cookie" not in wrong.headers
    assert client.post("/api/auth/sign-in/email",
                       json={"email": "", "password": "owner-token-for-test"}).status_code == 200
    assert client.get("/api/app/me").status_code == 200
    assert client.post("/api/auth/sign-out").status_code == 200
    client.cookies.clear()
    assert client.get("/api/app/me").status_code == 401


def test_socket_handshake_heartbeat_and_subscription(client):
    org = _org(client)
    channel = client.get(f"/api/app/organizations/{org}/dms").json()["dms"][0]["channelId"]
    with client.websocket_connect(f"/api/ws?orgId={org}") as socket:
        assert socket.receive_json()["type"] == "ready"
        socket.send_json({"type": "ping"})
        assert socket.receive_json() == {"type": "pong"}
        socket.send_json({"type": "subscribe", "channelId": channel})
        assert socket.receive_json() == {"type": "subscribed", "channelId": channel}
        socket.send_json({"type": "subscribe", "channelId": "nope"})
        assert socket.receive_json()["type"] == "error"
    with pytest.raises(Exception):
        with client.websocket_connect("/api/ws?orgId=someone-else"):
            pass


def test_every_startup_read_answers_for_this_home(client):
    """界面启动和打开私聊时读取的接口都要有合法应答，否则页面会停在错误屏。"""
    org = _org(client)
    agent = client.get(f"/api/app/organizations/{org}/agents").json()["agents"][0]["id"]
    channel = client.get(f"/api/app/organizations/{org}/dms").json()["dms"][0]["channelId"]
    reads = ["overview", "settings", "members", "people", "roles", "agent-status", "ai-status",
             "former-agents", "agent-groups", "channels", "channel-categories", "archived-channels",
             "unread-tasks", "unread-threads", "notifications/count", "notifications", "waiting", "missions",
             "machines", "machines/outages", "computer-setup", "approvals", "routines", "wiki/pages",
             "dashboards", "dashboards/home", "active-runs", "runs", "usage", "issues", "task-manager",
             "me/sidebar", "me/sidebar/order", "me/sidebar/tools", "me/sidebar/view",
             f"agents/{agent}", f"agents/{agent}/desktop", f"agents/{agent}/browser",
             f"agents/{agent}/browser/handoff", f"agents/{agent}/scopes",
             f"channels/{channel}/members", f"channels/{channel}/messages", f"channels/{channel}/threads",
             f"channels/{channel}/pins"]
    for path in reads:
        response = client.get(f"/api/app/organizations/{org}/{path}")
        assert response.status_code == 200, path
        assert client.get(f"/api/app/organizations/other/{path}").status_code == 404, path
    assert client.get("/api/app/models").status_code == 200
    assert client.get(f"/api/app/organizations/{org}/agents/unknown").status_code == 404


def test_app_frames_are_validated_before_they_leave():
    from src.gateway import app_protocol as F
    from src.gateway.protocol import ProtocolError
    assert F.make_app_frame(F.ERROR, code="not_found", channelId=None) == {"type": "error", "code": "not_found"}
    for bad in (lambda: F.make_app_frame("message.created", channelId="c"),   # 未登记的类型
                lambda: F.make_app_frame(F.READY, orgId="o"),                  # 缺必填
                lambda: F.make_app_frame(F.PONG, extra=1)):                    # 未登记字段
        with pytest.raises(ProtocolError):
            bad()
