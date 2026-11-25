"""The private Desktop HTML upload shares storage code with agent artifacts."""
from base64 import b64decode
from hashlib import sha256
from pathlib import Path
import struct
from urllib.parse import quote
import zlib

from fastapi.testclient import TestClient
import pytest

from src.agents.tools.artifacts import ArtifactStore
from src.utils.home_maintenance import home_maintenance
from tests.unit.test_app_profile_avatar import _JPEG, _png
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


@pytest.mark.parametrize(("name", "declared", "expected"), [
    ("记录.txt", "text/plain", "text/plain"),
    ("notes.md", "application/octet-stream", "text/markdown"),
    ("report.csv", "application/vnd.ms-excel", "text/csv"),
    ("schema.json", "application/json", "application/json"),
])
def test_private_drive_uploads_offer_text_preview_and_original_attachment(home, name, declared, expected):
    owner, base, root = home["owner"], home["base"], home["root"]
    path = f"{base}/artifacts"
    original = '"<em>此处是文本</em>"\r\n'.encode()
    published = owner.post(f"{path}/files", files=_files(name, original, declared))
    assert published.status_code == 201, published.text
    assert published.headers["cache-control"] == "no-store"
    detail = published.json()
    aid = detail["id"]
    assert detail["kind"] == "file" and detail["title"] == name
    assert detail["version"] == detail["shownVersion"] == 1
    assert detail["untrusted"] is True and detail["canEdit"] is True and detail["canShare"] is False
    assert detail["versioned"] is True
    assert detail["content"] == {"kind": "file", "name": name, "contentType": expected, "size": len(original),
                                 "sha256": sha256(original).hexdigest(), "text": original.decode()}
    assert owner.get(f"{path}/{aid}?version=1").json() == detail
    assert owner.get(f"{path}/{aid}?version=2").status_code == 404

    legacy = ArtifactStore(root).publish("Legacy", HTML)["id"]
    result = owner.get(path).json()
    assert result["counts"] == {"file": 1, "html": 1}
    assert [row["id"] for row in owner.get(path, params={"kinds": "file"}).json()["artifacts"]] == [aid]
    assert [row["id"] for row in owner.get(path, params={"kinds": "html"}).json()["artifacts"]] == [legacy]
    [version] = owner.get(f"{path}/{aid}/versions").json()["versions"]
    assert version["version"] == 1 and version["author"] == detail["createdBy"]
    raw = owner.get(f"{path}/{aid}/file")
    assert raw.status_code == 200 and raw.content == original
    assert raw.headers["content-type"] == "application/octet-stream"
    assert raw.headers["content-disposition"] == f"attachment; filename*=UTF-8''{quote(name, safe='')}"
    assert raw.headers["x-content-type-options"] == "nosniff"
    assert raw.headers["content-security-policy"] == "sandbox; default-src 'none'"
    assert raw.headers["cache-control"] == "no-store"
    assert owner.get(f"{path}/{aid}/file?format=html").status_code == 415
    signed = owner.get(f"{path}/{aid}/download").json()["url"]
    assert owner.get(signed).content == original
    assert owner.get(f"/artifact/{aid}/raw").status_code == 404

    member = _join(home)
    assert member.get(path, params={"kinds": "file"}).json()["artifacts"] == []
    assert member.post(f"{path}/files", files=_files(name, original, declared)).status_code == 403
    assert member.get(f"{path}/{aid}").status_code == 403
    assert member.get(f"{path}/{aid}/file").status_code == 403
    assert member.get(signed).status_code == 403
    assert owner.delete(f"{path}/{aid}").status_code == 204
    assert owner.get(f"{path}/{aid}/file").status_code == 404
    [trashed] = owner.get(f"{base}/artifacts-trash").json()["files"]
    assert trashed["id"] == aid and trashed["kind"] == "file"
    assert owner.post(f"{path}/{aid}/restore", json={}).status_code == 204
    assert owner.get(f"{path}/{aid}/file").content == original


