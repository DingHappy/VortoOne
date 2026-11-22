"""The Desktop contract exposes the existing HTML history to the signed-in owner."""

import json
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi.testclient import TestClient

from src.agents.tools.artifacts import ArtifactStore
from src.web import app_artifacts
from tests.unit.test_member_login import _join, home  # noqa: F401 — fixture


@pytest.fixture
def artifact(home, monkeypatch):  # noqa: F811
    monkeypatch.setenv("VORTOONE_WORKSPACE_SCOPE", "general")
    store = ArtifactStore(home["root"])
    first = store.publish("制品", "<p>初稿</p>")
    aid = first["id"]
    store.publish("制品", "<p>新版</p>", aid)
    return home, store, aid, f"{home['base']}/artifacts/{aid}"


def test_versions_pin_and_direct_attachment(artifact):
    home, store, aid, url = artifact
    client = home["owner"]
    response = client.get(f"{url}/versions")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    versions = response.json()["versions"]
    assert [entry["version"] for entry in versions] == [2, 1]
    assert all(entry["author"]["type"] == "agent" and entry["note"] is None for entry in versions)

    assert store.pin(aid, 1)["pinned"] == 1
    response = client.get(f"{url}/file")
    assert response.status_code == 200
    assert response.text == "<p>初稿</p>"
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get(f"{url}/file?version=2").text == "<p>新版</p>"
    assert client.get(f"{url}/file?version=99").status_code == 404
    unsupported = client.get(f"{url}/file?format=pdf")
    assert unsupported.status_code == 415 and unsupported.json()["error"]["code"] == "artifact.format_unavailable"


def test_timed_download_binds_version_and_expires(artifact, monkeypatch):
    home, store, aid, path = artifact
    client = home["owner"]
    now = int(app_artifacts.time.time())
    result = client.get(f"{path}/download")
    assert result.status_code == 200
    link = result.json()["url"]
    parts = urlsplit(link)
    query = parse_qs(parts.query)
    [expires] = query["expires"]
    assert now + 300 <= int(expires) <= now + 301
    assert parts.netloc == "" and parts.path == f"{path}/file"
    assert client.get(f"{path}/download?version=99").status_code == 404
    store.publish("制品", "<p>第三稿</p>", aid)
    assert client.get(link).text == "<p>新版</p>"

    modified = urlencode({**query, "version": ["1"]}, doseq=True)
    assert client.get(f"{parts.path}?{modified}").status_code == 403
    assert client.get(f"{parts.path}?version=2&expires={expires}").status_code == 403
    monkeypatch.setattr(app_artifacts.time, "time", lambda: int(expires))
    assert client.get(link).status_code == 403


@pytest.mark.parametrize("endpoint", ["versions", "download", "file"])
def test_artifact_content_scope_and_member_isolation(artifact, endpoint):
    home, _store, _aid, url = artifact
    member = _join(home)
    guest = TestClient(home["app"])
    assert member.get(f"{url}/{endpoint}").status_code == 403
    assert guest.get(f"{url}/{endpoint}").status_code == 401
    client = home["owner"]
    assert client.get(f"{home['base']}/artifacts/missing/{endpoint}").status_code == 404
    assert client.get(f"{url.replace(home['org'], 'different-home')}/{endpoint}").status_code == 404


def test_legacy_single_version_and_missing_modern_snapshot(artifact):
    home, store, aid, _url = artifact
    path = store.root / "legacy"
    path.mkdir()
    (path / "meta.json").write_text(json.dumps({"id": "legacy", "version": 1, "title": "旧制品"}))
    (path / "index.html").write_text("<p>旧制品</p>")
    client = home["owner"]
    legacy = f"{home['base']}/artifacts/legacy"
    assert client.get(f"{legacy}/file?version=1").text == "<p>旧制品</p>"
    assert client.get(f"{legacy}/file?version=2").status_code == 404
    (store.root / aid / "v1.html").unlink()
    assert client.get(f"{home['base']}/artifacts/{aid}/file?version=1").status_code == 404
