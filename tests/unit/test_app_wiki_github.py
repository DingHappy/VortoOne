"""Public GitHub Wiki import through the real Desktop API and a fake upstream transport."""

from io import BytesIO
import stat
from zipfile import ZipFile, ZipInfo

from fastapi.testclient import TestClient
import pytest

from src.utils.state_dir import state_dir
from src.web import app_wiki_github as github
from src.web.auth import SESSION_COOKIE
from tests.unit.test_member_agents import ORIGIN, TOKEN, _join, home  # noqa: F401 - fixture


class _Response:
    def __init__(self, status, data=b"", headers=None):
        self.status, self.data, self.headers = status, data, headers or {}
        self.content_length = None
        self.content = self

    async def iter_chunked(self, _):
        yield self.data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


class _Session:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def attach(self, monkeypatch):
        def session(**options):
            assert options["trust_env"] is False and options["auto_decompress"] is False
            assert "Authorization" not in options["headers"]
            return self

        monkeypatch.setattr(github.aiohttp, "ClientSession", session)
        return self

    def get(self, url, *, allow_redirects, **options):
        assert allow_redirects is False
        assert "Authorization" not in options.get("headers", {})
        self.calls.append((url, options))
        return next(self.replies)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


_API = "https://api.github.com/repos/Example/guide"
_ZIP = "https://codeload.github.com/Example/guide/legacy.zip/refs/heads/main"


def _zip(entries):
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


def _redirect(location=_ZIP):
    return _Response(302, headers={"Location": location})


def _path(home):
    return home["base"] + "/wiki/import/github"


def test_github_tree_preview_and_confirm_keep_markdown_provenance(home, monkeypatch):
    link = "https://github.com/Example/guide/tree/main/docs#reading"
    symlink = ZipInfo("guide-abc/docs/outside.md")
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    data = _zip([("guide-abc/docs/开始.md", "# Wiki"), ("guide-abc/docs/setup/Install.MARKDOWN", "install"),
                 ("guide-abc/docs/private.txt", "omit"), ("guide-abc/README.md", "omit"), (symlink, "/ignored")])
    github_session = _Session([_redirect(), _Response(200, data)]).attach(monkeypatch)
    owner = home["owner"]
    response = owner.get(_path(home), params={"url": link})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert github_session.calls == [(_API + "/zipball/main", {"headers": {"Accept": "application/vnd.github+json"}}),
                                    (_ZIP, {})]
    with ZipFile(BytesIO(response.content)) as archive:
        assert archive.namelist() == ["开始.md", "setup/Install.MARKDOWN"]
        text = archive.read("开始.md").decode()
    assert not (state_dir(home["root"]) / "wiki" / "pages.sqlite3").exists()

    body = {"notes": [{"title": "开始", "body": text, "aliases": []}],
            "source": {"type": "github", "name": "docs"}, "onConflict": "keep_both", "importId": "wiki-github"}
    submit = home["base"] + "/wiki/import"
    saved = owner.post(submit, json=body)
    assert saved.status_code == 200, saved.text
    [page] = saved.json()["created"]
    assert owner.post(submit, json=body).json() == saved.json()
    assert len(github_session.calls) == 2
    stored = owner.get(f"{home['base']}/wiki/pages/{page['slug']}").json()
    assert (stored["body"], stored["sourceType"], stored["sourceName"], stored["agentContext"]) == (
        "# Wiki", "github", "docs", False,
    )


@pytest.mark.parametrize("link", [
    "https://github.com/Example/guide/blob/feature%2Ftest/docs/Page.md",
    "https://raw.githubusercontent.com/Example/guide/feature%2Ftest/docs/Page.md",
])
def test_github_file_returns_a_single_note_and_uses_ref_query(home, monkeypatch, link):
    github_session = _Session([_Response(200, b"\xef\xbb\xbf# File")]).attach(monkeypatch)
    response = home["owner"].get(_path(home), params={"url": link})
    assert response.status_code == 200, response.text
    assert github_session.calls == [(_API + "/contents/docs/Page.md", {
        "params": {"ref": "feature/test"}, "headers": {"Accept": "application/vnd.github.raw+json"},
    })]
    with ZipFile(BytesIO(response.content)) as archive:
        assert archive.namelist() == ["Page.md"]
        assert archive.read("Page.md") == b"# File"


