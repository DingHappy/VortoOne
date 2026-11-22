"""新版界面里的「AI 密钥」：所有者加的 OpenAI 兼容端点要真的能用上，不能只存一份不生效的记录。

- 加的时候逐个模型真调一次（这里换成假端点，不联网）：调了工具 ok，只说话 no_tools，全失败就不存。
- 存下后登记成一个供应商：agent 的模型写 custom:<密钥>:<模型> 就走这个端点，key 不借给别家。
- 设为默认：这个家默认的模型端点换成它；换回服务配置、或删掉它，原来的端点回来。
- 服务配置里的那一个只读列出；密钥从不回给界面；只有所有者能看能改。
"""
import os

import pytest

from src.web import app_credentials as creds
from tests.unit.test_member_agents import _join, home  # noqa: F401 - pytest fixture

_ENV = ("OPENAI_API_BASE", "OPENAI_API_KEY", "DEFAULT_MODEL")
SECRET = "sk-test-secret-0123456789abcd"


@pytest.fixture(autouse=True)
def clean_env(home, monkeypatch):     # 在 home 之后：home 会清掉 OPENAI_API_KEY
    saved = {k: os.environ.get(k) for k in _ENV}
    os.environ["OPENAI_API_BASE"] = "https://relay.example/v1"
    os.environ["OPENAI_API_KEY"] = "sk-server-key-7777"
    os.environ["DEFAULT_MODEL"] = "relay-model"
    creds._SERVER_ENV = None
    calls = []

    async def check_model(base_url, key, model):
        calls.append((base_url, key, model))
        if key == "sk-bad-key-000000000":
            return {"model": model, "check": "failed", "error": "401 invalid key"}
        return {"model": model, "check": "no_tools" if model == "chatty" else "ok"}

    async def list_models(base_url, key):
        return [{"id": "m1"}, {"id": "m2"}], ""
    monkeypatch.setattr(creds, "check_model", check_model)
    monkeypatch.setattr(creds, "list_models", list_models)
    yield calls
    for name in [k for k in os.environ if k.startswith("VORTOONE_PROVIDER_CRED_")]:
        os.environ.pop(name)
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    creds._SERVER_ENV = None
    from src.llm.providers import reset_cache
    reset_cache()


def _add(home, key=SECRET, models=("good", "chatty"), base="https://my-endpoint.example/v1/"):
    return home["owner"].post(f"{home['base']}/credentials", json={
        "provider": "custom", "label": "我的端点", "apiKey": key,
        "endpoint": {"style": "openai", "baseURL": base,
                     "models": [{"id": m, **({"prices": {"input": 1, "output": 2}} if m == "good" else {})}
                                for m in models]}})


def test_adding_an_endpoint_tests_each_model_and_never_returns_the_key(home, clean_env):
    added = _add(home)
    assert added.status_code == 200, added.text
    body = added.json()
    assert body["provider"] == "custom" and body["keyHint"] == "abcd" and body["check"] == "ok"
    assert body["endpoint"]["baseURL"] == "https://my-endpoint.example/v1"
    assert body["checks"] == [{"model": "good", "check": "ok"}, {"model": "chatty", "check": "no_tools"}]
    assert SECRET not in added.text
    assert {c[2] for c in clean_env} == {"good", "chatty"}
    listed = home["owner"].get(f"{home['base']}/credentials")
    assert SECRET not in listed.text and "sk-server-key-7777" not in listed.text
    rows = listed.json()["credentials"]
    assert [r["id"] for r in rows] == ["server", body["id"]]       # 服务配置的那一个只读列在最前
    assert rows[0]["keyHint"] == "7777" and rows[0]["endpoint"]["models"] == [{"id": "relay-model"}]
    assert oct(os.stat(creds._path(home["root"])).st_mode & 0o777) == "0o600"
    again = _add(home)                                             # 同一把 key、同一个地址：更新，不加副本
    assert again.json()["reused"] is True and again.json()["id"] == body["id"]


