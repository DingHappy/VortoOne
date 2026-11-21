"""Desktop 外壳的交接登录：令牌换一次性交接码，交接码换登录 Cookie；令牌不经过页面。"""

import pytest
from fastapi.testclient import TestClient

TOKEN = "owner-token-for-test"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("VORTOCODE_API_TOKEN", "AUTODEV_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VORTOONE_API_TOKEN", TOKEN)
    from src.web.routers import app_api
    from src.web.server import app
    app_api._HANDOVERS.clear()
    yield TestClient(app, follow_redirects=False)
    app_api._HANDOVERS.clear()


def _code(client):
    response = client.post("/api/auth/handover", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200 and response.json()["expiresIn"] == 60
    return response.json()["code"]


def test_the_shell_trades_its_token_for_a_signed_in_window(client):
    from src.web.auth import SESSION_COOKIE
    code = _code(client)
    assert TOKEN not in code and len(code) >= 32
    assert client.get("/api/app/me").status_code == 401
    landed = client.get(f"/api/auth/handover/{code}")
    assert landed.status_code == 303 and landed.headers["location"] == "/"
    cookie = landed.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=") and "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert landed.headers["cache-control"] == "no-store"
    assert client.get("/api/app/me").status_code == 200


def test_a_code_works_once(client):
    code = _code(client)
    assert client.get(f"/api/auth/handover/{code}").headers["location"] == "/"
    client.cookies.clear()
    again = client.get(f"/api/auth/handover/{code}")
    assert again.headers["location"] == "/login" and "set-cookie" not in again.headers
    assert client.get("/api/app/me").status_code == 401


def test_a_code_expires(client, monkeypatch):
    import time
    code = _code(client)
    start = time.monotonic()
    monkeypatch.setattr(time, "monotonic", lambda: start + 61)
    late = client.get(f"/api/auth/handover/{code}")
    assert late.headers["location"] == "/login" and "set-cookie" not in late.headers


def test_wrong_codes_get_nothing(client):
    _code(client)
    for guess in ("x", "a" * 43, "..%2f..%2fetc"):
        response = client.get(f"/api/auth/handover/{guess}")
        assert response.status_code in {303, 404} and "set-cookie" not in response.headers


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"X-Api-Token": TOKEN}])
def test_only_the_explicit_owner_bearer_can_start_a_handover(client, headers):
    assert client.post("/api/auth/handover", headers=headers).status_code in {401, 403}


def test_a_signed_in_page_cannot_mint_more_sign_ins(client):
    from src.web.auth import SESSION_COOKIE
    client.cookies.set(SESSION_COOKIE, TOKEN)
    assert client.get("/api/app/me").status_code == 200
    assert client.post("/api/auth/handover").status_code == 403


def test_pending_handovers_are_bounded(client):
    for _ in range(32):
        _code(client)
    refused = client.post("/api/auth/handover", headers={"Authorization": f"Bearer {TOKEN}"})
    assert refused.status_code == 429


def test_without_an_owner_token_there_is_no_handover(client, monkeypatch):
    monkeypatch.delenv("VORTOONE_API_TOKEN")
    assert client.post("/api/auth/handover", headers={"Authorization": "Bearer "}).status_code == 403
