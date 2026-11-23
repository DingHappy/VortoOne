"""Private Desktop file history leaves every published file version immutable."""

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256

from fastapi.testclient import TestClient
import pytest

from src.agents.tools.artifacts import ArtifactStore
from src.utils.home_maintenance import home_maintenance
from tests.unit.test_app_artifact_upload import HTML, _files
from tests.unit.test_app_profile_avatar import _png
from tests.unit.test_member_login import ORIGIN, TOKEN, _join
from tests.unit.test_member_login import home  # noqa: F401


def _text_artifact(home):
    first = b"original\r\n"
    path = f"{home['base']}/artifacts"
    saved = home["owner"].post(f"{path}/files", files=_files("original.md", first, "text/markdown"))
    assert saved.status_code == 201, saved.text
    detail = saved.json()
    return f"{path}/{detail['id']}", detail, first


def test_owner_appends_text_versions_and_old_downloads_remain_immutable(home):
    owner = home["owner"]
    path, first, first_bytes = _text_artifact(home)
    assert first["canEdit"] is True and first["versioned"] is True
    original_url = owner.get(f"{path}/download").json()["url"]
    second_bytes = "# 修订\r\n".encode()
    posted = owner.post(f"{path}/files", files=_files("new.md", second_bytes, "application/octet-stream"),
                        data={"note": "  改了标题  "})
    assert posted.status_code == 201, posted.text
    assert posted.headers["cache-control"] == "no-store"
    second = posted.json()
    assert second["id"] == first["id"] and second["title"] == first["title"]
    assert second["createdAt"] == first["createdAt"] and second["createdBy"] == first["createdBy"]
    assert (second["version"], second["shownVersion"], second["canEdit"], second["versioned"]) == (2, 2, True, True)
    assert second["content"] == {"kind": "file", "name": "new.md", "contentType": "text/markdown",
                                 "size": len(second_bytes), "sha256": sha256(second_bytes).hexdigest(),
                                 "text": second_bytes.decode()}
    assert second["channelId"] is None and second["threadId"] is None and second["canShare"] is False
    assert owner.get(path).json() == second

    original = owner.get(f"{path}?version=1").json()
    assert (original["version"], original["shownVersion"], original["canEdit"]) == (2, 1, False)
    assert original["content"] == first["content"]
    for url in (f"{path}/file?version=1", original_url):
        raw = owner.get(url)
        assert raw.status_code == 200 and raw.content == first_bytes
        assert raw.headers["content-disposition"].endswith("original.md")
        assert raw.headers["content-type"] == "application/octet-stream"
    assert owner.get(f"{path}/file").content == second_bytes
    assert owner.get(f"{path}?version=3").status_code == 404
    assert owner.get(f"{path}/file?version=3").status_code == 404
    assert owner.get(f"{path}/download?version=3").status_code == 404

    history = owner.get(f"{path}/versions").json()["versions"]
    assert [(row["version"], row["note"], row["author"]) for row in history] == [
        (2, "改了标题", first["createdBy"]), (1, None, first["createdBy"]),
    ]
    fresh_store = ArtifactStore(home["root"], desktop_uploads=True)
    persisted = fresh_store.meta(first["id"])
    assert persisted["version"] == 2
    assert fresh_store.uploaded_version(persisted, 1)["sha256"] == sha256(first_bytes).hexdigest()
    assert owner.delete(path).status_code == 204
    assert owner.get(original_url).status_code == 404
    assert owner.post(f"{path}/restore", json={}).status_code == 204
    assert owner.get(f"{path}/versions").json()["versions"] == history
    assert owner.get(original_url).content == first_bytes
    assert owner.get(f"{path}/file").content == second_bytes


def test_text_versions_keep_owner_proof_and_members_cannot_read_or_append(home):
    owner = home["owner"]
    path, first, first_bytes = _text_artifact(home)
    append = _files("new.md", b"next", "text/markdown")
    member = _join(home)
    assert member.post(f"{path}/files", files=append).status_code == 403
    for url in (path, f"{path}/versions", f"{path}/file?version=1", f"{path}/download"):
        assert member.get(url).status_code == 403
    guest = TestClient(home["app"])
    assert guest.post(f"{path}/files", files=append).status_code == 401
    assert guest.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    assert guest.post(f"{path}/files", files=append, headers={"Origin": "https://other.example"}).status_code == 403
    assert owner.get(path).json() == first
    assert owner.get(f"{path}/file").content == first_bytes
    assert guest.post(f"{path}/files", files=append, headers=ORIGIN).status_code == 201


@pytest.mark.parametrize(("files", "fields", "status"), [
    (_files("different.txt", b"not markdown", "text/plain"), {}, 415),
    (_files("page.html", HTML, "text/html"), {}, 415),
    (_files("drawing.svg", b"<svg/>", "image/svg+xml"), {}, 415),
    (_files("invalid.md", b"\xff", "text/markdown"), {}, 415),
    (_files("invalid.md", b"ok", "text/html"), {}, 415),
    (_files("new.md", b"ok", "text/markdown"), {"channelId": "private-channel"}, 422),
    (_files("new.md", b"ok", "text/markdown"), {"threadId": "private-thread"}, 422),
    (_files("new.md", b"ok", "text/markdown"), {"title": "replacement"}, 400),
    (_files("new.md", b"ok", "text/markdown"), {"note": "text\x00control"}, 400),
    (_files("new.md", b"ok", "text/markdown"), {"note": "x" * (16 * 1024 + 1)}, 400),
])
def test_rejected_version_leaves_old_manifest_and_snapshot_unchanged(home, files, fields, status):
    owner = home["owner"]
    path, first, first_bytes = _text_artifact(home)
    before = owner.get(f"{path}/versions").json()
    rejected = owner.post(f"{path}/files", files=files, data=fields)
    assert rejected.status_code == status, rejected.text
    assert owner.get(path).json() == first
    assert owner.get(f"{path}/versions").json() == before
    assert owner.get(f"{path}/file").content == first_bytes


