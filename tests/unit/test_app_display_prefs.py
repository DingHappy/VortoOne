"""Per-person display choices shape real membership responses."""

import pytest
from fastapi.testclient import TestClient

from tests.unit.test_member_login import ORIGIN, _join, home  # noqa: F401 — fixture


def _membership(client):
    [workspace] = client.get("/api/app/me").json()["organizations"]
    return workspace


def test_activity_is_per_member_and_validated(home):
    owner, base = home["owner"], home["base"]
    member = _join(home)
    assert _membership(owner)["showActivity"] is True
    assert _membership(member)["showActivity"] is True
    assert member.put(f"{base}/me/activity", json={"show": False}).json() == {"show": False}
    assert _membership(member)["showActivity"] is False
    assert _membership(owner)["showActivity"] is True
    assert owner.put(f"{base}/me/activity", json={"show": "false"}).status_code == 422
    assert owner.put(f"{base}/me/activity", json={"show": False}).status_code == 200
    assert _membership(owner)["showActivity"] is False


def test_chat_look_merges_partial_updates_and_refuses_auto_transmit(home):
    owner, base = home["owner"], home["base"]
    member = _join(home)
    path = f"{base}/me/chat"
    assert owner.put(path, json={"dmTone": "strong"}).json() == {"dmTone": "strong"}
    expected = {"dmTone": "strong", "textSize": "xs"}
    assert owner.put(path, json={"textSize": "xs"}).json() == expected
    assert _membership(owner)["chatPrefs"] == expected
    assert _membership(member)["chatPrefs"] == {}
    assert member.put(path, json={"channelStyle": "compact"}).json() == {"channelStyle": "compact"}
    assert _membership(member)["chatPrefs"] == {"channelStyle": "compact"}
    assert member.put(path, json={"dmTone": "bad"}).status_code == 400
    assert member.put(path, json={"feedbackAuto": True, "dmTone": "light"}).status_code == 409
    assert _membership(member)["chatPrefs"] == {"channelStyle": "compact"}


def test_start_channel_requires_current_access(home):
    owner, base = home["owner"], home["base"]
    member = _join(home)
    channel = owner.post(f"{base}/channels", json={"name": "private", "visibility": "private"}).json()["id"]
    path = f"{base}/me/start"
    linked = {"page": "channel", "channelId": channel}
    assert member.put(path, json=linked).status_code == 404
    assert owner.put(path, json=linked).json() == linked
    assert _membership(owner)["start"] == linked
    assert member.put(path, json={"page": "last"}).json() == {"page": "last", "channelId": None}
    assert _membership(member)["start"] == {"page": "last", "channelId": None}
    assert owner.put(path, json={"page": "home", "channelId": channel}).status_code == 400
    assert owner.delete(f"{base}/channels/{channel}").status_code == 204
    assert _membership(owner)["start"] == {"page": "home", "channelId": None}


@pytest.mark.parametrize(("path", "body"), [
    ("activity", {"show": True}), ("chat", {"dmStyle": "left"}), ("start", {"page": "last"}),
])
def test_guest_and_foreign_workspace_cannot_edit_display(home, path, body):
    guest = TestClient(home["app"], headers=ORIGIN)
    assert guest.put(f"{home['base']}/me/{path}", json=body).status_code == 401
    member = _join(home)
    assert member.put(f"/api/app/organizations/foreign/me/{path}", json=body).status_code == 404
