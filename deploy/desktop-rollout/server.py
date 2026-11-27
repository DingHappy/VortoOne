"""Central service for the private VortoOne desktop update channel."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import stat
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.datastructures import Headers, MutableHeaders

PREFIX = "/api/desktop-rollout"
PUBLIC_ORIGIN = "https://vortoone.com"
ADMIN_COOKIE = "__Secure-vortoone-rollout"
ADMIN_COOKIE_PATH = PREFIX + "/admin"
STATIC = Path(__file__).with_name("static")
ENROLLMENT_SECONDS = 15 * 60
TICKET_SECONDS = 10 * 60
SESSION_SECONDS = 8 * 60 * 60
JSON_LIMIT = 4096
MANIFEST_LIMIT = 64 * 1024
ARTIFACT_LIMIT = 512 * 1024 * 1024

Platform = Literal["darwin-aarch64", "windows-x86_64-nsis", "linux-x86_64-deb"]
SUFFIXES = {
    "darwin-aarch64": "aarch64.app.tar.gz",
    "windows-x86_64-nsis": "windows-x64.exe",
    "linux-x86_64-deb": "ubuntu-amd64.deb",
}
VERSION_PATTERN = r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})"
VERSION_RE = re.compile(VERSION_PATTERN)
Version = Annotated[str, Field(pattern=f"^{VERSION_PATTERN}$", max_length=29)]
DeviceId = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{43}")
TICKET_RE = re.compile(r"[A-Za-z0-9_-]{22}")
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
PUBLIC_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'none'; connect-src 'self'; style-src 'self'; script-src 'self'; "
        "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS enrollments (
    code_hash BLOB PRIMARY KEY,
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    token_hash BLOB NOT NULL UNIQUE,
    name TEXT NOT NULL,
    platform TEXT NOT NULL,
    current_version TEXT NOT NULL,
    target_version TEXT,
    last_seen_at INTEGER NOT NULL,
    status TEXT NOT NULL,
    active INTEGER NOT NULL CHECK(active IN (0, 1))
);
CREATE TABLE IF NOT EXISTS paused_releases (
    version TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS tickets (
    ticket_hash BLOB PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(id),
    release TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS tickets_expiry ON tickets(expires_at);
CREATE INDEX IF NOT EXISTS tickets_device ON tickets(device_id);
CREATE INDEX IF NOT EXISTS tickets_release ON tickets(release);
CREATE TABLE IF NOT EXISTS admin_sessions (
    token_hash BLOB PRIMARY KEY,
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS request_limits (
    kind TEXT NOT NULL,
    client TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    attempts INTEGER NOT NULL,
    PRIMARY KEY(kind, client)
);
"""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Asset(StrictModel):
    filename: str
    signature: str = Field(min_length=1, max_length=8192, pattern=r"^[A-Za-z0-9+/=]+$")
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Candidate(StrictModel):
    version: Version
    commit: str = Field(pattern=r"^([a-f0-9]{40}|[a-f0-9]{64})$")
    notes: str = Field(max_length=16000)
    created_at: str = Field(max_length=40)
    platforms: dict[Platform, Asset] = Field(min_length=1, max_length=3)

    @field_validator("created_at")
    @classmethod
    def valid_date(cls, value: str) -> str:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError("timezone required")
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class Enroll(StrictModel):
    code: str = Field(pattern=r"^[A-Za-z0-9]{20}$")
    name: str = Field(min_length=1, max_length=100)
    platform: Platform
    version: Version

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        value = value.strip()
        if not value or not value.isprintable():
            raise ValueError("invalid device name")
        return value


class Check(StrictModel):
    platform: Platform
    version: Version


class Report(StrictModel):
    release: Version
    status: Literal["installing", "failed", "declined"]


class Login(StrictModel):
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("password")
    @classmethod
    def valid_password(cls, value: str) -> str:
        value.encode("utf-8")
        return value


