"""Folder access is an intersection of the saved Wiki rules and page audience."""

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from tests.unit.test_app_wiki import _page
from tests.unit.test_app_wiki_import import _batch
from tests.unit.test_member_agents import TOKEN, _join, home  # noqa: F401 - fixture


def _access(folder, *people):
    return {"folder": folder, "audience": {"roleIds": [], "userIds": list(people)},
            "agentIds": None, "editors": None, "agentEditorIds": None}


def _folder_view(folder, *people):
    return _access(folder, *sorted(people)) | {"agentIds": []}


def test_folder_restriction_inherits_to_pages_aliases_graph_and_history(home):
    owner, allowed, excluded = home["owner"], _join(home), _join(home, "outside@example.com", "外部")
    base = home["base"]
    wiki = base + "/wiki"
    endpoint = wiki + "/folder-audience"
    page = _page(owner, base, title="Old secret", folder="Ops/Runbooks", body="[[Index]]")
    public = _page(owner, base, title="Index", folder="Ops Setup", body="[[Old secret]]")
    alias = f"{wiki}/pages/{page['slug']}"
    changed = owner.post(alias + "/rename", json={"title": "Current secret"}).json()
    current = f"{wiki}/pages/{changed['slug']}"
    assert excluded.get(alias).status_code == 200
    assert owner.get(endpoint, params={"folder": "Ops"}).json() == {"own": None, "above": None}
    body = _access("Ops", allowed.member_id, excluded.member_id)
    assert allowed.get(endpoint, params={"folder": "Ops"}).status_code == 403
    assert allowed.put(endpoint, json=body).status_code == 403
    assert TestClient(home["app"]).put(endpoint, json=body).status_code == 401
    assert owner.get(endpoint.replace(base, "/api/app/organizations/other")).status_code == 404
    assert owner.put(endpoint, json=body).json() == {"folder": "Ops", "pages": 1}
    assert owner.put(endpoint, json=_access("Ops/Runbooks", allowed.member_id)).json() == {
        "folder": "Ops/Runbooks", "pages": 1,
    }

    inherited = _folder_view("Ops", allowed.member_id, excluded.member_id)
    own = _folder_view("Ops/Runbooks", allowed.member_id)
    rules = owner.get(endpoint, params={"folder": " Ops / Runbooks "})
    assert rules.status_code == 200 and rules.headers["cache-control"] == "no-store"
    assert rules.json() == {"own": own, "above": inherited}
    assert owner.put(endpoint, json=rules.json()["own"]).json() == {"folder": "Ops/Runbooks", "pages": 0}
    assert owner.get(current).json()["folderAudience"] == own
    detail = allowed.get(current).json()
    assert detail["audienceFolder"] == "Ops/Runbooks" and detail["audience"] is None
    assert "folderAudience" not in detail
    listed = {row["id"]: row for row in allowed.get(wiki + "/pages").json()["pages"]}
    assert listed[page["id"]]["audienceFolder"] == detail["audienceFolder"]
    assert "audienceFolder" not in listed[public["id"]]
    for url in (alias, current, alias + "/history", alias + "/versions/1"):
        assert excluded.get(url).status_code == 404
        assert allowed.get(url).status_code == 200
    assert {row["id"] for row in excluded.get(wiki + "/pages").json()["pages"]} == {public["id"]}
    assert excluded.get(wiki + "/graph").json() == {
        "nodes": [{"slug": public["slug"], "title": "Index", "folder": "Ops Setup", "tags": []}], "edges": [],
    }
    assert excluded.get(f"{wiki}/pages/{public['slug']}").json()["backlinks"] == []
    assert {edge["from"] for edge in allowed.get(wiki + "/graph").json()["edges"]} == {
        changed["slug"], public["slug"],
    }
    assert owner.put(endpoint, json=_access("Ops/Runbooks", allowed.member_id, excluded.member_id)).status_code == 409
    assert owner.put(endpoint, json={"folder": "Ops", "audience": None}).status_code == 409
    assert owner.put(endpoint, json=_access("Ops/Runbooks", allowed.member_id)).json()["pages"] == 0
    owner_id = owner.get("/api/app/me").json()["user"]["id"]
    assert owner.put(endpoint, json=_access("Ops/Runbooks", owner_id, allowed.member_id)).json()["pages"] == 0
    assert owner.get(endpoint, params={"folder": "Ops/Unknown"}).status_code == 404
    assert owner.delete(f"{base}/members/{excluded.member_id}").status_code == 204
    active_rule = owner.get(endpoint, params={"folder": "Ops"}).json()["own"]
    assert active_rule == _folder_view("Ops", allowed.member_id)
    assert owner.put(endpoint, json=active_rule).status_code == 200


