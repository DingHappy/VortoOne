import asyncio
from contextlib import asynccontextmanager
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from src.web.routers import desktop_releases
from src.web.server import app


_URL = "https://github.com/DingHappy/VortoOne/releases/download/v1.2.3/VortoOne_1.2.3_aarch64.dmg"
_PUBLISHED = "2026-11-15T06:51:47Z"
_SHA256 = "2a" * 32
_RESULT = {"version": "1.2.3", "url": _URL, "sha256": _SHA256, "publishedAt": _PUBLISHED, "size": 4096}


@pytest.fixture
def release():
    return {"tag_name": "v1.2.3", "draft": False, "prerelease": False, "published_at": _PUBLISHED,
            "assets": [{"name": "VortoOne_1.2.3_aarch64.dmg", "browser_download_url": _URL,
                        "state": "uploaded", "digest": "sha256:" + _SHA256, "size": 4096}]}


@pytest.fixture
def upstream(monkeypatch, release):
    source = SimpleNamespace(raw=json.dumps(release).encode(), status=200, calls=[])
    monkeypatch.setattr(desktop_releases, "_cache", (0, 503, None))
    monkeypatch.setenv("VORTOONE_API_TOKEN", "fixture-owner-token")

    class Client:
        async def readexactly(self, size):
            assert size == desktop_releases._RESPONSE_LIMIT + 1
            if len(source.raw) < size:
                raise asyncio.IncompleteReadError(source.raw, size)
            return source.raw[:size]

        @asynccontextmanager
        async def get(self, url, *, headers, allow_redirects):
            source.calls.append((url, headers, allow_redirects))
            yield SimpleNamespace(status=source.status, content=self)

    @asynccontextmanager
    async def session(*, timeout, trust_env, auto_decompress):
        assert timeout.total == 5
        assert not trust_env and not auto_decompress
        yield Client()

    monkeypatch.setattr(desktop_releases.aiohttp, "ClientSession", session)
    return source


def test_public_desktop_release_serves_checked_metadata_once(upstream):
    client = TestClient(app)
    path = "/api/desktop/latest"
    result = client.get(path, params={"target": "darwin", "arch": "aarch64"})
    assert result.status_code == 200 and result.json() == _RESULT
    assert result.headers["cache-control"] == "public, max-age=60"
    assert client.get(path, params={"target": "darwin", "arch": "arm64"}).json() == _RESULT
    assert client.post(path, json={}).status_code == 401

    url, headers, redirects = upstream.calls[0]
    assert len(upstream.calls) == 1
    assert url == "https://api.github.com/repos/DingHappy/VortoOne/releases/latest"
    assert not redirects
    assert set(headers) == {"Accept", "Accept-Encoding", "User-Agent"}


@pytest.mark.parametrize(("target", "arch"), [("darwin", "x86_64"), ("linux", "x86_64"), ("windows", "aarch64")])
def test_unpublished_platform_does_not_query_upstream(upstream, target, arch):
    result = TestClient(app).get("/api/desktop/latest", params={"target": target, "arch": arch})
    assert result.status_code == 404 and not upstream.calls


def test_no_desktop_release_for_other_first_party_artifact(upstream, release):
    release["assets"][0]["name"] = "VortoOne_1.2.3_aarch64.app.tar.gz"
    upstream.raw = json.dumps(release).encode()
    result = TestClient(app).get("/api/desktop/latest?target=darwin&arch=aarch64")
    assert result.status_code == 404


@pytest.mark.parametrize("failure", ["digest", "url", "redirect", "oversized", "unavailable"])
def test_unverified_download_is_never_offered(upstream, release, failure):
    if failure == "digest":
        release["assets"][0]["digest"] = None
    elif failure == "url":
        release["assets"][0]["browser_download_url"] = "https://example.test/other.dmg"
    elif failure == "redirect":
        upstream.status = 302
    elif failure == "unavailable":
        upstream.status = 429
    upstream.raw = (b" " * (desktop_releases._RESPONSE_LIMIT + 1)
                    if failure == "oversized" else json.dumps(release).encode())
    client = TestClient(app)
    for _ in range(2):
        result = client.get("/api/desktop/latest?target=darwin&arch=aarch64")
        assert result.status_code == 404
        assert result.headers["cache-control"] == "no-store"
        assert result.json()["error"]["code"] == "desktop.release_unavailable"
    assert len(upstream.calls) == 1
