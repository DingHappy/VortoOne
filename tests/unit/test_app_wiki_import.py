"""Markdown import batches exercise the Desktop wire contract and the Wiki store."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from src.agents.tools.artifacts import ArtifactStore
from src.utils.home_maintenance import home_maintenance
from src.workflow.members.registry import MemberRegistry
from src.workflow.wiki import Wiki
from tests.unit.test_app_wiki import _page, _wiki_audience
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


def test_obsidian_markdown_import_preserves_source_and_links(home):
    owner, member, wiki = home["owner"], _join(home), home["base"] + "/wiki"
    source = {"type": "obsidian", "name": "项目笔记"}
    body = "# 上线准备\n\n参见 [[部署/发布流程|上线步骤]]。"
    chapter = {"title": "上线准备", "body": body, "folder": "手册", "tags": ["ops"], "aliases": []}
    first_batch = _batch(chapter, source=source)
    second_batch = _batch({"title": "发布流程", "body": "# 发布", "folder": "部署", "aliases": []}, source=source)

    first = member.post(wiki + "/import", json=first_batch)
    assert first.status_code == 200, first.text
    [note] = first.json()["created"]
    assert (note["sourceType"], note["sourceName"], note["folder"], note["tags"]) == (
        "obsidian", "项目笔记", "手册", ["ops"],
    )
    assert note["updatedBy"] == {"type": "person", "id": member.member_id}
    assert note["agentContext"] is False
    assert member.post(wiki + "/import", json=first_batch).json() == first.json()

    second = member.post(wiki + "/import", json=second_batch)
    assert second.status_code == 200, second.text
    [target] = second.json()["created"]
    assert target["importId"] == note["importId"]
    assert owner.get(f"{wiki}/pages/{note['slug']}/versions/1").json() == {"version": 1, "body": body}
    assert owner.get(f"{wiki}/pages/{target['slug']}").json()["backlinks"] == [
        {"kind": "page", "slug": note["slug"], "title": note["title"]},
    ]


@pytest.mark.parametrize("aliases", ["别名", [" "], ["line\nbreak"], ["x" * 301], ["a"] * 51])
def test_obsidian_import_refuses_unusable_aliases(home, aliases):
    owner, wiki = home["owner"], home["base"] + "/wiki"
    good = {"title": "供导入", "body": "正文"}
    payload = _batch(good, good | {"aliases": aliases}, source={"type": "obsidian", "name": "项目笔记"})
    response = owner.post(wiki + "/import", json=payload)
    assert response.status_code == 400, response.text
    assert owner.get(wiki + "/pages").json() == {"pages": []}


def test_imported_aliases_resolve_and_search_only_visible_pages(home):
    owner, member, outsider = home["owner"], _join(home), _join(home, "alias-visitor@example.com", "外部")
    wiki = home["base"] + "/wiki"
    payload = _batch({"title": "索引", "body": "见 [[CAFE]]"},
                     {"title": "值班资料", "body": "正文", "aliases": [" Café ", "cafe", "排班"]})
    imported = member.post(wiki + "/import", json=payload)
    assert imported.status_code == 200, imported.text
    source, target = imported.json()["created"]
    assert target["aliases"] == ["Café", "排班"]
    assert member.post(wiki + "/import", json=payload).json() == imported.json()
    target_path = f"{wiki}/pages/{target['slug']}"
    edge = {"from": source["slug"], "to": target["slug"]}
    assert outsider.get(wiki + "/graph").json()["edges"] == [edge]
    assert outsider.get(wiki + "/search", params={"q": "cafe"}).json()["pages"][0]["id"] == target["id"]
    assert owner.get(target_path).json()["backlinks"][0]["slug"] == source["slug"]

    assert owner.put(target_path + "/audience", json=_wiki_audience(member.member_id)).status_code == 200
    assert outsider.get(wiki + "/graph").json()["edges"] == []
    assert outsider.get(wiki + "/search", params={"q": "cafe"}).json() == {"pages": []}
    assert outsider.get(target_path).status_code == 404
    assert member.get(wiki + "/graph").json()["edges"] == [edge]

    other = _batch({"title": "其它值班资料", "body": "正文", "aliases": ["cafe"]}, importId="other")
    received = owner.post(wiki + "/import", json=other)
    assert received.status_code == 200, received.text
    [another] = received.json()["created"]
    assert member.get(wiki + "/graph").json()["edges"] == []
    assert outsider.get(wiki + "/graph").json()["edges"] == [{"from": source["slug"], "to": another["slug"]}]

    canonical = _page(owner, home["base"], title="Cafe")
    assert owner.get(wiki + "/graph").json()["edges"] == [{"from": source["slug"], "to": canonical["slug"]}]


def test_obsidian_import_embeds_private_upload_for_owner_without_sharing_it(home):
    owner, member, base = home["owner"], _join(home), home["base"]
    wiki, drive = base + "/wiki", base + "/artifacts"
    original = b"type,total\nrelease,7\n"
    upload = owner.post(drive + "/files", files={"file": ("report.csv", original, "text/csv")})
    assert upload.status_code == 201, upload.text
    aid = upload.json()["id"]
    source = {"type": "obsidian", "name": "项目笔记"}
    body, bindings = "周报：\n\n![[资料/report.csv]]", {"资料/report.csv": aid}
    payload = _batch({"title": "发布事项", "body": body, "attachments": bindings}, source=source)
    imported = owner.post(wiki + "/import", json=payload)
    assert imported.status_code == 200, imported.text
    [page] = imported.json()["created"]
    assert "attachments" not in page and "source" not in page
    path = wiki + "/pages/" + page["slug"]
    detail = owner.get(path)
    assert detail.headers["cache-control"] == "no-store"
    assert detail.json()["source"] == {"type": "obsidian", "id": "项目笔记", "attachments": bindings}
    assert detail.json()["body"] == member.get(path).json()["body"] == body
    for response in (owner.get(wiki + "/pages"), owner.get(wiki + "/search?q=发布"),
                     owner.get(wiki + "/graph"), member.get(path),
                     owner.get(path + "/history"), owner.get(path + "/versions/1")):
        assert response.status_code == 200, response.text
        assert aid not in response.text

    file_path = f"{drive}/{aid}/file?version=1"
    assert owner.get(file_path).content == original
    new_bytes = b"type,total\nrelease,8\n"
    revision = owner.post(f"{drive}/{aid}/files", files={"file": ("report.csv", new_bytes, "text/csv")})
    assert revision.status_code == 201, revision.text
    assert owner.get(f"{drive}/{aid}/file").content == new_bytes
    assert owner.get(file_path).content == original
    assert owner.get(path).json()["source"]["attachments"] == bindings
    assert member.get(file_path).status_code == 403
    assert member.post(wiki + "/import", json=payload).status_code == 403
    assert owner.post(wiki + "/import", json=payload).json() == imported.json()

    assert owner.delete(f"{drive}/{aid}").status_code == 204
    assert owner.get(file_path).status_code == 404
    assert "source" not in owner.get(path).json()
    assert owner.post(wiki + "/import", json=payload).json() == imported.json()
    assert owner.get(file_path).status_code == 404
    assert owner.post(f"{drive}/{aid}/restore").status_code == 204
    assert owner.get(path).json()["source"]["attachments"] == bindings
    assert owner.get(file_path).content == original

    store = ArtifactStore(home["root"], desktop_uploads=True).root
    unavailable = store.with_suffix(".unavailable")
    store.rename(unavailable)
    store.symlink_to(unavailable, target_is_directory=True)
    detail = owner.get(path)
    assert detail.status_code == 200 and detail.json()["body"] == body
    assert "source" not in detail.json()


def test_wiki_attachment_import_refuses_unknown_non_file_or_busy_drive_atomically(home):
    owner, root, base = home["owner"], home["root"], home["base"]
    wiki, drive = base + "/wiki", base + "/artifacts"
    upload = owner.post(drive + "/files", files={"file": ("page.html", "<p>HTML</p>", "text/html")})
    assert upload.status_code == 201, upload.text
    aid = upload.json()["id"]
    good = {"title": "保留正文", "body": "普通笔记"}
    attached = {"title": "带附件", "body": "![[page.html]]", "attachments": {"page.html": aid}}
    for item, status in ((attached, 409), (attached | {"attachments": {"unknown.txt": "upload-" + "0" * 32}}, 409),
                         (attached | {"attachments": {"bad.txt": "../invalid"}}, 400),
                         (attached | {"attachments": []}, 400)):
        response = owner.post(wiki + "/import", json=_batch(good, item))
        assert response.status_code == status, response.text
    assert owner.get(wiki + "/pages").json() == {"pages": []}

    upload = owner.post(drive + "/files", files={"file": ("report.csv", b"release,7\n", "text/csv")})
    assert upload.status_code == 201, upload.text
    aid = upload.json()["id"]
    attached = {"title": "带附件", "body": "![[report.csv]]", "attachments": {"report.csv": aid}}
    with home_maintenance(root):
        busy = owner.post(wiki + "/import", json=_batch(good, attached))
        assert busy.status_code == 409 and busy.json()["error"]["code"] == "unavailable"
    assert owner.get(wiki + "/pages").json() == {"pages": []}

    # A linked metadata file cannot make an arbitrary local file count as a Drive upload.
    metadata = ArtifactStore(root, desktop_uploads=True).root / aid / "meta.json"
    target = metadata.with_suffix(".copy")
    metadata.rename(target)
    metadata.symlink_to(target)
    refused = owner.post(wiki + "/import", json=_batch(good, attached))
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "wiki_attachment_unavailable"
    assert owner.get(wiki + "/pages").json() == {"pages": []}


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
        (_batch(good, source={"type": "unsupported", "name": "store"}), 409),
        (_batch(good, good | {"aliases": [12]}), 400),
        (_batch(good, good | {"attachments": {"preview.png": "artifact-id"}}), 400),
        (_batch(good, good | {"folder": "../目录"}), 400),
        (_batch(good, good | {"audience": {"mode": "everyone"}}), 400),
        (_batch(good, good | {"body": {"text": ""}}), 400),
        (_batch(*(good for _ in range(201))), 400),
    ):
        assert owner.post(route, json=batch).status_code == code
    assert owner.get(base + "/wiki/pages").json() == {"pages": []}

    payload = _batch(good, source={"type": "obsidian", "name": "项目笔记"})
    assert TestClient(home["app"]).post(route, json=payload).status_code == 401
    assert member.post(route, json=payload, headers={"Origin": "https://different.test"}).status_code == 403
    assert member.post("/api/app/organizations/other/wiki/import", json=payload).status_code == 404
    assert owner.post(route, content="{}").status_code == 415
    assert owner.post(route, content="[", headers={"Content-Type": "application/json"}).status_code == 422
    assert member.post(route, json=payload).status_code == 200
    MemberRegistry(home["root"]).change_group(OWNER_P, "everyone", items=[])
    assert member.post(route, json=payload).status_code == 403
    assert owner.post(route, json=payload).status_code == 200
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
