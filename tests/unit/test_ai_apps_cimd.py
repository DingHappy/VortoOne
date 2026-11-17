"""AI 应用按地址登记（Client ID Metadata Document）：取说明文件、核对、缓存，以及应用怎么显示。"""
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from src.workflow.ai_apps import clients as C
from src.workflow.ai_apps import store as S
from tests.unit.test_ai_apps import ORIGIN, _pkce, home  # noqa: F401 —— 复用同一个服务夹具

CHATGPT = "https://chatgpt.com/oauth/vortoone-client.json"
CHATGPT_BACK = "https://chatgpt.com/connector_platform_oauth_redirect"


def _document(client_id=CHATGPT, redirect=CHATGPT_BACK, **extra):
    return json.dumps({"client_id": client_id, "client_name": "ChatGPT", "redirect_uris": [redirect],
                       "token_endpoint_auth_method": "private_key_jwt", **extra}).encode()


@pytest.fixture(autouse=True)
def clean():
    S.reset_document_failures()
    yield
    S.reset_document_failures()


def _start(store, client_id=CHATGPT, redirect=CHATGPT_BACK):
    return store.start({"client_id": client_id, "redirect_uri": redirect, "response_type": "code",
                        "code_challenge": _pkce()[1], "code_challenge_method": "S256"}, "https://one.example/mcp")


# ── 地址与文件 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value, ok", [
    (CHATGPT, True), ("https://claude.ai/oauth/claude-code-client-metadata", True),
    ("https://chatgpt.com/", False), ("https://chatgpt.com", False), ("http://chatgpt.com/x.json", False),
    ("https://127.0.0.1/x.json", False), ("https://[::1]/x.json", False), ("https://localhost/x.json", False),
    ("https://a.localhost/x.json", False), ("https://intranet/x.json", False), ("https://u@chatgpt.com/x", False),
    ("https://chatgpt.com/x#y", False), ("vcl_abc", False), (None, False)])
def test_which_client_ids_name_a_document(value, ok):
    assert (C.cimd_url(value) is not None) is ok


def test_a_document_must_name_its_own_address():
    assert C.parse_document(CHATGPT, _document())["redirect_uris"] == [CHATGPT_BACK]
    with pytest.raises(C.InvalidClient):
        C.parse_document(CHATGPT, _document(client_id="https://chatgpt.com/other.json"))
    with pytest.raises(C.InvalidClient):
        C.parse_document(CHATGPT, _document(redirect="myapp://cb"))
    with pytest.raises(C.InvalidClient):
        C.parse_document(CHATGPT, _document(token_endpoint_auth_method="client_secret_basic"))
    with pytest.raises(C.InvalidClient):
        C.parse_document(CHATGPT, b"<html>")
    with pytest.raises(C.InvalidClient):
        C.parse_document(CHATGPT, b" " * (C.DOCUMENT_BYTES + 1))


# ── 应用怎么显示 ──────────────────────────────────────────────────────────────

def test_a_document_on_the_vendors_own_host_is_verified():
    shown = C.display(CHATGPT_BACK, "ChatGPT", CHATGPT)
    assert shown["displayKind"] == "verified" and shown["brand"] == "openai" and shown["host"] == "chatgpt.com"
    assert shown["reservedNameWarning"] is False


def test_a_document_elsewhere_is_unverified_and_a_borrowed_name_is_flagged():
    shown = C.display("https://evil.example/cb", "ChatGPT", "https://evil.example/client.json")
    assert shown["displayKind"] == "https" and shown["brand"] is None and shown["host"] == "evil.example"
    assert shown["reservedNameWarning"] is True


def test_a_program_on_this_computer_holding_a_known_document_is_still_a_program():
    shown = C.display("http://127.0.0.1:5000/cb", "Claude", "https://claude.ai/oauth/client.json")
    assert shown["displayKind"] == "loopback" and shown["brand"] is None


def test_a_registered_app_returning_to_a_vendors_host_shows_that_brand():
    shown = C.display("https://claude.ai/api/mcp/auth_callback", "Claude", "vcl_x")
    assert shown["displayKind"] == "https" and shown["brand"] == "claude" and shown["reservedNameWarning"] is False


# ── 取文件：缓存、失败、旧副本 ────────────────────────────────────────────────

