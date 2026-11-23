"""The Desktop GitHub App card reports the server's actual availability."""

from pathlib import Path

from fastapi.testclient import TestClient

from tests.unit.test_member_agents import ORIGIN, OWNER_P, TOKEN, _join, home  # noqa: F401 - pytest fixture
from src.workflow.members.registry import MemberRegistry


_UNAVAILABLE = {"installable": False, "installations": [], "ownerCreates": False, "setup": None}
_SETUP_OFF = {"source": None, "slug": None, "htmlUrl": None, "state": "off", "canCreate": False}


def test_owner_sees_unconfigured_app_and_no_install_options(home):
    # A legacy Device Flow declaration is unrelated to a Desktop App installation.
    config = Path(home["root"]) / "config"
    config.mkdir(exist_ok=True)
    (config / "mcp.yaml").write_text(
        "servers:\n  - name: github\n    credential: github\n    oauth:\n"
        "      provider: github\n      client_id: Iv1.synthetic\n",
        encoding="utf-8",
    )
    url = f"{home['base']}/github-app"
    cookie = TestClient(home["app"], headers=ORIGIN)
    assert cookie.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    for client in (home["owner"], cookie):
        response = client.get(url)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {**_UNAVAILABLE, "setup": _SETUP_OFF}
    assert cookie.get(url, headers={"Origin": "https://other.example"}).status_code == 403


def test_members_only_see_availability_for_their_current_workspace(home):
    member = _join(home)
    url = f"{home['base']}/github-app"
    response = member.get(url)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == _UNAVAILABLE
    assert member.get("/api/app/organizations/another-workspace/github-app").status_code == 404
    assert member.get(url, headers={"Origin": "https://other.example"}).status_code == 403
    MemberRegistry(home["root"]).remove_member(OWNER_P, member.member_id)
    assert member.get(url).status_code == 403


def test_unproven_sessions_cannot_read_github_app_setup(home, monkeypatch):
    browser = TestClient(home["app"], headers=ORIGIN)
    url = f"{home['base']}/github-app"
    assert browser.get(url).status_code == 401
    monkeypatch.delenv("VORTOONE_API_TOKEN")
    response = browser.get(url)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "owner_proof_required"
