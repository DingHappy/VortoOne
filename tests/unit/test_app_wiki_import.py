"""Markdown import batches exercise the Desktop wire contract and the Wiki store."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from src.workflow.members.registry import MemberRegistry
from src.workflow.wiki import Wiki
from tests.unit.test_app_wiki import _page
from tests.unit.test_member_agents import ORIGIN, OWNER_P, TOKEN, _join, home  # noqa: F401 - fixture


def _batch(*notes, **overrides):
    return {"source": {"type": "files", "name": "上线手册"}, "notes": list(notes),
            "onConflict": "keep_both", "importId": "upload-1", **overrides}


def test_markdown_import_creates_versioned_pages_and_replays_batches(home):
    member, owner, base = _join(home), home["owner"], home["base"]
    wiki = base + "/wiki"
    body = "# 发布\n\n见 [[部署]]。\n\n```sh\n示例内容\n```\n\n![[cover.png]]"
    notes = [
        {"title": "首页", "folder": "平台/部署", "body": body, "tags": ["release", "release"], "aliases": []},
        {"title": "部署", "body": "", "aliases": [], "attachments": {}},
        {"title": " ", "body": ""},
        {"title": "长正文", "body": "字" * 200_001},
    ]
    payload = _batch(*notes, folder="笔记")
    posted = member.post(wiki + "/import", json=payload)
    assert posted.status_code == 200 and posted.headers["cache-control"] == "no-store", posted.text
    result = posted.json()
    assert result["failed"] == [{"title": " ", "reason": "title"}, {"title": "长正文", "reason": "too_long"}]
    assert result["skipped"] == []
    first, second = result["created"]
    assert first["folder"] == "笔记/平台/部署" and second["folder"] == "笔记"
    assert first["tags"] == ["release"] and first["sourceType"] == "files" and first["sourceName"] == "上线手册"
    assert first["importId"] and second["importId"] == first["importId"]
    assert first["updatedBy"] == {"type": "person", "id": member.member_id}
    assert first["version"] == 1 and first["agentContext"] is False and first["audience"] is None
    assert "body" not in first
    page_path = f"{wiki}/pages/{first['slug']}"
    assert owner.get(page_path + "/versions/1").json() == {"version": 1, "body": body}
    assert owner.get(page_path + "/history").json()["versions"][0]["author"] == first["updatedBy"]

    # A new client and Wiki/SQLite connection read the same batch receipt.
    retry = TestClient(home["app"], headers=ORIGIN, cookies=dict(member.cookies))
    assert retry.post(wiki + "/import", content=json.dumps(payload, sort_keys=True), headers={
        "Content-Type": "application/json",
    }).json() == result
    third_payload = _batch({"title": "操作", "body": "第二批"})
    third = member.post(wiki + "/import", json=third_payload).json()["created"][0]
    assert third["importId"] == first["importId"]
    assert retry.post(wiki + "/import", json=third_payload).json()["created"] == [third]
    assert {page["id"] for page in owner.get(wiki + "/pages").json()["pages"]} == {
        first["id"], second["id"], third["id"],
    }


def test_without_import_id_a_batch_can_be_imported_again(home):
    owner, base = home["owner"], home["base"]
    payload = _batch({"title": "单次导入", "body": ""})
    del payload["importId"]
    first, = owner.post(base + "/wiki/import", json=payload).json()["created"]
    second, = owner.post(base + "/wiki/import", json=payload).json()["created"]
    assert first["id"] != second["id"] and first["importId"] != second["importId"]
    assert second["title"] == "单次导入 (2)"


def test_conflicts_only_check_visible_titles_and_never_replace_notes(home):
    owner, member, base = home["owner"], _join(home), home["base"]
    wiki = base + "/wiki"
    owner_id = owner.get("/api/app/me").json()["user"]["id"]
    hidden = _page(owner, base, title="隐页", body="只给所有者",
                   audience={"mode": "people", "userIds": [owner_id]})
    visible = _page(owner, base, title="Deploy", body="保留旧记录", folder="其他文件夹")
    note = {"title": "deploy", "body": "新正文"}
    races = _batch(note, {"title": "隐页", "body": ""}, {"title": hidden["slug"], "body": ""}, onConflict="skip")
    skip = member.post(wiki + "/import", json=races).json()
    assert skip["skipped"] == ["deploy"]
    assert [page["title"] for page in skip["created"]] == ["隐页", hidden["slug"]]
    assert all(page["slug"] != hidden["slug"] for page in skip["created"])
    assert member.get(wiki + "/pages/" + hidden["slug"]).status_code == 404
    assert member.post(wiki + "/import", json=races).json() == skip

    payload = _batch(note, note)
    copies = member.post(wiki + "/import", json=payload).json()["created"]
    assert [page["title"] for page in copies] == ["deploy (2)", "deploy (3)"]
    assert len({page["slug"] for page in copies}) == 2
    assert all(page["importId"] == skip["created"][0]["importId"] for page in copies)
    assert owner.get(wiki + "/pages/" + visible["slug"]).json()["body"] == "保留旧记录"
    assert owner.get(wiki + "/pages/" + hidden["slug"]).json()["body"] == "只给所有者"

    another_actor = owner.post(wiki + "/import", json=payload).json()["created"]
    assert another_actor and another_actor[0]["importId"] != copies[0]["importId"]
    assert {page["id"] for page in another_actor}.isdisjoint(page["id"] for page in copies)


def test_replay_does_not_restore_trashed_or_purged_pages(home):
    owner, base = home["owner"], home["base"]
    wiki = base + "/wiki"
    payload = _batch({"title": "可以删除", "body": "只存入正常版本"})
    [page] = owner.post(wiki + "/import", json=payload).json()["created"]
    assert owner.delete(wiki + "/pages/" + page["slug"]).status_code == 204
    assert owner.post(wiki + "/import", json=payload).json() == {"created": [], "skipped": [], "failed": []}
    assert owner.post(wiki + "/trash/purge", json={"ids": [page["id"]]}).status_code == 200
    assert owner.post(wiki + "/import", json=payload).json()["created"] == []
    assert owner.get(wiki + "/pages").json() == {"pages": []}


def test_import_refuses_unsupported_payloads_and_checks_current_permissions(home, monkeypatch):
    owner, member, base = home["owner"], _join(home), home["base"]
    route = base + "/wiki/import"
    good = {"title": "供导入", "body": ""}
    for batch, code in (
        (_batch(good, source={"type": "obsidian", "name": "vault"}), 409),
        (_batch(good, good | {"aliases": ["别名"]}), 409),
        (_batch(good, good | {"attachments": {"preview.png": "artifact-id"}}), 409),
        (_batch(good, good | {"folder": "../目录"}), 400),
        (_batch(good, good | {"audience": {"mode": "everyone"}}), 400),
        (_batch(good, good | {"body": {"text": ""}}), 400),
        (_batch(*(good for _ in range(201))), 400),
    ):
        assert owner.post(route, json=batch).status_code == code
    assert owner.get(base + "/wiki/pages").json() == {"pages": []}

    payload = _batch(good)
    assert TestClient(home["app"]).post(route, json=payload).status_code == 401
    assert member.post(route, json=payload, headers={"Origin": "https://different.test"}).status_code == 403
    assert member.post("/api/app/organizations/other/wiki/import", json=payload).status_code == 404
    assert owner.post(route, content="{}").status_code == 415
    assert owner.post(route, content="[", headers={"Content-Type": "application/json"}).status_code == 422
    assert member.post(route, json=payload).status_code == 200
    MemberRegistry(home["root"]).change_group(OWNER_P, "everyone", items=[])
    assert member.post(route, json=payload).status_code == 403
    monkeypatch.setattr("src.web.routers.app_api._MAX_WIKI_IMPORT_BYTES", 64)
    assert owner.post(route, json=payload).status_code == 413


def test_batch_rows_versions_and_receipt_commit_or_roll_back_together(home, monkeypatch):
    owner, base = home["owner"], home["base"]
    wiki = base + "/wiki"
    payload = _batch({"title": "甲", "body": ""}, {"title": "乙", "body": ""})
    real_save = Wiki._save

    def fail_second(self, conn, page, note=None):
        real_save(self, conn, page, note)
        if page["title"] == "乙":
            raise sqlite3.OperationalError("fixture interrupted batch")

    with monkeypatch.context() as patch:
        patch.setattr(Wiki, "_save", fail_second)
        assert owner.post(wiki + "/import", json=payload).status_code == 409
    assert owner.get(wiki + "/pages").json() == {"pages": []}

    def post():
        client = TestClient(home["app"], headers={"Authorization": f"Bearer {TOKEN}"})
        return client.post(wiki + "/import", json=payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        left, right = [future.result(timeout=10) for future in (pool.submit(post), pool.submit(post))]
    assert left.status_code == right.status_code == 200
    assert left.json() == right.json()
    assert len(owner.get(wiki + "/pages").json()["pages"]) == 2
