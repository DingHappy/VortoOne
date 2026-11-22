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
from src.workflow.members.registry import MAX_MEMBERS, MemberRegistry, Missing, is_member_id

MAX_COMPUTERS = 256
MAX_MEMBER_COMPUTERS = 256
MAX_COMPUTERS_PER_MEMBER = 8
PAIRING_SECONDS = 300
_ID = re.compile(r"[a-f0-9]{32}")
_DIGEST = re.compile(r"[a-f0-9]{64}")
_TOKEN = re.compile(r"vcc_[A-Za-z0-9_-]{43}")
_CODE = re.compile(r"[A-Z2-7]{12}")
_MAX_BYTES = 1024 * 1024
_BINDING = ("home_instance_id", "connector_id", "paired_principal_id", "credential_epoch", "trust_epoch")
_PAIRING_FIELDS = {"pairing_id", "digest", "expires_at", "failures", "home_instance_id", "paired_principal_id", "trust_epoch"}
_GRANT_FIELDS = {*_BINDING, "name", "digest", "created_at", "revoked_at"}
_ANY_PAIRER = object()


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


def _member_subject(home_id: str, member_id: str) -> str:
    return uuid.uuid5(uuid.UUID(hex=home_id), f"vortoone:computer-member:{member_id}").hex


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
            required = {"schema_version", "computers", "pairing", "attempts", "created_pairings"}
            optional = {"member_pairings", "member_computers"}
            if (not isinstance(data, dict) or not required <= data.keys() <= required | optional
                    or type(data.get("schema_version")) is not int or data["schema_version"] != 1
                    or not isinstance(data["computers"], list) or len(data["computers"]) > MAX_COMPUTERS
                    or any(not isinstance(data[k], list) or len(data[k]) > 32
                           or any(not _integer(v) for v in data[k]) for k in ("attempts", "created_pairings"))):
                raise ValueError
            member_pairings = data.setdefault("member_pairings", {})
            member_computers = data.setdefault("member_computers", [])
            if (not isinstance(member_pairings, dict) or len(member_pairings) > MAX_MEMBERS
                    or not isinstance(member_computers, list) or len(member_computers) > MAX_MEMBER_COMPUTERS):
                raise ValueError
            self._check_grants(data["computers"], member_computers)
            self._check_pairing(data["pairing"])
            for member_id, slot in member_pairings.items():
                if (not is_member_id(member_id) or not isinstance(slot, dict) or set(slot) != {"pairing", "created_pairings"}
                        or not isinstance(slot["created_pairings"], list) or len(slot["created_pairings"]) > 6
                        or any(not _integer(tick) for tick in slot["created_pairings"])):
                    raise ValueError
                self._check_pairing(slot["pairing"], member_id=member_id)
            return data
        except FileNotFoundError:
            return {"schema_version": 1, "computers": [], "pairing": None, "attempts": [], "created_pairings": [],
                    "member_computers": [], "member_pairings": {}}
        except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
            raise ComputerUnavailable("电脑授权存储不可用") from error

    @staticmethod
    def _check_grants(owner_rows, member_rows):
        seen = set()
        for rows, members in ((owner_rows, False), (member_rows, True)):
            fields = _GRANT_FIELDS | ({"paired_member_id"} if members else set())
            for item in rows:
                if (not isinstance(item, dict) or set(item) != fields or any(not _id(item.get(k)) for k in _BINDING)
                        or item["connector_id"] in seen
                        or not isinstance(item["digest"], str) or not _DIGEST.fullmatch(item["digest"])
                        or not _integer(item["created_at"])
                        or (item["revoked_at"] is not None and not _integer(item["revoked_at"]))):
                    raise ValueError
                if members and (not is_member_id(item["paired_member_id"]) or item["revoked_at"] is not None
                                or item["paired_principal_id"] != _member_subject(item["home_instance_id"], item["paired_member_id"])):
                    raise ValueError
                _name(item["name"])
                seen.add(item["connector_id"])

    @staticmethod
    def _check_pairing(pairing, *, member_id=None):
        if pairing is None:
            return
        fields = _PAIRING_FIELDS | ({"paired_member_id"} if member_id is not None else set())
        if (not isinstance(pairing, dict) or set(pairing) != fields
                or any(not _id(pairing.get(k)) for k in ("pairing_id", "home_instance_id", "paired_principal_id", "trust_epoch"))
                or not isinstance(pairing["digest"], str) or not _DIGEST.fullmatch(pairing["digest"])
                or not _integer(pairing["expires_at"]) or type(pairing["failures"]) is not int
                or not 0 <= pairing["failures"] <= 5):
            raise ValueError
        if member_id is not None and (pairing["paired_member_id"] != member_id
                                      or pairing["paired_principal_id"] != _member_subject(pairing["home_instance_id"], member_id)):
            raise ValueError

    def _save(self, data):
        # Owner-only stores keep the original format until a member uses pairing.
        payload = {key: value for key, value in data.items()
                   if key not in {"member_computers", "member_pairings"} or value}
        fd, temporary = tempfile.mkstemp(prefix=".pending-", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
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

    def _require_member(self, member_id, *, pairing=False):
        if not is_member_id(member_id):
            raise ComputerDenied("需要在册成员")
        try:
            standing = MemberRegistry(self.root).standing(member_id)
        except Missing as error:
            raise ComputerDenied("需要在册成员") from error
        except (ValueError, OSError) as error:
            raise ComputerUnavailable("成员登记不可用") from error
        if pairing and "computers.connect_own" not in standing["permissions"]:
            raise ComputerDenied("没有连接自己电脑的权限")

    @staticmethod
    def _same_home(item, identity):
        return (identity["ownership_state"] == "claimed" and item["home_instance_id"] == identity["home_instance_id"]
                and item["trust_epoch"] == identity["trust_epoch"])

    def _valid_scope(self, item, identity, *, pairing=False):
        if not self._same_home(item, identity):
            return False
        member_id = item.get("paired_member_id")
        if member_id is None:
            return item["paired_principal_id"] == identity["owner_subject"]
        if (item["paired_principal_id"] == identity["owner_subject"]
                or item["paired_principal_id"] != _member_subject(item["home_instance_id"], member_id)):
            return False
        try:
            self._require_member(member_id, pairing=pairing)
        except ComputerDenied:
            return False
        return True

    def _current(self, item, identity):
        return item["revoked_at"] is None and self._valid_scope(item, identity)

    def _pairing_identity(self, *, legacy_confirmed=False) -> dict:
        try:
            return pairing_owner(self.root, legacy_confirmed=legacy_confirmed)
        except PermissionError as error:
            raise ComputerDenied(str(error)) from error
        except (HomeIdentityError, OSError) as error:
            raise ComputerUnavailable("家的身份不可用") from error

    @staticmethod
    def _slot(data, member_id, *, now=None):
        if member_id is None:
            return data
        slots = data["member_pairings"]
        if now is None:
            return slots.get(member_id)
        for expired in [key for key, slot in slots.items()
                        if (not slot["pairing"] or slot["pairing"]["expires_at"] <= now)
                        and not any(tick > now - 60 for tick in slot["created_pairings"])]:
            del slots[expired]
        if member_id not in slots and len(slots) >= MAX_MEMBERS:
            raise ComputerDenied("活动配对已达上限")
        return slots.setdefault(member_id, {"pairing": None, "created_pairings": []})

    def create_pairing(self, *, legacy_confirmed=False, member_id=None) -> dict:
        if member_id is not None:
            self._require_member(member_id, pairing=True)
        identity = self._pairing_identity(legacy_confirmed=member_id is None and legacy_confirmed)
        subject = identity["owner_subject"] if member_id is None else _member_subject(identity["home_instance_id"], member_id)
        now = int(self.clock())
        with self._lock():
            data = self._load()
            slot = self._slot(data, member_id, now=now)
            recent = [tick for tick in slot["created_pairings"] if tick > now - 60]
            if len(recent) >= 6:
                raise ComputerDenied("配对码生成过于频繁，请稍后重试")
            digests = {candidate["pairing"]["digest"] for candidate in (data, *data["member_pairings"].values())
                       if candidate["pairing"] is not None}
            for _ in range(3):
                code = "".join(secrets.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567") for _ in range(12))
                digest = _digest(code)
                if digest not in digests:
                    break
            else:
                raise ComputerUnavailable("无法分配独立配对码")
            pairing = {"pairing_id": uuid.uuid4().hex, "digest": digest, "expires_at": now + PAIRING_SECONDS,
                       "failures": 0, "home_instance_id": identity["home_instance_id"],
                       "paired_principal_id": subject, "trust_epoch": identity["trust_epoch"]}
            if member_id is not None:
                pairing["paired_member_id"] = member_id
            slot.update(pairing=pairing, created_pairings=recent + [now])
            self._save(data)
        if self.identity() != identity or not self._valid_scope(pairing, identity, pairing=True):
            raise ComputerDenied("配对人的成员身份或家的信任已变化")
        return {k: pairing[k] for k in ("pairing_id", "expires_at", "home_instance_id")} | {"code": code}

    def cancel_pairing(self, pairing_id, *, member_id=None):
        if not _id(pairing_id):
            raise ValueError("配对编号无效")
        if member_id is not None:
            self._require_member(member_id)
        with self._lock():
            data = self._load()
            slot = self._slot(data, member_id)
            if slot and slot["pairing"] and slot["pairing"]["pairing_id"] == pairing_id:
                slot["pairing"] = None
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
            digest = _digest(code) if isinstance(code, str) and _CODE.fullmatch(code) else ""
            slot = next((item for item in (data, *data["member_pairings"].values()) if item["pairing"]
                         and hmac.compare_digest(item["pairing"]["digest"], digest)), None)
            pairing = slot["pairing"] if slot else None
            correct = (pairing is not None and now < pairing["expires_at"] and pairing["failures"] < 5
                       and self._valid_scope(pairing, identity, pairing=True))
            if not correct:
                failed = pairing or data["pairing"]
                if failed:
                    failed["failures"] = min(5, failed["failures"] + 1)
                self._save(data)
                raise ComputerDenied("配对码无效、过期或已使用")
            try:
                item, token = self._issue(data, identity, connector_id, name, now, member_id=pairing.get("paired_member_id"))
            except ComputerDenied:
                self._save(data)
                raise
            slot["pairing"] = None
            self._save(data)
        binding = {k: item[k] for k in _BINDING}
        if not self._valid_scope(pairing, identity, pairing=True) or not self.check(binding):
            raise ComputerDenied("配对人的成员身份或家的信任已变化")
        return binding | {"token": token}

    def _issue(self, data, identity, connector_id, name, now, *, member_id=None):
        rows = data["computers"]
        subject = identity["owner_subject"]
        if member_id is not None:
            try:
                members = MemberRegistry(self.root).snapshot()["members"]
            except (ValueError, OSError) as error:
                raise ComputerUnavailable("成员登记不可用") from error
            rows = data["member_computers"]
            rows[:] = [row for row in rows if row["paired_member_id"] in members and self._same_home(row, identity)]
            if (len(rows) >= MAX_MEMBER_COMPUTERS
                    or sum(row["paired_member_id"] == member_id for row in rows) >= MAX_COMPUTERS_PER_MEMBER):
                raise ComputerDenied("成员电脑授权记录已达上限")
            subject = _member_subject(identity["home_instance_id"], member_id)
        elif len(rows) >= MAX_COMPUTERS:
            raise ComputerDenied("电脑授权记录已达上限")
        if any(item["connector_id"] == connector_id for item in (*data["computers"], *data["member_computers"])):
            raise ComputerDenied("这个电脑编号已使用，请在本机重新建立安装身份")
        token = "vcc_" + secrets.token_urlsafe(32)
        item = {"home_instance_id": identity["home_instance_id"], "connector_id": connector_id,
                "paired_principal_id": subject, "credential_epoch": uuid.uuid4().hex,
                "trust_epoch": identity["trust_epoch"], "name": name, "digest": _digest(token),
                "created_at": now, "revoked_at": None}
        if member_id is not None:
            item["paired_member_id"] = member_id
        rows.append(item)
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

    def authenticate(self, token, *, owner_only=False) -> dict:
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise ComputerDenied("电脑凭据无效")
        identity = self.identity()
        data = self._load()
        rows = data["computers"] if owner_only else (*data["computers"], *data["member_computers"])
        digest = _digest(token)
        found = next((item for item in rows if hmac.compare_digest(item["digest"], digest)
                      and self._current(item, identity)), None)
        if found is None:
            raise ComputerDenied("电脑凭据无效或已撤销")
        binding = {k: found[k] for k in _BINDING}
        if not self.check(binding):
            raise ComputerDenied("电脑凭据无效或已撤销")
        return binding

    def list(self, *, member_id=None) -> list:
        if member_id is not None:
            self._require_member(member_id)
        identity = self.identity()
        return [self._public(item) | {"active": self._current(item, identity)}
                for item in self._scope_rows(self._load(), member_id) if item.get("paired_member_id") == member_id]

    @staticmethod
    def _scope_rows(data, member_id):
        return data["computers"] if member_id is None else data["member_computers"]

    def rename(self, connector_id, name, *, member_id=None, current_only=False):
        name = _name(name)
        if member_id is not None:
            self._require_member(member_id)
        with self._lock():
            data = self._load()
            item = next((item for item in self._scope_rows(data, member_id) if item["connector_id"] == connector_id
                         and item.get("paired_member_id") == member_id), None)
            if item is None or (current_only and not self._current(item, self.identity())):
                raise KeyError(connector_id)
            item["name"] = name
            self._save(data)
            return self._public(item)

    def revoke(self, connector_id, *, member_id=None) -> dict:
        row, _ = self._revoke(connector_id, member_id, current_only=False)
        return row

    def revoke_current(self, connector_id, *, member_id=None) -> str:
        """Revoke an active pairing and return the credential epoch that lost access."""
        _, retired_epoch = self._revoke(connector_id, member_id, current_only=True)
        return retired_epoch

    def _revoke(self, connector_id, member_id, *, current_only) -> tuple[dict, str]:
        if member_id is not None:
            self._require_member(member_id)
        with self._lock():
            data = self._load()
            rows = self._scope_rows(data, member_id)
            item = next((item for item in rows if item["connector_id"] == connector_id
                         and item.get("paired_member_id") == member_id), None)
            if item is None or (current_only and not self._current(item, self.identity())):
                raise KeyError(connector_id)
            retired_epoch = item["credential_epoch"]
            if item["revoked_at"] is None:
                item["revoked_at"] = int(self.clock())
                if member_id is not None:
                    rows.remove(item)
                else:
                    item["credential_epoch"] = uuid.uuid4().hex
                self._save(data)
            return self._public(item), retired_epoch

    def _check_binding(self, binding, *, pairer=_ANY_PAIRER) -> bool:
        if not isinstance(binding, dict) or any(not _id(binding.get(k)) for k in _BINDING):
            return False
        identity = self.identity()
        data = self._load()
        valid = any(all(item[k] == binding[k] for k in _BINDING) and self._current(item, identity)
                    and (pairer is _ANY_PAIRER or item.get("paired_member_id") == pairer)
                    for item in (*data["computers"], *data["member_computers"]))
        return valid and self.identity() == identity

    def check(self, binding) -> bool:
        return self._check_binding(binding)

    def check_pairer(self, binding, member_id: str | None) -> bool:
        """A live grant belongs to this human. None denotes a verified home owner."""
        return self._check_binding(binding, pairer=member_id)