def test_a_key_the_endpoint_refuses_is_not_saved(home):
    refused = _add(home, key="sk-bad-key-000000000")
    assert refused.status_code == 400 and refused.json()["error"]["code"] == "credential.refused"
    assert [r["id"] for r in home["owner"].get(f"{home['base']}/credentials").json()["credentials"]] == ["server"]


def test_only_openai_compatible_custom_endpoints_are_accepted(home):
    owner, base = home["owner"], home["base"]
    anthropic = owner.post(f"{base}/credentials", json={"provider": "anthropic", "label": "A", "apiKey": SECRET})
    assert anthropic.status_code == 400 and anthropic.json()["error"]["code"] == "provider.unsupported"
    messages = owner.post(f"{base}/credentials", json={
        "provider": "custom", "label": "A", "apiKey": SECRET,
        "endpoint": {"style": "anthropic", "baseURL": "https://x.example", "models": [{"id": "m"}]}})
    assert messages.status_code == 400 and messages.json()["error"]["code"] == "endpoint.style_unsupported"


def test_agents_reach_the_endpoint_with_its_own_key(home):
    from src.llm.providers import chat_target
    cid = _add(home).json()["id"]
    target = chat_target(f"custom:{cid}:good")
    assert target is not None
    provider, model = target
    assert (provider.base_url, provider.api_key, model) == ("https://my-endpoint.example/v1", SECRET, "good")
    listed = home["owner"].get("/api/app/models").json()
    ids = {m["id"]: m for m in listed["models"]}
    assert ids[f"custom:{cid}:good"]["endpoint"] == {"credentialId": cid, "name": "good"}
    assert ids[f"custom:{cid}:good"]["inputUsdPerMTok"] == 1 and ids[f"custom:{cid}:chatty"]["unpriced"] is True
    assert listed["defaultModel"] == ""
    from src.sandbox.policy import child_env
    assert SECRET not in child_env().values()                      # 子进程拿不到这把 key


def test_making_it_default_switches_the_home_and_switching_back_restores_the_server(home):
    owner, base = home["owner"], home["base"]
    cid = _add(home).json()["id"]
    assert owner.post(f"{base}/credentials/{cid}/make-default", json={}).status_code == 200
    assert (os.environ["OPENAI_API_BASE"], os.environ["OPENAI_API_KEY"], os.environ["DEFAULT_MODEL"]) == (
        "https://my-endpoint.example/v1", SECRET, "good")             # 默认模型挑调过工具的那个
    assert owner.get("/api/app/models").json()["defaultModel"] == f"custom:{cid}:good"
    rows = owner.get(f"{base}/credentials").json()["credentials"]
    assert rows[0]["id"] == "server" and rows[0]["keyHint"] == "7777"   # 服务配置那一行还是原来那份
    assert owner.post(f"{base}/credentials/server/make-default", json={}).status_code == 200
    assert (os.environ["OPENAI_API_BASE"], os.environ["OPENAI_API_KEY"], os.environ["DEFAULT_MODEL"]) == (
        "https://relay.example/v1", "sk-server-key-7777", "relay-model")


def test_removing_the_default_needs_release_and_then_restores_the_server(home):
    owner, base = home["owner"], home["base"]
    cid = _add(home).json()["id"]
    owner.post(f"{base}/credentials/{cid}/make-default", json={})
    held = owner.delete(f"{base}/credentials/{cid}")
    assert held.status_code == 409 and held.json()["error"]["code"] == "credential.in_use"
    assert owner.delete(f"{base}/credentials/{cid}", params={"release": "true"}).status_code == 204
    assert os.environ["OPENAI_API_KEY"] == "sk-server-key-7777"
    rows = owner.get(f"{base}/credentials").json()["credentials"]
    assert next(r for r in rows if r["id"] == cid)["revokedAt"] is not None
    from src.llm.providers import chat_target
    assert chat_target(f"custom:{cid}:good") is None               # 停用后不再登记
    assert owner.delete(f"{base}/credentials/{cid}", params={"purge": "true", "release": "true"}).status_code == 204
    assert [r["id"] for r in owner.get(f"{base}/credentials").json()["credentials"]] == ["server"]


