"""接管 Codex 草稿第二批时补的：回收站和私有上传合在一起的行为，以及新入口的权限边界。

- #1029 的删除只认助理的制品库，#1026 的上传在另一个库：直接合起来，界面说「已移入回收站、可撤销」而文件没了。
- 新入口用成员、匿名、跨站借用登录 Cookie 各打一遍。
"""
import pytest
from fastapi.testclient import TestClient

from src.agents.tools.artifacts import ArtifactStore
from src.web.auth import SESSION_COOKIE
from tests.unit.test_member_login import TOKEN, _join, home  # noqa: F401 - fixture

HTML = "<h1>只给主人看的上传</h1>"


def _upload(home):
    made = home["owner"].post(f"{home['base']}/artifacts/files", files={"file": ("plan.html", HTML, "text/html")})
    assert made.status_code == 201, made.text
    return made.json()["id"]


def test_deleting_an_upload_can_be_undone(home):
    owner, base, aid = home["owner"], home["base"], _upload(home)
    assert owner.delete(f"{base}/artifacts/{aid}").status_code == 204
    assert owner.get(f"{base}/artifacts/{aid}").status_code == 404
    assert owner.get(f"{base}/artifacts/{aid}/file").status_code == 404
    trashed = owner.get(f"{base}/artifacts-trash").json()["files"]
    assert [(row["id"], row["createdBy"]["type"], row["channelId"]) for row in trashed] == [(aid, "person", None)]
    assert owner.post(f"{base}/artifacts/{aid}/restore").status_code == 204
    assert owner.get(f"{base}/artifacts/{aid}/file").text == HTML
    assert owner.get(f"{base}/artifacts-trash").json() == {"files": []}


def test_a_restored_upload_stays_private(home):
    owner, base, aid = home["owner"], home["base"], _upload(home)
    owner.delete(f"{base}/artifacts/{aid}")
    owner.post(f"{base}/artifacts/{aid}/restore")
    assert ArtifactStore(home["root"]).meta(aid) is None            # 没落进助理（和旧接口）读得到的库
    assert ArtifactStore(home["root"], desktop_uploads=True).html(aid) == HTML
    assert _join(home).get(f"{base}/artifacts").json()["artifacts"] == []


def test_both_kinds_share_one_recycle_bin_and_purge_is_final(home):
    owner, base, aid = home["owner"], home["base"], _upload(home)
    made = ArtifactStore(home["root"]).publish("助理做的", "<p>a</p>")["id"]
    for one in (aid, made):
        assert owner.delete(f"{base}/artifacts/{one}").status_code == 204
    files = {row["id"]: row["createdBy"]["type"] for row in owner.get(f"{base}/artifacts-trash").json()["files"]}
    assert files == {aid: "person", made: "agent"}
    assert owner.delete(f"{base}/artifacts-trash/{aid}").status_code == 204
    assert owner.post(f"{base}/artifacts/{aid}/restore").status_code in {404, 410}
    assert [row["id"] for row in owner.get(f"{base}/artifacts-trash").json()["files"]] == [made]


OWNER_ONLY = [
    ("POST", "{base}/artifacts/x/restore", None),
    ("DELETE", "{base}/artifacts-trash/x", None),
    ("GET", "{base}/join-requests", None),
    ("POST", "{base}/join-requests/x/approve", {}),
    ("POST", "{base}/join-requests/x/deny", {}),
    ("POST", "{base}/routines/x/duplicate", {}),
]
BORROWED = [
    *OWNER_ONLY,
    ("GET", "{base}/artifacts-trash", None),
    ("GET", "{base}/notification-prefs", None),
    ("DELETE", "{base}/me/avatar", None),
    ("PUT", "{base}/me/avatar", {"imageBase64": "AAAA", "contentType": "image/png"}),
]


def _hit(client, home, method, path, body, **kwargs):
    return client.request(method, path.format(base=home["base"]), **({} if body is None else {"json": body}), **kwargs)


@pytest.mark.parametrize("method,path,body", OWNER_ONLY)
def test_a_member_cannot_use_the_owners_entries(home, method, path, body):
    assert _hit(_join(home), home, method, path, body).status_code in {401, 403}


@pytest.mark.parametrize("method,path,body", [*BORROWED, ("GET", "{base}/routines/x/runs", None)])
def test_nobody_can_use_them_anonymously(home, method, path, body):
    assert _hit(TestClient(home["app"]), home, method, path, body).status_code in {401, 403}


@pytest.mark.parametrize("method,path,body", BORROWED)
def test_another_site_cannot_borrow_the_owners_cookie(home, method, path, body):
    borrowed = TestClient(home["app"])
    borrowed.cookies.set(SESSION_COOKIE, TOKEN)
    refused = _hit(borrowed, home, method, path, body, headers={"Origin": "https://evil.example"})
    assert refused.status_code in {401, 403}, refused.text


def test_a_member_sees_only_their_own_join_suggestions_and_cannot_close_anothers(home):
    base = home["base"]
    ana, ben = _join(home), _join(home, email="ben@example.com", name="小本")
    made = ana.post(f"{base}/join-requests/suggest", json={"email": "new@example.com"})
    assert made.status_code == 200, made.text
    assert ben.get(f"{base}/join-requests/mine").json() == {"suggestions": []}
    assert ben.delete(f"{base}/join-requests/{made.json()['id']}").status_code == 404
    assert len(home["owner"].get(f"{base}/join-requests").json()["requests"]) == 1
