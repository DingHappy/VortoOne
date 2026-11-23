"""Persist typed source and history for owner-private documents and tables."""
import csv
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.agents.tools.artifacts import ArtifactStore
from tests.unit.test_member_login import ORIGIN, TOKEN, _join, home  # noqa: F401 - fixture


def _create(home, content):
    result = home["owner"].post(home["base"] + "/artifacts", json={
        "title": "Private source",
        "content": content,
    })
    assert result.status_code == 201, result.text
    assert result.headers["cache-control"] == "no-store"
    return f"{home['base']}/artifacts/{result.json()['id']}", result.json()


@pytest.mark.parametrize(("source", "edited", "extension"), [
    ({"kind": "document", "markdown": "# Original\n"}, {"kind": "document", "markdown": "## Edited 文档\n"}, "md"),
    ({"kind": "table", "columns": ["Name", "Note"], "rows": [["A", "old"]]},
     {"kind": "table", "columns": ["Name", "Note"], "rows": [["A", "new, quoted\nline"]]}, "csv"),
])
def test_document_and_table_show_original_and_new_versions(home, source, edited, extension):
    owner = home["owner"]
    path, original = _create(home, source)
    assert original["content"] == source
    assert original["version"] == original["shownVersion"] == 1
    assert original["canEdit"] and original["versioned"]
    assert owner.get(home["base"] + "/artifacts", params={"kind": source["kind"]}).json()["counts"] == {
        source["kind"]: 1,
    }

    response = owner.post(path + "/versions", json={
        "content": edited, "baseVersion": 1, "note": "  Review changes  ", "title": "  Revised title  ",
    })
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    latest = response.json()
    assert latest["content"] == edited and latest["title"] == "Revised title"
    assert latest["id"] == original["id"]
    assert latest["version"] == latest["shownVersion"] == 2
    assert owner.get(path).json() == latest
    older = owner.get(path, params={"version": 1}).json()
    assert older["shownVersion"] == 1 and older["version"] == 2
    assert older["content"] == source
    history = owner.get(path + "/versions").json()["versions"]
    assert [(item["version"], item["note"], item["author"]) for item in history] == [
        (2, "Review changes", original["createdBy"]), (1, None, original["createdBy"]),
    ]
    for version, value in ((1, source), (2, edited)):
        download = owner.get(path + "/download", params={"version": version})
        assert download.status_code == 200
        raw = owner.get(download.json()["url"])
        assert raw.status_code == 200
        assert raw.headers["content-disposition"].endswith(f".{extension}")
        assert raw.headers["x-content-type-options"] == "nosniff"
        if extension == "md":
            assert raw.text == value["markdown"]
        else:
            assert list(csv.reader(io.StringIO(raw.text))) == [value["columns"], *value["rows"]]
    assert owner.get(path + "/file", params={"format": "pdf"}).status_code == 415


def test_stale_edits_bad_source_and_member_requests_never_persist(home):
    owner = home["owner"]
    path, original = _create(home, {"kind": "document", "markdown": "First"})
    payload = {"content": {"kind": "document", "markdown": "second"}, "baseVersion": 1}
    assert owner.post(path + "/versions", json=payload).status_code == 200
    assert owner.post(path + "/versions", json=payload).json()["error"]["code"] == "artifact.version_conflict"
    assert owner.post(path + "/versions", json={**payload, "baseVersion": True}).status_code == 400
    wrong = {"kind": "table", "columns": ["n"], "rows": [["cell"]]}
    assert owner.post(path + "/versions", json={"baseVersion": 2, "content": wrong}).status_code == 422
    assert owner.post(path + "/versions", json={"baseVersion": 2, "content": {**wrong, "rows": [[]]}}).status_code == 400
    assert owner.post(path + "/versions", json={**payload, "baseVersion": 2, "note": "bad\x00"}).status_code == 400
    assert owner.get(path + "/versions").json()["versions"][0]["version"] == 2

    member = _join(home)
    assert member.post(path + "/versions", json={**payload, "baseVersion": 2}).status_code == 403
    assert member.get(path).status_code == 403
    anonymous = TestClient(home["app"])
    assert anonymous.post(path + "/versions", json=payload).status_code == 401
    assert anonymous.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    assert anonymous.post(path + "/versions", json=payload, headers={"Origin": "https://other.example"}).status_code == 403
    assert anonymous.get(path, headers=ORIGIN).json()["id"] == original["id"]

    other = ArtifactStore(home["root"]).publish("Public HTML", "<p>keep</p>")
    unrelated = f"{home['base']}/artifacts/{other['id']}/versions"
    assert owner.post(unrelated, json=payload).status_code == 404
    assert ArtifactStore(home["root"]).html(other["id"]) == "<p>keep</p>"


def test_failed_manifest_commit_does_not_advance_version_and_can_be_retried(home, monkeypatch):
    owner = home["owner"]
    path, original = _create(home, {"kind": "document", "markdown": "First"})
    store = ArtifactStore(home["root"], desktop_uploads=True)
    metadata = store.root / original["id"] / "meta.json"
    payload = {"content": {"kind": "document", "markdown": "Second"}, "baseVersion": 1}
    from src.agents.tools import artifacts

    replace = artifacts.os.replace

    def refuse_metadata(source, destination):
        if Path(destination) == metadata:
            raise OSError("write failed")
        return replace(source, destination)

    with monkeypatch.context() as m:
        m.setattr(artifacts.os, "replace", refuse_metadata)
        assert owner.post(path + "/versions", json=payload).status_code == 503
    assert owner.get(path).json() == original
    assert owner.get(path, params={"version": 2}).status_code == 404
    assert not (metadata.parent / "v2.json").exists()
    retry = owner.post(path + "/versions", json=payload)
    assert retry.status_code == 200 and retry.json()["version"] == 2



@pytest.mark.parametrize("corrupt", ['{"kind":"document"}', r'{"kind":"document","markdown":"\ud800"}'])
def test_damaged_version_never_returns_an_invalid_document_or_unhandled_download(home, corrupt):
    owner = home["owner"]
    path, original = _create(home, {"kind": "document", "markdown": "First"})
    store = ArtifactStore(home["root"], desktop_uploads=True)
    (store.root / original["id"] / "v1.json").write_text(corrupt, encoding="utf-8")
    assert owner.get(path).status_code == 503
    assert owner.get(path + "/file").status_code == 409
