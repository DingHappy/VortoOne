"""所有者加的模型端点：key 和对话只去它自己的地址（#1033 的回归）。

看每个请求实际拿着哪把 key、去哪个地址、点名哪个模型，不看配置里存了什么：
- 切换默认端点的任何一刻建的客户端，地址和 key 都是同一家的；
- 停用、删掉的端点：用它的请求报错停下，不改发给默认端点；换 key 立刻跟上；
- 默认端点换了，已经在用默认端点的客户端跟着换，点名了端点的不动；
- 服务启动时先登记端点，再恢复任务、接 IM。
"""
import os
import sys

import pytest

from src.llm import client as llm
from src.web import app_credentials as creds

CID = "cred-0123456789ab"
SPEC = f"custom:{CID}:mine-model"
SERVER = ("https://server.example/v1", "fake-server-key")
MINE = ("https://mine.example/v1", "fake-mine-key")


@pytest.fixture
def wire(tmp_path, monkeypatch):
    saved = dict(os.environ)
    os.environ.update(OPENAI_API_BASE=SERVER[0], OPENAI_API_KEY=SERVER[1], DEFAULT_MODEL="server-model")
    monkeypatch.setattr(creds, "_SERVER_ENV", None)
    monkeypatch.setattr(llm, "_client_cache", {})
    monkeypatch.setitem(sys.modules, "openai", None)           # 走不依赖 SDK 的请求路径，不联网
    sent = []

    async def send(self, messages, model=None, temperature=None, max_tokens=None, **_kw):
        sent.append((self.config.base_url, self.config.api_key, model or self.config.model))
        return {"content": "ok", "usage": {}}
    monkeypatch.setattr(llm.LLMClient, "_chat_with_requests", send)
    root = str(tmp_path)
    creds._write(root, {"default": None, "credentials": [{
        "id": CID, "label": "我的端点", "key": MINE[1], "createdAt": creds._now(), "revokedAt": None,
        "endpoint": {"style": "openai", "baseURL": MINE[0], "models": [{"id": "mine-model", "check": "ok"}]}}]})
    creds.apply(root)

    async def ask(client, **kwargs):
        await client.chat([{"role": "user", "content": "私人内容"}], **kwargs)
        return sent[-1]
    yield {"root": root, "sent": sent, "ask": ask}
    for key in set(os.environ) - set(saved):
        del os.environ[key]
    os.environ.update(saved)
    from src.llm.providers import reset_cache
    reset_cache()


def _agent(spec=None):
    from src.agents.agent_loop import MainAgent
    agent = MainAgent([], llm=llm.LLMClient(), native=False)
    if spec:
        agent.set_model(spec)
    return agent


def _rekey(root, key):
    data = creds._read(root)
    data["credentials"][0]["key"] = key
    creds._write(root, data)
    creds.apply(root)


@pytest.mark.parametrize("back", [False, True])
def test_a_client_built_mid_switch_never_mixes_endpoints_and_keys(wire, monkeypatch, back):
    import threading
    if back:
        creds.make_default(wire["root"], CID)
    real, built, workers = os._Environ.__setitem__, [], []

    def halfway(self, key, value):
        real(self, key, value)
        if key == "OPENAI_API_BASE" and not workers:        # 地址已换、key 还没换的那一刻
            worker = threading.Thread(target=lambda: built.append(llm.LLMClient().config))
            workers.append(worker)
            worker.start()
            worker.join(0.2)
    monkeypatch.setattr(os._Environ, "__setitem__", halfway)
    creds.make_default(wire["root"], "server" if back else CID)
    workers[0].join(5)
    assert (built[0].base_url, built[0].api_key) in (SERVER, MINE)


async def test_a_revoked_endpoint_is_never_replaced_by_the_default_one(wire):
    creds.remove(wire["root"], CID, purge=False, release=True)
    with pytest.raises(ValueError, match="端点"):
        await wire["ask"](_agent(SPEC)._client())
    with pytest.raises(ValueError, match="端点"):
        await wire["ask"](llm.LLMClient(), model=SPEC)
    assert wire["sent"] == []


