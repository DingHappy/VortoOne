"""Live socket checks for the private browser relay, using a synthetic PNG."""
from contextlib import contextmanager
import json
import struct
import zlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.agents.identity_groups import ADMINS, EVERYONE
from src.agents.principal import OWNER, Principal
from src.gateway.computer_protocol import hello
from src.utils.state_dir import state_dir
from src.web.auth import SESSION_COOKIE, auth_middleware
from src.web.member_auth import MEMBER_COOKIE
from src.web.routers.auth_routes import router as auth_router
from src.web.routers.computers import router
from src.workflow.members.accounts import MemberAccounts
from src.workflow.members.registry import MemberRegistry
from tests.unit.test_computer_web import ORIGIN, OWNER as OWNER_TOKEN, browser, claim_native_home, connect
from tests.unit.test_computer_web import exchange, home, join_member  # noqa: F401 - pytest fixture

SURFACE = "7" * 32
MACHINE = "8" * 32
OWNER_ACTOR = Principal(OWNER, "owner")


def _chunk(tag, body):
    return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body))


def _image(width=1):
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, 1, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(b"\x00\xff\x88\x00")) + _chunk(b"IEND", b""))


@pytest.fixture
def private_preview(home, monkeypatch):
    claim_native_home(home, monkeypatch)
    owner = browser(home, owner=True)
    member, member_id = join_member(home, owner, "viewer@example.test")
    pairing = member.post(home["base"] + "/computer-pairings", json={})
    assert pairing.status_code == 201
    paired = exchange(home, pairing.json(), MACHINE, "Private browser computer")
    assert paired.status_code == 200
    home.update(grant=paired.json(), member=member, member_id=member_id, owner=owner,
                cookie=member.cookies[MEMBER_COOKIE])
    return home


@contextmanager
def _sockets():
    app = FastAPI()
    app.middleware("http")(auth_middleware)
    app.include_router(router)
    app.include_router(auth_router)
    with TestClient(app, base_url=ORIGIN) as api:
        yield api


def _publisher(api, grant):
    return api.websocket_connect("wss://home.test/api/computers/browser-preview",
                                 headers={"Authorization": f"Bearer {grant['token']}"})


def _watch(api, home, cookie, *, origin=ORIGIN):
    return api.websocket_connect(f"wss://home.test{home['base']}/machines/{MACHINE}/browser-preview",
                                 headers={"Origin": origin, "Cookie": cookie})


@contextmanager
def _active_offer(api, home):
    grant = home["grant"]
    with connect(api, grant) as control:
        control.send_json(hello(grant))
        generation = control.receive_json()["generation"]
        with _publisher(api, grant) as source:
            source.send_json({"type": "offer", "generation": generation, "surface_id": SURFACE})
            assert source.receive_json() == {"type": "offered", "expires_in": 300}
            yield source, control, generation


def _captured(source, image=None):
    capture = source.receive_json()
    assert set(capture) == {"type", "request_id", "surface_id"}
    assert capture["type"] == "capture" and capture["surface_id"] == SURFACE
    return capture["request_id"].encode("ascii") + (_image() if image is None else image)


def _machine_list(api, home, headers):
    response = api.get(home["base"] + "/machines", headers=headers)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    return response.json()["machines"]


def test_machine_list_only_advertises_a_live_offer_to_its_authorized_pairer(private_preview):
    home = private_preview
    admin, admin_id = join_member(home, home["owner"], "other-admin@example.test")
    registry = MemberRegistry(home["root"])
    registry.set_member_groups(OWNER_ACTOR, admin_id, [ADMINS])
    headers = {"Cookie": f"{MEMBER_COOKIE}={home['cookie']}"}
    grant = home["grant"]
    with _sockets() as api, connect(api, grant) as control:
        control.send_json(hello(grant))
        generation = control.receive_json()["generation"]
        before, = _machine_list(api, home, headers)
        assert before["online"] and before["browserPreviewAvailable"] is False
        with _publisher(api, grant) as source:
            source.send_json({"type": "offer", "generation": generation, "surface_id": SURFACE})
            assert source.receive_json()["type"] == "offered"
            machine, = _machine_list(api, home, headers)
            assert machine == {**before, "browserPreviewAvailable": True}
            assert SURFACE not in json.dumps(machine) and grant["token"] not in json.dumps(machine)
            assert _machine_list(api, home, {"Cookie": f"{SESSION_COOKIE}={OWNER_TOKEN}"}) == []
            assert _machine_list(api, home, {"Cookie": f"{MEMBER_COOKIE}={admin.cookies[MEMBER_COOKIE]}"}) == []
            registry.change_group(OWNER_ACTOR, EVERYONE, computer="none")
            assert _machine_list(api, home, headers) == [before]
            registry.change_group(OWNER_ACTOR, EVERYONE, computer="own_browser")
            assert _machine_list(api, home, headers) == [machine]
        assert _machine_list(api, home, headers) == [before]


def test_machine_list_owner_token_without_a_browser_cookie_does_not_advertise_a_share(home, monkeypatch):
    claim_native_home(home, monkeypatch)
    pairing = home["store"].create_pairing()
    home["grant"] = home["store"].pair(pairing["code"], MACHINE, "Owner browser computer")
    with _sockets() as api, _active_offer(api, home):
        authorized, = _machine_list(api, home, {"Cookie": f"{SESSION_COOKIE}={OWNER_TOKEN}"})
        bearer, = _machine_list(api, home, {"Authorization": f"Bearer {OWNER_TOKEN}"})
        assert authorized["browserPreviewAvailable"] is True
        assert bearer == {**authorized, "browserPreviewAvailable": False}


