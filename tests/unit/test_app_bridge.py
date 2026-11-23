"""新版界面发消息、实时事件、停止与审批：用替身助理走真实的会话回合路径。"""

import asyncio

import httpx
import pytest


class _Socket:
    def __init__(self):
        self.frames = []

    async def send_json(self, frame):
        self.frames.append(frame)

    def events(self, kind=None):
        rows = [f["event"] for f in self.frames if f.get("type") == "event"]
        return [e for e in rows if kind is None or e["type"] == kind]


class _Agent:
    """按主人的话决定这一回合做什么：普通回复、长跑、或先向内核要一次确认。"""
    history: list = []
    plan = None

    def __init__(self):
        self.started = asyncio.Event()
        self.finish_tools = asyncio.Event()
        self.tool_status = "failed"
        self.confirmed = None
        self.cancelled = False

    async def run_turn(self, text, mode, say, emit, stream_cb, images=None, audio=None, owner_text=None):
        from src.web.routers import realtime
        self.started.set()
        if text == "慢":
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        if text == "删文件":
            self.confirmed = await realtime.turn_bindings(self).confirm("删除 notes.txt？", "write")
            emit("已删除" if self.confirmed else "没有删除")
            return
        if text == "查看步骤":
            binding = realtime.turn_bindings(self)
            call = {"id": "demo-call", "name": "read_file", "args": {"path": "synthetic-private-path"}}
            binding.emit_tool_event("start", call)
            binding.emit_tool_event("progress", {**call, "summary": "synthetic-private-progress"})
            await self.finish_tools.wait()
            binding.emit_tool_event("finish", {**call, "status": self.tool_status, "result": "synthetic-private-result"})
        emit("收到：" + text)


@pytest.fixture
async def app_home(tmp_path, monkeypatch, request):
    from src.web import app_bridge
    from src.web.routers import realtime
    layout = getattr(request, "param", None)
    root = tmp_path
    if layout == "native-desktop":
        root = tmp_path / "home"
        root.mkdir()
    monkeypatch.chdir(root)
    for name in ("VORTOONE_API_TOKEN", "VORTOCODE_API_TOKEN", "AUTODEV_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    if layout not in {"native", "native-desktop"}:
        stand_in = _Agent()
        monkeypatch.setattr(realtime, "_new_agent", lambda: stand_in)
    else:
        monkeypatch.setenv("VORTOONE_DESKTOP_SIDECAR", "1")
        monkeypatch.setenv("VORTOONE_ENABLE_BROWSER_CONTROL", "0")
        monkeypatch.setenv("VORTOONE_WORKSPACE_SCOPE", "general")
    monkeypatch.setattr(realtime, "_schedule_turn_digest", lambda key, rid: None)

    async def no_mcp(agent, say):
        return None

    monkeypatch.setattr(realtime, "_ensure_mcp", no_mcp)
    app_bridge.reset_bridges()
    from src.web.server import app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://home") as http:
        org = (await http.get("/api/app/me")).json()["organizations"][0]["id"]
        channel = (await http.get(f"/api/app/organizations/{org}/dms")).json()["dms"][0]["channelId"]
        bridge = await app_bridge.main_chat_bridge(str(root))
        agent = realtime._SESSIONS[bridge.key]["agent"]
        socket = _Socket()
        bridge.subscribe(socket)
        yield {"http": http, "base": f"/api/app/organizations/{org}", "channel": channel, "agent": agent,
               "socket": socket, "bridge": bridge}
        # 会话表是进程级的：替身助理留在里面，会被之后遍历在线会话的测试碰到。
        # 先清掉排队的输入（否则停掉当前回合后队列会接着开下一轮），再停回合、摘订阅、删会话。
        for session in realtime._SESSIONS.values():
            if session.get("agent") is agent:
                session["prompt_queue"] = []
        for _ in range(20):
            tasks = [task for task in realtime._WS_AGENT_TASKS.values() if not task.done()]
            if not tasks:
                break
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        await realtime._detach_session_subscriber(bridge)
        for key in [key for key, session in realtime._SESSIONS.items() if session.get("agent") is agent]:
            realtime._SESSIONS.pop(key, None)
    app_bridge.reset_bridges()


async def _until(check, seconds=3.0):
    for _ in range(int(seconds / 0.01)):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("等待的状态没有出现")


@pytest.mark.parametrize("tool_status", ["failed", "blocked", "succeeded"])
async def test_task_manager_steps_use_live_and_archived_main_chat_records(app_home, monkeypatch, tool_status):
    from src.gateway.session_store import load_session
    from src.web.auth import SESSION_COOKIE
    from src.web.routers import realtime
    from src.web.server import app

    token = "synthetic-desktop-owner"
    monkeypatch.setenv("VORTOONE_API_TOKEN", token)
    http, base, channel, socket, agent, bridge = (app_home[k] for k in
                                                  ("http", "base", "channel", "socket", "agent", "bridge"))
    agent.tool_status = tool_status
    headers = {"Authorization": f"Bearer {token}"}
    sent = await http.post(f"{base}/channels/{channel}/messages", json={"body": "查看步骤"}, headers=headers)
    assert sent.status_code == 200, sent.text
    await _until(lambda: len(socket.events("agent.tool")) >= 2)
    [run] = (await http.get(f"{base}/task-manager", headers=headers)).json()["tasks"]
    path = f"{base}/task-manager/{run['runId']}/steps"
    response = await http.get(path, headers=headers)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store", response.text
    live = response.json()
    [call] = live["steps"]
    assert call["kind"] == "tool_call" and call["seq"] == 1
    assert call["data"] == {"name": "read_file", "toolUseId": "demo-call"}
    assert live["run"]["createdAt"] and live["run"]["finishedAt"] is None
    assert "synthetic-private" not in response.text and "查看步骤" not in response.text
    assert (await http.get(path)).status_code == 401
    assert (await http.get(f"{base}/task-manager/missing/steps", headers=headers)).status_code == 404
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://home",
                                 cookies={SESSION_COOKIE: token}, headers={"Origin": "http://other.test"}) as browser:
        assert (await browser.get(path)).status_code == 403

    agent.finish_tools.set()
    await _until(lambda: socket.events("run.finished"))
    await _until(lambda: bool(load_session(bridge.root, bridge.key)))
    monkeypatch.setattr(realtime, "live_session_view", lambda *args, **kwargs: None)
    saved = await http.get(path, headers=headers)
    assert saved.status_code == 200, saved.text
    assert "synthetic-private" not in saved.text and "查看步骤" not in saved.text
    assert saved.json()["run"]["finishedAt"] is not None
    started, finished = saved.json()["steps"]
    assert started == call
    assert finished["kind"] == "tool_result" and finished["seq"] == 2
    assert finished["data"] == {"toolUseId": "demo-call", "isError": tool_status != "succeeded"}


