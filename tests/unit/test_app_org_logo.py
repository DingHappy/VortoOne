"""Workspace branding follows the authenticated workspace and commits atomically."""

import base64
from concurrent.futures import ThreadPoolExecutor
import struct
from threading import Event
import zlib

import pytest
from fastapi.testclient import TestClient

from src.utils.state_dir import state_dir
from src.web import app_org_logo
from tests.unit.test_member_login import ORIGIN, PASSWORD, TOKEN, _group, _invite, _join, home  # noqa: F401


def _chunk(kind, data):
    payload = kind + data
    return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload))


def _png(red=255, *, width=1, height=1, image=None, metadata=False, alpha=255):
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    if image is None:
        image = zlib.compress((b"\x00" + bytes((red, 0, 0, alpha)) * width) * height)
    text = _chunk(b"tEXt", b"Comment\x00synthetic private metadata") if metadata else b""
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + text + _chunk(b"IDAT", image) + _chunk(b"IEND", b"")


def _upload(image):
    return {"imageBase64": base64.b64encode(image).decode("ascii"), "contentType": "image/png"}


def _data_url(image):
    return "data:image/png;base64," + _upload(image)["imageBase64"]


def _workspace(client):
    response = client.get("/api/app/me")
    assert response.status_code == 200
    return response.json()["organizations"][0]


def test_owner_changes_logo_for_current_members_only(home, monkeypatch):
    path = home["base"] + "/logo"
    owner = home["owner"]
    role = _group(home, "Workspace editor", ["configure"])
    member = _join(home, roleIds=[role])
    pending = TestClient(home["app"], headers=ORIGIN)
    invitation = _invite(home, "pending@example.com")
    assert pending.post("/api/auth/sign-up/email",
                        json={"name": "Pending", "email": "pending@example.com", "password": PASSWORD},
                        headers={"x-invitation-token": invitation}).status_code == 200
    guest = TestClient(home["app"])
    assert _workspace(owner)["logo"] is None
    changed = owner.put(path, json=_upload(_png(metadata=True)))
    assert changed.status_code == 200
    assert changed.json() == {"logo": _data_url(_png())}
    assert changed.headers["Cache-Control"] == "no-store"
    assert _workspace(owner)["logo"] == _workspace(member)["logo"] == _data_url(_png())
    assert member.get("/api/app/me").headers["Cache-Control"] == "no-store"
    assert pending.get("/api/app/me").json()["organizations"] == []
    assert guest.get("/api/app/me").status_code == guest.put(path, json=_upload(_png())).status_code == 401
    assert member.put(path, json=_upload(_png())).status_code == member.delete(path).status_code == 403
    assert owner.put("/api/app/organizations/foreign/logo", json=_upload(_png())).status_code == 404

    member_id = member.get("/api/app/me").json()["user"]["id"]
    assert owner.delete(f"{home['base']}/members/{member_id}").status_code == 204
    revoked = member.get("/api/app/me")
    assert revoked.status_code == 401 or revoked.json()["organizations"] == []
    assert _workspace(owner)["logo"] == _data_url(_png())

    with monkeypatch.context() as no_auth:
        no_auth.delenv("VORTOONE_API_TOKEN")
        assert "logo" not in _workspace(guest)
        assert guest.delete(path).status_code == 403
    assert owner.delete(path).status_code == owner.delete(path).status_code == 204
    assert _workspace(owner)["logo"] is None


def test_accepts_the_normal_canvas_dimensions_and_preserves_transparency(home):
    image = _png(width=512, height=512, alpha=0)
    owner = home["owner"]
    response = owner.put(home["base"] + "/logo", json=_upload(image))
    assert response.status_code == 200
    assert response.json()["logo"] == _workspace(owner)["logo"] == _data_url(image)


def test_browser_cookie_requires_same_origin_to_change_logo(home):
    path = home["base"] + "/logo"
    browser = TestClient(home["app"])
    assert browser.post("/api/auth/sign-in/email", json={"email": "", "password": TOKEN}).status_code == 200
    assert browser.put(path, json=_upload(_png())).status_code == 403
    assert browser.put(path, json=_upload(_png()), headers={"Origin": "https://other.example"}).status_code == 403
    assert browser.put(path, json=_upload(_png()), headers=ORIGIN).status_code == 200
    assert _workspace(browser)["logo"] == _data_url(_png())
    assert browser.delete(path, headers={"Origin": "https://other.example"}).status_code == 403
    assert browser.delete(path, headers=ORIGIN).status_code == 204


@pytest.mark.parametrize(("body", "status"), [
    (_upload(_png(image=b"bad deflate")), 400),
    (_upload(_png(image=zlib.compress(b"\x00" * 1000))), 400),
    (_upload(_png()[:29] + b"\x00\x00\x00\x00" + _png()[33:]), 400),
    (_upload(_png(width=513)), 413),
    ({"imageBase64": "not base64!", "contentType": "image/png"}, 400),
    ({"imageBase64": "https://example.com/logo.png", "contentType": "image/png"}, 400),
    ({"imageBase64": "YQ==", "contentType": "image/svg+xml"}, 400),
    ({"imageBase64": "A" * app_org_logo.MAX_REQUEST_BYTES, "contentType": "image/png"}, 413),
])
def test_invalid_replacements_leave_the_current_logo_in_place(home, body, status):
    owner = home["owner"]
    path = home["base"] + "/logo"
    assert owner.put(path, json=_upload(_png())).status_code == 200
    denied = owner.put(path, json=body)
    assert denied.status_code == status
    assert _workspace(owner)["logo"] == _data_url(_png())


def test_replace_failure_preserves_previous_logo(home, monkeypatch):
    owner = home["owner"]
    path = home["base"] + "/logo"
    assert owner.put(path, json=_upload(_png())).status_code == 200
    replace = app_org_logo.os.replace

    def disk_failure(source, target):
        if target.name == "logo.png":
            raise OSError("synthetic disk failure")
        return replace(source, target)

    with monkeypatch.context() as failure:
        failure.setattr(app_org_logo.os, "replace", disk_failure)
        assert owner.put(path, json=_upload(_png(127))).status_code == 409
    directory = state_dir(home["root"]) / "workspace-branding"
    assert sorted(file.name for file in directory.iterdir()) == ["logo.png"]
    assert _workspace(owner)["logo"] == _data_url(_png())
    (directory / "logo.png").write_bytes(b"corrupt local state")
    assert _workspace(owner)["logo"] is None
    assert owner.delete(path).status_code == 204


def test_a_conflicting_delete_cannot_interrupt_atomic_replacement(home, monkeypatch):
    owner = home["owner"]
    path = home["base"] + "/logo"
    assert owner.put(path, json=_upload(_png())).status_code == 200
    replace = app_org_logo.os.replace
    started, release = Event(), Event()

    def pause_replace(source, target):
        if target.name == "logo.png":
            started.set()
            assert release.wait(5)
        return replace(source, target)

    monkeypatch.setattr(app_org_logo.os, "replace", pause_replace)
    with ThreadPoolExecutor() as workers:
        pending = workers.submit(owner.put, path, json=_upload(_png(127)))
        try:
            assert started.wait(5)
            assert _workspace(owner)["logo"] == _data_url(_png())
            assert owner.delete(path).status_code == 409
        finally:
            release.set()
        assert pending.result(timeout=5).status_code == 200
    assert _workspace(owner)["logo"] == _data_url(_png(127))
