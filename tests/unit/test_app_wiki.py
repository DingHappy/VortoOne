"""Wiki authorization, wire shapes, and revisions through Desktop HTTP."""

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from src.workflow.members.registry import MemberRegistry
from src.workflow.wiki import MAX_BODY
from tests.unit.test_member_agents import ORIGIN, OWNER_P, TOKEN, _join, home  # noqa: F401 - fixture
from tests.unit.test_team_channels import _create


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


def _wiki_audience(*people):
    return {"audience": {"roleIds": [], "userIds": list(people)}}


def test_wiki_owner_narrows_page_without_losing_its_url_and_history(home):
    owner, member, outsider = home["owner"], _join(home), _join(home, "outer@example.com", "外部")
    base = home["base"]
    wiki = base + "/wiki"
    public = _page(owner, base, title="公开索引")
    page = _page(owner, base, title="值班说明", body="值班资料，见 [[公开索引]]。")
    url = f"{wiki}/pages/{page['slug']}"
    change = url + "/audience"
    payload = _wiki_audience(member.member_id)

    assert outsider.get(url).status_code == 200
    assert member.put(change, json=payload).status_code == 403
    assert TestClient(home["app"]).put(change, json=payload).status_code == 401
    assert owner.put(change.replace(base, "/api/app/organizations/other"), json=payload).status_code == 404
    saved = owner.put(change, json=payload)
    assert saved.status_code == 200 and saved.headers["cache-control"] == "no-store", saved.text
    assert saved.json()["audience"] == payload["audience"]
    assert saved.json()["slug"] == page["slug"] and saved.json()["agentContext"] is False
    assert saved.json()["agentIds"] == []
    assert saved.json()["version"] == 2
    assert owner.put(change, json=payload).json() == saved.json()
    assert member.get(url).json()["body"] == page["body"]
    assert member.get(url + "/versions/1").json() == {"version": 1, "body": page["body"]}

    for path in (url, url + "/history", url + "/versions/1"):
        assert outsider.get(path).status_code == 404
    assert [row["id"] for row in outsider.get(wiki + "/pages").json()["pages"]] == [public["id"]]
    assert outsider.get(f"{wiki}/pages/{public['slug']}").json()["backlinks"] == []
    assert outsider.get(wiki + "/graph").json()["edges"] == []

    for expanded in ({"audience": None}, _wiki_audience(member.member_id, outsider.member_id)):
        refused = owner.put(change, json=expanded)
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "wiki_audience_expansion"
    assert owner.get(url).json()["version"] == 2
    only_me = owner.get("/api/app/me").json()["user"]["id"]
    narrowed = owner.put(change, json=_wiki_audience(only_me))
    assert narrowed.status_code == 200 and narrowed.json()["audience"] == _wiki_audience(only_me)["audience"]
    assert member.get(url).status_code == 404


def test_wiki_audience_validates_axes_and_current_page(home):
    owner, member = home["owner"], _join(home)
    base = home["base"]
    made = _page(owner, base)
    url = f"{base}/wiki/pages/{made['slug']}"
    change = url + "/audience"
    payload = _wiki_audience(member.member_id)
    for body in ({}, _wiki_audience("unknown"), {"audience": {"userIds": []}}):
        assert owner.put(change, json=body).status_code == 400
    for body in (
        {"audience": {"roleIds": ["admin"], "userIds": []}},
        payload | {"agentIds": ["unknown"]},
        payload | {"editors": {"roleIds": [], "userIds": []}},
        payload | {"agentEditorIds": ["unknown"]},
    ):
        response = owner.put(change, json=body)
        assert response.status_code == 409 and response.json()["error"]["code"] == "wiki_audience_unsupported"
    assert owner.get(url).json() == made
    neutral = {"agentIds": [], "editors": None, "agentEditorIds": []}
    assert owner.put(change, json=payload | neutral).status_code == 200
    assert owner.delete(url).status_code == 204
    assert owner.put(change, json=payload).status_code == 404


def test_wiki_audience_checks_previous_grants_under_the_write_lock(home):
    member, other = _join(home), _join(home, "other@example.com", "其他")
    made = _page(home["owner"], home["base"])
    change = f"{home['base']}/wiki/pages/{made['slug']}/audience"

    def narrow(person):
        client = TestClient(home["app"], headers={"Authorization": f"Bearer {TOKEN}"})
        return client.put(change, json=_wiki_audience(person))

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(narrow, [member.member_id, other.member_id]))
    accepted, denied = sorted(responses, key=lambda response: response.status_code)
    assert [accepted.status_code, denied.status_code] == [200, 409]
    assert denied.json()["error"]["code"] == "wiki_audience_expansion"
    assert home["owner"].get(change.removesuffix("/audience")).json()["audience"] == accepted.json()["audience"]