def test_documents_are_fetched_once_kept_five_minutes_and_used_stale_for_a_day(tmp_path):
    clock, calls, answer = [1000.0], [], {"body": _document()}

    def fetch(url):
        calls.append(url)
        if isinstance(answer["body"], Exception):
            raise answer["body"]
        return answer["body"]
    store = S.AiApps(str(tmp_path), clock=lambda: clock[0], fetch=fetch)
    _start(store)
    _start(store)
    assert calls == [CHATGPT]                                  # 5 分钟内不再取
    clock[0] += 301
    answer["body"] = OSError("down")
    _start(store)                                              # 取不到：用旧副本
    assert len(calls) == 2
    _start(store)
    assert len(calls) == 2                                     # 20 秒内不再试
    clock[0] += S.DOCUMENT_STALE
    with pytest.raises(S.StartError) as refused:
        _start(store)
    assert refused.value.page == "app_desconhecido"            # 一天都没取到：不再认


def test_an_unreachable_or_wrong_document_means_an_unknown_app(tmp_path):
    def fetch(url):
        return _document(client_id="https://chatgpt.com/someone-else.json")
    with pytest.raises(S.StartError) as refused:
        _start(S.AiApps(str(tmp_path), fetch=fetch))
    assert refused.value.page == "app_desconhecido"


def test_the_return_address_must_be_one_the_document_lists(tmp_path):
    store = S.AiApps(str(tmp_path), fetch=lambda url: _document())
    with pytest.raises(S.StartError) as refused:
        _start(store, redirect="https://evil.example/cb")
    assert refused.value.page == "endereco_de_retorno"


def test_the_real_fetch_refuses_private_targets_and_redirects(monkeypatch):
    import urllib.error

    from src.agents import web_fetch
    with pytest.raises(web_fetch.FetchError):
        S.fetch_document("https://127.0.0.1/client.json")
    with pytest.raises(web_fetch.FetchError):
        S.fetch_document("http://chatgpt.com/client.json")
    monkeypatch.setattr(web_fetch, "_host_is_safe", lambda host: True)

    def redirect(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 302, "Found", {"Location": "https://10.0.0.1/x"}, None)
    monkeypatch.setattr(web_fetch, "_urlopen", redirect)
    with pytest.raises(web_fetch.FetchError):
        S.fetch_document(CHATGPT)


# ── 整条路 ────────────────────────────────────────────────────────────────────

def test_an_app_named_by_its_document_signs_in_end_to_end(home, monkeypatch):  # noqa: F811
    monkeypatch.setattr(S, "fetch_document", lambda url: _document())
    assert home["anon"].get("/.well-known/oauth-authorization-server").json()[
        "client_id_metadata_document_supported"] is True
    verifier, challenge = _pkce()
    started = home["anon"].get("/api/oauth/authorize", params={
        "response_type": "code", "client_id": CHATGPT, "redirect_uri": CHATGPT_BACK, "state": "st",
        "code_challenge": challenge, "code_challenge_method": "S256", "scope": "read write",
        "resource": "http://testserver/mcp"}, follow_redirects=False)
    request_id = parse_qs(urlsplit(started.headers["location"]).query)["req"][0]
    client = home["owner"].get(f"/api/app/authorize/{request_id}").json()["request"]["client"]
    assert (client["displayKind"], client["brand"], client["host"], client["name"]) == \
        ("verified", "openai", "chatgpt.com", "ChatGPT")
    # https 回调的请求不弹给 Desktop 外壳
    assert home["owner"].get("/api/app/authorize-requests").json()["requests"] == []
    target = home["owner"].post(f"/api/app/authorize/{request_id}", json={
        "decision": "allow", "orgId": home["org"], "access": "write"}, headers=ORIGIN).json()["redirectTo"]
    assert target.startswith(CHATGPT_BACK + "?")
    code = parse_qs(urlsplit(target).query)["code"][0]
    tokens = home["anon"].post("/api/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": CHATGPT, "redirect_uri": CHATGPT_BACK,
        "code_verifier": verifier, "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": "ignored.jwt.value"})
    assert tokens.status_code == 200, tokens.text
    [app] = home["owner"].get(f"{home['base']}/apps").json()["apps"]
    assert (app["displayKind"], app["brand"], app["host"]) == ("verified", "openai", "chatgpt.com")
