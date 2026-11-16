"""新版界面里与专员的私聊：读写都落在现有的派活记录上，并沿用它的主人令牌规矩。"""

import pytest

from tests.unit.test_staff_contract import staff_setup

TOKEN = "offline-owner"


@pytest.fixture
def home(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.gateway.main_conversation import claim_main_chat
    from src.web import app_bridge
    from src.web.server import app

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VORTOONE_API_TOKEN", TOKEN)
    registry, member, pool, service = staff_setup(tmp_path)
    pool.jobs.clear()
    claim_main_chat(str(tmp_path), "sid-owner")
    monkeypatch.setattr("src.scheduling.staff_runner.get_staff_service", lambda root: service)
    app_bridge.reset_bridges()
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {TOKEN}"
    org = client.get("/api/app/me").json()["organizations"][0]["id"]
    base = f"/api/app/organizations/{org}"
    agent = next(a for a in client.get(f"{base}/agents").json()["agents"] if a["handle"] == member["id"])
    channel = client.post(f"{base}/dms", json={"agentId": agent["id"]}).json()["channelId"]
    return {"client": client, "base": base, "agent": agent, "channel": channel, "member": member,
            "pool": pool, "service": service, "registry": registry}


async def _finish(pool, task_id):
    execute, callbacks = pool.jobs[task_id]
    async with callbacks["admission"]():
        await execute()


async def test_a_message_becomes_one_staff_task_and_its_result_comes_back(home):
    client, base, channel, pool, service = (home[k] for k in ("client", "base", "channel", "pool", "service"))
    assert home["agent"]["canTalk"] is True
    sent = client.post(f"{base}/channels/{channel}/messages", json={"body": "读一下这份材料", "clientId": "c1"})
    assert sent.status_code == 200, sent.text
    message = sent.json()
    assert message["body"] == "读一下这份材料" and message["clientId"] == "c1"

    task_id, = pool.jobs
    task = service.collaboration("sid-owner").get(task_id)
    assert task.kind == "delegation" and task.collaboration["dispatch"]["member"] == home["member"]["id"]
    assert task.collaboration["dispatch"]["delivery"] == "notify" and task.collaboration["acceptance"] == []

    runs = client.get(f"{base}/channels/{channel}/active-runs").json()["runs"]
    assert [(r["runId"], r["triggerMessageId"]) for r in runs] == [(task_id, message["id"])]
    assert [m["id"] for m in client.get(f"{base}/channels/{channel}/messages").json()["messages"]] == [message["id"]]

    await _finish(pool, task_id)
    listed = client.get(f"{base}/channels/{channel}/messages").json()["messages"]
    assert [(m["author"]["type"], m["body"]) for m in listed] == [("person", "读一下这份材料"), ("agent", "逐条交回结论")]
    assert listed[1]["author"]["id"] == home["agent"]["id"]
    assert client.get(f"{base}/channels/{channel}/active-runs").json() == {"runs": []}


@pytest.mark.parametrize("credentials", ["none", "cookie", "wrong"])
def test_staff_conversations_need_the_explicit_owner_bearer(home, credentials):
    from src.web.auth import SESSION_COOKIE
    client, base, channel, pool = (home[k] for k in ("client", "base", "channel", "pool"))
    client.headers.pop("Authorization")
    if credentials == "cookie":
        client.cookies.set(SESSION_COOKIE, TOKEN)
    if credentials == "wrong":
        client.headers["Authorization"] = "Bearer wrong"
    sent = client.post(f"{base}/channels/{channel}/messages", json={"body": "读一下"})
    read = client.get(f"{base}/channels/{channel}/messages")
    assert sent.status_code in {401, 403} and read.status_code in {401, 403}
    assert not pool.jobs


def test_a_message_cannot_carry_authority(home):
    client, base, channel, pool, service = (home[k] for k in ("client", "base", "channel", "pool", "service"))
    sent = client.post(f"{base}/channels/{channel}/messages",
                       json={"body": "读一下", "grants": {"web": True}, "role": "developer", "delivery": "review"})
    assert sent.status_code == 200
    task = service.collaboration("sid-owner").get(next(iter(pool.jobs)))
    assert task.collaboration["dispatch"]["member"] == home["member"]["id"]
    assert task.collaboration["dispatch"]["delivery"] == "notify"       # 多带的字段不被采用


def test_paused_member_does_not_take_messages(home):
    client, base, channel, pool, registry, member = (
        home[k] for k in ("client", "base", "channel", "pool", "registry", "member"))
    registry.set_member_status(member["id"], "paused", registry.member(member["id"])["revision"])
    agent = next(a for a in client.get(f"{base}/agents").json()["agents"] if a["handle"] == member["id"])
    assert agent["canTalk"] is False
    refused = client.post(f"{base}/channels/{channel}/messages", json={"body": "读一下"})
    assert refused.status_code == 409 and not pool.jobs


def test_stop_reaches_the_task_only_with_owner_bearer(home):
    client, base, channel, pool, service = (home[k] for k in ("client", "base", "channel", "pool", "service"))
    stopped = []
    service.runner.cancel = lambda task_id: stopped.append(task_id) or True
    client.post(f"{base}/channels/{channel}/messages", json={"body": "读一下"})
    task_id, = pool.jobs
    client.headers.pop("Authorization")
    assert client.post(f"{base}/runs/{task_id}/cancel", json={}).status_code in {401, 403} and not stopped
    client.headers["Authorization"] = f"Bearer {TOKEN}"
    assert client.post(f"{base}/runs/{task_id}/cancel", json={}).status_code == 204 and stopped == [task_id]
    assert client.post(f"{base}/runs/task-unknown0000/cancel", json={}).status_code == 409


async def test_watch_pushes_the_result_when_the_task_finishes(home):
    import asyncio
    from src.web import app_staff
    client, base, channel, pool = (home[k] for k in ("client", "base", "channel", "pool"))

    class Socket:
        frames: list = []

        async def send_json(self, frame):
            self.frames.append(frame["event"])

    socket = Socket()
    watching = asyncio.create_task(app_staff.watch(socket, ".", home["member"]["id"], interval=0.01))
    try:
        await asyncio.sleep(0.05)
        sent = (await asyncio.to_thread(client.post, f"{base}/channels/{channel}/messages", json={"body": "读一下"})).json()
        task_id, = pool.jobs
        for _ in range(200):
            if any(e["type"] == "agent.run" for e in socket.frames):
                break
            await asyncio.sleep(0.01)
        await _finish(pool, task_id)
        for _ in range(200):
            if any(e["type"] == "run.finished" for e in socket.frames):
                break
            await asyncio.sleep(0.01)
    finally:
        watching.cancel()
    kinds = [e["type"] for e in socket.frames]
    assert kinds.index("agent.run") < kinds.index("run.finished")
    created = [e["message"] for e in socket.frames if e["type"] == "message.created"]
    assert [m["body"] for m in created] == ["读一下", "逐条交回结论"] and created[0]["id"] == sent["id"]
    assert next(e for e in socket.frames if e["type"] == "run.finished")["status"] == "done"