def test_page_restriction_and_search_respect_inherited_readers(home):
    owner, allowed, excluded = home["owner"], _join(home), _join(home, "outside@example.com", "外部")
    base = home["base"]
    wiki = base + "/wiki"
    page = _page(owner, base, title="Runbook search", folder="Ops/Runbooks")
    url = f"{wiki}/pages/{page['slug']}/audience"
    assert owner.put(wiki + "/folder-audience", json=_access("Ops", allowed.member_id)).status_code == 200
    refused = owner.put(url, json={"audience": {"roleIds": [], "userIds": [excluded.member_id]}})
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "wiki_audience_expansion"
    assert excluded.get(wiki + "/search", params={"q": "Runbook"}).json() == {"pages": []}
    visible, = allowed.get(wiki + "/search", params={"q": "Runbook"}).json()["pages"]
    assert visible["id"] == page["id"] and visible["audienceFolder"] == "Ops"
    assert owner.put(url, json={"audience": {"roleIds": [], "userIds": [allowed.member_id]}}).status_code == 200
    assert owner.put(url, json={"audience": {"roleIds": [], "userIds": []}}).status_code == 200
    assert allowed.get(wiki + "/search", params={"q": "Runbook"}).json() == {"pages": []}


def test_folder_restriction_holds_on_move_trash_import_and_new_pages(home):
    owner, writer, base = home["owner"], _join(home), home["base"]
    wiki = base + "/wiki"
    endpoint = wiki + "/folder-audience"
    hold = "Work/Restricted"
    draft = _page(owner, base, title="Archived record", folder=hold)
    trash_url = f"{wiki}/pages/{draft['slug']}"
    assert owner.delete(trash_url).status_code == 204

    batch = _batch({"title": "Imported note", "folder": hold, "body": "Memo"})
    imported, = writer.post(wiki + "/import", json=batch).json()["created"]
    imported_url = f"{wiki}/pages/{imported['slug']}"
    public = _page(owner, base, title="Unrestricted record")
    public_url = f"{wiki}/pages/{public['slug']}"
    assert owner.put(endpoint, json=_access("Work")).json() == {"folder": "Work", "pages": 1}
    assert writer.post(wiki + "/import", json=batch).json() == {"created": [], "skipped": [], "failed": []}
    current_pages = writer.get(wiki + "/pages").json()
    mixed = _batch({"title": "Would be public", "body": "First note"},
                   *batch["notes"], importId="after-rule")
    blocked = writer.post(wiki + "/import", json=mixed)
    assert blocked.status_code == 404 and blocked.json()["error"]["code"] == "not_found"
    assert writer.get(wiki + "/pages").json() == current_pages
    assert writer.get(imported_url).status_code == 404
    assert writer.get(wiki + "/pages", params={"deleted": True}).json() == {"pages": []}
    selected = {"ids": [draft["id"]]}
    assert writer.post(wiki + "/trash/restore", json=selected).json() == {"restored": 0, "kept": 1, "failed": []}
    assert owner.post(wiki + "/trash/restore", json=selected).json()["restored"] == 1
    assert writer.get(trash_url).status_code == 404
    assert writer.post(wiki + "/bulk/delete", json={"folder": "Work"}).json() == {"deleted": 0, "kept": 0}
    assert writer.post(wiki + "/pages", json={"title": "New", "body": "Text", "folder": hold}).status_code == 404
    assert writer.post(public_url + "/move", json={"folder": hold}).status_code == 404
    assert owner.post(public_url + "/move", json={"folder": hold}).status_code == 200
    assert writer.get(public_url).status_code == 404
    for response in (
        owner.post(imported_url + "/move", json={"folder": ""}),
        owner.put(imported_url, json={"baseVersion": 1, "body": "Changed", "folder": ""}),
    ):
        assert response.status_code == 409 and response.json()["error"]["code"] == "wiki_audience_expansion"
    assert owner.get(imported_url).json()["body"] == "Memo"

    protected = _page(owner, base, title="New protected", folder=hold)
    ordinary = _page(writer, base, title="New protected")
    assert protected["slug"] != ordinary["slug"] == "new-protected"
    assert writer.get(f"{wiki}/pages/{protected['slug']}").status_code == 404


