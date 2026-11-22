"""Local feedback respects authentication, workspace boundaries and retry IDs."""

import json
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from src.utils.state_dir import state_dir
from src.web import app_feedback
from src.web.auth import SESSION_COOKIE
from tests.unit.test_member_agents import ORIGIN, TOKEN, _join, home  # noqa: F401 - pytest fixture


PATH = "/api/app/feedback"


def _body(home, text="测试反馈"):
    return {"id": str(uuid4()), "text": text, "orgId": home["base"].rsplit("/", 1)[-1]}


def _inbox(client, home, *, headers=None, **query):
    return client.get(PATH, params={"orgId": home["base"].rsplit("/", 1)[-1], **query}, headers=headers)


def _store_path(home):
    return state_dir(home["root"]) / "feedback" / "inbox.sqlite3"


def test_owner_reads_only_saved_text_and_cursor_is_stable(home):
    owner = home["owner"]
    config = owner.get("/api/app/auth-config").json()
    assert config["feedback"] and config["feedbackLocal"]
    assert _inbox(owner, home).json() == {"items": [], "nextCursor": None}
    assert not _store_path(home).exists()

    owner_report = _body(home, "  这是一条文字反馈  ")
    result = owner.post(PATH, json=owner_report)
    assert result.status_code == 204 and result.content == b""
    assert result.headers["cache-control"] == "no-store"
    assert owner.post(PATH, json=owner_report).status_code == 204
    assert owner.post(PATH, json={**owner_report, "text": "重新使用编号"}).status_code == 409

    member = _join(home, "member@example.test", "成员姓名")
    member_report = _body(home, "成员提交的反馈")
    assert member.post(PATH, json=member_report).status_code == 204
    assert member.post(PATH, json=owner_report).status_code == 409
    assert _inbox(member, home).status_code == 403
    assert _store_path(home).is_file()
    assert _store_path(home).stat().st_mode & 0o777 == 0o600

    first = _inbox(owner, home, limit=1)
    assert first.headers["cache-control"] == "no-store"
    page = first.json()
    assert page["items"] == [{
        "id": member_report["id"], "text": "成员提交的反馈",
        "createdAt": page["items"][0]["createdAt"], "author": "member",
    }]
    assert page["items"][0]["createdAt"].endswith("Z")
    assert type(page["nextCursor"]) is int
    assert "member@example.test" not in first.text and "成员姓名" not in first.text
    next_page = _inbox(owner, home, limit=1, cursor=page["nextCursor"]).json()
    assert next_page["nextCursor"] is None
    assert [(item["id"], item["text"], item["author"]) for item in next_page["items"]] == [
        (owner_report["id"], "这是一条文字反馈", "owner"),
    ]


def test_feedback_needs_authenticated_owner_or_active_same_origin_member(home, monkeypatch):
    owner = home["owner"]
    guest = TestClient(home["app"])
    body = _body(home)
    assert guest.post(PATH, json=body).status_code == 401
    assert _inbox(guest, home).status_code == 401

    browser = TestClient(home["app"])
    browser.cookies.set(SESSION_COOKIE, TOKEN)
    assert browser.post(PATH, json=body).status_code == 403
    assert browser.post(PATH, json=body, headers={"Origin": "https://other.example"}).status_code == 403
    assert _inbox(browser, home, headers={"Origin": "https://other.example"}).status_code == 403
    assert browser.post(PATH, json=body, headers=ORIGIN).status_code == 204
    assert _inbox(browser, home).status_code == 200

    member = _join(home)
    assert member.post(PATH, json=_body(home), headers={"Origin": "https://other.example"}).status_code == 403
    assert owner.delete(f"{home['base']}/members/{member.member_id}").status_code == 204
    assert member.post(PATH, json=_body(home)).status_code == 401
    assert len(_inbox(owner, home).json()["items"]) == 1

    monkeypatch.delenv("VORTOONE_API_TOKEN")
    config = guest.get("/api/app/auth-config").json()
    assert not config["feedback"] and not config["feedbackLocal"]
    assert guest.post(PATH, json=body).status_code == 403
    assert _inbox(guest, home).status_code == 403


@pytest.mark.parametrize(
    "extra", [
        {"facts": []}, {"files": []}, {"about": {"kind": "message"}},
        {"app": "web"}, {"where": "/private"}, {"email": "person@example.test"},
        {"id": "invalid"}, {"text": " \t "}, {"text": "a" * 4001},
        {"text": "\ud800"},
    ],
)
def test_rejects_unsupported_and_invalid_payloads(home, extra):
    response = home["owner"].post(PATH, content=json.dumps({**_body(home), **extra}),
                                  headers={"Content-Type": "application/json"})
    assert response.status_code == 400 and not _store_path(home).exists()


def test_wrong_workspace_oversized_or_corrupt_store_fail_truthfully(home):
    owner = home["owner"]
    body = _body(home)
    assert owner.post(PATH, json={**body, "orgId": "other"}).status_code == 404
    assert owner.get(PATH, params={"orgId": "other"}).status_code == 404
    assert owner.post(PATH, data=body).status_code == 415
    assert owner.post(PATH, content=" " * (app_feedback.MAX_BODY + 1),
                      headers={"Content-Type": "application/json"}).status_code == 413
    assert not _store_path(home).exists()

    storage = _store_path(home)
    storage.parent.mkdir(parents=True)
    storage.write_bytes(b"not a feedback database")
    initial = storage.read_bytes()
    assert _inbox(owner, home).status_code == 503
    assert owner.post(PATH, json=body).status_code == 503
    assert storage.read_bytes() == initial


def test_escaped_unicode_at_character_limit_round_trips(home):
    report = _body(home, "😀" * app_feedback.MAX_TEXT)
    owner = home["owner"]
    result = owner.post(PATH, content=json.dumps(report), headers={"Content-Type": "application/json"})
    assert result.status_code == 204
    assert _inbox(owner, home).json()["items"][0]["text"] == report["text"]
