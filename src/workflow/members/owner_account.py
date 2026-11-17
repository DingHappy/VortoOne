"""所有者的账号：邮箱加密码，以及装好时的一次性认领链接（HOME_AND_MEMBERS_DESIGN 3.1）。

- 新装好的家还没有所有者账号。安装时用 ``vc owner-claim`` 开一个一次性认领链接，只显示在安装者的终端里；
  第一个凭它填名字、邮箱、密码的人成为所有者，链接随即作废。认领链接还在等人用的时候，成员登录和注册一律不收。
- 所有者令牌照旧：Desktop 外壳和命令行用它。邮箱密码登录后拿到的就是所有者的登录 Cookie，和出示令牌一样。
- 和成员账号分开存、分开核对：成员账号文件里的任何凭据都换不到所有者身份，这里也不收成员。
  所有者的邮箱不能再被成员注册。
- 密码只存加盐的 scrypt 结果，认领令牌只存摘要。输错的计数和成员登录共用同一套（同一个邮箱连错会被暂时锁住）。
- 老的家（只用令牌、没有所有者账号）照常；想要邮箱密码，同样跑一次 ``vc owner-claim``。
"""
from __future__ import annotations

import json
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from src.utils.state_dir import state_dir
from src.workflow.members import accounts as _accounts
from src.workflow.members.registry import login_name

_FILE = "owner.json"
_LOCK = threading.RLock()
CLAIM_HOURS = 24


class WrongClaim(PermissionError):
    """认领链接不对、过期或已经用过。"""


class Taken(RuntimeError):
    """已经有所有者了。"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class OwnerAccount:
    def __init__(self, root: str):
        self.path = state_dir(root) / "members" / _FILE

    def _load(self) -> Dict[str, Any]:
        if self.path.is_symlink():
            raise _accounts.Damaged("所有者账号文件不能是符号链接")
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            raise _accounts.Damaged("所有者账号文件读不出来") from error
        if not isinstance(value, dict):
            raise _accounts.Damaged("所有者账号文件结构无效")
        return value

    def _save(self, value: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{_FILE}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
            temporary.chmod(0o600)
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    # ── 读 ────────────────────────────────────────────────────────────────────

    def profile(self) -> Optional[Dict[str, str]]:
        """所有者的名字和邮箱；还没有账号是 None。读不出来也是 None（登录照旧只认令牌）。"""
        try:
            with _LOCK:
                value = self._load()
        except _accounts.Damaged:
            return None
        if not value.get("password"):
            return None
        return {"name": str(value.get("name") or ""), "email": str(value.get("email") or "")}

    def waiting(self, *, now: Optional[datetime] = None) -> bool:
        """还没有所有者账号，而且有一个没过期的认领链接在等人用。"""
        try:
            with _LOCK:
                value = self._load()
        except _accounts.Damaged:
            return False
        claim = value.get("claim") or {}
        return not value.get("password") and str(claim.get("expires") or "") > _accounts._iso(now or _now())

    # ── 认领 ──────────────────────────────────────────────────────────────────

    def claim_link(self, *, reset: bool = False, now: Optional[datetime] = None) -> Tuple[str, str]:
        """开一个认领令牌（顶掉之前没用的），返回（令牌，过期时刻）。已经有所有者账号时报 Taken。
        reset：忘了密码——在这台电脑上跑的命令才能这样做；去掉现在的密码，重新认领。"""
        moment = now or _now()
        with _LOCK:
            value = self._load()
            if value.get("password") and not reset:
                raise Taken("这个家已经有所有者账号了；忘了密码就加 --reset 重新认领")
            value.pop("password", None)
            token, expires = secrets.token_urlsafe(32), _accounts._iso(moment + timedelta(hours=CLAIM_HOURS))
            value["claim"] = {"digest": _accounts._digest(token), "expires": expires}
            self._save(value)
            return token, expires

    def claim(self, token: str, name: str, email: str, password: str, *, now: Optional[datetime] = None) -> None:
        """凭认领令牌成为所有者。令牌不对、过期、用过报 WrongClaim；已经有所有者报 Taken；名字、邮箱、密码不合格报 ValueError。"""
        clean_name = str(name or "").strip()
        if not clean_name or len(clean_name) > 80:
            raise ValueError("名字要 1 到 80 个字")
        address = login_name(email)
        secret = _accounts._password(password)
        if secret == clean_name:
            raise ValueError("密码不能和名字一样")
        stamp = _accounts._iso(now or _now())
        with _LOCK:
            value = self._load()
            if value.get("password"):
                raise Taken("这个家已经有所有者了")
            claim = value.get("claim") or {}
            if not (isinstance(token, str) and claim.get("digest")
                    and secrets.compare_digest(str(claim["digest"]), _accounts._digest(token))
                    and str(claim.get("expires") or "") > stamp):
                raise WrongClaim("认领链接不对、过期或已经用过")
            value = {"name": clean_name, "email": address, "password": _accounts._sealed(secret), "created": stamp}
            self._save(value)

    # ── 登录 ──────────────────────────────────────────────────────────────────

    def is_owner_email(self, email: str) -> bool:
        found = self.profile()
        try:
            return bool(found) and login_name(email) == found["email"]
        except ValueError:
            return False

    def sign_in(self, email: str, password: str, *, clock: Optional[float] = None) -> bool:
        """邮箱和密码是不是所有者的。不是所有者的邮箱返回 False（交给成员登录去核对，不在这里记错）。
        是所有者的邮箱但密码不对：记一次错并抛 WrongCredentials；连错太多抛 Locked。"""
        if not self.is_owner_email(email):
            return False
        tick = time.monotonic() if clock is None else clock
        address = login_name(email)
        if _accounts.MemberAccounts._locked(address, tick):
            raise _accounts.Locked("输错次数太多，请过一会儿再试")
        with _LOCK:
            sealed = self._load().get("password") or _accounts._DECOY
        secret = password if isinstance(password, str) and len(password) <= _accounts.PASSWORD_MAX else ""
        if not _accounts._matches(sealed, secret):
            _accounts.MemberAccounts._failed(address, tick)
            raise _accounts.WrongCredentials("邮箱或密码不对")
        _accounts._FAILURES.pop(address, None)
        return True