async def test_task_manager_steps_use_terminal_phase_for_cancelled_run(app_home, monkeypatch):
    token = "synthetic-desktop-owner"
    monkeypatch.setenv("VORTOONE_API_TOKEN", token)
    headers = {"Authorization": f"Bearer {token}"}
    http, base, channel, socket = (app_home[k] for k in ("http", "base", "channel", "socket"))
    sent = await http.post(f"{base}/channels/{channel}/messages", json={"body": "查看步骤"}, headers=headers)
    assert sent.status_code == 200, sent.text
    await _until(lambda: len(socket.events("agent.tool")) >= 2)
    [run] = (await http.get(f"{base}/task-manager", headers=headers)).json()["tasks"]
    run_id = run["runId"]
    response = await http.post(f"{base}/runs/{run_id}/cancel", json={}, headers=headers)
    assert response.status_code == 204, response.text
    await _until(lambda: socket.events("run.finished"))
    assert socket.events("run.finished")[0]["status"] == "canceled"
    response = await http.get(f"{base}/task-manager/{run_id}/steps", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["run"]["finishedAt"] is not None
    assert [step["kind"] for step in payload["steps"]] == ["tool_call"]


async def test_a_message_runs_a_turn_and_the_reply_arrives(app_home):
    http, base, channel, socket = (app_home[k] for k in ("http", "base", "channel", "socket"))
    sent = (await http.post(f"{base}/channels/{channel}/messages", json={"body": "你好", "clientId": "c-1"})).json()
    assert sent["body"] == "你好" and sent["clientId"] == "c-1" and sent["author"]["type"] == "person"

    await _until(lambda: socket.events("run.finished"))
    created = socket.events("message.created")
    assert [e["message"]["body"] for e in created] == ["你好", "收到：你好"]
    assert created[0]["messageId"] == sent["id"] and created[0]["message"]["clientId"] == "c-1"
    run = socket.events("agent.run")[0]
    assert run["status"] == "running" and run["triggerMessageId"] == sent["id"]
    assert socket.events("run.finished")[0] == {**{k: run[k] for k in ("channelId", "threadId", "runId", "agentId")},
                                                 "type": "run.finished", "status": "done"}

    listed = (await http.get(f"{base}/channels/{channel}/messages")).json()["messages"]
    assert [m["id"] for m in listed] == [e["messageId"] for e in created]      # 读到的与推送的是同一批 id
    assert [m["sequence"] for m in listed] == [1, 2]
    assert [e["message"]["sequence"] for e in created] == [1, 2]
    assert [e["message"]["createdAt"] for e in created] == [m["createdAt"] for m in listed]
    from src.web.routers.realtime import live_session_view
    stored = live_session_view(app_home["bridge"].key)["transcript"]
    assert [item["sequence"] for item in stored] == [1, 2]  # 序号随记录保存，不随读取或裁剪重排
    assert (await http.get(f"{base}/channels/{channel}/active-runs")).json() == {"runs": []}


async def test_empty_or_modelless_messages_do_not_start_a_turn(app_home, monkeypatch):
    http, base, channel = (app_home[k] for k in ("http", "base", "channel"))
    assert (await http.post(f"{base}/channels/{channel}/messages", json={"body": "  "})).status_code == 400
    monkeypatch.delenv("OPENAI_API_KEY")
    refused = await http.post(f"{base}/channels/{channel}/messages", json={"body": "在吗"})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "no_ai"
    assert not app_home["agent"].started.is_set()
    assert (await http.get(f"{base}/channels/{channel}/messages")).json()["messages"] == []


async def test_stop_interrupts_the_running_turn(app_home):
    http, base, channel, socket, agent = (app_home[k] for k in ("http", "base", "channel", "socket", "agent"))
    await http.post(f"{base}/channels/{channel}/messages", json={"body": "慢"})
    await _until(lambda: socket.events("agent.run") and agent.started.is_set())
    runs = (await http.get(f"{base}/channels/{channel}/active-runs")).json()["runs"]
    assert [r["status"] for r in runs] == ["running"]

    assert (await http.post(f"{base}/runs/{runs[0]['runId']}/cancel", json={})).status_code == 204
    await _until(lambda: socket.events("run.finished"))
    assert agent.cancelled and socket.events("run.finished")[0]["status"] == "canceled"
    assert (await http.post(f"{base}/runs/{runs[0]['runId']}/cancel", json={})).status_code == 409


async def test_a_message_sent_during_a_turn_waits_in_line_and_stays_visible(app_home):
    http, base, channel, socket = (app_home[k] for k in ("http", "base", "channel", "socket"))
    await http.post(f"{base}/channels/{channel}/messages", json={"body": "慢"})
    await _until(lambda: app_home["agent"].started.is_set())
    queued = (await http.post(f"{base}/channels/{channel}/messages", json={"body": "第二条"})).json()
    listed = (await http.get(f"{base}/channels/{channel}/messages")).json()["messages"]
    assert [m["body"] for m in listed] == ["慢", "第二条"] and listed[1]["id"] == queued["id"]


@pytest.mark.parametrize("approve", [True, False])
async def test_a_kernel_confirmation_becomes_an_approval_the_owner_decides(app_home, approve):
    http, base, channel, socket, agent = (app_home[k] for k in ("http", "base", "channel", "socket", "agent"))
    await http.post(f"{base}/channels/{channel}/messages", json={"body": "删文件"})
    await _until(lambda: socket.events("approval.created"))
    pending = socket.events("approval.created")[0]["approval"]
    assert pending["status"] == "pending" and pending["canDecide"] and "删除 notes.txt" in pending["detail"]

    listed = (await http.get(f"{base}/approvals")).json()["approvals"]
    assert [a["id"] for a in listed] == [pending["id"]]
    cards = [m for m in (await http.get(f"{base}/channels/{channel}/messages")).json()["messages"]
             if (m.get("meta") or {}).get("approvalId") == pending["id"]]
    assert [c["id"] for c in cards] == [pending["messageId"]]
    assert [r["status"] for r in (await http.get(f"{base}/active-runs")).json()["runs"]] == ["waiting_approval"]
    assert agent.confirmed is None                                  # 主人没决定之前，工具一直等着

    always = await http.post(f"{base}/approvals/{pending['id']}/approve", json={"always": True})
    assert always.status_code == 400 and agent.confirmed is None     # 一次批准不会被扩大成常设放行

    verb = "approve" if approve else "refuse"
    decided = (await http.post(f"{base}/approvals/{pending['id']}/{verb}", json={})).json()
    assert decided["status"] == ("approved" if approve else "refused")
    await _until(lambda: socket.events("run.finished"))
    assert agent.confirmed is approve
    assert socket.events("approval.decided")[0]["approval"]["status"] == decided["status"]
    assert socket.events("message.created")[-1]["message"]["body"] == ("已删除" if approve else "没有删除")
    assert (await http.get(f"{base}/approvals")).json() == {"approvals": []}
    assert (await http.post(f"{base}/approvals/{pending['id']}/approve", json={})).status_code == 409


async def test_external_content_warning_is_part_of_what_the_owner_reads(app_home):
    from src.web import app_views as views
    shown = views.approval(".", {"id": "c1", "text": "发送邮件？", "tainted": True,
                                 "created": "2026-11-16T01:00:00+00:00"})
    assert "外部内容" in shown["detail"] and "发送邮件？" in shown["detail"]
    assert shown["expiresAt"] == "2026-11-16T01:05:00.000Z"


@pytest.fixture
async def website_home(app_home, tmp_path, monkeypatch):
    from src.agents import web_fetch
    from src.agents.confirmation_context import start_confirmation_turn
    from src.agents.gate import make_confirm_gate
    from src.agents.tools.browser import build_browser_tools
    from src.agents.tools.website_browser import shutdown_browsers
    from src.browser import handoff_preview
    from src.browser.connections import BrowserConnections
    from src.browser.page_privacy import HANDOFF_PREVIEW_STATE_JS
    from src.web.auth import SESSION_COOKIE
    from src.web.routers import realtime
    from tests.unit.test_browser_control import _FakeBrowser
    from tests.unit.test_computer_browser_preview import _image

    http, base, channel, socket, agent = (app_home[k] for k in ("http", "base", "channel", "socket", "agent"))
    monkeypatch.setattr(web_fetch, "refusal_reason", lambda _host: "")
    monkeypatch.setattr(handoff_preview, "MIN_FRAME_INTERVAL", 0)
    owner_token = "synthetic-preview-owner"
    monkeypatch.setenv("VORTOONE_API_TOKEN", owner_token)
    http.headers["Authorization"] = f"Bearer {owner_token}"
    browser_auth = {"Origin": "https://home", "Cookie": f"{SESSION_COOKIE}={owner_token}"}
    workspace = tmp_path / "project"
    workspace.mkdir()
    registry = BrowserConnections(str(workspace), state_root=tmp_path / "browser-state")
    browser = _FakeBrowser()
    original_evaluate = browser.evaluate
    capture_started, release_capture, capture_cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    release_capture.set()
    captured = []

    async def evaluate(program):
        if program == HANDOFF_PREVIEW_STATE_JS:
            return {"url": browser.url, "document": 1000, "protected": False, "frames": False}
        return await original_evaluate(program)

    async def screenshot():
        captured.append(browser.url)
        capture_started.set()
        try:
            await release_capture.wait()
        except asyncio.CancelledError:
            capture_cancelled.set()
            raise
        return _image()

    monkeypatch.setattr(browser, "evaluate", evaluate)
    monkeypatch.setattr(browser, "screenshot", screenshot)
    gate = make_confirm_gate(realtime.turn_bindings(agent).ask, can_ask_human=True)
    tools = {tool.name: tool for tool in build_browser_tools(str(workspace), gate,
             browser_factory=lambda: browser, connections=registry)}

    async def run_login(text, mode, say, emit, stream_cb, **_kwargs):
        start_confirmation_turn()
        result = await tools["browser_open"].handler({"url": "https://example.test/login?code=synthetic-secret"})
        agent.confirmed = isinstance(result, str) and "主人已确认恢复" in result
        emit("已核对登录" if agent.confirmed else "保持暂停")

    monkeypatch.setattr(agent, "run_turn", run_login)
    try:
        sent = (await http.post(f"{base}/channels/{channel}/messages", json={"body": "登录网站"})).json()
        await _until(lambda: socket.events("approval.created"))
        pending = socket.events("approval.created")[0]["approval"]
        assert pending["websiteLogin"] == {
            "origin": "https://example.test", "accountLabel": "临时匿名窗口", "deviceMode": "desktop",
            "canPreview": True, "livePreview": True,
        }
        assert "synthetic-secret" not in str(pending)
        run = socket.events("agent.run")[0]
        assert run["triggerMessageId"] == sent["id"]
        assert (pending["runId"], pending["agentId"]) == (run["runId"], run["agentId"])
        route = f"{base}/approvals/{pending['id']}"
        snapshot = await http.get(route)
        assert snapshot.json() == pending and snapshot.headers["cache-control"] == "no-store"
        listed = await http.get(f"{base}/approvals")
        assert listed.json() == {"approvals": [pending]} and listed.headers["cache-control"] == "no-store"
        assert agent.confirmed is None and pending["status"] == "pending" and pending["canDecide"]
        yield {**app_home, "route": route, "pending": pending, "browser": browser, "browser_auth": browser_auth,
               "capture_started": capture_started, "release_capture": release_capture,
               "capture_cancelled": capture_cancelled, "captured": captured}
    finally:
        release_capture.set()
        await shutdown_browsers()


@pytest.mark.parametrize("ending", ["decision", "logout"])
async def test_website_login_approval_is_bound_to_the_chat_run_and_decided_once(website_home, ending):
    from tests.unit.test_computer_browser_preview import _image

    home = website_home
    http, route, browser_auth = home["http"], home["route"], home["browser_auth"]
    frame_route = f"https://home{route}/browser-preview"
    inflight = None
    try:
        assert (await http.post(frame_route)).status_code == 403
        assert (await http.post(frame_route, headers={**browser_auth, "Origin": "https://other"})).status_code == 403
        assert (await http.post(frame_route, headers=browser_auth)).status_code == 409
        assert not home["captured"]
        home["browser"].url = "https://example.test/ready"
        frame = await http.post(frame_route, headers=browser_auth)
        assert frame.status_code == 200 and frame.content == _image()
        assert frame.headers["content-type"] == "image/png" and frame.headers["cache-control"] == "no-store"
        assert (await http.get(route)).json()["status"] == "pending"
        home["capture_started"].clear()
        home["release_capture"].clear()
        inflight = asyncio.create_task(http.post(frame_route, headers=browser_auth))
        await asyncio.wait_for(home["capture_started"].wait(), 2)
        if ending == "logout":
            shut = await http.post("https://home/api/app/device-accounts/sign-out", headers=browser_auth)
            assert shut.status_code == 200
            assert "canPreview" not in (await http.get(route)).json()["websiteLogin"]
            home["release_capture"].set()
            stopped = await inflight
            assert stopped.status_code == 410 and not stopped.content
            assert (await http.post(frame_route, headers=browser_auth)).status_code == 404
        answers = await asyncio.gather(
            http.post(f"{route}/approve", json={}),
            http.post(f"{route}/refuse", json={}),
        )
        assert sorted(reply.status_code for reply in answers) == [200, 409]
        if ending == "decision":
            home["release_capture"].set()
            stopped = await inflight
            assert stopped.status_code == 410 and not stopped.content
        await _until(lambda: home["socket"].events("run.finished"))
        assert home["agent"].confirmed == (answers[0].status_code == 200)
        decided, = home["socket"].events("approval.decided")
        assert decided["approval"]["websiteLogin"] == home["pending"]["websiteLogin"]
        assert (await http.get(route)).status_code == 404
        assert (await http.post(frame_route, headers=browser_auth)).status_code == 404
    finally:
        home["release_capture"].set()
        if inflight is not None:
            await asyncio.gather(inflight, return_exceptions=True)


@pytest.mark.parametrize("ending", ["decision", "logout"])
async def test_website_login_live_view_stops_on_protection_disconnect_and_lost_authority(website_home, ending):
    import socket

    import aiohttp
    import uvicorn

    from src.web.server import app
    from tests.unit.test_computer_browser_preview import _image

    home = website_home
    browser, route, captured = home["browser"], home["route"], home["captured"]
    browser.url = "https://example.test/ready"
    ready = {"type": "ready", "min_frame_ms": 500}
    next_frame = {"type": "next"}
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        frame_route = f"{origin}{route}/browser-preview"
        watch = frame_route + "/watch"
        server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="critical",
                                              log_config=None, access_log=False))
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            await _until(lambda: server.started)
            async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=3)) as client:
                headers = {**home["browser_auth"], "Origin": origin}
                for path, auth in (
                    (watch, {"Origin": origin}),
                    (watch, {**headers, "Origin": "https://wrong.test"}),
                    (watch + "?unused=1", headers),
                    (watch.replace(home["pending"]["id"], "unrelated"), headers),
                ):
                    with pytest.raises(aiohttp.WSServerHandshakeError) as refused:
                        await client.ws_connect(path, headers=auth)
                    assert refused.value.status == 403

                async with client.ws_connect(watch, headers=headers) as viewer:
                    assert await viewer.receive_json(timeout=2) == ready
                    assert not captured
                    with pytest.raises(aiohttp.WSServerHandshakeError) as concurrent:
                        await client.ws_connect(watch, headers=headers)
                    assert concurrent.value.status == 403
                    async with client.post(frame_route, headers=headers) as response:
                        assert response.status == 409
                    await viewer.send_json(next_frame)
                    assert await viewer.receive_bytes(timeout=2) == _image()
                    await viewer.send_json(next_frame)
                    assert await viewer.receive_bytes(timeout=2) == _image()
                    browser.url = "https://example.test/login"
                    await viewer.send_json(next_frame)
                    assert await viewer.receive_json(timeout=2) == {"type": "paused"}
                    assert (await viewer.receive(timeout=2)).type == aiohttp.WSMsgType.CLOSE
                    assert captured == ["https://example.test/ready"] * 2

                browser.url = "https://example.test/ready"
                home["capture_started"].clear()
                home["release_capture"].clear()
                async with client.ws_connect(watch, headers=headers) as viewer:
                    assert await viewer.receive_json(timeout=2) == ready
                    await viewer.send_json(next_frame)
                    await asyncio.wait_for(home["capture_started"].wait(), 2)
                    await asyncio.wait_for(viewer.close(), 2)
                    await asyncio.wait_for(home["capture_cancelled"].wait(), 2)

                home["capture_started"].clear()
                async with client.ws_connect(watch, headers=headers) as viewer:
                    assert await viewer.receive_json(timeout=2) == ready
                    await viewer.send_json(next_frame)
                    await asyncio.wait_for(home["capture_started"].wait(), 2)
                    if ending == "logout":
                        path = "/api/app/device-accounts/sign-out"
                        reply = await home["http"].post("https://home" + path, headers=home["browser_auth"])
                    else:
                        reply = await home["http"].post(f"{route}/refuse", json={})
                    assert reply.status_code == 200
                    home["release_capture"].set()
                    assert (await viewer.receive(timeout=2)).type == aiohttp.WSMsgType.CLOSE
                with pytest.raises(aiohttp.WSServerHandshakeError) as expired:
                    await client.ws_connect(watch, headers=headers)
                assert expired.value.status == 403
        finally:
            home["release_capture"].set()
            server.should_exit = True
            await asyncio.wait_for(serving, 3)


