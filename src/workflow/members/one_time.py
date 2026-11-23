"""一次性登录：已经登录的人给自己的另一台设备开一个短时、只能用一次的登录凭据（手机扫码、连接链接）。

- 凭据只换得到开它的那个人：所有者开的换所有者的登录，成员开的换这个成员账号的一个新会话。
  成员开的永远换不到所有者。
- 只存摘要；5 分钟过期，用一次就作废，错的、过期的、用过的回同一个答复。
- 只在进程内存里：服务重启，没用掉的全部作废。每个人同时最多 5 个，整个服务最多 64 个。
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from typing import Callable, Dict, Optional, Tuple

SECONDS = 300
EACH_MAX, ALL_MAX = 5, 64
OWNER = "owner"


class TooMany(RuntimeError):
    """等着被用的一次性登录太多了。"""


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8", "surrogatepass")).hexdigest()


class OneTimeSignIns:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._pending: Dict[str, Tuple[str, float, str]] = {}      # 摘要 → (谁, 过期时刻, 密码代次)

    def _drop_expired(self, now: float) -> None:
        for key in [key for key, (_who, expires, _generation) in self._pending.items() if expires <= now]:
            del self._pending[key]

    def mint(self, who: str, *, generation: str = "") -> Tuple[str, int]:
        """给 who（``OWNER`` 或成员账号 id）开一个，返回（凭据，有效秒数）。凭据只在这里出现一次。"""
        if not who:
            raise ValueError("不知道是谁")
        now = self._clock()
        with self._lock:
            self._drop_expired(now)
            if (len(self._pending) >= ALL_MAX
                    or sum(1 for item, _expires, _generation in self._pending.values() if item == who) >= EACH_MAX):
                raise TooMany("等着用的登录太多，请稍后再试")
            token = secrets.token_urlsafe(32)
            self._pending[_digest(token)] = (who, now + SECONDS, generation)
            return token, SECONDS

    def redeem(self, token: str) -> Optional[str]:
        """用掉一个：对了返回是谁，不对、过期、用过的都返回 None。"""
        found = self.redeem_with_generation(token)
        return found[0] if found is not None else None

    def redeem_with_generation(self, token: str) -> Optional[Tuple[str, str]]:
        """Consume a code and return the recorded identity and credential generation."""
        if not isinstance(token, str) or not 20 <= len(token) <= 200:
            return None
        now = self._clock()
        with self._lock:
            found = self._pending.pop(_digest(token), None)
        if found is None or found[1] <= now:
            return None
        return found[0], found[2]

    def forget(self, who: str) -> None:
        """这个人还没用掉的全部作废（被移除、改了密码时）。"""
        with self._lock:
            for key in [key for key, (item, _expires, _generation) in self._pending.items() if item == who]:
                del self._pending[key]


PENDING = OneTimeSignIns()
