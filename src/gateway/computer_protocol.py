"""Bounded computer control protocol. Negotiation does not grant file access."""
from __future__ import annotations

import ipaddress
import json
import re
from urllib.parse import urlsplit, urlunsplit

MAX_FRAME_BYTES = 256 * 1024
PROTOCOL_MIN = 1
PROTOCOL_MAX = 2
FILES_PROTOCOL = 2
FEATURES = ("control-only",)
CAPABILITIES = {key: False for key in ("read", "write", "command", "browser", "codex")}
BINDING_FIELDS = ("home_instance_id", "connector_id", "paired_principal_id", "credential_epoch", "trust_epoch")
_ID = re.compile(r"[a-f0-9]{32}\Z")


class ComputerProtocolError(ValueError):
    """Untrusted input is invalid; never include its contents in an error."""


def identifier(value: object) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ComputerProtocolError("invalid computer identity")
    return value


def origin_url(value: object, *, allow_loopback_http: bool = False) -> str:
    """Only a native-confirmed local service may opt into literal-loopback HTTP."""
    if not isinstance(value, str) or not value or len(value) > 2048 or any(ord(c) <= 32 for c in value):
        raise ComputerProtocolError("invalid home address")
    try:
        url = urlsplit(value)
        host, port = url.hostname, url.port
        if not host or url.username is not None or url.password is not None or url.query or url.fragment or url.path not in ("", "/"):
            raise ValueError
        if url.scheme != "https":
            if url.scheme != "http" or not allow_loopback_http or not ipaddress.ip_address(host).is_loopback:
                raise ValueError
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
        host = host.encode("idna").decode("ascii").lower()
        if not re.fullmatch(r"[a-z0-9.:-]+", host) or ".." in host:
            raise ValueError
        authority = f"[{host}]" if ":" in host else host
        if port is not None and port != (443 if url.scheme == "https" else 80):
            authority += f":{port}"
        return urlunsplit((url.scheme, authority, "", "", ""))
    except (ValueError, UnicodeError):
        raise ComputerProtocolError("invalid home address") from None


def decode_frame(raw: str | bytes) -> dict:
    try:
        encoded = raw.encode("utf-8") if isinstance(raw, str) else raw
        if len(encoded) > MAX_FRAME_BYTES:
            raise ValueError
        # Duplicate keys are ambiguous and must not be silently overwritten.
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError
                result[key] = value
            return result
        value = json.loads(encoded, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise ComputerProtocolError("invalid computer frame") from None


def binding_fields(binding: dict) -> dict:
    return {key: identifier(binding.get(key)) for key in BINDING_FIELDS}


def hello(binding: dict, *, files: bool = False) -> dict:
    return {"type": "hello", **binding_fields(binding), "protocol_min": PROTOCOL_MIN,
            "protocol_max": PROTOCOL_MAX if files else PROTOCOL_MIN, "features": list(FEATURES)}


def validate_hello(frame: dict, binding: dict) -> dict:
    expected = set(BINDING_FIELDS) | {"type", "protocol_min", "protocol_max", "features"}
    if set(frame) != expected or frame.get("type") != "hello" or binding_fields(frame) != binding_fields(binding):
        raise ComputerProtocolError("computer identity mismatch")
    low, high = frame.get("protocol_min"), frame.get("protocol_max")
    if type(low) is not int or type(high) is not int or not 1 <= low <= high or low > PROTOCOL_MAX or high < PROTOCOL_MIN:
        raise ComputerProtocolError("incompatible computer protocol")
    if frame.get("features") != list(FEATURES):
        raise ComputerProtocolError("incompatible computer features")
    return {"protocol": min(high, PROTOCOL_MAX)}


def welcome(binding: dict, generation: str, protocol: int = PROTOCOL_MIN) -> dict:
    return {"type": "welcome", **binding_fields(binding), "generation": identifier(generation),
            "protocol": protocol, "features": list(FEATURES), "capabilities": dict(CAPABILITIES), "folders": []}


def validate_welcome(frame: dict, binding: dict, *, files: bool = False) -> str:
    generation = identifier(frame.get("generation"))
    capabilities = frame.get("capabilities")
    protocol = frame.get("protocol")
    if (type(protocol) is not int or protocol not in ((1, 2) if files else (1,))
            or not isinstance(capabilities, dict)
            or set(capabilities) != set(CAPABILITIES)
            or any(type(v) is not bool or v for v in capabilities.values())
            or frame != welcome(binding, generation, protocol)):
        raise ComputerProtocolError("invalid computer welcome")
    return generation


def control_frame(frame: dict, generation: str) -> str:
    kind = frame.get("type")
    if kind not in {"ping", "pong", "ack"} or frame != {"type": kind, "generation": generation}:
        raise ComputerProtocolError("unsupported computer action")
    return kind
