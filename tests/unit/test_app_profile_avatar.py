"""Desktop profile photos use only the caller's avatar and the active member roster."""

import base64
import struct
import zlib

from fastapi.testclient import TestClient
import pytest

from src.utils.state_dir import state_dir
from tests.unit.test_member_login import ORIGIN, PASSWORD, TOKEN, _invite, _join
from tests.unit.test_member_login import home  # noqa: F401

_PORTRAIT = "1.m.s2.short.brown.none.none.none.none.none.none.none.soft.none.dark.tee.navy.smile.sky"
# A synthetic, solid color 2 × 2 JPEG, cropped and encoded once for the fixture.
_JPEG = (
    "/9j/4AAQSkZJRgABAQAASABIAAD/4QBMRXhpZgAATU0AKgAAAAgAAYdpAAQAAAABAAAAGgAAAAAAA6ABAAMAAAABAAEAAKACAAQAAAABAAAAAqADAAQ"
    "AAAABAAAAAgAAAAD/7QA4UGhvdG9zaG9wIDMuMAA4QklNBAQAAAAAAAA4QklNBCUAAAAAABDUHYzZjwCyBOmACZjs+EJ+/8AAEQgAAgACAwEiAAIRAQ"
    "MRAf/EAB8AAAEFAQEBAQEBAAAAAAAAAAABAgMEBQYHCAkKC//EALUQAAIBAwMCBAMFBQQEAAABfQECAwAEEQUSITFBBhNRYQcicRQygZGhCCNCscEVU"
    "tHwJDNicoIJChYXGBkaJSYnKCkqNDU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6g4SFhoeIiYqSk5SVlpeYmZqio6Slpqeoqaqy"
    "s7S1tre4ubrCw8TFxsfIycrS09TV1tfY2drh4uPk5ebn6Onq8fLz9PX29/j5+v/EAB8BAAMBAQEBAQEBAQEAAAAAAAABAgMEBQYHCAkKC//EALURAAI"
    "BAgQEAwQHBQQEAAECdwABAgMRBAUhMQYSQVEHYXETIjKBCBRCkaGxwQkjM1LwFWJy0QoWJDThJfEXGBkaJicoKSo1Njc4OTpDREVGR0hJSlNUVVZXWF"
    "laY2RlZmdoaWpzdHV2d3h5eoKDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uLj5OXm5+jp6vLz9PX29"
    "/j5+v/bAEMAAgICAgICAwICAwUDAwMFBgUFBQUGCAYGBgYGCAoICAgICAgKCgoKCgoKCgwMDAwMDA4ODg4ODw8PDw8PDw8PD//bAEMBAgICBAQEBwQE"
    "BxALCQsQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEP/dAAQAAf/aAAwDAQACEQMRAD8A+uKKKK/zjP7UP//"
    "Z"
)


def _png(width=2, height=2, color=b"\xf8\xa0\x30"):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    pixels = (b"\0" + color * width) * height
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")


def _upload(data=None, **more):
    return {"imageBase64": base64.b64encode(data if data is not None else _png()).decode(),
            "contentType": "image/png", **more}


def _avatars(client, base, endpoint):
    return {row["userId"]: row["avatarUrl"] for row in client.get(base + "/" + endpoint).json()[endpoint]}


def test_people_keep_their_own_avatar_and_can_remove_it(home):
    owner, base = home["owner"], home["base"]
    member = _join(home)
    owner_id = owner.get("/api/app/me").json()["user"]["id"]
    member_id = member.get("/api/app/me").json()["user"]["id"]
    path = base + "/me/avatar"
    drawn = owner.put(path, json=_upload(portrait=_PORTRAIT))
    assert drawn.status_code == 200 and drawn.headers["cache-control"] == "no-store"
    owner_url = drawn.json()["avatarUrl"]
    assert owner_url.endswith("#px=" + _PORTRAIT)
    cropped = member.put(path, json={"imageBase64": _JPEG, "contentType": "image/jpeg"})
    assert cropped.status_code == 200
    member_url = cropped.json()["avatarUrl"]
    assert member_id in member_url and "#" not in member_url

    assert owner.patch(base + "/me", json={"timezone": "Europe/Paris"}).status_code == 200
    assert member.patch(base + "/me", json={"timezone": "Asia/Tokyo"}).status_code == 200
    people = owner.get(base + "/people").json()["people"]
    assert {row["userId"]: row["timezone"] for row in people} == {owner_id: "Europe/Paris", member_id: "Asia/Tokyo"}
    assert _avatars(member, base, "members") == {owner_id: owner_url, member_id: member_url}
    assert _avatars(owner, base, "people") == {owner_id: owner_url, member_id: member_url}
    assert owner.put("/api/app/me/work-area", json={"area": "dev"}).status_code == 204
    assert member.put("/api/app/me/work-area", json={"area": "marketing"}).status_code == 204
    owner_profile = owner.get("/api/app/me").json()["user"]
    member_profile = member.get("/api/app/me").json()["user"]
    assert (owner_profile["image"], owner_profile["workArea"]) == (owner_url, "dev")
    assert (member_profile["image"], member_profile["workArea"]) == (member_url, "marketing")
    picture = member.get(owner_url)
    assert (picture.status_code, picture.headers["content-type"], picture.content) == (200, "image/png", _png())
    assert picture.headers["cache-control"] == "no-store" and picture.headers["x-content-type-options"] == "nosniff"
    assert picture.headers["cross-origin-resource-policy"] == "same-origin"
    picture = owner.get(member_url)
    assert (picture.status_code, picture.headers["content-type"], picture.content) == (
        200, "image/jpeg", base64.b64decode(_JPEG))

    replacement = owner.put(path, json=_upload(_png(color=b"\x01\x02\x03")))
    assert replacement.status_code == 200 and replacement.json()["avatarUrl"] != owner_url
    assert owner.get(owner_url).status_code == 404
    assert member.delete(path).status_code == 204
    assert member.delete(path).status_code == 204
    assert owner.get(member_url).status_code == 404
    assert _avatars(member, base, "people") == {owner_id: replacement.json()["avatarUrl"], member_id: None}
    assert "profile-avatars/" in (state_dir(home["root"]) / ".gitignore").read_text()