def test_private_browser_requires_same_origin_and_the_pairers_session(private_preview):
    home = private_preview
    admin, admin_id = join_member(home, home["owner"], "admin@example.test")
    MemberRegistry(home["root"]).set_member_groups(OWNER_ACTOR, admin_id, [ADMINS])
    cookie = f"{MEMBER_COOKIE}={home['cookie']}"
    with _sockets() as api, _active_offer(api, home) as (source, _, _):
        for other, origin in ((f"{SESSION_COOKIE}={OWNER_TOKEN}", ORIGIN),
                              (f"{MEMBER_COOKIE}={admin.cookies[MEMBER_COOKIE]}", ORIGIN),
                              (cookie, "http://home.test"), ("", ORIGIN)):
            with pytest.raises(WebSocketDisconnect) as denied:
                with _watch(api, home, other, origin=origin):
                    pytest.fail("private browser viewing unexpectedly opened")
            assert denied.value.code == 1008
        with _watch(api, home, cookie) as viewer:
            assert viewer.receive_json() == {"type": "ready", "min_frame_ms": 500}
            viewer.send_json({"type": "next"})
            source.send_bytes(_captured(source))
            assert viewer.receive_bytes() == _image()

    records = [json.loads(line) for line in (state_dir(home["root"]) / "audit.log").read_text().splitlines()]
    (event,) = [record for record in records if record.get("operation") == "观看私有浏览器"]
    assert event["actor_id"] == home["member_id"] and event["connector_id"] == MACHINE
    assert set(event) == {"id", "ts", "category", "decision", "decision_source", "operation", "operation_kind",
                          "actor_id", "connector_id", "surface_id"}


@pytest.mark.parametrize("revoke", ["role", "session"])
def test_browser_drops_a_frame_after_live_authority_changes(private_preview, revoke):
    home = private_preview
    cookie = f"{MEMBER_COOKIE}={home['cookie']}"
    with _sockets() as api, _active_offer(api, home) as (source, _, _), _watch(api, home, cookie) as viewer:
        assert viewer.receive_json()["type"] == "ready"
        viewer.send_json({"type": "next"})
        frame = _captured(source)
        if revoke == "role":
            MemberRegistry(home["root"]).change_group(OWNER_ACTOR, EVERYONE, computer="none")
        else:
            MemberAccounts(home["root"]).sign_out(home["cookie"])
        source.send_bytes(frame)
        with pytest.raises(WebSocketDisconnect) as closed:
            viewer.receive_bytes()
        assert closed.value.code == 1008


def test_owner_logout_closes_its_open_preview_before_returning_an_inflight_frame(home, monkeypatch):
    claim_native_home(home, monkeypatch)
    pairing = home["store"].create_pairing()
    home["grant"] = home["store"].pair(pairing["code"], MACHINE, "Owner browser computer")
    cookie = f"{SESSION_COOKIE}={OWNER_TOKEN}"
    with _sockets() as api, _active_offer(api, home) as (source, _, _), _watch(api, home, cookie) as viewer:
        assert viewer.receive_json()["type"] == "ready"
        viewer.send_json({"type": "next"})
        frame = _captured(source)
        response = api.post("/api/auth/logout", headers={"Cookie": cookie})
        assert response.status_code == 200
        source.send_bytes(frame)
        with pytest.raises(WebSocketDisconnect) as closed:
            viewer.receive_bytes()
        assert closed.value.code == 1008


def test_browser_reconnect_requires_a_new_preview_offer(private_preview):
    home = private_preview
    with _sockets() as api, _active_offer(api, home) as (source, old, generation):
        with connect(api, home["grant"]) as replacement:
            replacement.send_json(hello(home["grant"]))
            assert replacement.receive_json()["generation"] != generation
            assert old.receive_json()["reason"] == "connection_replaced"
            old.send_json({"type": "ack", "generation": generation})
            status, = _machine_list(api, home, {"Cookie": f"{MEMBER_COOKIE}={home['cookie']}"})
            assert status["online"] and status["browserPreviewAvailable"] is False
            with pytest.raises(WebSocketDisconnect):
                source.receive_json()
            with _publisher(api, home["grant"]) as stale:
                stale.send_json({"type": "offer", "generation": generation, "surface_id": SURFACE})
                with pytest.raises(WebSocketDisconnect) as closed:
                    stale.receive_json()
                assert closed.value.code == 1008


@pytest.mark.parametrize("violation", ["unsolicited", "oversize_dimensions", "wrong_request"])
def test_browser_rejects_frames_outside_a_bounded_capture(private_preview, violation):
    home = private_preview
    with _sockets() as api, _active_offer(api, home) as (source, _, _):
        if violation == "unsolicited":
            source.send_bytes(b"a" * 32 + _image())
            with pytest.raises(WebSocketDisconnect) as closed:
                source.receive_json()
            assert closed.value.code == 1008
            return
        with _watch(api, home, f"{MEMBER_COOKIE}={home['cookie']}") as viewer:
            assert viewer.receive_json()["type"] == "ready"
            viewer.send_json({"type": "next"})
            frame = _captured(source, _image(width=1601) if violation == "oversize_dimensions" else _image())
            source.send_bytes(b"z" * 32 + frame[32:] if violation == "wrong_request" else frame)
            with pytest.raises(WebSocketDisconnect) as closed:
                viewer.receive_bytes()
            assert closed.value.code == 1008