def test_member_can_move_only_when_every_readers_access_is_unchanged(home):
    owner, allowed, excluded = home["owner"], _join(home), _join(home, "other@example.com", "同事")
    base = home["base"]
    wiki = base + "/wiki"
    endpoint = wiki + "/folder-audience"
    folder = "Ops/Runbooks"
    _page(owner, base, title="Anchor", folder=folder)
    page = _page(owner, base, title="Movable")
    url = f"{wiki}/pages/{page['slug']}"
    assert owner.put(endpoint, json=_access(folder, allowed.member_id)).status_code == 200

    denied = allowed.post(url + "/move", json={"folder": folder})
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "forbidden"
    assert allowed.put(url, json={"baseVersion": 1, "body": "Discarded edit", "folder": folder}).status_code == 403
    assert excluded.get(url).json() == page
    same = allowed.post(url + "/move", json={"folder": "Open"})
    assert same.status_code == 200 and same.json()["folder"] == "Open"
    assert excluded.get(url).status_code == 200
    assert owner.post(url + "/move", json={"folder": folder}).status_code == 200
    assert excluded.get(url).status_code == 404
    assert allowed.post(url + "/move", json={"folder": folder + "/Detail"}).status_code == 200
    assert allowed.post(url + "/move", json={"folder": "Open"}).status_code == 409
    assert excluded.get(url).status_code == 404


def test_root_audience_refuses_unsupported_axes_and_updates_under_lock(home):
    owner, first, second = home["owner"], _join(home), _join(home, "second@example.com", "同事")
    base = home["base"]
    wiki = base + "/wiki"
    endpoint = wiki + "/folder-audience"
    page = _page(owner, base, title="Shared", folder="Operations")
    body = _access("", first.member_id)
    for invalid in (body | {"folder": "../"}, {"folder": ""}, _access("", "unknown")):
        assert owner.put(endpoint, json=invalid).status_code == 400
    for unsupported in (body | {"editors": {}}, body | {"agentIds": ["agent"]},
                        body | {"agentEditorIds": ["agent"]},
                        {"folder": "", "audience": {"roleIds": ["role-id"], "userIds": []}}):
        assert owner.put(endpoint, json=unsupported).status_code == 409
    assert owner.get(endpoint, params={"folder": "Operations"}).json() == {"own": None, "above": None}

    def narrow(person):
        client = TestClient(home["app"], headers={"Authorization": f"Bearer {TOKEN}"})
        return client.put(endpoint, json=_access("", person))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(narrow, [first.member_id, second.member_id]))
    accepted, denied = sorted(results, key=lambda result: result.status_code)
    assert accepted.status_code == 200 and accepted.json() == {"folder": "", "pages": 1}
    assert denied.status_code == 409 and denied.json()["error"]["code"] == "wiki_audience_expansion"
    page_url = f"{wiki}/pages/{page['slug']}"
    assert sorted(reader.get(page_url).status_code for reader in (first, second)) == [200, 404]
    assert owner.get(page_url).json()["audienceFolder"] == ""
    assert owner.get(wiki + "/pages").json()["pages"][0]["audienceFolder"] == ""
    saved = owner.get(endpoint).json()
    assert saved["above"] is None
    assert owner.get(endpoint, params={"folder": "Operations"}).json() == {"own": None, "above": saved["own"]}
