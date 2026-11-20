"""Encrypted password and optional verification submissions for one authorized login."""
from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
import json
import re
import time
from typing import TYPE_CHECKING
from urllib.parse import unquote
import uuid

from src.browser.cdp import CdpBrowser
from src.gateway.computer_browser_wire import LOGIN_FORM_SECONDS, LOGIN_VALUE_BYTES, LoginForm, login_submission
from src.gateway.computer_protocol import decode_frame

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey


_LOGIN_PATH = r"(?:^|/)(?:login|log-in|signin|sign-in|sessions?|authenticate)(?:\.(?:html?|php|aspx?))?/?$"
_CODE_PATH = r"(?:^|/)(?:otp|mfa|2fa|two-factor|verify|verify-code|verification|challenge)(?:\.(?:html?|php|aspx?))?/?$"
_AUTH_PATH = re.compile(f"(?:{_LOGIN_PATH}|{_CODE_PATH})", re.IGNORECASE)
_PAGE_WAIT_SECONDS = 15.0


def login_step_path(path: str) -> bool:
    return _AUTH_PATH.search(unquote(path)) is not None


_LOGIN_FORM_JS = r"""function (origin, offer, kind, credentials) {
  const previous = globalThis.__vortooneLogin;
  delete globalThis.__vortooneLogin;
  const page = new URL(location.href);
  const loginPath = new RegExp(__LOGIN_PATH__, 'i');
  const codePath = new RegExp(__CODE_PATH__, 'i');
  const isAuthPath = (path) => {
    const decoded = decodeURIComponent(path);
    return loginPath.test(decoded) || (kind === 'code' && codePath.test(decoded));
  };
  if (page.protocol !== 'https:' || page.origin !== origin || page.search || page.hash || page.username || page.password ||
      !isAuthPath(page.pathname) || document.readyState === 'loading' || document.querySelector('iframe,frame,base[target]')) {
    return null;
  }
  const visible = (el) => el.getClientRects().length > 0 && getComputedStyle(el).visibility === 'visible';
  const inputs = [...document.querySelectorAll('input')].filter(visible);
  const passwords = inputs.filter(el => el.type === 'password');
  const candidates = kind === 'code' ? inputs.filter(el => ['text', 'tel'].includes(el.type) &&
    el.autocomplete.toLowerCase().split(/\s+/).includes('one-time-code')) : passwords;
  if (candidates.length !== 1 || (kind === 'code' && passwords.length !== 0)) return null;
  const field = candidates[0], form = field.form;
  if (!form || form.method !== 'post' || form.enctype !== 'application/x-www-form-urlencoded' ||
      (form.target && form.target !== '_self') || form.noValidate) return null;
  const action = new URL(form.action, page.href);
  if (action.protocol !== 'https:' || action.origin !== origin || action.search || action.hash ||
      action.username || action.password || !isAuthPath(action.pathname)) return null;
  const elements = [...form.elements];
  if (elements.some(el => el instanceof HTMLInputElement && el.type === 'file')) return null;
  const other = elements.filter(el => el instanceof HTMLTextAreaElement ||
    (el instanceof HTMLInputElement && !['hidden', 'submit', 'button', 'reset', 'checkbox', 'radio'].includes(el.type)))
    .filter(el => el !== field && visible(el));
  if (other.length > (kind === 'code' ? 0 : 1)) return null;
  const username = other[0];
  if (username && (!(username instanceof HTMLInputElement) ||
    !['text', 'email', 'tel'].includes(username.type) ||
    !(username.type === 'email' || /(?:^|\s)(?:username|email)(?:\s|$)/i.test(username.autocomplete) ||
      /^(?:user(?:name)?|email|login|account|identifier)$/i.test(username.name)))) return null;
  const fields = username ? [username, field] : [field];
  if (/\bnew-password\b/i.test(field.autocomplete) || fields.some(el =>
    !el.name || el.matches(':disabled') || el.readOnly || el.value ||
    elements.filter(other => other.name === el.name).length !== 1)) return null;
  const buttons = elements.filter(el => (el instanceof HTMLButtonElement || el instanceof HTMLInputElement) &&
    el.type === 'submit' && !el.matches(':disabled') && visible(el));
  if (buttons.length !== 1) return null;
  const button = buttons[0];
  if (['formaction', 'formmethod', 'formtarget', 'formenctype', 'formnovalidate'].some(a => button.hasAttribute(a))) {
    return null;
  }
  const names = fields.map(el => el.name);
  if (credentials === null) {
    globalThis.__vortooneLogin = { offer, kind, page: page.href, action: action.href, form, field, username, button, names };
    return { username: Boolean(username), document: performance.timeOrigin };
  }
  if (!previous || previous.offer !== offer || previous.kind !== kind || previous.page !== page.href || previous.action !== action.href ||
      previous.form !== form || previous.field !== field || previous.username !== username ||
      previous.button !== button || names.some((name, index) => name !== previous.names[index])) return false;
  const value = kind === 'code' ? credentials.code : credentials.password;
  const values = username ? [credentials.username, value] : [value];
  if (fields.some((el, index) => el.maxLength >= 0 && values[index].length > el.maxLength)) return false;
  const policy = document.createElement('meta');
  policy.httpEquiv = 'Content-Security-Policy';
  policy.content = "form-action 'self'";
  document.head.append(policy);
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  fields.forEach((el, index) => setter.call(el, values[index]));
  if (fields.some((el, index) => el.value !== values[index]) || !HTMLFormElement.prototype.checkValidity.call(form)) {
    fields.forEach(el => setter.call(el, ''));
    return false;
  }
  HTMLFormElement.prototype.requestSubmit.call(form, button);
  return true;
}""".replace("__LOGIN_PATH__", json.dumps(_LOGIN_PATH)).replace("__CODE_PATH__", json.dumps(_CODE_PATH))


