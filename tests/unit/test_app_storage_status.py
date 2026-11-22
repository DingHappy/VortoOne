from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.unit.test_member_login import ORIGIN, TOKEN, _group, _join, home  # noqa: F401 - pytest fixture

_BUCKET_ROUTE_METHODS = (
    ("PUT", ""),
    ("POST", "/test"),
    ("POST", "/migrate"),
)


def test_storage_status_describes_the_disk_without_paths_or_capacity(home):
    owner = home["owner"]
    response = owner.get(f"{home['base']}/storage")
    assert response.status_code == 200
    assert response.json() == {
        "kind": "agentes",
        "platform": {"kind": "disk", "folder": None},
        "s3": None,
        "migration": None,
    }
    assert response.headers["cache-control"] == "no-store"
    assert owner.get("/api/app/organizations/another-home/storage").status_code == 404
    migration = owner.get(f"{home['base']}/storage/migration")
    assert migration.status_code == 200
    assert migration.json() is None
    assert migration.headers["cache-control"] == "no-store"
    assert owner.get("/api/app/organizations/another-home/storage/migration").status_code == 404


def test_storage_requires_owner_proof_on_every_route(home, monkeypatch):
    storage = f"{home['base']}/storage"
    routes = (("GET", ""), ("GET", "/migration"), *_BUCKET_ROUTE_METHODS)
    configure = _group(home, "配置", ["configure"])
    member = _join(home, roleIds=[configure])
    assert "configure" in member.get("/api/app/me").json()["organizations"][0]["permissions"]
    guest = TestClient(home["app"])
    browser = TestClient(home["app"])
    assert browser.post("/api/auth/login", json={"token": TOKEN}).status_code == 200

    for method, tail in routes:
        assert member.request(method, storage + tail).status_code == 403
        assert guest.request(method, storage + tail).status_code == 401
        assert browser.request(method, storage + tail, headers={"Origin": "https://other.test"}).status_code == 403
    assert browser.get(storage).status_code == 200
    assert browser.post(f"{storage}/test").status_code == 403
    assert browser.post(f"{storage}/test", headers=ORIGIN).status_code == 409

    monkeypatch.setenv("VORTOONE_API_TOKEN", "")
    assert guest.get(storage).status_code == 403


def test_unsupported_bucket_operations_do_not_use_or_save_secrets(home, monkeypatch):
    owner, storage = home["owner"], f"{home['base']}/storage"
    root = Path(home["root"])
    before = set(root.rglob("*"))

    def unexpected_connection(*args, **kwargs):
        pytest.fail("unsupported storage operation attempted to resolve an endpoint")

    monkeypatch.setattr("socket.getaddrinfo", unexpected_connection)
    for method, tail in _BUCKET_ROUTE_METHODS:
        response = owner.request(method, storage + tail, json={
            "kind": "s3", "s3": {"endpoint": "https://bucket.invalid", "secretAccessKey": "fixture-secret-only"},
        })
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "storage.s3_unavailable"
        assert "当前工作区使用服务器磁盘" in response.json()["error"]["message"]
        assert "fixture-secret-only" not in response.text

    assert set(root.rglob("*")) == before
    assert owner.get(storage).json()["s3"] is None
    assert owner.get(f"{storage}/migration").json() is None
