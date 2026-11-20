"""Use synthetic credentials to verify the paired browser's one-time login box."""
import asyncio
import base64
import json

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from src.browser.handoff_login import BrowserHandoffLogin
from src.browser.handoff_preview import BrowserHandoffFrame
from src.browser.page_privacy import HANDOFF_PREVIEW_STATE_JS
from src.gateway.computer_browser_wire import LOGIN_WAITING, SITE_READY, LoginForm


SITE = "https://example.test"
LOGIN = {"username": "sample@example.test", "password": "合成测试 % +"}
CODE = {"code": "05401726"}


def seal_login(form: LoginForm, fields: dict, *, site: str = SITE) -> dict:
    sender = ec.generate_private_key(ec.SECP256R1())
    epk = sender.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    recipient = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), base64.b64decode(form.public_key))
    key = HKDF(hashes.SHA256(), 32, epk, b"agentes.chat secret v1").derive(sender.exchange(ec.ECDH(), recipient))
    aad = f"vortoone-browser-login|v1|{form.offer}|{site}".encode()
    iv = b"\x13" * 12
    ct = AESGCM(key).encrypt(iv, json.dumps(fields).encode(), aad)
    box = {name: base64.b64encode(raw).decode() for name, raw in {"epk": epk, "iv": iv, "ct": ct}.items()}
    return {"type": "submit_login", "offer": form.offer, "box": {"v": 1, **box}}


class Browser:
    def __init__(self):
        self.document = 1000
        self.path = "/login"
        self.loaded = True
        self.delivered = []

    async def call_isolated(self, program, arguments, *, current):
        if not current():
            return None
        origin, _, kind, fields = arguments
        assert origin == SITE
        if self.path != {"password": "/login", "code": "/verify"}[kind]:
            return None
        if fields is None:
            return {"username": kind == "password", "document": self.document}
        assert all(value not in program for value in fields.values())
        self.delivered.append(fields)
        return True

    async def evaluate(self, program):
        assert program == HANDOFF_PREVIEW_STATE_JS
        return {"url": SITE + self.path, "document": self.document, "frames": False, "loaded": self.loaded,
                "protected": self.path == "/login"}

    async def screenshot(self):
        raise AssertionError("login pages and post-submission documents never yield a screenshot")


@pytest.mark.parametrize("case", ["valid", "wrong_site", "expired", "revoked", "unsupported_value"])
async def test_login_is_bound_to_one_live_offer_and_never_becomes_a_frame(case, monkeypatch):
    elapsed, allowed = 0, True
    monkeypatch.setattr("src.browser.handoff_login.time", type("Clock", (), {"monotonic": lambda: elapsed}))
    browser = Browser()
    login = BrowserHandoffLogin(browser, SITE, lambda: allowed)
    frame = BrowserHandoffFrame(browser, SITE, asyncio.Lock(), lambda: allowed, login=login)
    viewer = frame.begin_viewing()

    packet = await frame.pull(lambda: True, viewer=viewer)
    form = LoginForm.from_packet(packet)
    credentials = {**LOGIN, "password": "x" * 257} if case == "unsupported_value" else LOGIN
    message = seal_login(form, credentials, site="https://other.test" if case == "wrong_site" else SITE)
    assert credentials["password"] not in json.dumps(message, ensure_ascii=False)
    if case == "expired":
        elapsed = 91
    if case == "revoked":
        allowed = False
    assert (await frame.submit_login(message)) is (case == "valid")
    assert not await frame.submit_login(message)
    assert browser.delivered == ([LOGIN] if case == "valid" else [])
    if case == "valid":
        browser.path = "/ready"
        assert not await frame.site_ready()
        browser.document += 1
        assert await frame.site_ready()
        assert await frame.pull(lambda: True, viewer=viewer) == SITE_READY


@pytest.mark.parametrize("case", ["valid", "expired", "password", "malformed_code"])
async def test_verification_needs_a_new_document_after_password_and_is_submitted_at_most_once(case, monkeypatch):
    elapsed = 0
    monkeypatch.setattr("src.browser.handoff_login.time", type("Clock", (), {"monotonic": lambda: elapsed}))
    browser = Browser()
    login = BrowserHandoffLogin(browser, SITE, lambda: True)
    frame = BrowserHandoffFrame(browser, SITE, asyncio.Lock(), lambda: True, login=login)
    viewer = frame.begin_viewing()

    async def pull():
        return await frame.pull(lambda: True, viewer=viewer)

    password = LoginForm.from_packet(await pull())
    assert password.kind == "password"
    first_submission = seal_login(password, LOGIN)
    assert await frame.submit_login(first_submission)
    browser.path = "/verify"
    assert await pull() == LOGIN_WAITING
    browser.document += 1
    verification = LoginForm.from_packet(await pull())
    assert verification.kind == "code" and not verification.username and verification.offer != password.offer
    assert not await frame.site_ready()

    fields = {"password": LOGIN["password"]} if case == "password" else {"code": "12AB"} if case == "malformed_code" else CODE
    message = seal_login(verification, fields)
    if case == "expired":
        elapsed = 91
    assert (await frame.submit_login(message)) is (case == "valid")
    assert not await frame.submit_login(message)
    assert not await frame.submit_login(first_submission)
    assert browser.delivered == ([LOGIN, CODE] if case == "valid" else [LOGIN])
    if case == "valid":
        browser.document += 1
        assert await pull() == LOGIN_WAITING
        browser.path, browser.loaded = "/ready", False
        assert not await frame.site_ready()
        browser.loaded = True
        assert await pull() == SITE_READY
        assert await frame.site_ready()