@dataclass(repr=False)
class _Offer:
    form: LoginForm
    document: float
    expires_at: float
    private_key: EllipticCurvePrivateKey


class BrowserHandoffLogin:
    def __init__(self, browser: CdpBrowser, origin: str, current: Callable[[], bool]):
        self._browser = browser
        self._origin = origin
        self._current = current
        self._offer: _Offer | None = None
        self.touched_document: float | None = None
        self._next_kind: str | None = "password"
        self._page_deadline = 0.0

    def awaiting_page(self) -> bool:
        return self._current() and time.monotonic() < self._page_deadline

    def discard_offer(self) -> None:
        self._offer = None

    async def offer(self) -> bytes | None:
        self.discard_offer()
        kind = self._next_kind
        if kind is None or not self._current():
            return None
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec
        except ImportError:
            return None
        offer_id = uuid.uuid4().hex
        state = await self._browser.call_isolated(_LOGIN_FORM_JS, [self._origin, offer_id, kind, None], current=self._current)
        if (not self._current() or not isinstance(state, dict) or set(state) != {"username", "document"}
                or type(state["username"]) is not bool or type(state["document"]) not in (float, int)
                or not 0 < state["document"] <= 1e15 or state["document"] == self.touched_document
                or (kind == "code" and state["username"])):
            return None
        key = ec.generate_private_key(ec.SECP256R1())
        public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        form = LoginForm(offer_id, base64.b64encode(public).decode("ascii"), state["username"], kind)
        self._offer = _Offer(form, state["document"], time.monotonic() + LOGIN_FORM_SECONDS, key)
        return form.packet()

    async def submit(self, message: dict) -> bool:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF

        pending, self._offer = self._offer, None
        if pending is None or pending.form.kind != self._next_kind:
            return False

        def current() -> bool:
            return self._current() and time.monotonic() < pending.expires_at

        try:
            offer_id, (epk, iv, ciphertext) = login_submission(message)
            if offer_id != pending.form.offer or not current():
                return False
            peer = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), epk)
            shared = pending.private_key.exchange(ec.ECDH(), peer)
            # Match the existing Web Crypto secret-box format, binding it to this offer and site.
            key = HKDF(hashes.SHA256(), 32, epk, b"agentes.chat secret v1").derive(shared)
            aad = f"vortoone-browser-login|v1|{offer_id}|{self._origin}".encode("utf-8")
            fields = decode_frame(AESGCM(key).decrypt(iv, ciphertext, aad))
            if not _valid_fields(pending.form, fields) or not current():
                return False
            # The browser may echo input or retain it on an error page. Stop frames before injection.
            self.touched_document = pending.document
            self._next_kind = None
            self._page_deadline = 0.0
            args = [self._origin, offer_id, pending.form.kind, fields]
            if await self._browser.call_isolated(_LOGIN_FORM_JS, args, current=current) is not True:
                return False
            if pending.form.kind == "password":
                self._next_kind = "code"
            self._page_deadline = time.monotonic() + _PAGE_WAIT_SECONDS
            return True
        except (InvalidTag, OSError, RuntimeError, KeyError, ValueError):
            return False


def _valid_fields(form: LoginForm, fields: dict) -> bool:
    if form.kind == "code":
        code = fields.get("code")
        return set(fields) == {"code"} and isinstance(code, str) and re.fullmatch(r"[0-9]{4,12}", code) is not None
    expected = {"username", "password"} if form.username else {"password"}
    return set(fields) == expected and all(_valid_value(value) for value in fields.values())


def _valid_value(value: object) -> bool:
    return (isinstance(value, str) and 0 < len(value.encode("utf-8")) <= LOGIN_VALUE_BYTES
            and not any(character in value for character in "\r\n\0"))
