"""Desktop's notification settings reflect only the caller's configured destinations."""

import time

import pytest
from fastapi.testclient import TestClient

from src.gateway import mobile_push
from src.gateway.mobile_devices import MobileDevices
from tests.unit.test_member_login import ORIGIN, PASSWORD, TOKEN, _invite, _join
from tests.unit.test_member_login import home  # noqa: F401
from tests.unit.test_mobile_push import _key_pair


_DEFAULT_CATEGORIES = [{"id": "waiting", "on": True}, {"id": "replies", "on": True}]


def _register(store, name, token):
    device = store.issue(name)["device"]["id"]
    _, public_key = _key_pair()
    store.register_push(device, mobile_push.normalize_registration(token, public_key, "production"))
    return device


def test_only_owner_sees_configured_active_push_destinations(home, monkeypatch):
    owner, path = home["owner"], home["base"] + "/notification-prefs"
    member = _join(home)
    store = MobileDevices(home["root"])
    first = _register(store, "Phone 1", "ab" * 32)
    _register(store, "Same phone again", "ab" * 32)
    second = _register(store, "Phone 2", "cd" * 32)
    revoked = _register(store, "Revoked phone", "ef" * 32)
    _register(MobileDevices(home["root"], clock=lambda: time.time() - 31 * 86400),
              "Stale phone", "dc" * 32)
    store.issue("Connected without notifications")
    store.revoke(revoked)
    assert not any(device_id == first for device_id, _ in store.push_targets())

    monkeypatch.setattr(mobile_push, "configured", lambda: False)
    assert owner.get(path).json() == {"categories": [], "phones": 0}
    assert owner.put(path, json={"category": "replies", "on": False}).status_code == 409
    assert store.push_preferences() == {"waiting": True, "replies": True}
    monkeypatch.setattr(mobile_push, "configured", lambda: True)
    configured = owner.get(path)
    assert configured.status_code == 200
    assert configured.headers["cache-control"] == "no-store"
    assert configured.json() == {"categories": _DEFAULT_CATEGORIES, "phones": 2}
    assert member.get(path).json() == {"categories": [], "phones": 0}

    assert member.put(path, json={"category": "replies", "on": False}).status_code == 403
    assert store.push_preferences() == {"waiting": True, "replies": True}
    store.revoke(second)
    assert owner.get(path).json() == {"categories": _DEFAULT_CATEGORIES, "phones": 1}


def test_notification_destinations_require_current_member_or_owner_session(home, monkeypatch):
    path = home["base"] + "/notification-prefs"
    visitor = TestClient(home["app"])
    assert visitor.get(path).status_code == 401
    token = _invite(home, email="pending@example.test")
    pending = TestClient(home["app"], headers=ORIGIN)
    assert pending.post("/api/auth/sign-up/email", headers={"x-invitation-token": token}, json={
        "name": "Pending member", "email": "pending@example.test", "password": PASSWORD,
    }).status_code == 200
    assert pending.get(path).status_code == 403

    owner = TestClient(home["app"])
    assert owner.post("/api/auth/login", headers=ORIGIN, json={"token": TOKEN}).status_code == 200
    assert owner.get(path).status_code == 200
    foreign = {"Origin": "https://other.example.test"}
    assert owner.get(path, headers=foreign).status_code == 403
    assert owner.put(path, headers=foreign, json={"category": "waiting", "on": False}).status_code == 403
    assert visitor.put(path, json={"category": "waiting", "on": False}).status_code == 401
    assert _join(home).get(path, headers=foreign).status_code == 403
    assert owner.get(path.replace(home["org"], "unknown-space")).status_code == 404

    monkeypatch.delenv("VORTOONE_API_TOKEN", raising=False)
    assert visitor.get(path).status_code == 403


def test_corrupt_push_store_does_not_report_an_owner_phone_count(home, monkeypatch):
    owner, path = home["owner"], home["base"] + "/notification-prefs"
    member = _join(home)
    store = MobileDevices(home["root"])
    _register(store, "Phone", "ab" * 32)
    store.path.write_text("damaged store")
    monkeypatch.setattr(mobile_push, "configured", lambda: True)
    answer = owner.get(path)
    assert answer.status_code == 409
    assert answer.headers["cache-control"] == "no-store"
    assert answer.json() == {"error": {"code": "unavailable", "message": "无法读取手机通知登记"}}
    assert member.get(path).json() == {"categories": [], "phones": 0}
    assert owner.put(path, json={"category": "waiting", "on": False}).status_code == 409
    assert store.path.read_text() == "damaged store"


@pytest.mark.parametrize("body", [
    {"category": "mentions", "on": False},
    {"category": "tasks", "on": False},
    {"category": "replies", "on": "false"},
    {"category": "waiting", "on": False, "accountId": "another-account"},
])
def test_unimplemented_or_invalid_preferences_are_refused_without_writing(home, monkeypatch, body):
    monkeypatch.setattr(mobile_push, "configured", lambda: True)
    path = home["base"] + "/notification-prefs"
    assert home["owner"].put(path, json=body).status_code == 422
    assert home["owner"].get(path).json() == {"categories": _DEFAULT_CATEGORIES, "phones": 0}
    assert not MobileDevices(home["root"]).path.exists()


async def test_saved_preferences_control_only_their_mobile_pushes(home, monkeypatch):
    monkeypatch.setattr(mobile_push, "configured", lambda: True)
    sent = []

    async def send(registration, text):
        sent.append((registration["token"], text))
        return "ok"

    monkeypatch.setattr(mobile_push, "send", send)
    root = home["root"]
    owner, path = home["owner"], home["base"] + "/notification-prefs"
    _register(MobileDevices(root), "Phone", "ab" * 32)
    changed = owner.put(path, json={"category": "waiting", "on": False})
    assert changed.status_code == 200
    assert changed.headers["cache-control"] == "no-store"
    assert changed.json() == {"categories": [{"id": "waiting", "on": False}, _DEFAULT_CATEGORIES[1]], "phones": 1}
    assert owner.get(path).json() == changed.json()
    assert not await mobile_push.push_owner(root, "waiting disabled", category="waiting")
    assert await mobile_push.push_owner(root, "reply", category="replies")
    assert await mobile_push.push_owner(root, "existing scheduled notice")

    assert owner.put(path, json={"category": "replies", "on": False}).status_code == 200
    restored = owner.put(path, json={"category": "waiting", "on": True})
    assert restored.status_code == 200
    assert MobileDevices(root).push_preferences() == {"waiting": True, "replies": False}
    assert not await mobile_push.push_owner(root, "replies disabled", category="replies")
    assert await mobile_push.push_owner(root, "approval", category="waiting")
    assert sent == [("ab" * 32, text) for text in ("reply", "existing scheduled notice", "approval")]
