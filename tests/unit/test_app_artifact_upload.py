"""The private Desktop HTML upload shares storage code with agent artifacts."""
from fastapi.testclient import TestClient
import pytest

from src.agents.tools.artifacts import ArtifactStore
from tests.unit.test_member_login import ORIGIN, TOKEN, _join
from tests.unit.test_member_login import home  # noqa: F401


HTML = "<!doctype html><h1>桌面制品</h1>"


def _files(name="example.html", content=HTML, content_type="text/html"):
    return {"file": (name, content, content_type)}


def test_owner_uploads_a_private_html_artifact_and_reads_it_in_drive(home, monkeypatch):
    owner, base, root = home["owner"], home["base"], home["root"]
    legacy = ArtifactStore(root).publish("Legacy", "<p>legacy</p>")["id"]
    path = f"{base}/artifacts"
    created = owner.post(f"{path}/files", files=_files(), data={"title": " 驾驶舱 "})
    assert created.status_code == 201, created.text
    assert created.headers["content-type"] == "application/json"
    assert created.headers["cache-control"] == "no-store"
    view = created.json()
    artifact_id = view["id"]
    assert view["createdBy"] == view["updatedBy"] == {
        "type": "person", "id": owner.get("/api/app/me").json()["user"]["id"],
    }
    assert (view["title"], view["kind"], view["version"], view["shownVersion"]) == ("驾驶舱", "html", 1, 1)
    assert view["content"] == {"kind": "html", "html": HTML}
    assert view["untrusted"] is True and view["canShare"] is False and view["versioned"] is False
    assert view["channelId"] is None and view["threadId"] is None
    assert owner.get(f"{path}/{artifact_id}").json() == view
    assert owner.get(f"{path}/{artifact_id}", params={"version": 2}).status_code == 404
    assert [a["id"] for a in owner.get(path, params={"q": "驾驶舱"}).json()["artifacts"]] == [artifact_id]
    assert {a["id"] for a in owner.get(path).json()["artifacts"]} == {legacy, artifact_id}

    assert ArtifactStore(root, desktop_uploads=True).html(artifact_id) == HTML

    member = _join(home)
    listing = member.get(path)
    assert listing.status_code == 200
    assert listing.json()["artifacts"] == []
    assert member.get(f"{path}/{artifact_id}").status_code == 403
    assert member.delete(f"{path}/{artifact_id}").status_code == 403
    monkeypatch.delenv("VORTOONE_API_TOKEN")
    visitor = TestClient(home["app"])
    assert visitor.get(f"{path}/{artifact_id}").status_code == 404
    assert [item["id"] for item in visitor.get(path).json()["artifacts"]] == [legacy]
    assert visitor.post(f"{path}/files", files=_files()).status_code == 403

    monkeypatch.setenv("VORTOONE_API_TOKEN", TOKEN)
    assert owner.delete(f"{path}/{artifact_id}").status_code == 204
    assert owner.get(f"{path}/{artifact_id}").status_code == 404
    assert owner.get(f"{path}/{legacy}").status_code == 200


def test_legacy_artifact_routes_do_not_discover_private_uploads(home):
    owner, base, root = home["owner"], home["base"], home["root"]
    legacy_store = ArtifactStore(root)
    legacy = legacy_store.publish("Legacy", "<p>legacy</p>")["id"]
    posted = owner.post(f"{base}/artifacts/files", files=_files())
    assert posted.status_code == 201, posted.text
    artifact_id = posted.json()["id"]

    assert legacy_store.html(artifact_id) is None
    # In particular, no same-origin raw HTML path can load a Desktop upload.
    for route in (f"/api/artifacts/{artifact_id}", f"/artifact/{artifact_id}", f"/artifact/{artifact_id}/raw"):
        assert owner.get(route).status_code == 404
    assert owner.get(f"/artifact/{legacy}/raw").status_code == 200


def test_an_upload_needs_an_owner_with_bearer_or_same_origin_cookie(home):
    path = f"{home['base']}/artifacts/files"
    member = _join(home)
    assert member.post(path, files=_files()).status_code == 403
    browser = TestClient(home["app"])
    assert browser.post(path, files=_files()).status_code == 401
    signed_in = browser.post("/api/auth/login", json={"token": TOKEN})
    assert signed_in.status_code == 200
    assert browser.post(path, files=_files(), headers={"Origin": "https://other.example"}).status_code == 403
    assert browser.get(f"{home['base']}/artifacts").json()["artifacts"] == []
    assert browser.post(path, files=_files(), headers=ORIGIN).status_code == 201


@pytest.mark.parametrize(
    ("file", "data", "status"),
    [
        (_files("image.svg", "<svg/>", "image/svg+xml"), {}, 415),
        (_files("mail.txt", "test", "text/plain"), {}, 415),
        (_files("text.html", b"\xff"), {}, 415),
        (_files("text.html", ""), {}, 400),
        (_files(), {"channelId": "another-channel"}, 422),
        (_files(), {"threadId": "a-thread"}, 422),
        (_files(), {"title": "a" * 201}, 400),
        (_files(), {"path": "unsafe"}, 400),
        ([("file", ("a.html", HTML, "text/html")), ("file", ("b.html", HTML, "text/html"))], {}, 400),
    ],
)
def test_unsupported_or_invalid_upload_does_not_publish_file(home, file, data, status):
    path = f"{home['base']}/artifacts"
    rejected = home["owner"].post(f"{path}/files", files=file, data=data)
    assert rejected.status_code == status, rejected.text
    assert home["owner"].get(path).json()["artifacts"] == []


def test_oversized_upload_is_rejected_before_publish(home, monkeypatch):
    monkeypatch.setenv("VORTOONE_ARTIFACT_MAX_BYTES", "30")
    path = f"{home['base']}/artifacts"
    res = home["owner"].post(f"{path}/files", files=_files(content="<p>" + "a" * 100 + "</p>"))
    assert res.status_code == 413, res.text
    assert home["owner"].get(path).json()["artifacts"] == []


def test_failed_upload_does_not_publish_a_partial_file(home, monkeypatch):
    def fail(directory, *_args):
        directory.mkdir()
        (directory / "index.html").write_text("partially written", encoding="utf-8")
        raise OSError("private fixture path")

    monkeypatch.setattr(ArtifactStore, "_publish_at", staticmethod(fail))
    path = f"{home['base']}/artifacts"
    res = home["owner"].post(f"{path}/files", files=_files())
    assert res.status_code == 503
    assert "private fixture path" not in res.text
    uploads = ArtifactStore(home["root"], desktop_uploads=True)
    assert list(uploads.root.iterdir()) == []
    assert home["owner"].get(path).json()["artifacts"] == []
