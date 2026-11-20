"""Size, timing and PNG framing shared by the browser publisher and relay."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import struct

from src.gateway.computer_protocol import decode_frame, identifier

OFFER_SECONDS = 300
FRAME_TIMEOUT = 3.0
PROTECTED = b"\x00"
SITE_READY = b"\x01"
INPUT_APPLIED = b"\x02"
LOGIN_FORM = b"\x03"
LOGIN_SENT = b"\x04"
LOGIN_WAITING = b"\x05"
LOGIN_FORM_SECONDS = 90
LOGIN_VALUE_BYTES = 256
LOGIN_PAYLOAD_BYTES = 1100
INPUT_FRAME_SECONDS = 3.0
INPUT_TEXT_BYTES = 96
MIN_FRAME_INTERVAL = 0.5
CONTROL_BYTES = 2048
MAX_PNG_BYTES = 768 * 1024
MAX_WIDTH, MAX_HEIGHT = 1600, 1200
_PNG_HEADER = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
_PNG_END = b"\x00\x00\x00\x00IEND\xae\x42\x60\x82"


class BrowserPreviewDenied(ValueError):
    pass


def _base64(value: object, minimum: int, maximum: int) -> bytes:
    if not isinstance(value, str) or len(value) > 4 * ((maximum + 2) // 3):
        raise BrowserPreviewDenied("invalid login envelope")
    data = base64.b64decode(value, validate=True)
    if not minimum <= len(data) <= maximum or base64.b64encode(data).decode("ascii") != value:
        raise BrowserPreviewDenied("invalid login envelope")
    return data


@dataclass(frozen=True)
class LoginForm:
    offer: str
    public_key: str
    username: bool
    kind: str = "password"

    def message(self) -> dict:
        return {"type": "login_form", "offer": self.offer, "public_key": self.public_key,
                "kind": self.kind, "username": self.username, "expires_in": LOGIN_FORM_SECONDS}

    def packet(self) -> bytes:
        return LOGIN_FORM + json.dumps({"offer": self.offer, "public_key": self.public_key,
                                        "kind": self.kind, "username": self.username}).encode("ascii")

    @classmethod
    def from_packet(cls, packet: bytes) -> LoginForm:
        if not packet.startswith(LOGIN_FORM) or len(packet) > 256:
            raise BrowserPreviewDenied("invalid login form")
        data = decode_frame(packet[1:])
        if (set(data) != {"offer", "public_key", "kind", "username"} or type(data["username"]) is not bool
                or data["kind"] not in ("password", "code") or (data["kind"] == "code" and data["username"])):
            raise BrowserPreviewDenied("invalid login form")
        offer = identifier(data["offer"])
        if _base64(data["public_key"], 65, 65)[0] != 4:
            raise BrowserPreviewDenied("invalid login public key")
        return cls(offer, data["public_key"], data["username"], data["kind"])


def login_submission(message: dict) -> tuple[str, tuple[bytes, bytes, bytes]]:
    if set(message) != {"type", "offer", "box"} or message["type"] != "submit_login":
        raise BrowserPreviewDenied("invalid login submission")
    box = message["box"]
    if (not isinstance(box, dict) or set(box) != {"v", "epk", "iv", "ct"}
            or type(box["v"]) is not int or box["v"] != 1):
        raise BrowserPreviewDenied("invalid login envelope")
    epk = _base64(box["epk"], 65, 65)
    if epk[0] != 4:
        raise BrowserPreviewDenied("invalid login public key")
    return identifier(message["offer"]), (epk, _base64(box["iv"], 12, 12),
                                          _base64(box["ct"], 17, LOGIN_PAYLOAD_BYTES + 16))


def browser_input(action: object) -> dict:
    if not isinstance(action, dict):
        raise BrowserPreviewDenied("invalid browser input")
    kind = action.get("kind")
    fields = set(action)
    if kind == "click" and fields == {"kind", "x", "y"}:
        if all(type(action[axis]) is int and 0 <= action[axis] < 10000 for axis in ("x", "y")):
            return action
    elif kind == "scroll" and fields == {"kind", "delta"}:
        if type(action["delta"]) is int and 0 < abs(action["delta"]) <= 600:
            return action
    elif kind == "text" and fields == {"kind", "text"}:
        text = action["text"]
        if isinstance(text, str) and text.isprintable() and len(text.encode("utf-8")) <= INPUT_TEXT_BYTES:
            return action
    elif kind == "backspace" and fields == {"kind"}:
        return action
    raise BrowserPreviewDenied("invalid browser input")


def validate_png(data: bytes) -> bytes:
    if (not 45 <= len(data) <= MAX_PNG_BYTES
            or not data.startswith(_PNG_HEADER) or not data.endswith(_PNG_END)):
        raise BrowserPreviewDenied("invalid browser frame")
    width, height = struct.unpack_from(">II", data, len(_PNG_HEADER))
    if not 0 < width <= MAX_WIDTH or not 0 < height <= MAX_HEIGHT:
        raise BrowserPreviewDenied("invalid browser frame dimensions")
    return data