def test_wiki_graph_uses_visible_pages_and_current_slugs_for_both_directions(home):
    owner, member, base = home["owner"], _join(home), home["base"]
    wiki = f"{base}/wiki"
    target = _page(owner, base, title="Déploy Guide", folder="Ops", tags=["release"])
    embedded = _page(owner, base, title="照片")
    removed = _page(owner, base, title="旧说明")
    private = _page(owner, base, title="私有页", body="[[Deploy Guide]]",
                    audience={"mode": "people", "userIds": [owner.get("/api/app/me").json()["user"]["id"]]})
    referring = _page(owner, base, title="入口", body=(
        "[[OPs/Deploy Guide#步骤|发布]] [[Déploy Guide]] [[入口]] "
        "![[照片]] [[旧说明]] [[私有页]] [[不存在]]"
    ))
    assert owner.delete(f"{wiki}/pages/{removed['slug']}").status_code == 204

    member_graph = member.get(f"{wiki}/graph")
    assert member_graph.status_code == 200 and member_graph.headers["cache-control"] == "no-store"
    graph = member_graph.json()
    assert {node["slug"] for node in graph["nodes"]} == {target["slug"], embedded["slug"], referring["slug"]}
    assert next(node for node in graph["nodes"] if node["slug"] == target["slug"]) == {
        "slug": target["slug"], "title": "Déploy Guide", "folder": "Ops", "tags": ["release"]}
    assert graph["edges"] == [{"from": referring["slug"], "to": target["slug"]}]
    assert {(edge["from"], edge["to"]) for edge in owner.get(f"{wiki}/graph").json()["edges"]} == {
        (referring["slug"], target["slug"]), (referring["slug"], private["slug"]),
        (private["slug"], target["slug"]),
    }

    current = owner.post(f"{wiki}/pages/{target['slug']}/rename", json={"title": "发布指南"}).json()
    assert member.get(f"{wiki}/graph").json()["edges"] == [{"from": referring["slug"], "to": current["slug"]}]
    assert member.get(f"{wiki}/pages/{current['slug']}").json()["backlinks"] == [
        {"kind": "page", "slug": referring["slug"], "title": referring["title"]}]
    assert TestClient(home["app"]).get(f"{wiki}/graph").status_code == 401
    assert owner.get("/api/app/organizations/other/wiki/graph").status_code == 404


def test_wiki_search_returns_current_matching_titles_in_visible_page_shape(home):
    owner, member, outsider = home["owner"], _join(home), _join(home, "reader@example.com", "读者")
    wiki = f"{home['base']}/wiki"
    prefix = _page(owner, home["base"], title="Release guide", tags=["维护"])
    other = _page(owner, home["base"], title="Monthly release notes")
    private = _page(member, home["base"], title="Release draft", audience={
        "mode": "people", "userIds": [member.member_id],
    })
    deleted = _page(owner, home["base"], title="Release old")
    unrelated = _page(owner, home["base"], title="Checklist", body="Release")
    assert owner.delete(f"{wiki}/pages/{deleted['slug']}").status_code == 204
    MemberRegistry(home["root"]).change_group(OWNER_P, "everyone", items=[])

    search = f"{wiki}/search"
    params = {"q": "  ReLEaSe  "}
    for reader in (owner, member):
        result = reader.get(search, params=params)
        assert result.status_code == 200
        assert result.headers["cache-control"] == "no-store"
        listed = {page["id"]: page for page in reader.get(f"{wiki}/pages").json()["pages"]}
        returned = result.json()["pages"]
        assert {page["id"] for page in returned[:2]} == {prefix["id"], private["id"]}
        assert returned[2:] == [listed[other["id"]]]
        assert returned == [listed[page["id"]] for page in returned]
        assert all("body" not in page for page in returned)
        assert reader.get(search).json() == reader.get(f"{wiki}/pages").json()

    assert [page["id"] for page in outsider.get(search, params=params).json()["pages"]] == [
        prefix["id"], other["id"],
    ]
    for part in ("draft", "old", "unmatched"):
        assert outsider.get(search, params={"q": part}).json() == {"pages": []}
    assert owner.get(search, params={"q": "checklist"}).json()["pages"][0]["id"] == unrelated["id"]
    assert owner.get(search, params={"q": "x" * 201}).status_code == 400
    assert owner.get(search, params={"q": "\0"}).status_code == 400
    assert TestClient(home["app"]).get(search, params=params).status_code == 401
    assert owner.get("/api/app/organizations/other/wiki/search", params=params).status_code == 404


def _quote(client, channel, body):
    result = client.post(f"{channel}/messages", json={"body": body})
    assert result.status_code == 200, result.text
    return {"text": f"> {body}\n\n_来自频道消息_", "source": {
        "kind": "message", "channelId": channel.rsplit("/", 1)[-1], "messageId": result.json()["id"],
    }}