def test_avatar_rejects_guests_cross_origin_and_accounts_not_on_the_roster(home):
    owner, base = home["owner"], home["base"]
    path = base + "/me/avatar"
    member = _join(home)
    made = member.put(path, json=_upload())
    assert made.status_code == 200
    avatar = made.json()["avatarUrl"]
    visitor = TestClient(home["app"])
    assert visitor.get(avatar).status_code == 401
    assert visitor.put(path, json=_upload()).status_code == 401
    assert visitor.delete(path).status_code == 401

    token = _invite(home, "joining@example.test")
    pending = TestClient(home["app"], headers=ORIGIN)
    assert pending.post("/api/auth/sign-up/email", headers={"x-invitation-token": token}, json={
        "name": "要加入的人", "email": "joining@example.test", "password": PASSWORD,
    }).status_code == 200
    assert pending.get(avatar).status_code == 403
    assert pending.put(path, json=_upload()).status_code == 403
    assert pending.delete(path).status_code == 403

    cookie = TestClient(home["app"])
    assert cookie.post("/api/auth/login", headers=ORIGIN, json={"token": TOKEN}).status_code == 200
    other = {"Origin": "https://other.example.test"}
    assert cookie.get(avatar).status_code == 200
    assert cookie.get(avatar, headers=other).status_code == 403
    assert cookie.put(path, headers=other, json=_upload()).status_code == 403
    assert cookie.delete(path, headers=other).status_code == 403
    assert member.get(avatar, headers=other).status_code == 403
    assert member.put(path, headers=other, json=_upload()).status_code == 403
    assert member.delete(path, headers=other).status_code == 403

    assert cookie.put(path, headers=ORIGIN, json=_upload()).status_code == 200
    assert cookie.delete(path, headers=ORIGIN).status_code == 204
    member_id = member.get("/api/app/me").json()["user"]["id"]
    assert owner.delete(f"{base}/members/{member_id}").status_code == 204
    assert owner.get(avatar).status_code == 404
    assert member.get(avatar).status_code == 401


@pytest.mark.parametrize("body", [
    {}, _upload(userId="someone-else"), _upload(contentType="image/svg+xml"),
    _upload(portrait="javascript:alert(0)"), _upload(_png(height=3)), _upload(_png(513, 513)),
    _upload(b"<svg/>"), _upload(_png()[:32]), _upload(_png(), contentType="image/jpeg"),
    {"imageBase64": "%%%%", "contentType": "image/png"},
])
def test_invalid_avatar_does_not_replace_a_saved_avatar(home, body):
    owner = home["owner"]
    path = home["base"] + "/me/avatar"
    seeded = owner.put(path, json=_upload())
    assert seeded.status_code == 200
    refused = owner.put(path, json=body)
    assert refused.status_code == 400 and refused.headers["cache-control"] == "no-store"
    assert owner.get(seeded.json()["avatarUrl"]).content == _png()


def test_avatar_upload_bounds_and_authenticated_owner_proof(home, monkeypatch):
    owner, base = home["owner"], home["base"]
    path = base + "/me/avatar"
    overflow = owner.put(path, json=_upload(b"x" * (769 * 1024)))
    assert overflow.status_code == 413
    assert owner.put(path, content="{}", headers={"content-type": "text/plain"}).status_code == 400
    monkeypatch.delenv("VORTOONE_API_TOKEN", raising=False)
    assert owner.put(path, json=_upload()).status_code == 403
    assert owner.delete(path).status_code == 403
    assert not (state_dir(home["root"]) / "profile-avatars").exists()


def test_avatar_never_writes_through_a_symlinked_media_directory(home, tmp_path):
    destination = tmp_path / "other-directory"
    destination.mkdir()
    directory = state_dir(home["root"]) / "profile-avatars"
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory.symlink_to(destination, target_is_directory=True)
    owner = home["owner"]
    path = home["base"] + "/me/avatar"
    assert owner.put(path, json=_upload()).status_code == 409
    assert owner.delete(path).status_code == 409
    assert not list(destination.iterdir())


def test_tokenless_development_does_not_disclose_existing_portrait_urls(home, monkeypatch):
    owner, base = home["owner"], home["base"]
    member = _join(home)
    for client in (owner, member):
        response = client.put(base + "/me/avatar", json=_upload(portrait=_PORTRAIT))
        assert response.status_code == 200
    owner_id = owner.get("/api/app/me").json()["user"]["id"]
    member_id = member.get("/api/app/me").json()["user"]["id"]
    urls = _avatars(owner, base, "people")
    assert urls[owner_id].endswith("#px=" + _PORTRAIT)
    assert urls[member_id].endswith("#px=" + _PORTRAIT)

    monkeypatch.delenv("VORTOONE_API_TOKEN", raising=False)
    visitor = TestClient(home["app"])
    assert visitor.get("/api/app/me").json()["user"]["image"] is None
    assert _avatars(visitor, base, "people") == {owner_id: None, member_id: None}
    for url in urls.values():
        assert visitor.get(url).status_code == 403
