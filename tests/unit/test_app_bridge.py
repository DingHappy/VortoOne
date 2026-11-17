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
        emit("收到：" + text)


@pytest.fixture
async def app_home(tmp_path, monkeypatch):
    from src.web import app_bridge
    from src.web.routers import realtime
    monkeypatch.chdir(tmp_path)
    for name in ("VORTOONE_API_TOKEN", "VORTOCODE_API_TOKEN", "AUTODEV_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    agent = _Agent()
    monkeypatch.setattr(realtime, "_new_agent", lambda: agent)
    monkeypatch.setattr(realtime, "_schedule_turn_digest", lambda key, rid: None)

    async def no_mcp(agent, say):
        return None

    monkeypatch.setattr(realtime, "_ensure_mcp", no_mcp)
    app_bridge.reset_bridges()
    from src.web.server import app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://home") as http:
        org = (await http.get("/api/app/me")).json()["organizations"][0]["id"]
        channel = (await http.get(f"/api/app/organizations/{org}/dms")).json()["dms"][0]["channelId"]
        bridge = await app_bridge.main_chat_bridge(str(tmp_path))
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