@pytest.mark.parametrize(("app_home", "ending"), [
    ("native", "resume"), ("native", "switch_off"),
    ("native-desktop", "desktop_before_open"), ("native-desktop", "desktop_resume"),
], indirect=["app_home"])
async def test_native_task_browser_is_opt_in_owner_bound_and_disposed_with_the_turn(app_home, monkeypatch, ending):
    import json
    import os
    from pathlib import Path

    from src.agents import web_fetch
    from src.agents.tools import website_browser
    from src.browser.backend import BrowserBackend
    from src.browser.page_privacy import HANDOFF_PREVIEW_STATE_JS, SNAPSHOT_JS
    from src.gateway import protocol, task_browser
    from src.gateway.desktop_control_lease import claim_desktop, desktop_lease_file, mark_desktop_takeover
    from src.web.auth import SESSION_COOKIE
    from src.web.routers import realtime
    from tests.unit.test_browser_handoff import Window
    from tests.unit.test_computer_browser_preview import _image

    agent, http, socket = (app_home[k] for k in ("agent", "http", "socket"))
    origin = "http://127.0.0.1:4321"
    token = "test-task-browser-owner"
    auth = {"Origin": origin, "Cookie": f"{SESSION_COOKIE}={token}"}
    bearer = {"Authorization": f"Bearer {token}"}
    root = Path.cwd()
    desktop = ending.startswith("desktop_")
    if desktop and os.name != "posix":
        pytest.skip("the native desktop publisher uses POSIX file leases")
    control_directory = root.parent / "computer-connections"
    monkeypatch.setenv("VORTOONE_API_TOKEN", token)
    monkeypatch.setattr(task_browser, "_native_home", None)
    task_browser.register_native_home(root)
    marker = root.parent / "task-browser.enabled"
    monkeypatch.setattr(web_fetch, "refusal_reason", lambda _host: "")
    monkeypatch.setattr("src.browser.handoff_preview.MIN_FRAME_INTERVAL", 0)
    monkeypatch.setattr(agent, "_native", False)
    launches = []

    class Browser(Window):
        async def evaluate(self, expression):
            if expression == HANDOFF_PREVIEW_STATE_JS:
                return {"url": self.url, "document": 1, "protected": False, "frames": False}
            return await super().evaluate(expression)

        async def screenshot(self):
            return _image()

    browser = Browser()

    def create(launch):
        launches.append(launch)
        return browser

    monkeypatch.setattr(website_browser, "chromium_backend", lambda: BrowserBackend(
        "chromium-test", 1, frozenset({"desktop"}), create,
    ))

    class Model:
        def __init__(self, allowed):
            self.allowed = allowed
            self.calls = 0

        async def chat(self, messages, **_kwargs):
            self.calls += 1
            browser_tools = {name for name in agent.tools if name.startswith("browser_")}
            exposed = {"browser_open", "browser_login"} if desktop else {"browser_open"}
            assert browser_tools == (exposed if self.allowed else set())
            if self.allowed and self.calls == 1:
                return {"content": json.dumps({"tool": "browser_open", "args": {"url": "https://example.test/login"}})}
            if self.allowed and ending == "desktop_resume" and self.calls == 2:
                assert any("先前的确认已失效" in str(message.get("content")) for message in messages)
                return {"content": json.dumps({"tool": "browser_login", "args": {}})}
            if self.allowed:
                result = {
                    "resume": "主人已确认恢复", "switch_off": "先前的接管确认已失效",
                    "desktop_before_open": "已暂停本次操作", "desktop_resume": "主人已确认恢复",
                }[ending]
                assert any(result in str(message.get("content")) for message in messages)
            return {"content": "浏览器回合已处理"}

    path = f"{app_home['base']}/channels/{app_home['channel']}/messages"

    async def send(target, headers, allowed=False):
        result = len(socket.events("run.finished"))
        model = Model(allowed)
        monkeypatch.setattr(agent, "_llm", model)
        reply = await http.post(target + path, headers=headers, json={"body": f"核对专用浏览器 {result}"})
        assert reply.status_code == 200
        if not allowed:
            await _until(lambda: len(socket.events("run.finished")) > result)
            assert socket.events("run.finished")[-1]["status"] == "done" and model.calls == 1

    await send(origin, auth)
    marker.touch()
    try:
        await send("https://home", {**auth, "Origin": "https://home"})
        await send(origin, {**bearer, "Origin": origin})
        await send(origin, {**auth, "X-Forwarded-For": "192.0.2.10"})
        assert not socket.events("approval.created") and not launches

        finished = len(socket.events("run.finished"))
        await send(origin, auth, allowed=True)
        await _until(lambda: socket.events("approval.created") or len(socket.events("run.finished")) > finished)
        assert socket.events("approval.created"), socket.events("run.finished")[-1]
        opened = socket.events("approval.created")[0]["approval"]
        assert "打开 https://example.test" in opened["title"]
        assert not launches and not browser.running
        await realtime.client_message(app_home["bridge"], {
            "type": protocol.AGENT_CONFIRM_RESPONSE, "id": opened["id"], "ok": True, "scope": "once",
        })
        assert app_home["bridge"].pending(opened["id"])

        def approval_path(item):
            return f"{origin}{app_home['base']}/approvals/{item['id']}"

        if ending == "desktop_before_open":
            with desktop_lease_file(control_directory) as fd:
                assert claim_desktop(fd)
                mark_desktop_takeover(fd)
                assert (await http.post(approval_path(opened) + "/approve", headers=auth, json={})).status_code == 200
                await _until(lambda: len(socket.events("run.finished")) > finished)
                assert not launches and not browser.reads and len(socket.events("approval.created")) == 1
                assert socket.events("run.finished")[-1]["status"] == "done"
            return

        assert (await http.post(approval_path(opened) + "/approve", headers=auth, json={})).status_code == 200
        await _until(lambda: len(socket.events("approval.created")) == 2)
        handoff = socket.events("approval.created")[1]["approval"]
        assert handoff["runId"] == opened["runId"] and handoff["websiteLogin"]["livePreview"]
        assert browser.running and not browser.clicks and not browser.typed and SNAPSHOT_JS not in browser.reads
        route = approval_path(handoff)
        assert (await http.post(route + "/browser-preview", headers=auth)).status_code == 409
        browser.url = "https://example.test/ready"
        preview = await http.post(route + "/browser-preview", headers=auth)
        assert preview.status_code == 200 and preview.content == _image()
        if ending == "switch_off":
            marker.unlink()
            marker.touch()
            assert (await http.post(route + "/browser-preview", headers=auth)).status_code == 404
        if ending == "desktop_resume":
            with desktop_lease_file(control_directory) as fd:
                assert claim_desktop(fd)
                mark_desktop_takeover(fd)
                assert (await http.post(route + "/browser-preview", headers=auth)).status_code == 404
                assert SNAPSHOT_JS not in browser.reads
            assert (await http.post(route + "/browser-preview", headers=auth)).status_code == 404
            assert (await http.post(route + "/approve", headers=auth, json={})).status_code == 200
            await _until(lambda: len(socket.events("approval.created")) == 3)
            renewed = socket.events("approval.created")[2]["approval"]
            assert renewed["runId"] == opened["runId"] and "本机桌面已交还" in renewed["detail"]
            assert browser.running and SNAPSHOT_JS not in browser.reads
            route = approval_path(renewed)
        assert (await http.post(route + "/approve", headers=auth, json={})).status_code == 200
        await _until(lambda: any(event["runId"] == handoff["runId"] for event in socket.events("run.finished")))
        assert socket.events("run.finished")[-1]["status"] == "done"
        assert browser.reads.count(SNAPSHOT_JS) == (1 if ending in ("resume", "desktop_resume") else 0)
        launch, = launches
        assert not launch.profile_dir.exists() and not browser.running
        assert "browser_open" not in agent.tools
    finally:
        marker.unlink(missing_ok=True)
        await website_browser.shutdown_browsers()