@pytest.mark.parametrize("purge", [False, True])
async def test_a_running_agent_stops_when_its_endpoint_is_removed(wire, purge):
    client = _agent(SPEC)._client()
    assert await wire["ask"](client) == (*MINE, "mine-model")
    creds.remove(wire["root"], CID, purge=purge, release=True)
    with pytest.raises(ValueError, match="端点"):
        await wire["ask"](client)
    assert len(wire["sent"]) == 1


async def test_a_copied_client_stops_too(wire):
    copied = llm.LLMClient(_agent(SPEC)._client().config.model_copy(deep=True))
    creds.remove(wire["root"], CID, purge=False, release=True)
    with pytest.raises(ValueError, match="端点"):
        await wire["ask"](copied)
    assert wire["sent"] == []


async def test_a_running_agent_follows_a_rotated_key_and_back(wire):
    client = _agent(SPEC)._client()
    _rekey(wire["root"], "fake-rotated-key")
    assert await wire["ask"](client) == (MINE[0], "fake-rotated-key", "mine-model")
    _rekey(wire["root"], MINE[1])
    assert await wire["ask"](client) == (*MINE, "mine-model")


async def test_clients_on_the_default_follow_it_and_back(wire):
    main, cached = _agent()._client(), llm.get_llm_client("cheap")
    creds.make_default(wire["root"], CID)
    for client in (main, cached, llm.get_llm_client("powerful"), llm.LLMClient()):
        assert await wire["ask"](client) == (*MINE, "mine-model")
    creds.remove(wire["root"], CID, purge=False, release=True)
    for client in (main, cached):
        assert await wire["ask"](client) == (*SERVER, "server-model")


async def test_an_agent_pinned_to_the_default_endpoint_stays_pinned(wire):
    creds.make_default(wire["root"], CID)
    client = _agent(SPEC)._client()
    creds.make_default(wire["root"], "server")
    assert await wire["ask"](client) == (*MINE, "mine-model")


async def test_unpinning_goes_to_the_current_default_with_the_chosen_model(wire):
    agent = _agent(SPEC)
    creds.make_default(wire["root"], CID)
    creds.make_default(wire["root"], "server")
    agent.set_model("another-server-model")
    assert await wire["ask"](agent._client()) == (*SERVER, "another-server-model")


def test_external_frameworks_refuse_endpoint_models(wire):
    from src.agents.team_engines import EngineUnavailable, engine_env
    with pytest.raises(EngineUnavailable, match="端点"):
        engine_env("opencode", SPEC)


async def test_startup_registers_endpoints_before_tasks_are_recovered(monkeypatch):
    from src.web import runtime
    order = []

    class Stop(Exception):
        pass

    class Host:
        async def __aenter__(self):
            order.append("recover")
            raise Stop

        async def __aexit__(self, *_args):
            return False
    monkeypatch.setattr("src.gateway.runtime.runtime_lifespan", lambda *_a, **_k: Host())
    monkeypatch.setattr("src.web.app_credentials.apply", lambda root: order.append("register"))
    with pytest.raises(Stop):
        async with runtime.runtime_lifespan():
            pass
    assert order == ["register", "recover"]


async def test_the_default_model_follows_on_the_same_relay(wire):
    """自定义端点和服务配置是同一个中转、只是默认模型不同：已经在用默认的客户端也要换模型，手动选的不动。"""
    main, chosen = _agent()._client(), _agent("picked-by-hand")._client()
    data = creds._read(wire["root"])
    data["credentials"][0]["key"] = SERVER[1]
    data["credentials"][0]["endpoint"]["baseURL"] = SERVER[0]
    creds._write(wire["root"], data)
    creds.apply(wire["root"])
    creds.make_default(wire["root"], CID)
    assert await wire["ask"](main) == (*SERVER, "mine-model")
    assert await wire["ask"](chosen) == (*SERVER, "picked-by-hand")
    creds.make_default(wire["root"], "server")
    assert await wire["ask"](main) == (*SERVER, "server-model")