class Empty(StrictModel):
    pass


class Assignment(StrictModel):
    version: Version | None


class BatchAssignment(Assignment):
    device_ids: list[DeviceId] = Field(min_length=1, max_length=50)

    @field_validator("device_ids")
    @classmethod
    def distinct_devices(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("distinct device IDs required")
        return value


class Pause(StrictModel):
    paused: bool


class RequestPolicy:
    """Enforce the central origin, bounded JSON and private-response headers."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def protected_send(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for key, value in PUBLIC_HEADERS.items():
                    headers[key] = value
            await send(message)

        async def refuse(status, detail):
            await JSONResponse({"detail": detail}, status)(scope, receive, protected_send)

        headers = Headers(scope=scope)
        path, method = scope["path"], scope["method"]
        if (
            path.startswith(ADMIN_COOKIE_PATH + "/")
            and method not in {"GET", "HEAD", "OPTIONS"}
            and headers.getlist("origin") != [PUBLIC_ORIGIN]
        ):
            await refuse(403, "invalid_origin")
            return
        if path.startswith(PREFIX + "/") and method in {"POST", "PUT", "PATCH"}:
            empty_body = path == PREFIX + "/leave" and method == "POST"
            types = headers.getlist("content-type")
            if not empty_body and (
                len(types) != 1 or types[0].split(";", 1)[0].strip().lower() != "application/json"
            ):
                await refuse(415, "json_required")
                return
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > JSON_LIMIT:
                    await refuse(413, "request_too_large")
                    return
                if not message.get("more_body"):
                    break
            if empty_body and body:
                await refuse(422, "invalid_request")
                return
            original_receive = receive
            sent_body = False

            async def limited_receive():
                nonlocal sent_body
                if sent_body:
                    return await original_receive()
                sent_body = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}

            receive = limited_receive
        await self.app(scope, receive, protected_send)


class Store:
    def __init__(self, root: Path):
        self.releases = root / "releases"
        self.database = root / "rollout.sqlite3"
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.database, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with self.connection(transaction=False) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    @contextmanager
    def connection(self, *, write: bool = False, transaction: bool = True) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.database, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            if transaction:
                conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield conn
            except BaseException:
                if transaction:
                    conn.rollback()
                raise
            else:
                if transaction:
                    conn.commit()
        finally:
            conn.close()

    def candidate(self, version: str) -> Candidate | None:
        if not VERSION_RE.fullmatch(version) or self.releases.is_symlink():
            return None
        directory = self.releases / version
        if directory.is_symlink():
            return None
        try:
            manifest = directory / "release.json"
            descriptor = os.open(manifest, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    return None
                raw = source.read(MANIFEST_LIMIT + 1)
            if len(raw) > MANIFEST_LIMIT:
                return None
            candidate = Candidate.model_validate_json(raw)
            if candidate.version != version:
                return None
            for platform, asset in candidate.platforms.items():
                if asset.filename != f"VortoOne_{version}_{SUFFIXES[platform]}":
                    return None
                if not _valid_asset((directory / asset.filename).lstat()):
                    return None
        except (OSError, ValidationError):
            return None
        return candidate

    def candidates(self) -> list[Candidate]:
        try:
            versions = sorted(
                (item.name for item in self.releases.iterdir() if VERSION_RE.fullmatch(item.name)),
                key=_version_parts,
                reverse=True,
            )
        except FileNotFoundError:
            return []
        return [candidate for version in versions if (candidate := self.candidate(version)) is not None]

    def limit(self, kind: str, client: str, now: int, maximum: int) -> None:
        # A central counter shared by all ingress nodes and workers.
        with self.connection(write=True) as conn:
            conn.execute("DELETE FROM request_limits WHERE expires_at <= ?", (now,))
            attempts = conn.execute(
                """INSERT INTO request_limits(kind, client, expires_at, attempts) VALUES (?, ?, ?, 1)
                   ON CONFLICT(kind, client) DO UPDATE SET attempts = MIN(attempts + 1, ?)
                   RETURNING attempts, expires_at""",
                (kind, client, now + 60, maximum + 1),
            ).fetchone()
        if attempts["attempts"] > maximum:
            raise HTTPException(429, "rate_limited", headers={"Retry-After": str(attempts["expires_at"] - now)})

    def eligible(
        self, conn: sqlite3.Connection, device: sqlite3.Row, current: str
    ) -> Candidate | None:
        target = device["target_version"]
        if not device["active"] or target is None or _version_parts(current) >= _version_parts(target):
            return None
        if conn.execute("SELECT 1 FROM paused_releases WHERE version = ?", (target,)).fetchone():
            return None
        candidate = self.candidate(target)
        if candidate is None or device["platform"] not in candidate.platforms:
            return None
        return candidate


def _version_parts(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def _valid_asset(info: os.stat_result) -> bool:
    # Match the native updater's bound before offering an artifact it cannot download.
    return stat.S_ISREG(info.st_mode) and 0 < info.st_size <= ARTIFACT_LIMIT


def _at(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def hash_admin_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    rounds = 600000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return f"pbkdf2_sha256${rounds}${salt.hex()}${digest.hex()}"


def _password_config(value: str) -> tuple[int, bytes, bytes]:
    match = re.fullmatch(r"pbkdf2_sha256\$([0-9]{6,7})\$([a-f0-9]{32,128})\$([a-f0-9]{64})", value)
    if match is None:
        raise ValueError("ROLLOUT_ADMIN_PASSWORD_HASH must be a PBKDF2-SHA256 hash")
    rounds = int(match[1])
    if not 600000 <= rounds <= 2000000 or len(match[2]) % 2:
        raise ValueError("Invalid rollout password hash parameters")
    return rounds, bytes.fromhex(match[2]), bytes.fromhex(match[3])


def _client(request: Request) -> str:
    # Use the ASGI peer. The server's trusted-proxy configuration controls whether it is forwarded.
    return request.client.host if request.client else "unknown"


def _device(conn: sqlite3.Connection, request: Request, *, include_disabled: bool = False) -> sqlite3.Row:
    authorization = request.headers.getlist("authorization")
    match = re.fullmatch(r"Bearer ([A-Za-z0-9_-]{43})", authorization[0], re.IGNORECASE) if len(authorization) == 1 else None
    if match is not None:
        row = conn.execute("SELECT * FROM devices WHERE token_hash = ?", (_digest(match[1]),)).fetchone()
        if row is not None and (row["active"] or include_disabled):
            return row
    raise HTTPException(401, "device_auth_required")


def _session(request: Request) -> str:
    token = request.cookies.get(ADMIN_COOKIE, "")
    return token if TOKEN_RE.fullmatch(token) else ""


def _admin(conn: sqlite3.Connection, request: Request, now: int) -> str:
    token = _session(request)
    if token and conn.execute(
        "SELECT 1 FROM admin_sessions WHERE token_hash = ? AND expires_at > ?", (_digest(token), now)
    ).fetchone():
        return token
    raise HTTPException(401, "admin_auth_required")


def _admin_device(conn: sqlite3.Connection, device_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "device_not_found")
    return row


def _provided_status(device: sqlite3.Row, current: str, eligible: Candidate | None) -> str:
    target = device["target_version"]
    if target is None:
        return "enrolled"
    if _version_parts(current) >= _version_parts(target):
        return "up_to_date"
    if current == device["current_version"] and device["status"] in {"installing", "failed", "declined"}:
        return device["status"]
    return "update_available" if eligible is not None else "assigned"


def _assign_devices(store: Store, conn: sqlite3.Connection, device_ids: list[str], version: str | None) -> None:
    devices = [_admin_device(conn, device_id) for device_id in device_ids]
    if any(not device["active"] for device in devices):
        raise HTTPException(409, "device_disabled")
    if version is not None:
        candidate = store.candidate(version)
        if candidate is None:
            raise HTTPException(404, "release_not_found")
        if any(device["platform"] not in candidate.platforms for device in devices):
            raise HTTPException(409, "release_platform_unavailable")
    for device in devices:
        if version == device["target_version"]:
            continue
        status = "enrolled" if version is None else (
            "up_to_date" if _version_parts(device["current_version"]) >= _version_parts(version) else "assigned"
        )
        conn.execute("UPDATE devices SET target_version = ?, status = ? WHERE id = ?", (version, status, device["id"]))
        conn.execute("DELETE FROM tickets WHERE device_id = ?", (device["id"],))


def _retire(conn: sqlite3.Connection, device_id: str) -> None:
    conn.execute(
        "UPDATE devices SET active = 0, target_version = NULL, status = 'disabled' WHERE id = ?", (device_id,)
    )
    conn.execute("DELETE FROM tickets WHERE device_id = ?", (device_id,))


def create_app(
    data_dir: Path | None = None,
    admin_password_hash: str | None = None,
    *,
    clock: Callable[[], float] = time.time,
) -> FastAPI:
    """Build the standalone app. Run as `uvicorn server:create_app --factory`."""
    rounds, salt, expected_password = _password_config(
        admin_password_hash if admin_password_hash is not None else os.environ["ROLLOUT_ADMIN_PASSWORD_HASH"]
    )
    store = Store(Path(data_dir if data_dir is not None else os.environ["ROLLOUT_DATA_DIR"]))
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None, redirect_slashes=False)
    app.add_middleware(RequestPolicy)

    def now() -> int:
        return int(clock())

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _error):
        # FastAPI's default validation errors may include the submitted password or code.
        return JSONResponse({"detail": "invalid_request"}, 422)

    @app.exception_handler(Exception)
    async def unexpected_error(_request, _error):
        return JSONResponse({"detail": "server_error"}, 500, headers=PUBLIC_HEADERS)

    @app.get("/admin/releases/")
    def admin_page():
        return FileResponse(STATIC / "index.html", media_type="text/html")

    @app.get("/admin/releases/style.css")
    def admin_style():
        return FileResponse(STATIC / "style.css", media_type="text/css")

    @app.get("/admin/releases/app.js")
    def admin_script():
        return FileResponse(STATIC / "app.js", media_type="text/javascript")

    @app.post(PREFIX + "/enroll")
    def enroll(body: Enroll, request: Request):
        stamp = now()
        store.limit("enroll", _client(request), stamp, 20)
        device_id, token = secrets.token_hex(16), secrets.token_urlsafe(32)
        with store.connection(write=True) as conn:
            consumed = conn.execute(
                "DELETE FROM enrollments WHERE code_hash = ? AND expires_at > ?",
                (_digest(body.code.upper()), stamp),
            )
            if not consumed.rowcount:
                raise HTTPException(401, "invalid_enrollment_code")
            conn.execute(
                """INSERT INTO devices(id, token_hash, name, platform, current_version, last_seen_at, status, active)
                   VALUES (?, ?, ?, ?, ?, ?, 'enrolled', 1)""",
                (device_id, _digest(token), body.name, body.platform, body.version, stamp),
            )
        return {"token": token, "device_id": device_id}

    @app.post(PREFIX + "/check")
    def check(body: Check, request: Request):
        stamp = now()
        with store.connection(write=True) as conn:
            device = _device(conn, request)
            if device["platform"] != body.platform:
                raise HTTPException(409, "device_platform_changed")
            candidate = store.eligible(conn, device, body.version)
            conn.execute(
                "UPDATE devices SET current_version = ?, last_seen_at = ?, status = ? WHERE id = ?",
                (body.version, stamp, _provided_status(device, body.version, candidate), device["id"]),
            )
            conn.execute("DELETE FROM tickets WHERE expires_at <= ?", (stamp,))
            if candidate is None:
                return Response(status_code=204)
            asset = candidate.platforms[body.platform]
            ticket = secrets.token_urlsafe(16)
            conn.execute(
                "INSERT INTO tickets(ticket_hash, device_id, release, expires_at) VALUES (?, ?, ?, ?)",
                (_digest(ticket), device["id"], candidate.version, stamp + TICKET_SECONDS),
            )
        return {
            "version": candidate.version,
            "notes": candidate.notes,
            "platforms": {
                body.platform: {
                    "url": f"{PUBLIC_ORIGIN}{PREFIX}/download/{ticket}/{asset.filename}",
                    "signature": asset.signature,
                }
            },
        }

    @app.api_route(PREFIX + "/download/{ticket}/{filename}", methods=["GET", "HEAD"])
    def download(ticket: str, filename: str, request: Request):
        if not TICKET_RE.fullmatch(ticket):
            raise HTTPException(404, "download_unavailable")
        with store.connection() as conn:
            device = _device(conn, request)
            grant = conn.execute(
                "SELECT release FROM tickets WHERE ticket_hash = ? AND device_id = ? AND expires_at > ?",
                (_digest(ticket), device["id"], now()),
            ).fetchone()
            if grant is None or grant["release"] != device["target_version"]:
                raise HTTPException(404, "download_unavailable")
            candidate = store.eligible(conn, device, device["current_version"])
            if candidate is None or candidate.platforms[device["platform"]].filename != filename:
                raise HTTPException(404, "download_unavailable")
            file = store.releases / candidate.version / filename
            try:
                info = file.lstat()
            except OSError:
                raise HTTPException(404, "download_unavailable") from None
            if not _valid_asset(info):
                raise HTTPException(404, "download_unavailable")
        return FileResponse(file, media_type="application/octet-stream", filename=filename, stat_result=info)

    @app.post(PREFIX + "/report", status_code=204)
    def report(body: Report, request: Request):
        with store.connection(write=True) as conn:
            device = _device(conn, request, include_disabled=True)
            if not device["active"]:
                raise HTTPException(409, "device_disabled")
            if body.release != device["target_version"]:
                raise HTTPException(409, "release_not_assigned")
            if body.status == "installing" and store.eligible(conn, device, device["current_version"]) is None:
                raise HTTPException(409, "release_not_eligible")
            if _version_parts(device["current_version"]) >= _version_parts(body.release):
                raise HTTPException(409, "release_already_running")
            conn.execute(
                "UPDATE devices SET status = ?, last_seen_at = ? WHERE id = ?",
                (body.status, now(), device["id"]),
            )
        return Response(status_code=204)

    @app.post(PREFIX + "/leave", status_code=204)
    def leave(request: Request):
        with store.connection(write=True) as conn:
            device = _device(conn, request)
            _retire(conn, device["id"])
        return Response(status_code=204)

    @app.post(ADMIN_COOKIE_PATH + "/login", status_code=204)
    def login(body: Login, request: Request):
        stamp = now()
        store.limit("login", _client(request), stamp, 8)
        supplied = hashlib.pbkdf2_hmac("sha256", body.password.encode("utf-8"), salt, rounds)
        if not hmac.compare_digest(supplied, expected_password):
            raise HTTPException(401, "invalid_admin_password")
        token = secrets.token_urlsafe(32)
        with store.connection(write=True) as conn:
            conn.execute("DELETE FROM admin_sessions WHERE expires_at <= ?", (stamp,))
            prior = _session(request)
            if prior:
                conn.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (_digest(prior),))
            conn.execute(
                "INSERT INTO admin_sessions(token_hash, expires_at) VALUES (?, ?)",
                (_digest(token), stamp + SESSION_SECONDS),
            )
        response = Response(status_code=204)
        response.set_cookie(
            ADMIN_COOKIE, token, max_age=SESSION_SECONDS, path=ADMIN_COOKIE_PATH, secure=True,
            httponly=True, samesite="strict",
        )
        return response

    @app.post(ADMIN_COOKIE_PATH + "/logout", status_code=204)
    def logout(body: Empty, request: Request):
        with store.connection(write=True) as conn:
            token = _admin(conn, request, now())
            conn.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (_digest(token),))
        response = Response(status_code=204)
        response.delete_cookie(ADMIN_COOKIE, path=ADMIN_COOKIE_PATH, secure=True, httponly=True, samesite="strict")
        return response

    @app.get(ADMIN_COOKIE_PATH + "/state")
    def state(request: Request):
        with store.connection() as conn:
            _admin(conn, request, now())
            rows = conn.execute(
                """SELECT id, name, platform, current_version, target_version, last_seen_at, status, active
                   FROM devices ORDER BY last_seen_at DESC, id"""
            ).fetchall()
            paused = {row["version"] for row in conn.execute("SELECT version FROM paused_releases")}
        return {
            "devices": [
                {**dict(row), "last_seen_at": _at(row["last_seen_at"]), "active": bool(row["active"])}
                for row in rows
            ],
            "releases": [
                {
                    "version": release.version, "platforms": sorted(release.platforms),
                    "paused": release.version in paused, "created_at": release.created_at,
                }
                for release in store.candidates()
            ],
        }

    @app.post(ADMIN_COOKIE_PATH + "/enrollments")
    def enrollment_code(body: Empty, request: Request):
        stamp = now()
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(20))
        expires = stamp + ENROLLMENT_SECONDS
        with store.connection(write=True) as conn:
            _admin(conn, request, stamp)
            conn.execute("DELETE FROM enrollments WHERE expires_at <= ?", (stamp,))
            conn.execute("INSERT INTO enrollments(code_hash, expires_at) VALUES (?, ?)", (_digest(code), expires))
        return {"code": code, "expires_at": _at(expires)}

    @app.put(ADMIN_COOKIE_PATH + "/devices/{device_id}/assignment", status_code=204)
    def assign(device_id: DeviceId, body: Assignment, request: Request):
        with store.connection(write=True) as conn:
            _admin(conn, request, now())
            _assign_devices(store, conn, [device_id], body.version)
        return Response(status_code=204)

    @app.put(ADMIN_COOKIE_PATH + "/assignments", status_code=204)
    def assign_batch(body: BatchAssignment, request: Request):
        with store.connection(write=True) as conn:
            _admin(conn, request, now())
            _assign_devices(store, conn, body.device_ids, body.version)
        return Response(status_code=204)

    @app.delete(ADMIN_COOKIE_PATH + "/devices/{device_id}", status_code=204)
    def disable(device_id: DeviceId, request: Request):
        with store.connection(write=True) as conn:
            _admin(conn, request, now())
            _admin_device(conn, device_id)
            _retire(conn, device_id)
        return Response(status_code=204)

    @app.patch(ADMIN_COOKIE_PATH + "/releases/{version}", status_code=204)
    def pause(version: Version, body: Pause, request: Request):
        with store.connection(write=True) as conn:
            _admin(conn, request, now())
            if store.candidate(version) is None:
                raise HTTPException(404, "release_not_found")
            if body.paused:
                conn.execute("INSERT OR IGNORE INTO paused_releases(version) VALUES (?)", (version,))
                conn.execute("DELETE FROM tickets WHERE release = ?", (version,))
                conn.execute(
                    """UPDATE devices SET status = 'assigned'
                       WHERE active = 1 AND target_version = ? AND status = 'update_available'""",
                    (version,),
                )
            else:
                conn.execute("DELETE FROM paused_releases WHERE version = ?", (version,))
        return Response(status_code=204)

    return app