@pytest.mark.parametrize("app_home", ["native"], indirect=True)
@pytest.mark.parametrize("ending", ["resume", "logout", "control", "login", "login_code"])
async def test_paired_browser_follows_the_selected_computer_and_original_turn(app_home, tmp_path, monkeypatch, ending):
    import json
    import socket
    import sys
    from pathlib import Path

    import aiohttp
    import uvicorn

    from src.agents import web_fetch
    from src.gateway.computer_browser_source import START
    from src.gateway.computer_browser_wire import LoginForm
    from src.gateway.computer_protocol import hello
    from src.gateway.connected_computers import ConnectedComputers
    from src.web.auth import SESSION_COOKIE
    from src.web.server import app
    from tests.unit.test_browser_handoff_login import CODE, LOGIN, seal_login
    from tests.unit.test_computer_browser_preview import _image
    from tests.unit.test_computer_browser_source import fake_chromium
    from tests.unit.test_computer_connector import config, isolated_environment
    from tests.unit.test_computer_web import claim_native_home

    monkeypatch.setenv("VORTOONE_API_TOKEN", "test-paired-task-owner")
    monkeypatch.setattr(web_fetch, "refusal_reason", lambda _host: "")
    claim_native_home({"root": str(tmp_path)}, monkeypatch)
    store = ConnectedComputers(tmp_path)
    machine_id, other_id = "a" * 32, "b" * 32
    grant = store.pair(store.create_pairing()["code"], machine_id, "Chosen computer")
    other = store.pair(store.create_pairing()["code"], other_id, "Different computer")
    agent, events = app_home["agent"], app_home["socket"]
    site = "https://example.test"
    login_enabled = ending in ("login", "login_code")

    class Model:
        calls = 0

        async def chat(self, messages, **_kwargs):
            self.calls += 1
            assert "browser_paired_handoff" in agent.tools and "browser_open" not in agent.tools
            if self.calls == 1:
                return {"content": json.dumps({"tool": "browser_paired_handoff", "args": {"origin": site}})}
            expected = "所有者未完成" if ending == "logout" else "所有者已确认继续，配对电脑的专用窗口"
            assert any(expected in str(message.get("content")) for message in messages)
            return {"content": "配对电脑核对回合已处理"}

    monkeypatch.setattr(agent, "_llm", Model())
    monkeypatch.setattr(agent, "_native", False)
    executable, log = fake_chromium(tmp_path)
    cwd, _, env = isolated_environment(tmp_path)
    env.update(VORTOONE_BROWSER_PATH=str(executable), TMPDIR=str(tmp_path))

    def set_page(path):
        (tmp_path / "page-state.json").write_text(json.dumps({
            "url": site + path, "document": 1000, "protected": False, "frames": False,
            "login": login_enabled, "requires_code": ending == "login_code",
        }))

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="critical",
                                              log_config=None, access_log=False))
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        process = None
        try:
            await _until(lambda: server.started)
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "src.runtime_entry", "computer-connector", cwd=cwd, env=env,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )

            async def send(frame):
                process.stdin.write(json.dumps(frame).encode() + b"\n")
                await process.stdin.drain()

            async def status(state):
                async def read():
                    while line := await process.stdout.readline():
                        frame = json.loads(line)
                        if frame.get("type") == "browser_preview":
                            assert frame["state"] == state and frame["connector_id"] == machine_id
                            return frame["generation"]
                    pytest.fail("connector exited before sending browser status")
                return await asyncio.wait_for(read(), 6)

            await send(config(origin, browser_preview=True, **grant))
            generation = await status("idle")
            assert not log.exists()
            base = origin + app_home["base"]
            auth = {"Origin": origin, "Cookie": f"{SESSION_COOKIE}=test-paired-task-owner"}
            other_auth = {"Authorization": "Bearer " + other["token"]}
            async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=5)) as client:
                async def post(path, body=None, headers=auth):
                    async with client.post(path, json=body if body is not None else {}, headers=headers) as reply:
                        return reply.status, await reply.json()

                sent, message = await post(f"{base}/channels/{app_home['channel']}/messages",
                                           {"body": f"请在配对电脑上人工核对 {site}"})
                assert sent == 200
                await _until(lambda: events.events("approval.created") or events.events("run.finished"))
                approval = events.events("approval.created")[0]["approval"]
                assert approval["websiteLogin"]["pairedBrowser"] is True
                assert events.events("agent.run")[0]["triggerMessageId"] == message["id"]
                assert approval["runId"] == events.events("agent.run")[0]["runId"]
                route = f"{base}/approvals/{approval['id']}"
                pair = route + "/paired-browser"
                watch = route + "/browser-preview/watch"
                assert await post(pair) == (200, {"sharing": False})
                assert (await post(route + "/approve"))[1]["error"]["code"] == "browser_task_not_ready"
                assert (await post(pair, {"machineId": machine_id}, {**auth, "Origin": "https://other.test"}))[0] == 403
                issued, invitation = await post(pair, {"machineId": machine_id})
                assert issued == 200 and invitation["expiresIn"] == 120
                code = invitation["code"]
                async with client.ws_connect(origin + "/api/computers/ws", headers=other_auth) as other_control:
                    await other_control.send_json(hello(other))
                    welcomed = await other_control.receive_json(timeout=2)
                    async with client.ws_connect(origin + "/api/computers/browser-preview", headers=other_auth) as source:
                        await source.send_json({"type": "offer", "generation": welcomed["generation"],
                                                "surface_id": "d" * 32, "task_code": code})
                        assert (await source.receive(timeout=2)).type == aiohttp.WSMsgType.CLOSE
                assert await post(pair) == (200, {"sharing": False})
                control_enabled = ending == "control" or login_enabled
                controls = {"allow_control": True} if control_enabled else {}
                if login_enabled:
                    controls["allow_login"] = True
                await send({"type": START, "generation": generation, "task_code": code, **controls})
                assert await status("sharing") == generation
                sharing = {"sharing": True, "machineId": machine_id}
                if control_enabled:
                    sharing.update(canControl=True, controlling=False)
                assert await post(pair) == (200, sharing)
                generic_watch = f"{base}/machines/{machine_id}/browser-preview"
                with pytest.raises(aiohttp.WSServerHandshakeError):
                    await client.ws_connect(generic_watch, headers=auth)
                set_page("/login")

                async def login_form(viewer, kind="password"):
                    await viewer.send_json({"type": "next"})
                    form = await viewer.receive_json(timeout=2)
                    assert form.get("type") == "login_form", form.get("type")
                    parsed = LoginForm(form["offer"], form["public_key"], kind == "password", kind)
                    assert form == parsed.message()
                    return parsed

                async with client.ws_connect(watch, headers=auth) as viewer:
                    assert (await viewer.receive_json(timeout=2))["type"] == "ready"
                    if login_enabled:
                        form = await login_form(viewer)
                        await viewer.send_json(seal_login(form, LOGIN))
                    else:
                        await viewer.send_json({"type": "next"})
                        assert await viewer.receive_json(timeout=2) == {"type": "paused"}
                    assert (await viewer.receive(timeout=2)).type == aiohttp.WSMsgType.CLOSE
                assert (await post(route + "/approve"))[0] == 409
                assert "Page.captureScreenshot" not in log.read_text()
                if login_enabled:
                    await asyncio.sleep(0.525)
                else:
                    set_page("/ready")
                click = {"type": "input", "action": {"kind": "click", "x": 5000, "y": 5000}}
                if ending == "control":
                    async with client.ws_connect(watch, headers=auth) as waiting:
                        assert (await waiting.receive_json(timeout=2))["type"] == "ready"
                        await waiting.send_json(click)
                        assert (await waiting.receive(timeout=2)).type == aiohttp.WSMsgType.CLOSE
                elif ending == "resume":
                    async with client.ws_connect(watch, headers=auth) as read_only:
                        assert (await read_only.receive_json(timeout=2))["type"] == "ready"
                        await read_only.send_json({"type": "next"})
                        assert await read_only.receive_bytes(timeout=2) == _image()
                        await read_only.send_json({"type": "take_control"})
                        assert (await read_only.receive(timeout=2)).type == aiohttp.WSMsgType.CLOSE
                    await asyncio.sleep(0.525)
                async with client.ws_connect(watch, headers=auth) as viewer:
                    assert (await viewer.receive_json(timeout=2))["type"] == "ready"
                    if login_enabled:
                        form = await login_form(viewer)
                    else:
                        await viewer.send_json({"type": "next"})
                        assert await viewer.receive_bytes(timeout=2) == _image()
                    if control_enabled:
                        await viewer.send_json({"type": "take_control"})
                        assert await viewer.receive_json(timeout=2) == {"type": "control_ready"}
                        assert (await post(pair))[1]["controlling"] is True
                        code, blocked = await post(route + "/approve")
                        assert code == 409 and "交还页面控制" in blocked["error"]["message"]
                        with pytest.raises(aiohttp.WSServerHandshakeError):
                            await client.ws_connect(watch, headers=auth)

                        if login_enabled:
                            await asyncio.sleep(0.525)
                            fresh = await login_form(viewer)
                            assert fresh.offer != form.offer
                            submissions = [seal_login(fresh, LOGIN)]
                            await viewer.send_json(submissions[-1])
                            assert await viewer.receive_json(timeout=2) == {"type": "login_submitted"}
                            await asyncio.sleep(0.525)
                            if ending == "login_code":
                                code_form = await login_form(viewer, "code")
                                assert code_form.offer != fresh.offer
                                assert (await post(route + "/approve"))[0] == 409
                                submissions.append(seal_login(code_form, CODE))
                                await viewer.send_json(submissions[-1])
                                assert await viewer.receive_json(timeout=2) == {"type": "login_submitted"}
                                await asyncio.sleep(0.525)
                            await viewer.send_json({"type": "next"})
                            assert await viewer.receive_json(timeout=2) == {"type": "login_ready"}
                        else:
                            async def next_image():
                                await asyncio.sleep(0.525)
                                await viewer.send_json({"type": "next"})
                                assert await viewer.receive_bytes(timeout=2) == _image()

                            for action in (click["action"], {"kind": "text", "text": "表单测试"},
                                           {"kind": "scroll", "delta": 480}, {"kind": "backspace"}):
                                await next_image()
                                await viewer.send_json({"type": "input", "action": action})
                                assert await viewer.receive_json(timeout=2) == {"type": "input_applied"}
                            await next_image()
                            set_page("/login")
                            await viewer.send_json(click)
                            assert await viewer.receive_json(timeout=2) == {"type": "paused"}
                    elif ending == "logout":
                        assert (await post(origin + "/api/app/device-accounts/sign-out"))[0] == 200
                        assert (await post(pair))[0] == 404
                        assert (await post(route + "/approve"))[0] == 409
                        assert (await post(route + "/refuse"))[0] == 200
                    else:
                        assert (await post(route + "/approve"))[0] == 200
                    assert (await viewer.receive(timeout=3)).type == aiohttp.WSMsgType.CLOSE
                if control_enabled:
                    assert await post(pair) == (200, sharing)
                    if ending == "control":
                        assert (await post(route + "/approve"))[0] == 409
                        set_page("/ready")
                    assert (await post(route + "/approve"))[0] == 200
                assert await status("idle") == generation
                await _until(lambda: events.events("run.finished"))
                assert events.events("run.finished")[-1]["status"] == "done"
                assert (await post(pair))[0] == 404
                commands = [json.loads(row) for row in log.read_text().splitlines()]
                assert not Path(commands[0]["profile"]).exists()
                assert sum(command.get("method") == "Page.captureScreenshot" for command in commands) == {
                    "control": 6, "resume": 2, "logout": 1, "login": 0, "login_code": 0,
                }[ending]
                assert not any(command.get("method") == "Page.navigate" for command in commands)
                inputs = [row for row in commands if row.get("method", "").startswith("Input.")]
                if ending == "control":
                    assert [row["method"] for row in inputs] == [
                        *["Input.dispatchMouseEvent"] * 3, "Input.insertText", "Input.synthesizeScrollGesture",
                        "Input.dispatchKeyEvent", "Input.dispatchKeyEvent",
                    ]
                    assert [row["params"]["type"] for row in inputs[:3]] == ["mouseMoved", "mousePressed", "mouseReleased"]
                    assert inputs[3]["params"] == {"text": "表单测试"}
                    assert inputs[4]["params"]["yDistance"] == -480
                    assert [row["params"]["type"] for row in inputs[5:]] == ["rawKeyDown", "keyUp"]
                    assert all(row["params"]["key"] == "Backspace" for row in inputs[5:])
                    audit = (tmp_path / ".vortoone" / "audit.log").read_text()
                    assert "接管配对电脑的任务浏览器" in audit and "结束配对电脑的浏览器接管" in audit
                    assert "表单测试" not in audit
                else:
                    assert not inputs
                if login_enabled:
                    assert sum(row.get("method") == "Runtime.callFunctionOn" for row in commands) == 2 + 2 * len(submissions)
                    audit = (tmp_path / ".vortoone" / "audit.log").read_text()
                    assert audit.count("提交配对电脑的站点登录") == len(submissions)
                    assert all(value not in audit for value in (*LOGIN.values(), *CODE.values()))
                    assert all(submitted["box"]["ct"] not in audit for submitted in submissions)
                assert "browser_paired_handoff" not in agent.tools
        finally:
            if process is not None:
                process.stdin.close()
                await asyncio.wait_for(process.wait(), 5)
                assert process.returncode == 0 and not await process.stderr.read()
            server.should_exit = True
            await asyncio.wait_for(serving, 3)