def test_append_public_message_keeps_history_and_filters_source_on_current_access(home):
    owner, member, outsider = home["owner"], _join(home), _join(home, "outside@example.com", "外部成员")
    base = home["base"]
    channel = _create(home, "wiki来源")
    assert member.post(f"{channel}/join").status_code == 204
    entry = _quote(owner, channel, "公开摘录")
    [original] = owner.get(f"{channel}/messages").json()["messages"]
    page = _page(member, base, body="旧正文  \n\n")
    url = f"{base}/wiki/pages/{page['slug']}"

    appended = member.post(f"{url}/append", json=entry)
    assert appended.status_code == 200, appended.text
    result = appended.json()
    assert result["body"] == page["body"] + entry["text"] + "\n"
    assert result["version"] == 2 and result["updatedBy"] == {"type": "person", "id": member.member_id}
    [reference] = result["backlinks"]
    assert reference == {
        **entry["source"], "channelName": "wiki来源", "excerpt": "公开摘录", "at": original["createdAt"],
    }
    assert member.post(f"{url}/append", json=entry).json() == result
    assert owner.get(f"{url}/versions/1").json() == {"version": 1, "body": page["body"]}
    assert outsider.get(url).json()["backlinks"] == []
    assert outsider.get(url).json()["body"] == result["body"]
    assert outsider.post(f"{channel}/join").status_code == 204
    assert outsider.get(url).json()["backlinks"] == [reference]

    assert owner.delete(channel).status_code == 204
    assert member.get(url).json()["backlinks"] == []
    assert member.post(f"{url}/append", json=entry).status_code == 404
    assert member.get(url).json()["body"] == result["body"]
    assert owner.post(f"{channel}/unarchive").status_code == 204
    assert member.get(url).json()["backlinks"] == [reference]
    assert member.put(url, json={"body": "删除摘录", "baseVersion": 2}).json()["backlinks"] == []


def test_append_checks_both_source_and_page_permissions_and_payload_bounds(home):
    owner, member, base = home["owner"], _join(home), home["base"]
    public = _create(home, "append-public")
    other = _create(home, "append-other")
    private = _create(home, "append-hidden", visibility="private")
    secure = _page(owner, base, title="限制页", audience={
        "mode": "people", "userIds": [owner.get("/api/app/me").json()["user"]["id"]],
    })
    page = _page(owner, base, title="已存在页面")
    url = f"{base}/wiki/pages/{page['slug']}"
    entry = _quote(owner, public, "allow")
    hidden = _quote(owner, private, "private")
    assert owner.post(f"{url}/append", json=hidden).status_code == 409
    assert member.post(f"{url}/append", json=hidden).status_code == 404
    assert member.post(f"{url}/append", json=entry).status_code == 404
    assert member.post(f"{public}/join").status_code == 204
    assert owner.post(f"{url}/append", json=entry | {
        "source": entry["source"] | {"channelId": other.rsplit("/", 1)[-1]},
    }).status_code == 404
    assert owner.post(f"{url}/append", json=entry | {
        "source": entry["source"] | {"channelId": home["channel"]},
    }).status_code == 409
    assert member.post(f"{base}/wiki/pages/{secure['slug']}/append", json=entry).status_code == 404
    assert member.post(f"{url}/append", json=entry, headers={"Origin": "https://outside.test"}).status_code == 403
    assert TestClient(home["app"]).post(f"{url}/append", json=entry).status_code == 401
    assert owner.post(f"{url}/append", json=entry | {"text": "x" * MAX_BODY}).status_code == 400
    assert owner.post(f"{url}/append", json=entry | {"text": "x" * (MAX_BODY + 1)}).status_code == 422
    assert owner.post(f"{url}/append", json=entry | {"text": " \n "}).status_code == 400
    assert owner.post(f"{url}/append", json=entry | {"source": entry["source"] | {"channelName": "伪造"}}).status_code == 422
    assert owner.post(f"{url}/lock", json={"locked": True}).status_code == 200
    assert member.post(f"{url}/append", json=entry).status_code == 403
    assert owner.post(f"{url}/lock", json={"locked": False}).status_code == 200
    MemberRegistry(home["root"]).change_group(OWNER_P, "everyone", items=[])
    assert member.post(f"{url}/append", json=entry).status_code == 403
    assert owner.get(url).json()["body"] == page["body"]


def test_concurrent_appends_preserve_each_distinct_quote_once(home):
    owner, base = home["owner"], home["base"]
    channel = _create(home, "wiki并发")
    first, second = _quote(owner, channel, "第一"), _quote(owner, channel, "第二")
    page = _page(owner, base, body="")
    url = f"{base}/wiki/pages/{page['slug']}"

    def submit(body):
        client = TestClient(home["app"], headers={"Authorization": f"Bearer {TOKEN}"})
        return client.post(f"{url}/append", json=body)

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(submit, (first, second, first, second)))
    assert [response.status_code for response in responses] == [200] * 4
    current = owner.get(url).json()
    assert current["version"] == 3
    assert current["body"].count(first["text"]) == current["body"].count(second["text"]) == 1
    assert {backlink["messageId"] for backlink in current["backlinks"]} == {
        first["source"]["messageId"], second["source"]["messageId"]}


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
