"""接管 Codex 草稿第一批时补的：两个 PR 各自是绿的，合在一起才坏；以及新入口的权限边界。

- #1026 把上传的 HTML 放进单独的私有库，#986 的下载和历史只查助理的制品库：上传的文件点「下载」是 404。
- 新入口逐个用成员、匿名、跨站借用登录 Cookie 各打一遍。
"""
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from src.web.auth import SESSION_COOKIE
from tests.unit.test_member_login import TOKEN, _join, home  # noqa: F401 - fixture

HTML = "<h1>只给主人看的上传</h1>"


@pytest.fixture
def uploaded(home):
    made = home["owner"].post(f"{home['base']}/artifacts/files", files={"file": ("plan.html", HTML, "text/html")})
    assert made.status_code == 201, made.text
    return {**home, "artifact": f"{home['base']}/artifacts/{made.json()['id']}"}


def test_an_uploaded_file_has_a_history_and_downloads(uploaded):
    owner, path = uploaded["owner"], uploaded["artifact"]
    history = owner.get(f"{path}/versions")
    assert history.status_code == 200, history.text
    rows = history.json()["versions"]
    assert [row["version"] for row in rows] == [1]
    assert rows[0]["author"] == {"type": "person", "id": owner.get("/api/app/me").json()["user"]["id"]}
    link = owner.get(f"{path}/download")
    assert link.status_code == 200, link.text
    got = owner.get(link.json()["url"])
    assert got.status_code == 200 and got.text == HTML
    assert got.headers["content-type"] == "application/octet-stream"
    assert got.headers["content-disposition"].startswith("attachment;")


def test_a_download_link_is_not_a_capability(uploaded):
    owner, path = uploaded["owner"], uploaded["artifact"]
    link = owner.get(f"{path}/download").json()["url"]
    assert _join(uploaded).get(link).status_code in {401, 403}
    assert TestClient(uploaded["app"]).get(link).status_code in {401, 403}
    signature = parse_qs(urlsplit(link).query)["signature"][0]
    assert owner.get(link.replace(signature, "0" * 64)).status_code == 403


ENTRIES = [
    ("GET", "{artifact}/versions", None),
    ("GET", "{artifact}/download", None),
    ("GET", "{artifact}/file", None),
    ("POST", "{base}/artifacts/files", "upload"),
    ("POST", "{base}/roles/everyone/members/preview", {"userIds": []}),
    ("POST", "{base}/invitations/anything/resend", {}),
    ("GET", "/api/app/feedback?orgId={org}", None),
]


def _hit(client, home, method, path, body, **kwargs):
    url = path.format(artifact=home["artifact"], base=home["base"], org=home["base"].rsplit("/", 1)[-1])
    if body == "upload":
        return client.request(method, url, files={"file": ("x.html", "<p>x</p>", "text/html")}, **kwargs)
    return client.request(method, url, **({} if body is None else {"json": body}), **kwargs)


@pytest.mark.parametrize("method,path,body", ENTRIES)
def test_a_member_cannot_use_the_owners_entries(uploaded, method, path, body):
    assert _hit(_join(uploaded), uploaded, method, path, body).status_code in {401, 403}


@pytest.mark.parametrize("method,path,body", ENTRIES)
def test_nobody_can_use_them_anonymously(uploaded, method, path, body):
    assert _hit(TestClient(uploaded["app"]), uploaded, method, path, body).status_code in {401, 403}


@pytest.mark.parametrize("method,path,body", [*ENTRIES, ("GET", "/api/app/me/unread", None)])
def test_another_site_cannot_borrow_the_owners_cookie(uploaded, method, path, body):
    borrowed = TestClient(uploaded["app"])
    borrowed.cookies.set(SESSION_COOKIE, TOKEN)
    refused = _hit(borrowed, uploaded, method, path, body, headers={"Origin": "https://evil.example"})
    assert refused.status_code in {401, 403}, refused.text


def test_another_site_cannot_submit_feedback_as_the_owner(uploaded):
    borrowed = TestClient(uploaded["app"])
    borrowed.cookies.set(SESSION_COOKIE, TOKEN)
    sent = borrowed.post("/api/app/feedback", headers={"Origin": "https://evil.example"}, json={
        "id": "00000000-0000-4000-8000-000000000001", "orgId": uploaded["base"].rsplit("/", 1)[-1], "text": "假冒"})
    assert sent.status_code in {401, 403}