@pytest.mark.parametrize("app_home", ["native"], indirect=True)
@pytest.mark.parametrize("ending", ["turn", "replacement", "revocation"])
async def test_role_display_uses_the_current_paired_browser_run(app_home, tmp_path, monkeypatch, ending):
    import json
    import socket
    from contextlib import AsyncExitStack

    import aiohttp
    import uvicorn

    from src.agents import web_fetch
    from src.agents.identity_groups import ADMINS
    from src.gateway.computer_protocol import hello
    from src.gateway.connected_computers import ConnectedComputers
    from src.web import app_views
    from src.web.auth import SESSION_COOKIE
    from src.web.member_auth import MEMBER_COOKIE
    from src.web.server import app
    from src.workflow.members.registry import MemberRegistry
    from tests.unit.test_computer_browser_preview import OWNER_ACTOR, _image
    from tests.unit.test_computer_web import OWNER, browser, claim_native_home, join_member

    monkeypatch.setenv("VORTOONE_API_TOKEN", OWNER)
    monkeypatch.setattr(web_fetch, "refusal_reason", lambda _host: "")
    root = str(tmp_path)
    home = {"base": app_home["base"], "root": root, "app": app}
    claim_native_home(home, monkeypatch)
    admin, admin_id = join_member(home, browser(home, owner=True), "admin@example.test")
    MemberRegistry(root).set_member_groups(OWNER_ACTOR, admin_id, [ADMINS])
    member_cookie = admin.cookies[MEMBER_COOKIE]
    store = ConnectedComputers(root)
    machine_id, other_id = "a" * 32, "b" * 32
    chosen = store.pair(store.create_pairing()["code"], machine_id, "Chosen display computer")
    other = store.pair(store.create_pairing()["code"], other_id, "Another computer")

    class Model:
        async def chat(self, *_args, **_kwargs):
            return {"content": json.dumps({"tool": "browser_paired_handoff", "args": {"origin": "https://example.test"}})}

    monkeypatch.setattr(app_home["agent"], "_llm", Model())
    monkeypatch.setattr(app_home["agent"], "_native", False)
    agent_id = app_views.main_agent_id(root)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        base = origin + home["base"]
        desktop = f"{base}/agents/{agent_id}/desktop"
        server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="critical",
                                              log_config=None, access_log=False))
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            await _until(lambda: server.started)
            async with AsyncExitStack() as stack:
                client = await stack.enter_async_context(aiohttp.ClientSession(trust_env=False))
                owner_headers = {"Origin": origin, "Cookie": f"{SESSION_COOKIE}={OWNER}"}
                admin_headers = {"Origin": origin, "Cookie": f"{MEMBER_COOKIE}={member_cookie}"}

                async def connect(grant):
                    headers = {"Authorization": "Bearer " + grant["token"]}
                    control = await stack.enter_async_context(client.ws_connect(origin + "/api/computers/ws", headers=headers))
                    await control.send_json(hello(grant))
                    return headers, (await control.receive_json(timeout=2))["generation"]

                async def publish(headers, generation, kind, task_code=None):
                    stream = await stack.enter_async_context(client.ws_connect(
                        origin + f"/api/computers/{kind}-preview", headers=headers))
                    offer = {"type": "offer", "generation": generation, "surface_id": "c" * 32}
                    if task_code is not None:
                        offer["task_code"] = task_code
                    await stream.send_json(offer)
                    assert (await stream.receive_json(timeout=2))["type"] == "offered"
                    return stream

                async def status(headers=owner_headers):
                    async with client.get(desktop, headers=headers) as reply:
                        assert reply.headers["Cache-Control"] == "no-store"
                        return reply.status, await reply.json()

                headers, generation = await connect(chosen)
                other_headers, other_generation = await connect(other)
                await publish(other_headers, other_generation, "desktop")
                assert "pairedRun" not in (await status())[1]

                async with client.post(f"{base}/channels/{app_home['channel']}/messages",
                                       headers=owner_headers, json={"body": "在配对电脑核对网页"}) as reply:
                    assert reply.status == 200
                events = app_home["socket"]
                await _until(lambda: events.events("approval.created"))
                approval = events.events("approval.created")[0]["approval"]
                run_id = approval["runId"]
                approval_path = f"{base}/approvals/{approval['id']}/paired-browser"
                async with client.post(approval_path, headers=owner_headers, json={"machineId": machine_id}) as reply:
                    assert reply.status == 200
                    code = (await reply.json())["code"]
                assert "pairedRun" not in (await status())[1]
                await publish(headers, generation, "browser", code)
                binding = {"runId": run_id, "machineId": machine_id, "machineName": "Chosen display computer"}
                assert (await status())[1]["pairedRun"] == {**binding, "displayShared": False}
                publisher = await publish(headers, generation, "desktop")
                assert (await status())[1]["pairedRun"] == {**binding, "displayShared": True}

                denied, hidden = await status(admin_headers)
                assert denied in (403, 404) and "pairedRun" not in hidden
                assert "pairedRun" not in (await status({"Authorization": f"Bearer {OWNER}"}))[1]

                watch = desktop + "/paired-preview/" + run_id
                for url, auth in [(watch, admin_headers), (watch, {**owner_headers, "Origin": "https://different.test"}),
                                  (watch + "-stale", owner_headers)]:
                    with pytest.raises(aiohttp.WSServerHandshakeError) as denied:
                        await client.ws_connect(url, headers=auth)
                    assert denied.value.status == 403
                async with client.ws_connect(watch, headers=owner_headers) as viewer:
                    assert (await viewer.receive_json(timeout=2))["type"] == "ready"
                    await viewer.send_json({"type": "next"})
                    capture = await publisher.receive_json(timeout=2)
                    await publisher.send_bytes(capture["request_id"].encode("ascii") + _image())
                    assert await viewer.receive_bytes(timeout=2) == _image()
                    await viewer.send_json({"type": "take_control"})
                    assert (await viewer.receive(timeout=2)).type == aiohttp.WSMsgType.CLOSE

                await asyncio.sleep(0.525)
                async with client.ws_connect(watch, headers=owner_headers) as viewer:
                    assert (await viewer.receive_json(timeout=2))["type"] == "ready"
                    await viewer.send_json({"type": "next"})
                    capture = await publisher.receive_json(timeout=2)
                    if ending == "turn":
                        async with client.post(f"{base}/runs/{run_id}/cancel", headers=owner_headers) as reply:
                            assert reply.status == 204
                        await _until(lambda: events.events("run.finished"))
                    elif ending == "replacement":
                        _, next_generation = await connect(chosen)
                        assert next_generation != generation
                    else:
                        await asyncio.to_thread(store.revoke, machine_id)
                    assert "pairedRun" not in (await status())[1]
                    if ending == "turn":
                        await publisher.send_bytes(capture["request_id"].encode("ascii") + _image())
                    assert (await viewer.receive(timeout=4)).type == aiohttp.WSMsgType.CLOSE
                    with pytest.raises(aiohttp.WSServerHandshakeError):
                        await client.ws_connect(watch, headers=owner_headers)
        finally:
            server.should_exit = True
            await asyncio.wait_for(serving, 5)