@pytest.mark.parametrize(("name", "original", "declared", "expected"), [
    ("界面.PNG", _png(), "application/octet-stream", "image/png"),
    ("picture.jpg", b64decode(_JPEG), "image/jpeg", "image/jpeg"),
    ("picture.jpeg", b64decode(_JPEG), "application/octet-stream", "image/jpeg"),
])
def test_private_drive_uploads_keep_original_image_bytes_and_safe_metadata(home, name, original, declared, expected):
    owner, base = home["owner"], home["base"]
    path = f"{base}/artifacts"
    published = owner.post(f"{path}/files", files=_files(name, original, declared))
    assert published.status_code == 201, published.text
    detail = published.json()
    artifact_id = detail["id"]
    assert detail["kind"] == "file" and detail["title"] == name
    assert detail["untrusted"] is True and detail["canShare"] is False and detail["versioned"] is False
    assert detail["content"] == {"kind": "file", "name": name, "contentType": expected,
                                 "size": len(original), "sha256": sha256(original).hexdigest()}
    assert owner.get(f"{path}/{artifact_id}?version=1").json() == detail
    assert owner.get(f"{path}/{artifact_id}/file?format=html").status_code == 415
    signed = owner.get(f"{path}/{artifact_id}/download").json()["url"]
    for url in (f"{path}/{artifact_id}/file", signed):
        downloaded = owner.get(url)
        assert downloaded.status_code == 200 and downloaded.content == original
        assert downloaded.headers["content-type"] == "application/octet-stream"
        assert downloaded.headers["content-disposition"] == f"attachment; filename*=UTF-8''{quote(name, safe='')}"
        assert downloaded.headers["x-content-type-options"] == "nosniff"
        assert downloaded.headers["content-security-policy"] == "sandbox; default-src 'none'"
        assert downloaded.headers["cross-origin-resource-policy"] == "same-origin"
        assert downloaded.headers["cache-control"] == "no-store"

    member = _join(home)
    assert member.post(f"{path}/files", files=_files(name, original, declared)).status_code == 403
    assert member.get(path, params={"kinds": "file"}).json()["artifacts"] == []
    assert member.get(f"{path}/{artifact_id}").status_code == 403
    assert member.get(f"{path}/{artifact_id}/file").status_code == 403
    assert member.get(signed).status_code == 403


def _image_chunk(kind, content):
    return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content))


@pytest.mark.parametrize(("name", "data", "mime"), [
    ("spoof.png", HTML.encode(), "image/png"),
    ("spoof.jpg", _png(), "image/jpeg"),
    ("spoof.png", _png(), "text/html"),
    ("extra.png", _png() + b"trailing bytes", "image/png"),
    ("extra.jpg", b64decode(_JPEG) + b"trailing bytes\xff\xd9", "image/jpeg"),
    ("truncated.png", _png()[:-12], "image/png"),
    ("truncated.jpg", b64decode(_JPEG)[:-2], "image/jpeg"),
    ("animated.png", _png()[:33] + _image_chunk(b"acTL", struct.pack(">II", 2, 0)) + _png()[33:], "image/png"),
])
def test_invalid_image_upload_does_not_publish_metadata_or_bytes(home, name, data, mime):
    owner, base, root = home["owner"], home["base"], home["root"]
    path = f"{base}/artifacts"
    response = owner.post(f"{path}/files", files=_files(name, data, mime))
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "artifact.upload_format"
    assert owner.get(path).json()["artifacts"] == []
    assert not ArtifactStore(root, desktop_uploads=True).root.exists()


@pytest.mark.parametrize(("width", "height"), [(12_001, 2), (8_000, 8_000)])
def test_image_dimension_limits_are_independent_of_compressed_byte_size(home, width, height):
    owner, base = home["owner"], home["base"]
    path = f"{base}/artifacts"
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    fixture = _png()
    image = fixture[:8] + _image_chunk(b"IHDR", header) + fixture[33:]
    rejected = owner.post(f"{path}/files", files=_files("large.png", image, "image/png"))
    assert rejected.status_code == 413
    assert rejected.json()["error"]["code"] == "artifact.upload_too_large"
    assert owner.get(path).json()["artifacts"] == []


def test_image_upload_obeys_artifact_byte_limit(home, monkeypatch):
    monkeypatch.setenv("VORTOONE_ARTIFACT_MAX_BYTES", "30")
    owner, base = home["owner"], home["base"]
    path = f"{base}/artifacts"
    rejected = owner.post(f"{path}/files", files=_files("large.png", _png(), "image/png"))
    assert rejected.status_code == 413
    assert rejected.json()["error"]["code"] == "artifact.upload_too_large"
    assert owner.get(path).json()["artifacts"] == []


def test_download_rejects_a_same_size_uploaded_file_replacement(home):
    owner, base, root = home["owner"], home["base"], home["root"]
    path = f"{base}/artifacts"
    png = _png()
    posted = owner.post(f"{path}/files", files=_files("original.png", png, "image/png"))
    assert posted.status_code == 201, posted.text
    artifact_id = posted.json()["id"]
    signed = owner.get(f"{path}/{artifact_id}/download").json()["url"]

    uploaded = ArtifactStore(root, desktop_uploads=True).root / artifact_id / "upload.bin"
    uploaded.write_bytes(b"x" * len(png))
    for url in (f"{path}/{artifact_id}/file", signed):
        response = owner.get(url)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "artifact.unavailable"