def test_note_can_contain_line_breaks_and_more_than_a_title(home):
    owner = home["owner"]
    path, _, _ = _text_artifact(home)
    note = "Notes\r\n" + "新版本说明" * 50
    appended = owner.post(f"{path}/files", files=_files("next.md", b"next", "text/markdown"), data={"note": note})
    assert appended.status_code == 201, appended.text
    assert owner.get(f"{path}/versions").json()["versions"][0]["note"] == note


def test_append_refuses_html_image_structured_and_unknown_ids(home):
    owner, root = home["owner"], home["root"]
    path = f"{home['base']}/artifacts"
    legacy = ArtifactStore(root).publish("Legacy", "<p>legacy</p>")["id"]
    private = owner.post(f"{path}/files", files=_files()).json()["id"]
    image = ArtifactStore(root, desktop_uploads=True).publish_file("Image", "image.png", "image/png", _png())["id"]
    document = owner.post(path, json={"title": "Document", "content": {"kind": "document", "markdown": "Original"}})
    assert document.status_code == 201, document.text
    document_id = document.json()["id"]
    append = _files("new.txt", b"next", "text/plain")
    for artifact_id in (legacy, private, image, document_id):
        assert owner.post(f"{path}/{artifact_id}/files", files=append).status_code == 415
        [untouched] = owner.get(f"{path}/{artifact_id}/versions").json()["versions"]
        assert untouched["version"] == 1
    photo = owner.get(f"{path}/{image}").json()
    assert photo["canEdit"] is False and photo["versioned"] is False
    assert "text" not in photo["content"] and photo["content"]["contentType"] == "image/png"
    assert owner.post(f"{path}/missing/files", files=append).status_code == 404


def test_append_body_limit_fails_before_changing_history(home, monkeypatch):
    owner = home["owner"]
    path, first, _ = _text_artifact(home)
    monkeypatch.setenv("VORTOONE_ARTIFACT_MAX_BYTES", "12")
    rejected = owner.post(f"{path}/files", files=_files("new.md", b"next" * 4, "text/markdown"))
    assert rejected.status_code == 413
    assert owner.get(path).json() == first


def test_append_failure_and_retry_keep_orphan_versions_out_of_reads(home, monkeypatch):
    owner, root = home["owner"], home["root"]
    path, first, first_bytes = _text_artifact(home)
    file_dir = ArtifactStore(root, desktop_uploads=True).root / first["id"]
    unpublished = file_dir / "upload-v2.bin"
    unpublished.write_bytes(b"left by an interrupted write")
    assert owner.get(f"{path}/file?version=2").status_code == 404
    assert owner.get(f"{path}?version=2").status_code == 404

    original_write = ArtifactStore._replace_upload_bytes

    def fail_manifest(file, data):
        if file.name == "meta.json":
            raise OSError("private fixture path")
        original_write(file, data)

    append = _files("new.md", b"second", "text/markdown")
    with monkeypatch.context() as changed:
        changed.setattr(ArtifactStore, "_replace_upload_bytes", staticmethod(fail_manifest))
        failed = owner.post(f"{path}/files", files=append)
    assert failed.status_code == 503 and "private fixture path" not in failed.text
    assert not unpublished.exists()
    assert owner.get(path).json() == first
    assert owner.get(f"{path}/file").content == first_bytes
    assert owner.get(f"{path}/download?version=2").status_code == 404

    retried = owner.post(f"{path}/files", files=append)
    assert retried.status_code == 201 and retried.json()["version"] == 2
    assert owner.get(f"{path}/file?version=2").content == b"second"
    assert owner.get(f"{path}/file?version=1").content == first_bytes


def test_concurrent_artifact_store_writers_serialize_versions(home):
    owner, root = home["owner"], home["root"]
    path, first, first_bytes = _text_artifact(home)
    a = ArtifactStore(root, desktop_uploads=True)
    b = ArtifactStore(root, desktop_uploads=True)
    artifact_id = first["id"]
    append = _files("new.md", b"test", "text/markdown")
    with home_maintenance(root):
        assert owner.post(f"{path}/files", files=append).status_code == 409
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(a.append_uploaded_file, artifact_id, "a.md", "text/markdown", b"a", "a")
        two = pool.submit(b.append_uploaded_file, artifact_id, "b.md", "text/markdown", b"b", "b")
        changed = {row["version"]: row["name"] for row in (one.result(), two.result())}

    assert set(changed) == {2, 3}
    assert owner.get(f"{path}/file?version=1").content == first_bytes
    for version in (2, 3):
        assert owner.get(f"{path}/file?version={version}").content == changed[version][0].encode()
    [latest, previous, _] = owner.get(f"{path}/versions").json()["versions"]
    assert (latest["version"], previous["version"]) == (3, 2)
    assert {latest["note"], previous["note"]} == {"a", "b"}