def test_website_login_projection_requires_a_run_and_an_origin_without_credentials():
    from src.web import app_views as views
    pending = {"id": "test", "rid": "run", "operation_kind": "interact",
               "created": "2026-11-16T01:00:00+00:00", "text": "请到运行端完成登录"}
    site = {"version": 1, "action": "login", "origin": "https://example.test", "account_label": "test",
            "device_mode": "desktop"}
    for invalid in (
        {"rid": None}, {"operation_kind": "read"}, {"website": {**site, "origin": "https://example.test?code=secret"}},
        {"website": {**site, "origin": "https://user:secret@example.test"}}, {"website": {**site, "action": "open"}},
    ):
        assert "websiteLogin" not in views.approval(".", {**pending, "website": site, **invalid})


async def test_a_confirmation_from_another_session_cannot_be_decided_here(app_home):
    from src.web.routers import realtime
    future = asyncio.get_running_loop().create_future()
    realtime._PENDING_CONFIRMS["other"] = {"future": future, "text": "别的会话的确认", "tainted": False,
                                           "session": "sid-someone-else", "created": "2026-11-16T01:00:00+00:00"}
    try:
        http, base = app_home["http"], app_home["base"]
        assert (await http.get(f"{base}/approvals")).json() == {"approvals": []}
        assert (await http.post(f"{base}/approvals/other/approve", json={})).status_code == 409
        assert not future.done()
    finally:
        realtime._PENDING_CONFIRMS.pop("other", None)


def test_channel_events_are_validated_before_they_leave():
    from src.gateway import app_protocol as F
    from src.gateway.protocol import ProtocolError
    ended = F.channel_event("c", "agent.tool", threadId="t", runId="r", agentId="a", tool=None)
    assert ended["tool"] is None and ended["channelId"] == "c"
    for bad in (lambda: F.channel_event("c", "agent.delta", text="x"),
                lambda: F.channel_event("c", "run.finished", runId="r"),
                lambda: F.channel_event("c", "message.created", messageId="m", message={}, secret=1)):
        with pytest.raises(ProtocolError):
            bad()