def test_large_text_file_can_be_downloaded_without_inline_content(home):
    owner, base = home["owner"], home["base"]
    original = b"payload\n" * 5000
    posted = owner.post(f"{base}/artifacts/files", files=_files("large.txt", original, "text/plain"))
    assert posted.status_code == 201, posted.text
    path = f"{base}/artifacts/{posted.json()['id']}"
    for detail in [posted.json(), owner.get(path).json()]:
        assert detail["content"]["size"] == len(original)
        assert "text" not in detail["content"]
    assert owner.get(f"{path}/file").content == original


def test_file_upload_failure_keeps_uncommitted_bytes_out_of_drive(home, monkeypatch):
    original = Path.write_text

    def fail_metadata(path, *args, **kwargs):
        if path.name == "meta.json" and path.parent.parent.name.startswith(".upload-"):
            raise OSError("private fixture path")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_metadata)
    path = f"{home['base']}/artifacts"
    rejected = home["owner"].post(f"{path}/files", files=_files("notes.md", "sample", "text/markdown"))
    assert rejected.status_code == 503 and "private fixture path" not in rejected.text
    assert home["owner"].get(path).json()["artifacts"] == []
    assert not list(ArtifactStore(home["root"], desktop_uploads=True).root.iterdir())


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
        (_files("notes.txt", "<p>text</p>", "text/html"), {}, 415),
        (_files("notes.txt", b"\xff", "text/plain"), {}, 415),
        (_files("notes.txt", b"hello\x00", "text/plain"), {}, 400),
        (_files("../notes.txt", b"plain", "text/plain"), {}, 400),
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


def test_busy_workspace_refuses_upload_and_delete_without_changing_artifacts(home):
    owner, root = home["owner"], home["root"]
    path = f"{home['base']}/artifacts"
    saved = owner.post(f"{path}/files", files=_files())
    assert saved.status_code == 201, saved.text
    artifact_id = saved.json()["id"]
    saved_path = f"{path}/{artifact_id}"
    uploads = ArtifactStore(root, desktop_uploads=True)

    with home_maintenance(root):
        busy_upload = owner.post(f"{path}/files", files=_files("next.html"))
        busy_delete = owner.delete(saved_path)
        # 删除现在是移入回收站：忙时回 423，界面把 409 当成「撤销已过期」
        for response, status in ((busy_upload, 409), (busy_delete, 423)):
            assert response.status_code == status, response.text
            assert response.json()["error"]["code"] == "unavailable"
            assert root not in response.text
        assert [row["id"] for row in owner.get(path).json()["artifacts"]] == [artifact_id]
        assert uploads.html(artifact_id) == HTML

    assert owner.post(f"{path}/files", files=_files("next.html")).status_code == 201
    assert owner.delete(saved_path).status_code == 204
    assert owner.get(saved_path).status_code == 404


@pytest.mark.parametrize(("name", "mime", "stored"), [
    ("报价.pdf", "application/pdf", "application/pdf"),
    ("table.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("bundle.zip", "text/html", "application/octet-stream"),           # 冒充成网页的：记成只能下载
    ("notes.bin", "text/plain", "application/octet-stream"),           # 冒充成文本的：不当成可预览、可改的
    ("photo.webp", "image/webp", "application/octet-stream"),
    ("letter.rtf", "text/rtf", "application/octet-stream"),
    ("data.xml", "application/xml", "application/octet-stream"),
])
def test_other_formats_are_kept_as_files_you_can_only_download(home, name, mime, stored):
    # 2026-11-24：通用文件上传接上（接 Codex 半成品）。原样保存，只给附件下载，不预览、不改
    owner, path = home["owner"], f"{home['base']}/artifacts"
    data = b"%PDF-1.7\n\x00\xff binary"
    made = owner.post(f"{path}/files", files=_files(name, data, mime))
    assert made.status_code == 201, made.text
    detail = made.json()
    assert detail["content"]["contentType"] == stored and detail["content"]["size"] == len(data)
    assert "text" not in detail["content"] and detail["versioned"] is False and detail["canEdit"] is False
    raw = owner.get(f"{path}/{detail['id']}/file")
    assert raw.content == data and raw.headers["content-type"] == "application/octet-stream"
    assert raw.headers["content-disposition"].startswith("attachment;")
    assert raw.headers["content-security-policy"] == "sandbox; default-src 'none'"
    assert raw.headers["x-content-type-options"] == "nosniff"
    again = owner.post(f"{path}/{detail['id']}/files", files=_files(name, data, mime))
    assert again.status_code == 415                                     # 不能追加版本


def test_an_empty_or_misplaced_file_is_still_refused(home):
    owner, path = home["owner"], f"{home['base']}/artifacts/files"
    assert owner.post(path, files=_files("empty.pdf", b"", "application/pdf")).status_code == 400
    assert owner.post(path, files=_files("doc.pdf", b"x", "application/pdf"), data={"channelId": "c"}).status_code == 422
    assert owner.get(f"{home['base']}/artifacts").json()["artifacts"] == []