def test_github_preview_is_owner_only_and_never_fetches_on_refusal(home, monkeypatch):
    member = _join(home)
    admin = home["owner"]
    group = admin.post(home["base"] + "/roles", json={"name": "Wiki Editors", "permissions": ["edit_wiki"]})
    assert group.status_code == 200, group.text
    assert admin.put(f"{home['base']}/members/{member.member_id}/roles",
                     json={"roleIds": [group.json()["id"]]}).status_code == 200

    session = _Session([]).attach(monkeypatch)
    params = {"url": "https://github.com/Example/guide"}
    path = _path(home)
    visitor = TestClient(home["app"])
    assert visitor.get(path, params=params).status_code == 401
    assert member.get(path, params=params).status_code == 403
    assert admin.get(path.replace(home["base"], "/api/app/organizations/unknown"), params=params).status_code == 404
    cookie = TestClient(home["app"], cookies={SESSION_COOKIE: TOKEN})
    assert cookie.get(path, params=params, headers={"Origin": "https://outside.example"}).status_code == 403
    body = {"notes": [{"title": "Note", "body": "body"}], "source": {"type": "github", "name": "guide"},
            "onConflict": "skip"}
    assert member.post(home["base"] + "/wiki/import", json=body, headers=ORIGIN).status_code == 403
    assert session.calls == []


@pytest.mark.parametrize("url", [
    "http://github.com/Example/guide",
    "https://github.com@elsewhere.invalid/Example/guide",
    "https://github.com:443/Example/guide",
    "https://github.com/Example/%2e%2e",
    "https://github.com/Example/guide/tree/main/%2e%2e",
    "https://github.com/Example/guide/blob/main/docs%2fPage.md",
    "https://github.com/Example/guide/blob/main/docs/Splash.png",
    "https://github.com/Example/guide?token=fixture",
])
def test_github_preview_rejects_unrecognized_links_before_upstream(home, monkeypatch, url):
    session = _Session([]).attach(monkeypatch)
    response = home["owner"].get(_path(home), params={"url": url})
    assert response.status_code == 422 and response.json()["error"]["code"] == "wiki_github_invalid_link"
    assert session.calls == []


def test_github_preview_checks_redirects_and_reports_private_and_oversize(home, monkeypatch):
    session = _Session([_redirect("http://localhost/Example/guide"), _Response(404),
                        _redirect(), _Response(200, b"too long")]).attach(monkeypatch)
    owner, params = home["owner"], {"url": "https://github.com/Example/guide"}
    invalid = owner.get(_path(home), params=params)
    assert invalid.status_code == 502
    assert len(session.calls) == 1
    missing = owner.get(_path(home), params=params)
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "wiki_github_not_public"
    assert len(session.calls) == 2
    monkeypatch.setattr(github, "_ARCHIVE_BYTES", 4)
    assert owner.get(_path(home), params=params).status_code == 413
    assert [url for url, _ in session.calls] == [_API + "/zipball"] * 3 + [_ZIP]


@pytest.mark.parametrize(("entries", "status"), [
    ([("guide-main/docs/../oops.md", "bad")], 502),
    ([("guide-main/image.png", b"no notes")], 404),
    ([("guide-main/is-binary.md", b"\xff")], 415),
    ([("guide-main/huge.md", b"too long")], 413),
], ids=["path", "empty", "encoding", "note-limit"])
def test_github_preview_rejects_content_that_cannot_be_imported(home, monkeypatch, entries, status):
    _Session([_redirect(), _Response(200, _zip(entries))]).attach(monkeypatch)
    monkeypatch.setattr(github, "_NOTE_BYTES", 7)
    response = home["owner"].get(_path(home), params={"url": "https://github.com/Example/guide"})
    assert response.status_code == status, response.text
