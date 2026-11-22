"""Wiki authorization, wire shapes, and revisions through Desktop HTTP."""

from fastapi.testclient import TestClient

from src.workflow.members.registry import MemberRegistry
from tests.unit.test_member_agents import ORIGIN, OWNER_P, TOKEN, _join, home  # noqa: F401 - fixture


def _page(client, base, **fields):
    response = client.post(f"{base}/wiki/pages", json={"title": "排期说明", "body": "# 第一版\n\n资料", **fields})
    assert response.status_code == 201, response.text
    return response.json()


def test_wiki_owner_and_member_share_edits_revisions_and_rename_alias(home):
    owner, member, base = home["owner"], _join(home), home["base"]
    made = _page(member, base, folder="团队/手册", tags=["上线"], title="Deploy Guide")
    root = f"{base}/wiki/pages"
    url = f"{root}/{made['slug']}"
    assert made["version"] == 1 and made["authors"] == [{"type": "person", "id": member.member_id}]
    assert made["agentContext"] is False
    listed = owner.get(root)
    assert listed.status_code == 200 and listed.headers["cache-control"] == "no-store"
    assert [page["id"] for page in listed.json()["pages"]] == [made["id"]]
    assert "body" not in listed.json()["pages"][0]
    assert owner.get(url).json()["body"] == made["body"]

    body = {"body": "第二版", "baseVersion": 1, "tags": ["维护"], "note": "修订"}
    edited = owner.put(url, json=body)
    assert edited.status_code == 200 and edited.json()["version"] == 2
    conflict = member.put(url, json=body | {"body": "过期编辑"})
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "version_conflict"
    assert member.get(url).json()["body"] == "第二版"
    history = member.get(f"{url}/history")
    assert [(row["version"], row["note"]) for row in history.json()["versions"]] == [(2, "修订"), (1, None)]
    assert member.get(f"{url}/versions/1").json() == {"version": 1, "body": made["body"]}
    assert owner.get(f"{url}/versions/99").status_code == 404

    link = _page(owner, base, title="常用入口", body="见 [[Deploy Guide#步骤]]")
    renamed = member.post(f"{url}/rename", json={"title": "发布指南"})
    assert renamed.status_code == 200 and renamed.json()["slug"] == "发布指南"
    assert renamed.json()["backlinks"] == [{"kind": "page", "slug": link["slug"], "title": link["title"]}]
    assert owner.get(url).json()["id"] == made["id"]
    moved = member.post(f"{url}/move", json={"folder": ""})
    assert moved.status_code == 200 and moved.json()["folder"] == ""
    assert member.post(f"{url}/move", json={"folder": "../外部"}).status_code == 400
    pinned = member.post(f"{url}/pin", json={"pinned": True})
    assert pinned.status_code == 200 and pinned.json()["pinned"] is True
    assert member.post(f"{url}/pin", json={"pinned": "true"}).status_code == 400
    assert owner.get(url).json()["pinned"] is True


def test_wiki_private_notes_never_appear_in_outsider_lists_links_or_versions(home):
    owner, member, outsider = home["owner"], _join(home), _join(home, "outer@example.com", "外部")
    base = home["base"]
    root = f"{base}/wiki/pages"
    public = _page(owner, base, title="公共页")
    private = _page(member, base, title="私有页", body="[[公共页]] 受限资料",
                    audience={"mode": "people", "userIds": [member.member_id]})
    assert private["audience"] == {"roleIds": [], "userIds": [member.member_id]}
    assert [page["id"] for page in outsider.get(root).json()["pages"]] == [public["id"]]
    assert outsider.get(f"{root}/{public['slug']}").json()["backlinks"] == []
    assert owner.get(f"{root}/{public['slug']}").json()["backlinks"][0]["slug"] == private["slug"]
    url = f"{root}/{private['slug']}"
    for suffix in ("", "/history", "/versions/1"):
        assert outsider.get(url + suffix).status_code == 404
        assert owner.get(url + suffix).status_code == 200
    assert outsider.put(url, json={"body": "不能修改", "baseVersion": 1}).status_code == 404
    assert outsider.post(f"{url}/rename", json={"title": "不能改名"}).status_code == 404
    assert outsider.delete(url).status_code == 404
    assert member.post(root, json={"title": "排除自己", "body": "", "audience": {
        "mode": "people", "userIds": [outsider.member_id],
    }}).status_code == 403


