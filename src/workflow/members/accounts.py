"""成员的账号：邮箱加密码、登录会话、重置密码的链接（本地部署这一套）。

账号和「在这个空间里的成员身份」是两层：账号证明「我是这个邮箱的人」，成员身份在登记处
（``registry.py``），由账号的 ``member`` 指过去。本地部署时两者一对一；以后云端一个账号进多个
空间时，改的是这条指向，不是账号本身。

- 密码只存加盐的 scrypt 结果；会话令牌和重置令牌只存摘要，文件泄露也换不回能用的凭据。
- 所有者不在这里：所有者用主人令牌。这里的任何凭据都换不到所有者身份。
- 邮箱只当登录名，不验证、不发信。忘了密码由管得了这个成员的人生成一次性重置链接。
- 同一个邮箱连续输错密码会被暂时锁住（进程内计数，重启清零）。
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, Optional, Tuple

from src.utils.state_dir import state_dir
from src.workflow.members.registry import login_name

_FILE = "accounts.json"
_MAX_BYTES = 2 * 1024 * 1024
_LOCK = threading.RLock()
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}
PASSWORD_MIN, PASSWORD_MAX = 8, 128
SESSION_DAYS, RESET_HOURS = 30, 24
MAX_ACCOUNTS, MAX_SESSIONS_EACH = 1000, 10
LOCK_AFTER, LOCK_SECONDS = 5, 15 * 60

# 邮箱 → (连续失败次数, 最近一次失败的时刻)。只在进程内；条数有上限，满了先清过期的。
_FAILURES: Dict[str, Tuple[int, float]] = {}
_FAILURES_MAX = 2000
# 登录入口不用先登录就能打，每次核对都要算一遍 scrypt：整个服务每分钟的失败次数也封顶，
# 免得有人换着邮箱把机器算满。代价是被打的时候别人也暂时登不上（已登录的不受影响）。
ALL_FAILURES_PER_MINUTE = 120
_RECENT = {"since": 0.0, "count": 0}


class Damaged(ValueError):
    """账号文件读不出来。停下，不当成「没有账号」。"""


class WrongCredentials(PermissionError):
    """邮箱或密码不对（不区分是哪个，免得被拿来探测谁有账号）。"""


class Locked(PermissionError):
    """输错太多次，暂时不能再试。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _digest(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8", "surrogatepass")).hexdigest()


def _hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, **_SCRYPT).hex()


def _password(value: Any) -> str:
    if not isinstance(value, str) or not PASSWORD_MIN <= len(value) <= PASSWORD_MAX:
        raise ValueError(f"密码要 {PASSWORD_MIN} 到 {PASSWORD_MAX} 个字符")
    return value


def _sealed(password: str) -> Dict[str, str]:
    salt = secrets.token_bytes(16)
    return {"salt": salt.hex(), "hash": _hash(password, salt)}


# 没有这个邮箱时也照样算一次，耗时和「有账号但密码错」一样，从响应快慢看不出谁有账号
_DECOY = _sealed(secrets.token_urlsafe(16))


def _matches(sealed: Dict[str, Any], password: str) -> bool:
    try:
        expected = _hash(password, bytes.fromhex(str(sealed.get("salt") or "")))
    except ValueError:
        return False
    return secrets.compare_digest(expected, str(sealed.get("hash") or ""))


