"""独立的电脑配对与摘要授权；没有文件、命令或其它执行能力。"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import time
import uuid

from src.utils.state_dir import state_dir
from src.workflow.home_identity import HomeIdentityError, ensure_home_identity, pairing_owner

MAX_COMPUTERS = 256
PAIRING_SECONDS = 300
_ID = re.compile(r"[a-f0-9]{32}")
_DIGEST = re.compile(r"[a-f0-9]{64}")
_TOKEN = re.compile(r"vcc_[A-Za-z0-9_-]{43}")
_CODE = re.compile(r"[A-Z2-7]{12}")
_MAX_BYTES = 1024 * 1024
_BINDING = ("home_instance_id", "connector_id", "paired_principal_id", "credential_epoch", "trust_epoch")


class ComputerDenied(PermissionError):
    """配对或当前授权不成立。"""


class ComputerUnavailable(RuntimeError):
    """存储或身份无法安全核对。"""


def _name(value) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > 80
            or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        raise ValueError("电脑名称需要 1–80 个字符")
    return value.strip()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()


def _id(value) -> bool:
    return isinstance(value, str) and bool(_ID.fullmatch(value))


def _integer(value) -> bool:
    return type(value) is int and value >= 0


class ConnectedComputers:
    def __init__(self, root, *, clock=time.time):
        self.root = root
        self.directory = state_dir(root) / "connected-computers"
        self.path = self.directory / "grants.json"
        self.clock = clock

    def identity(self) -> dict:
        try:
            return ensure_home_identity(self.root)
        except (HomeIdentityError, OSError) as error:
            raise ComputerUnavailable("家的身份不可用") from error

    def _directory(self, *, create=False):
        if self.directory.is_symlink() or self.directory.parent.is_symlink():
            raise ComputerUnavailable("电脑授权目录不可用")
        if create:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.exists():
            info = self.directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or (os.name != "nt" and info.st_mode & 0o077):
                raise ComputerUnavailable("电脑授权目录权限过宽")

    @contextmanager
    def _lock(self):
        self._directory(create=True)
        try:
            fd = os.open(self.directory / ".lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        except OSError as error:
            raise ComputerUnavailable("电脑授权锁不可用") from error
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or (os.name != "nt" and info.st_mode & 0o077):
                raise ComputerUnavailable("电脑授权锁不可用")
            try:
                if os.name == "nt":
                    import msvcrt
                    if not info.st_size:
                        os.write(fd, b"0")
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise ComputerUnavailable("电脑授权正在更新，请重试") from error
            yield
        finally:
            os.close(fd)

    def _load(self) -> dict:
        self._directory()
        try:
            info = self.path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_BYTES
                    or (os.name != "nt" and info.st_mode & 0o077)):
                raise ComputerUnavailable("电脑授权文件不可用")
            fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as handle:
                opened = os.fstat(handle.fileno())
                if (not stat.S_ISREG(opened.st_mode) or opened.st_size > _MAX_BYTES
                        or (os.name != "nt" and opened.st_mode & 0o077)):
                    raise ValueError
                raw = handle.read(_MAX_BYTES + 1)
            if len(raw) > _MAX_BYTES:
                raise ValueError
            data = json.loads(raw)
            if (not isinstance(data, dict) or set(data) != {"schema_version", "computers", "pairing", "attempts", "created_pairings"}
                    or type(data.get("schema_version")) is not int or data["schema_version"] != 1
                    or not isinstance(data["computers"], list) or len(data["computers"]) > MAX_COMPUTERS
                    or any(not isinstance(data[k], list) or len(data[k]) > 32
                           or any(not _integer(v) for v in data[k]) for k in ("attempts", "created_pairings"))):
                raise ValueError
            seen = set()
            for item in data["computers"]:
                if (not isinstance(item, dict) or set(item) != {*_BINDING, "name", "digest", "created_at", "revoked_at"}
                        or any(not _id(item.get(k)) for k in _BINDING)
                        or item["connector_id"] in seen
                        or not isinstance(item["digest"], str) or not _DIGEST.fullmatch(item["digest"])
                        or not _integer(item["created_at"])
                        or (item["revoked_at"] is not None and not _integer(item["revoked_at"]))):
                    raise ValueError
                _name(item["name"])
                seen.add(item["connector_id"])
            pairing = data["pairing"]
            if pairing is not None:
                if (not isinstance(pairing, dict)
                        or set(pairing) != {"pairing_id", "digest", "expires_at", "failures", "home_instance_id", "paired_principal_id", "trust_epoch"}
                        or any(not _id(pairing.get(k)) for k in ("pairing_id", "home_instance_id", "paired_principal_id", "trust_epoch"))
                        or not isinstance(pairing["digest"], str) or not _DIGEST.fullmatch(pairing["digest"])
                        or not _integer(pairing["expires_at"]) or type(pairing["failures"]) is not int
                        or not 0 <= pairing["failures"] <= 5):
                    raise ValueError
            return data
        except FileNotFoundError:
            return {"schema_version": 1, "computers": [], "pairing": None, "attempts": [], "created_pairings": []}
        except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
            raise ComputerUnavailable("电脑授权存储不可用") from error

    def _save(self, data):
        fd, temporary = tempfile.mkstemp(prefix=".pending-", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            if os.name != "nt":
                directory = os.open(self.directory, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)

    @staticmethod
    def _public(item):
        return {k: item[k] for k in (*_BINDING, "name", "created_at", "revoked_at")}

    @staticmethod
    def _current(item, identity):
        return (identity["ownership_state"] == "claimed" and item["revoked_at"] is None
                and item["home_instance_id"] == identity["home_instance_id"]
                and item["paired_principal_id"] == identity["owner_subject"]
                and item["trust_epoch"] == identity["trust_epoch"])

    def _pairing_identity(self, *, legacy_confirmed=False) -> dict:
        try:
            return pairing_owner(self.root, legacy_confirmed=legacy_confirmed)
        except PermissionError as error:
            raise ComputerDenied(str(error)) from error
        except (HomeIdentityError, OSError) as error:
            raise ComputerUnavailable("家的身份不可用") from error

    def create_pairing(self, *, legacy_confirmed=False) -> dict:
        identity = self._pairing_identity(legacy_confirmed=legacy_confirmed)
        now = int(self.clock())
        code = "".join(secrets.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567") for _ in range(12))
        with self._lock():
            data = self._load()
            recent = [tick for tick in data["created_pairings"] if tick > now - 60]
            if len(recent) >= 6:
                raise ComputerDenied("配对码生成过于频繁，请稍后重试")
            pairing = {"pairing_id": uuid.uuid4().hex, "digest": _digest(code), "expires_at": now + PAIRING_SECONDS,
                       "failures": 0, "home_instance_id": identity["home_instance_id"],
                       "paired_principal_id": identity["owner_subject"], "trust_epoch": identity["trust_epoch"]}
            # 仅一份活动码，错误尝试可以确切计入该码；新码立即顶掉旧码。
            data.update(pairing=pairing, created_pairings=recent + [now])
            self._save(data)
        if self.identity() != identity:
            raise ComputerDenied("家的所有者或信任已变化")
        return {k: pairing[k] for k in ("pairing_id", "expires_at", "home_instance_id")} | {"code": code}

    def cancel_pairing(self, pairing_id):
        if not _id(pairing_id):
            raise ValueError("配对编号无效")
        with self._lock():
            data = self._load()
            if data["pairing"] and data["pairing"]["pairing_id"] == pairing_id:
                data["pairing"] = None
                self._save(data)

    def pair(self, code, connector_id, name) -> dict:
        if not _id(connector_id):
            raise ValueError("电脑编号需要 32 位小写十六进制字符")
        name = _name(name)
        now, identity = int(self.clock()), self.identity()
        with self._lock():
            data = self._load()
            attempts = [tick for tick in data["attempts"] if tick > now - 60]
            if len(attempts) >= 30:
                raise ComputerDenied("配对尝试过于频繁，请稍后重试")
            data["attempts"] = attempts + [now]
            pairing = data["pairing"]
            correct = (pairing is not None and now < pairing["expires_at"] and pairing["failures"] < 5
                       and isinstance(code, str) and bool(_CODE.fullmatch(code))
                       and hmac.compare_digest(pairing["digest"], _digest(code))
                       and identity["ownership_state"] == "claimed"
                       and pairing["home_instance_id"] == identity["home_instance_id"]
                       and pairing["paired_principal_id"] == identity["owner_subject"]
                       and pairing["trust_epoch"] == identity["trust_epoch"])
            if not correct:
                if pairing:
                    pairing["failures"] = min(5, pairing["failures"] + 1)
                self._save(data)
                raise ComputerDenied("配对码无效、过期或已使用")
            try:
                item, token = self._issue(data, identity, connector_id, name, now)
            except ComputerDenied:
                self._save(data)
                raise
            data["pairing"] = None
            self._save(data)
        binding = {k: item[k] for k in _BINDING}
        if not self.check(binding):
            raise ComputerDenied("家的所有者或信任已变化")
        return binding | {"token": token}

    @staticmethod
    def _issue(data, identity, connector_id, name, now):
        if len(data["computers"]) >= MAX_COMPUTERS:
            raise ComputerDenied("电脑授权记录已达上限")
        if any(item["connector_id"] == connector_id for item in data["computers"]):
            raise ComputerDenied("这个电脑编号已使用，请在本机重新建立安装身份")
        token = "vcc_" + secrets.token_urlsafe(32)
        item = {"home_instance_id": identity["home_instance_id"], "connector_id": connector_id,
                "paired_principal_id": identity["owner_subject"], "credential_epoch": uuid.uuid4().hex,
                "trust_epoch": identity["trust_epoch"], "name": name, "digest": _digest(token),
                "created_at": now, "revoked_at": None}
        data["computers"].append(item)
        return item, token

    def pair_native(self, connector_id, name, *, expected_identity=None) -> dict:
        """仅可信 native 启动票据验证后调用；不接受 owner-key 作为调用本方法的凭证。"""
        if not _id(connector_id):
            raise ValueError("电脑编号需要 32 位小写十六进制字符")
        name = _name(name)
        identity = self._pairing_identity(legacy_confirmed=False)
        if expected_identity is not None and expected_identity != {
                key: identity[key] for key in ("home_instance_id", "trust_epoch")}:
            raise ComputerDenied("家的所有者或信任已变化")
        with self._lock():
            data = self._load()
            item, token = self._issue(data, identity, connector_id, name, int(self.clock()))
            # 自动登记不生成、消费或顶掉真人的码，也不改变人工失败/发码计数。
            self._save(data)
        binding = {key: item[key] for key in _BINDING}
        if not self.check(binding):
            raise ComputerDenied("家的所有者或信任已变化")
        return binding | {"token": token}

    def authenticate(self, token) -> dict:
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise ComputerDenied("电脑凭据无效")
        identity = self.identity()
        found = next((item for item in self._load()["computers"]
                      if hmac.compare_digest(item["digest"], _digest(token)) and self._current(item, identity)), None)
        if found is None:
            raise ComputerDenied("电脑凭据无效或已撤销")
        binding = {k: found[k] for k in _BINDING}
        if not self.check(binding):
            raise ComputerDenied("电脑凭据无效或已撤销")
        return binding

    def list(self) -> list:
        identity = self.identity()
        return [self._public(item) | {"active": self._current(item, identity)} for item in self._load()["computers"]]

    def rename(self, connector_id, name):
        name = _name(name)
        with self._lock():
            data = self._load()
            item = next((item for item in data["computers"] if item["connector_id"] == connector_id), None)
            if item is None:
                raise KeyError(connector_id)
            item["name"] = name
            self._save(data)
            return self._public(item)

    def revoke(self, connector_id) -> dict:
        with self._lock():
            data = self._load()
            item = next((item for item in data["computers"] if item["connector_id"] == connector_id), None)
            if item is None:
                raise KeyError(connector_id)
            if item["revoked_at"] is None:
                item.update(revoked_at=int(self.clock()), credential_epoch=uuid.uuid4().hex)
                self._save(data)
            return self._public(item)

    def check(self, binding) -> bool:
        if not isinstance(binding, dict) or any(not _id(binding.get(k)) for k in _BINDING):
            return False
        identity = self.identity()
        valid = any(self._current(item, identity) and all(item[k] == binding[k] for k in _BINDING)
                    for item in self._load()["computers"])
        return valid and self.identity() == identity