def test_wiki_lock_and_context_keep_editing_and_agent_permissions_separate(home):
    owner, member, base = home["owner"], _join(home), home["base"]
    made = _page(owner, base)
    url = f"{base}/wiki/pages/{made['slug']}"
    assert member.post(f"{url}/lock", json={"locked": True}).status_code == 403
    locked = owner.post(f"{url}/lock", json={"locked": True})
    assert locked.status_code == 200 and locked.json()["locked"] is True
    assert member.get(url).json()["canEdit"] is False
    assert member.put(url, json={"baseVersion": 2, "body": "不能改"}).status_code == 403
    assert member.post(f"{url}/move", json={"folder": "其他"}).status_code == 403
    assert member.post(f"{url}/pin", json={"pinned": True}).status_code == 403
    assert owner.post(f"{url}/lock", json={"locked": False}).json()["locked"] is False

    for reader in (owner, member):
        refused = reader.post(f"{url}/context", json={"on": True})
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "wiki_context_unavailable"
        off = reader.post(f"{url}/context", json={"on": False})
        assert off.status_code == 200 and off.json()["agentContext"] is False
        assert off.json()["version"] == 3
    assert owner.post(f"{url}/context", json={"on": 1}).status_code == 400


def test_wiki_trash_filters_hidden_pages_and_owner_purges_only_deleted_pages(home):
    owner, member, base = home["owner"], _join(home), home["base"]
    wiki = f"{base}/wiki"
    one = _page(member, base, title="一", folder="可归档")
    locked = _page(owner, base, title="锁定", folder="可归档")
    private = _page(owner, base, title="主人私有", audience={
        "mode": "people", "userIds": [owner.get("/api/app/me").json()["user"]["id"]],
    })
    assert owner.post(f"{wiki}/pages/{locked['slug']}/lock", json={"locked": True}).status_code == 200
    assert member.post(f"{wiki}/bulk/delete", json={"folder": "可归档"}).json() == {"deleted": 1, "kept": 1}
    assert member.get(f"{wiki}/pages/{one['slug']}").status_code == 404
    assert owner.delete(f"{wiki}/pages/{private['slug']}").status_code == 204
    assert [page["id"] for page in member.get(f"{wiki}/pages", params={"deleted": "true"}).json()["pages"]] == [one["id"]]
    assert member.post(f"{wiki}/trash/restore", json={"ids": [private["id"], one["id"]]}).json() == {
        "restored": 1, "kept": 1, "failed": [],
    }
    assert member.get(f"{wiki}/pages/{one['slug']}").json()["body"] == one["body"]
    assert member.delete(f"{wiki}/pages/{locked['slug']}").status_code == 403
    assert member.delete(f"{wiki}/pages/{one['slug']}").status_code == 204
    assert member.post(f"{wiki}/trash/purge", json={}).status_code == 403
    assert owner.post(f"{wiki}/trash/purge", json={"ids": [one["id"], locked["id"]]}).json() == {"purged": 1, "kept": 1}
    assert owner.get(f"{wiki}/pages/{locked['slug']}").status_code == 200
    assert owner.post(f"{wiki}/trash/restore", json={"ids": [one["id"]]}).json()["restored"] == 0
    assert owner.post(f"{wiki}/trash/purge", json={}).json() == {"purged": 1, "kept": 0}
    assert owner.get(f"{wiki}/pages", params={"deleted": "true"}).json() == {"pages": []}


def test_wiki_rechecks_member_edit_rights_and_does_not_accept_anonymous_or_foreign_writes(home):
    member, owner, base = _join(home), home["owner"], home["base"]
    root = f"{base}/wiki/pages"
    created = _page(owner, base)
    assert TestClient(home["app"]).get(root).status_code == 401
    assert member.post(root, json={"title": "伪造来源", "body": "内容"}, headers={"Origin": "https://other.test"}).status_code == 403
    assert owner.get("/api/app/organizations/other/wiki/pages").status_code == 404
    MemberRegistry(home["root"]).change_group(OWNER_P, "everyone", items=[])
    assert member.get(f"{root}/{created['slug']}").json()["canEdit"] is False
    assert member.post(root, json={"title": "没有编辑权限", "body": ""}).status_code == 403
    stale_owner = TestClient(home["app"])
    assert stale_owner.post("/api/auth/login", headers=ORIGIN, json={"token": TOKEN}).status_code == 200
    assert stale_owner.post(root, json={"title": "没有来源", "body": ""}).status_code == 403
    assert stale_owner.post(root, headers=ORIGIN, json={"title": "有来源", "body": ""}).status_code == 201