class MemberAccounts:
    def __init__(self, root: str):
        self.root = str(root)
        self.directory = state_dir(root) / "members"

    # ── 存取 ──────────────────────────────────────────────────────────────────

    def _load(self) -> Dict[str, Any]:
        path = self.directory / _FILE
        if self.directory.is_symlink():
            raise Damaged("成员登记目录不能是符号链接")
        if not path.exists():
            return {"version": 1, "accounts": {}, "sessions": {}}
        if path.is_symlink() or path.stat().st_size > _MAX_BYTES:
            raise Damaged("账号文件无效或过大")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeError, ValueError) as error:
            raise Damaged("账号文件损坏") from error
        if not isinstance(value, dict) or value.get("version") != 1 \
                or not isinstance(value.get("accounts"), dict) or not isinstance(value.get("sessions"), dict):
            raise Damaged("账号文件结构无效")
        return value

    def _save(self, state: Dict[str, Any]) -> None:
        if self.directory.is_symlink():
            raise Damaged("成员登记目录不能是符号链接")
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / _FILE
        if path.is_symlink():
            raise Damaged("账号文件不能是符号链接")
        encoded = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
        if len(encoded.encode("utf-8")) > _MAX_BYTES:
            raise ValueError("账号文件过大，未写入")
        temporary = self.directory / f".{_FILE}.{uuid.uuid4().hex}.tmp"
        try:
            temporary.write_text(encoded, encoding="utf-8")
            temporary.chmod(0o600)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def _change(self) -> Iterator[Dict[str, Any]]:
        with _LOCK:
            handle = None
            try:
                import fcntl
                self.directory.mkdir(parents=True, exist_ok=True)
                handle = open(self.directory / ".accounts.lock", "a+")   # noqa: SIM115 —— 锁要持有到写完
                fcntl.flock(handle, fcntl.LOCK_EX)
            except (ImportError, OSError):
                handle = None
            try:
                state = self._load()
                yield state
                self._save(state)
            finally:
                if handle is not None:
                    handle.close()

    @staticmethod
    def _by_email(state: Dict[str, Any], email: str) -> Optional[str]:
        return next((key for key, item in state["accounts"].items() if item.get("email") == email), None)

    @staticmethod
    def _end_sessions(state: Dict[str, Any], account_id: str) -> None:
        for key in [key for key, item in state["sessions"].items() if item.get("account") == account_id]:
            del state["sessions"][key]

    # ── 账号 ──────────────────────────────────────────────────────────────────

    def exists(self, email: str) -> bool:
        with _LOCK:
            return self._by_email(self._load(), login_name(email)) is not None

    def create(self, email: str, name: str, password: str, *, now: Optional[datetime] = None) -> str:
        """新建账号，返回账号 id。能不能建（有没有有效的邀请）由入口先查好，这里不管。"""
        address, secret = login_name(email), _password(password)
        label = name.strip() if isinstance(name, str) else ""
        if not 1 <= len(label) <= 64 or any(ord(ch) < 32 or ord(ch) == 127 for ch in label):
            raise ValueError("名字不能为空，最长 64 个字")
        if label == secret:
            raise ValueError("密码不能和名字一样")
        with self._change() as state:
            if self._by_email(state, address) is not None:
                raise ValueError("这个邮箱已经有账号了")
            if len(state["accounts"]) >= MAX_ACCOUNTS:
                raise ValueError("账号数已到上限")
            account_id = f"a_{uuid.uuid4().hex[:12]}"
            state["accounts"][account_id] = {"email": address, "name": label, "password": _sealed(secret),
                                             "member": "", "created": _iso(now or _now())}
            return account_id

    def has_email(self, email: str) -> bool:
        """这个邮箱有没有成员账号（所有者认领时用：所有者的邮箱不能和成员的重）。"""
        try:
            address = login_name(email)
        except ValueError:
            return False
        with _LOCK:
            return self._by_email(self._load(), address) is not None

    def get(self, account_id: str) -> Optional[Dict[str, Any]]:
        """账号的公开部分（没有密码）。"""
        with _LOCK:
            account = self._load()["accounts"].get(str(account_id))
        return None if account is None else {
            "id": str(account_id), "email": account["email"], "name": account["name"],
            "member": str(account.get("member") or ""), "created": str(account.get("created") or "")}

    def bind_member(self, account_id: str, member_id: str) -> None:
        """账号接受邀请后，记下它在这个空间里是哪个成员。"""
        with self._change() as state:
            account = state["accounts"].get(str(account_id))
            if account is None:
                raise LookupError("没有这个账号")
            account["member"] = str(member_id)

    def account_of(self, member_id: str) -> Optional[str]:
        with _LOCK:
            return next((key for key, item in self._load()["accounts"].items()
                         if member_id and item.get("member") == str(member_id)), None)

    def remove_member(self, member_id: str) -> None:
        """成员被移出空间：他的账号和所有会话一并删掉。本地部署账号只属于这一个空间。"""
        with self._change() as state:
            for account_id in [key for key, item in state["accounts"].items()
                               if member_id and item.get("member") == str(member_id)]:
                self._end_sessions(state, account_id)
                del state["accounts"][account_id]

    # ── 登录与会话 ────────────────────────────────────────────────────────────

    @staticmethod
    def _locked(address: str, clock: float) -> bool:
        count, last = _FAILURES.get(address, (0, 0.0))
        if count and clock - last >= LOCK_SECONDS:
            _FAILURES.pop(address, None)
            return False
        return count >= LOCK_AFTER

    @staticmethod
    def _failed(address: str, clock: float) -> None:
        if len(_FAILURES) >= _FAILURES_MAX:
            for key in [key for key, (_count, last) in _FAILURES.items() if clock - last >= LOCK_SECONDS]:
                del _FAILURES[key]
            if len(_FAILURES) >= _FAILURES_MAX:       # 仍然满：丢掉最早的一半，不让计数表无限长
                for key in sorted(_FAILURES, key=lambda item: _FAILURES[item][1])[:_FAILURES_MAX // 2]:
                    del _FAILURES[key]
        _FAILURES[address] = (_FAILURES.get(address, (0, 0.0))[0] + 1, clock)

    def sign_in(self, email: str, password: str, *, now: Optional[datetime] = None,
                clock: Optional[float] = None) -> Tuple[str, str]:
        """核对邮箱和密码，开一个会话，返回（账号 id，会话令牌）。令牌只在这里出现一次。"""
        tick = time.monotonic() if clock is None else clock
        try:
            address = login_name(email)
        except ValueError:
            raise WrongCredentials("邮箱或密码不对") from None
        if tick - _RECENT["since"] >= 60:
            _RECENT.update(since=tick, count=0)
        if self._locked(address, tick) or _RECENT["count"] >= ALL_FAILURES_PER_MINUTE:
            raise Locked("输错次数太多，请过一会儿再试")
        secret = password if isinstance(password, str) and len(password) <= PASSWORD_MAX else ""
        with _LOCK:
            state = self._load()
        account_id = self._by_email(state, address)
        sealed = state["accounts"][account_id]["password"] if account_id else _DECOY
        if not (_matches(sealed, secret) and account_id):
            self._failed(address, tick)
            _RECENT["count"] += 1
            raise WrongCredentials("邮箱或密码不对")
        _FAILURES.pop(address, None)
        return account_id, self.open_session(account_id, now=now)

    def open_session(self, account_id: str, *, now: Optional[datetime] = None) -> str:
        moment = now or _now()
        with self._change() as state:
            if str(account_id) not in state["accounts"]:
                raise LookupError("没有这个账号")
            self._drop_expired(state, moment)
            mine = sorted((item.get("created") or "", key) for key, item in state["sessions"].items()
                          if item.get("account") == account_id)
            for _created, key in mine[:max(0, len(mine) - MAX_SESSIONS_EACH + 1)]:   # 太多设备：最早的先退出
                del state["sessions"][key]
            token = secrets.token_urlsafe(32)
            state["sessions"][_digest(token)] = {"account": str(account_id), "created": _iso(moment),
                                                 "expires": _iso(moment + timedelta(days=SESSION_DAYS))}
            return token

    @staticmethod
    def _drop_expired(state: Dict[str, Any], moment: datetime) -> None:
        stamp = _iso(moment)
        for key in [key for key, item in state["sessions"].items() if str(item.get("expires") or "") <= stamp]:
            del state["sessions"][key]

    def session(self, token: str, *, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
        """会话令牌对应的账号（公开部分）。令牌不对、过期、账号已删都返回 None。只读，不续期。"""
        if not token or len(str(token)) > 256:
            return None
        try:
            with _LOCK:
                state = self._load()
        except (Damaged, OSError):
            return None                                # 读不出来：当作没登录，不放行
        item = state["sessions"].get(_digest(token))
        if item is None or str(item.get("expires") or "") <= _iso(now or _now()):
            return None
        account = state["accounts"].get(str(item.get("account")))
        return None if account is None else {
            "id": str(item["account"]), "email": account["email"], "name": account["name"],
            "member": str(account.get("member") or "")}

    def sign_out(self, token: str) -> None:
        with self._change() as state:
            state["sessions"].pop(_digest(token), None)

    # ── 重置密码 ──────────────────────────────────────────────────────────────

    def reset_link(self, account_id: str, *, now: Optional[datetime] = None) -> Tuple[str, str]:
        """给一个账号开重置令牌，返回（令牌，过期时刻）。谁有权开由入口查；新开的顶掉旧的。"""
        moment = now or _now()
        with self._change() as state:
            account = state["accounts"].get(str(account_id))
            if account is None:
                raise LookupError("没有这个账号")
            token, expires = secrets.token_urlsafe(32), _iso(moment + timedelta(hours=RESET_HOURS))
            account["reset"] = {"digest": _digest(token), "expires": expires}
            return token, expires

    def reset_password(self, token: str, password: str, *, now: Optional[datetime] = None) -> str:
        """凭重置令牌改密码，返回账号 id。令牌用一次作废；这个账号在各处的登录全部退出。"""
        secret, digest, stamp = _password(password), _digest(token), _iso(now or _now())
        with self._change() as state:
            for account_id, account in state["accounts"].items():
                reset = account.get("reset") or {}
                if reset.get("digest") and secrets.compare_digest(str(reset["digest"]), digest) \
                        and str(reset.get("expires") or "") > stamp:
                    if account.get("name") == secret:
                        raise ValueError("密码不能和名字一样")
                    account["password"] = _sealed(secret)
                    account.pop("reset", None)
                    self._end_sessions(state, account_id)
                    _FAILURES.pop(str(account.get("email")), None)
                    return account_id
            raise LookupError("重置链接不对或已过期")
