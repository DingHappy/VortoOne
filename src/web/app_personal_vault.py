"""配置了本机私密目录的个人库。仅由 App API 管理，不连接工具运行时。"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid
from collections import Counter
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
from urllib.parse import urlsplit

from src.utils.state_dir import state_dir
from src.workflow.home_identity import ensure_home_identity

DIRECTORY = ".vortoone-personal-vault"
MAX_BODY = 32_768
_MAX_CSV_BYTES = 2 * 1024 * 1024
_MAX_KEYS_BYTES = 512 * 1024
_MAX_IMPORT_KEYS = 200
_MAX_IMPORT_ROWS = 10_000
# JSON escapes can use six bytes for a single ASCII byte, plus the request fields.
MAX_CSV_BODY = 6 * _MAX_CSV_BYTES + 512
MAX_KEYS_BODY = 6 * _MAX_KEYS_BYTES + 64 * 1024
_MAX_SECRET = 16_384
_MAX_ITEMS = 64
_MAX_FILE = 8 * 1024 * 1024
_FORMAT = b"VOPV1"
_KEY_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_ACCESS = {"everyone": False, "roleIds": [], "userIds": [], "allAgents": False, "agentIds": []}
_FIELDS = {"title", "description", "website", "match", "username", "secret", "keyName", "access"}


class VaultError(ValueError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


def _unavailable() -> VaultError:
    return VaultError(503, "vault.unavailable", "此服务的个人 Vault 未启用或暂不可用")


def _invalid() -> VaultError:
    return VaultError(422, "vault.invalid", "个人 Vault 条目不符合要求")


def too_large() -> VaultError:
    return VaultError(413, "vault.too_large", "个人 Vault 请求过大")


def _private(info, mode: int, *, directory: bool = False) -> bool:
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    return (kind(info.st_mode) and info.st_uid == os.geteuid()
            and stat.S_IMODE(info.st_mode) == mode and (directory or info.st_nlink == 1))


def _open(path: Path, flags: int = os.O_RDONLY):
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not _private(os.fstat(fd), 0o600):
            raise _unavailable()
        return os.fdopen(fd, "rb" if flags == os.O_RDONLY else "r+b")
    except BaseException:
        os.close(fd)
        raise


def _directory(root: str) -> Path:
    configured = os.environ.get("VORTOONE_PERSONAL_VAULT_DIR")
    if os.name != "posix" or not configured:
        raise _unavailable()
    path = Path(configured)
    if not path.is_absolute() or path.name != DIRECTORY or not _private(path.lstat(), 0o700, directory=True):
        raise _unavailable()
    path = path.resolve(strict=True)
    home = Path(root).resolve(strict=True)
    active_state = state_dir(root).resolve()
    if (path.name != DIRECTORY or any(path.is_relative_to(base) or base.is_relative_to(path)
                                      for base in (home, active_state))):
        raise _unavailable()
    return path


def _cipher(directory: Path):
    # Desktop 运行包已带 cryptography；没有现有可选依赖的部署保持关闭。
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    with _open(directory / "master.key") as handle:
        key = handle.read(33)
    if len(key) != 32:
        raise _unavailable()
    return AESGCM(key)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _text(value, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise _invalid()
    value = value.strip()
    if (required and not value) or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in value):
        raise _invalid()
    return value


def _website(value) -> str:
    url = _text(value, 2048, required=True)
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise _invalid() from None
    if (parts.scheme != "https" or not parts.hostname or "@" in parts.netloc or port == 0 or parts.path
            or any(char in url for char in ("\\", "?", "#")) or any(char.isspace() for char in url)):
        raise _invalid()
    return f"https://{parts.netloc.lower()}"


def _check_access(body: dict, *, creating: bool) -> None:
    if creating and body.get("scope") != "personal":
        raise VaultError(403, "vault.personal_only", "此服务仅提供个人 Vault")
    if "access" in body:
        access = body["access"]
        if (not isinstance(access, dict) or set(access) - set(_ACCESS)
                or any(type(value) is not type(_ACCESS[field]) or value != _ACCESS[field]
                       for field, value in access.items())):
            raise VaultError(403, "vault.sharing_unavailable", "个人 Vault 暂不支持向成员或 Agent 授权")


def _apply(item: dict, body: dict, *, creating: bool) -> None:
    if set(body) - (_FIELDS | {"kind", "scope"} if creating else _FIELDS):
        raise _invalid()
    _check_access(body, creating=creating)
    kind = item["kind"]
    if kind not in ("login", "key"):
        raise _invalid()
    if set(body) & ({"keyName"} if kind == "login" else {"website", "match", "username"}):
        raise _invalid()
    if creating or "title" in body:
        item["title"] = _text(body.get("title"), 120, required=True)
    if "description" in body:
        item["description"] = _text(body["description"], 300)
    if creating or "secret" in body:
        secret = body.get("secret")
        if (not isinstance(secret, str) or not secret
                or len(secret.encode("utf-8", errors="surrogatepass")) > _MAX_SECRET
                or any(0xD800 <= ord(char) <= 0xDFFF for char in secret)):
            raise _invalid()
        item["secret"] = secret
    if kind == "login":
        if creating or "website" in body:
            item["website"] = _website(body.get("website"))
        if "match" in body:
            if body["match"] != "exact":
                raise _invalid()
            item["match"] = "exact"
        if "username" in body:
            item["username"] = _text(body["username"], 200) or None
    elif creating or "keyName" in body:
        name = _text(body.get("keyName"), 128, required=True)
        if not _KEY_NAME.fullmatch(name):
            raise _invalid()
        item["keyName"] = name


def _new_item(body: dict) -> dict:
    now = _timestamp()
    item = {"id": uuid.uuid4().hex, "kind": body.get("kind"), "description": "",
            "createdAt": now, "updatedAt": now}
    _apply(item, body, creating=True)
    return item


def _keep_new(items: dict, item: dict) -> None:
    if len(items) >= _MAX_ITEMS:
        raise VaultError(409, "vault.limit", "个人 Vault 条目已达上限")
    items[item["id"]] = item


def _csv_format(header: list[str]) -> tuple[str, str, str, str]:
    columns = set(header)
    if not 3 <= len(header) <= 128 or len(columns) != len(header) or "" in columns:
        raise _invalid()
    bitwarden = ("login_uri", "login_username", "login_password")
    if set(bitwarden) <= columns:
        return ("bitwarden", *bitwarden)
    if {"title", "website", "username", "password"} <= columns:
        return ("1password", "website", "username", "password")
    if {"url", "username", "password"} <= columns:
        return ("chrome" if "name" in columns else "generic", "url", "username", "password")
    if {"website", "username", "password"} <= columns:
        return ("generic", "website", "username", "password")
    raise _invalid()


def _login_origin(raw: str) -> str:
    raw = raw.strip()
    if any(ord(char) < 32 or char.isspace() or char == "\\" for char in raw):
        raise _invalid()
    try:
        url = urlsplit(raw)
    except ValueError:
        raise _invalid() from None
    if any(mark in url.netloc for mark in ",;\"'"):
        raise _invalid()
    return _website(f"{url.scheme}://{url.netloc}")


def _login_key(item: dict) -> tuple[str | None, int, str | None]:
    url = urlsplit(item["website"])
    return url.hostname, url.port or 443, item.get("username")


def _csv_logins(value: str) -> tuple[str, list[dict], Counter[str]]:
    if not isinstance(value, str):
        raise _invalid()
    try:
        length = len(value.encode("utf-8"))
    except UnicodeError:
        raise _invalid() from None
    if length > _MAX_CSV_BYTES:
        raise too_large()

    reader = csv.reader(io.StringIO(value.removeprefix("\ufeff"), newline=""), strict=True)
    logins: list[dict] = []
    skipped: Counter[str] = Counter()
    try:
        header = [part.strip().lower() for part in next(reader, [])]
        format_name, site_column, user_column, secret_column = _csv_format(header)
        for index, row in enumerate(reader):
            if index >= _MAX_IMPORT_ROWS:
                raise too_large()
            if not row:
                continue
            if len(row) != len(header):
                skipped["invalid"] += 1
                continue
            fields = dict(zip(header, row))
            if format_name == "bitwarden" and fields.get("type", "login") != "login":
                skipped["invalid"] += 1
                continue
            secret = fields[secret_column]
            if not secret:
                skipped["no_password"] += 1
                continue
            try:
                site = _login_origin(fields[site_column])
            except VaultError:
                skipped["not_https"] += 1
                continue
            try:
                logins.append(_new_item({
                    "kind": "login", "scope": "personal", "website": site,
                    "title": fields.get("name", "").strip() or fields.get("title", "").strip() or site,
                    "username": fields[user_column], "secret": secret,
                }))
            except VaultError:
                skipped["invalid"] += 1
    except csv.Error:
        raise _invalid() from None
    return format_name, logins, skipped


def _key_imports(entries: list[dict]) -> list[tuple[dict, bool]]:
    if not isinstance(entries, list) or not entries:
        raise _invalid()
    if len(entries) > _MAX_IMPORT_KEYS:
        raise too_large()
    parsed = []
    names = set()
    total_bytes = 0
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"keyName", "secret", "replace"}:
            raise _invalid()
        name = _text(entry["keyName"], 128, required=True)
        replace = entry["replace"]
        if type(replace) is not bool or name in names:
            raise _invalid()
        item = _new_item({"title": name[:120], "kind": "key", "scope": "personal",
                          "keyName": name, "secret": entry["secret"]})
        total_bytes += len(item["secret"].encode("utf-8"))
        if total_bytes > _MAX_KEYS_BYTES:
            raise too_large()
        names.add(name)
        parsed.append((item, replace))
    return parsed


def _metadata(item: dict, user_id: str) -> dict:
    return {
        "id": item["id"], "kind": item["kind"], "scope": "personal", "ownerUserId": user_id,
        "title": item["title"], "description": item["description"], "website": item.get("website"),
        "match": item.get("match", "exact"), "username": item.get("username"), "keyName": item.get("keyName"),
        "keyHint": None, "hasSecret": bool(item["secret"]), "contact": None, "connectionId": None,
        "sourceName": None, "accessFromConnection": False, "access": _ACCESS, "missing": False,
        "createdByUserId": user_id, "createdByAgentId": None, "lastUsedAt": None,
        "createdAt": item["createdAt"], "updatedAt": item["updatedAt"], "can": {"edit": True, "delete": True},
    }


class PersonalVault:
    def __init__(self, root: str, user_id: str, *, member_account: str | None = None):
        self.user_id = user_id
        try:
            self.directory = _directory(root)
            self.cipher = _cipher(self.directory)
            home = ensure_home_identity(root)
            subject = ("member", member_account, user_id) if member_account else ("owner", home["owner_subject"])
            if home["ownership_state"] != "claimed" or not home["owner_subject"]:
                raise VaultError(403, "vault.home_unclaimed", "认领这个家后才可以使用个人 Vault")
            # 恢复和重新认领切换代次。使用独立文件，旧会话不能读旧库，新库不会覆盖它。
            namespace = [home["home_instance_id"], home["trust_epoch"], *subject]
            self.aad = _FORMAT + json.dumps(namespace, separators=(",", ":")).encode()
            self.path = self.directory / (hashlib.sha256(self.aad).hexdigest() + ".sealed")
        except (OSError, ImportError, RuntimeError):
            raise _unavailable() from None

    def _read(self) -> dict:
        from cryptography.exceptions import InvalidTag

        try:
            with _open(self.path) as handle:
                blob = handle.read(_MAX_FILE + 1)
            if not blob.startswith(_FORMAT) or len(blob) > _MAX_FILE:
                raise _unavailable()
            start = len(_FORMAT)
            items = json.loads(self.cipher.decrypt(blob[start:start + 12], blob[start + 12:], self.aad))
            if not isinstance(items, dict):
                raise _unavailable()
            return items
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, InvalidTag):
            raise _unavailable() from None

    def _write(self, items: dict) -> None:
        nonce = os.urandom(12)
        blob = _FORMAT + nonce + self.cipher.encrypt(nonce, json.dumps(items).encode(), self.aad)
        fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=self.directory)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(blob)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.directory, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            Path(temporary).unlink(missing_ok=True)

    @contextmanager
    def _change(self):
        import fcntl

        try:
            with _open(self.path.with_suffix(".lock"), os.O_RDWR | os.O_CREAT) as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                items = self._read()
                yield items
                self._write(items)
        except OSError:
            raise _unavailable() from None

    @staticmethod
    def _item(items: dict, item_id: str) -> dict:
        if item_id not in items:
            raise VaultError(404, "vault.not_found", "没有这个 Vault 条目")
        return items[item_id]

    def overview(self) -> dict:
        return {"items": [_metadata(item, self.user_id) for item in self._read().values()],
                "connections": [], "sources": [], "canManageTeam": False}

    def item(self, item_id: str) -> dict:
        return {**_metadata(self._item(self._read(), item_id), self.user_id), "uses": [], "unreadable": False}

    def create(self, body: dict) -> dict:
        item = _new_item(body)
        with self._change() as items:
            _keep_new(items, item)
        return _metadata(item, self.user_id)

    def import_csv(self, body: dict) -> dict:
        if set(body) != {"csv", "scope", "dryRun"} or type(body["dryRun"]) is not bool:
            raise _invalid()
        _check_access(body, creating=True)
        format_name, logins, skipped = _csv_logins(body["csv"])
        added = updated = 0
        with nullcontext(self._read()) if body["dryRun"] else self._change() as items:
            saved: dict[tuple[str | None, int, str | None], list[dict]] = {}
            for item in items.values():
                if item["kind"] == "login":
                    saved.setdefault(_login_key(item), []).append(item)
            seen = set()
            for login in logins:
                key = _login_key(login)
                candidates = saved.get(key, [])
                if key in seen or len(candidates) > 1 or (candidates and candidates[0]["secret"] == login["secret"]):
                    skipped["duplicate"] += 1
                elif not candidates:
                    _keep_new(items, login)
                    added += 1
                else:
                    candidates[0].update(secret=login["secret"], updatedAt=login["updatedAt"])
                    updated += 1
                seen.add(key)
        return {"added": added, "updated": updated, "format": format_name,
                "skipped": [{"reason": reason, "count": count} for reason, count in sorted(skipped.items())]}

    def import_keys(self, body: dict) -> dict:
        if set(body) - {"scope", "access", "keys"}:
            raise _invalid()
        _check_access(body, creating=True)
        incoming = _key_imports(body.get("keys"))
        added = replaced = skipped = 0
        with self._change() as items:
            saved: dict[str, list[dict]] = {}
            for item in items.values():
                if item["kind"] == "key":
                    saved.setdefault(item["keyName"], []).append(item)
            for key, replace in incoming:
                candidates = saved.get(key["keyName"], [])
                if not candidates:
                    _keep_new(items, key)
                    added += 1
                elif not replace:
                    skipped += 1
                elif len(candidates) != 1:
                    raise VaultError(409, "vault.conflict", "同名密钥有多个，请先在个人 Vault 中处理")
                else:
                    if candidates[0]["secret"] != key["secret"]:
                        candidates[0].update(secret=key["secret"], updatedAt=key["updatedAt"])
                    replaced += 1
        return {"added": added, "replaced": replaced, "skipped": skipped}

    def update(self, item_id: str, body: dict) -> dict:
        with self._change() as items:
            item = self._item(items, item_id)
            _apply(item, body, creating=False)
            item["updatedAt"] = _timestamp()
        return _metadata(item, self.user_id)

    def remove(self, item_id: str) -> None:
        with self._change() as items:
            self._item(items, item_id)
            del items[item_id]