def test_an_agent_using_it_is_named_before_removal(home):
    owner, base = home["owner"], home["base"]
    cid = _add(home).json()["id"]
    agent = owner.post(f"{base}/agents", json={"name": "端点用户", "handle": "endpointer", "model": f"custom:{cid}:good"})
    assert agent.status_code == 200, agent.text
    row = next(r for r in owner.get(f"{base}/credentials").json()["credentials"] if r["id"] == cid)
    assert row["usedBy"] == [{"id": agent.json()["id"], "name": "端点用户"}]
    held = owner.delete(f"{base}/credentials/{cid}")
    assert held.status_code == 409 and "端点用户" in held.json()["error"]["message"]


def test_rotating_tests_the_new_key_first(home):
    owner, base = home["owner"], home["base"]
    cid = _add(home).json()["id"]
    bad = owner.post(f"{base}/credentials/{cid}/rotate", json={"apiKey": "sk-bad-key-000000000"})
    assert bad.status_code == 400
    rotated = owner.post(f"{base}/credentials/{cid}/rotate", json={"apiKey": "sk-new-secret-99999999zzzz"})
    assert rotated.status_code == 200 and rotated.json()["keyHint"] == "zzzz"
    from src.llm.providers import chat_target
    assert chat_target(f"custom:{cid}:good")[0].api_key == "sk-new-secret-99999999zzzz"


def test_changing_models_tests_them_again(home):
    owner, base = home["owner"], home["base"]
    cid = _add(home).json()["id"]
    changed = owner.put(f"{base}/credentials/{cid}/models", json={"models": [{"id": "chatty"}]})
    assert changed.status_code == 200
    assert changed.json() == {"models": [{"id": "chatty", "check": "no_tools"}],
                              "checks": [{"model": "chatty", "check": "no_tools"}]}
    assert owner.post(f"{base}/credentials/{cid}/check", json={}).json() == {"check": "unchecked"}


def test_listing_an_endpoints_models_saves_nothing(home):
    listed = home["owner"].post(f"{home['base']}/credentials/endpoint-models",
                                json={"style": "openai", "baseURL": "https://x.example/v1/", "apiKey": SECRET})
    assert listed.json() == {"baseURL": "https://x.example/v1", "models": [{"id": "m1"}, {"id": "m2"}]}
    assert [r["id"] for r in home["owner"].get(f"{home['base']}/credentials").json()["credentials"]] == ["server"]


def test_the_server_row_is_changed_in_the_server_config(home):
    owner, base = home["owner"], home["base"]
    for call in (lambda: owner.delete(f"{base}/credentials/server"),
                 lambda: owner.post(f"{base}/credentials/server/rotate", json={"apiKey": SECRET}),
                 lambda: owner.put(f"{base}/credentials/server/models", json={"models": [{"id": "x"}]})):
        answer = call()
        assert answer.status_code == 409 and answer.json()["error"]["code"] == "credential.server"


def test_members_cannot_see_or_change_keys(home):
    member = _join(home)
    assert member.get(f"{home['base']}/credentials").status_code == 403
    assert _add({**home, "owner": member}).status_code == 403


def test_changing_keys_needs_the_owner_proof(home, monkeypatch):
    """只读列表对所有者开放；加、改、删要带所有者证明（Bearer 或同源会话）。"""
    monkeypatch.setattr("src.web.app_staff.owner_proof", lambda request: False)
    assert home["owner"].get(f"{home['base']}/credentials").status_code == 200
    refused = _add(home)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "owner_proof_required"
