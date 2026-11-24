"""例行工作的 webhook 触发（主人 2026-11-24 定：做，带密钥；触发进来的内容按外部内容）。

每条 webhook 例行工作有一个随机的触发地址（``/api/hooks/routines/<地址>``），地址本身就是一道门：
不知道地址的人叫不动它，换一个新地址旧的立刻失效。另外可以设签名密钥：设了以后每次调用都要在
``X-VortoOne-Signature: sha256=<HMAC-SHA256>`` 里带上请求体（GET 是查询串）的签名，对不上一律拒绝。

- 请求体最多 ``MAX_BYTES``，每条例行工作每分钟最多 ``PER_MINUTE`` 次；``Idempotency-Key`` 24 小时内重复的
  只回「重复」，不再运行。
- 最近 ``KEEP_CALLS`` 次调用留个记录（时间、结果、状态码、大小、进了哪次运行），不存内容。
- 密钥要用来核对签名，只能原样存；文件只给本机这个用户读写（0600）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional

from src.utils.state_dir import state_dir

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
MAX_BYTES, PER_MINUTE, KEEP_CALLS, KEY_HOURS = 64 * 1024, 30, 20, 24
PATH = "/api/hooks/routines/"
_FILE = "routine_hooks.json"
_LOCK = threading.RLock()


class HookDamaged(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def signature(secret: str, data: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), data, hashlib.sha256).hexdigest()


def methods_of(value: Any) -> List[str]:
    if value is None:
        return ["POST"]
    if (not isinstance(value, list) or not value or len(value) > len(METHODS)
            or any(item not in METHODS for item in value) or len(set(value)) != len(value)):
        raise ValueError("methods 只能是 GET、POST、PUT、PATCH、DELETE 里的几个，不能为空")
    return [item for item in METHODS if item in value]


class RoutineHooks:
    def __init__(self, root: str, *, clock: Callable[[], float] = time.monotonic):
        self.root = str(root)
        self.directory = state_dir(root) / "staff"
        self._clock = clock

    # ── 存取 ──────────────────────────────────────────────────────────────────

    def _load(self) -> Dict[str, Any]:
        path = self.directory / _FILE
        if not path.exists():
            return {"version": 1, "hooks": {}}
        if path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
            raise HookDamaged("webhook 记录无效或过大")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeError, ValueError) as error:
            raise HookDamaged("webhook 记录损坏") from error
        if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("hooks"), dict):
            raise HookDamaged("webhook 记录结构无效")
        return value

    def _save(self, state: Dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / _FILE
        if path.is_symlink():
            raise HookDamaged("webhook 记录不能是符号链接")
        temporary = self.directory / f".{_FILE}.{uuid.uuid4().hex}.tmp"
        try:
            temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.chmod(0o600)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def _change(self) -> Iterator[Dict[str, Any]]:
        with _LOCK:
            state = self._load()
            yield state
            self._save(state)

    @staticmethod
    def _fresh(methods: List[str]) -> Dict[str, Any]:
        return {"address": secrets.token_urlsafe(24), "secret": None, "methods": methods, "calls": [], "keys": {},
                "minute": []}

    # ── 设置（管得了这条例行工作的人）─────────────────────────────────────────

    def prepare(self, routine_id: str, methods: List[str]) -> None:
        """新建 webhook 例行工作时先记下方法；地址这时就有，例行工作批准登记以后才会应答。"""
        with self._change() as state:
            state["hooks"][routine_id] = self._fresh(methods)

    def has(self, routine_id: str) -> bool:
        with _LOCK:
            return routine_id in self._load()["hooks"]

    def _hook(self, state: Dict[str, Any], routine_id: str) -> Dict[str, Any]:
        found = state["hooks"].get(routine_id)
        if found is None:
            found = state["hooks"][routine_id] = self._fresh(["POST"])
        return found

    @staticmethod
    def _view(hook: Dict[str, Any], origin: str) -> Dict[str, Any]:
        return {"url": origin.rstrip("/") + PATH + hook["address"] if origin else None,
                "signed": bool(hook.get("secret")), "methods": list(hook["methods"]),
                "calls": [dict(call) for call in reversed(hook["calls"])]}

    def view(self, routine_id: str, origin: str) -> Dict[str, Any]:
        with self._change() as state:
            hook = self._hook(state, routine_id)
            if origin:
                hook["origin"] = origin               # 列表里也能给出完整地址
            return self._view(hook, origin)

    def summary(self, routine_id: str) -> Dict[str, Any]:
        """例行工作列表里的 trigger：地址用上次所有者打开它时的服务地址。"""
        with _LOCK:
            hook = self._load()["hooks"].get(routine_id) or {"address": "", "methods": ["POST"], "calls": []}
        return {"url": (hook.get("origin") or "").rstrip("/") + PATH + hook["address"] if hook.get("origin") else None,
                "signed": bool(hook.get("secret")), "methods": list(hook["methods"])}

    def new_address(self, routine_id: str, origin: str) -> Dict[str, Any]:
        with self._change() as state:
            hook = self._hook(state, routine_id)
            hook["address"] = secrets.token_urlsafe(24)
            hook["keys"] = {}
            return self._view(hook, origin)

    def new_secret(self, routine_id: str, origin: str) -> tuple:
        with self._change() as state:
            hook = self._hook(state, routine_id)
            hook["secret"] = secrets.token_urlsafe(32)
            return hook["secret"], self._view(hook, origin)

    def remove_secret(self, routine_id: str, origin: str) -> Dict[str, Any]:
        with self._change() as state:
            hook = self._hook(state, routine_id)
            hook["secret"] = None
            return self._view(hook, origin)

    def forget(self, routine_id: str) -> None:
        with self._change() as state:
            state["hooks"].pop(routine_id, None)

    # ── 有人打这个地址 ────────────────────────────────────────────────────────

    def routine_at(self, address: str) -> Optional[str]:
        if not isinstance(address, str) or not 16 <= len(address) <= 64:
            return None
        with _LOCK:
            hooks = self._load()["hooks"]
        return next((key for key, hook in hooks.items() if hmac.compare_digest(hook["address"], address)), None)

    def admit(self, routine_id: str, method: str, data: bytes, signed_as: str, key: str) -> Optional[tuple]:
        """核对一次调用。不放行时记下并返回（结果，状态码）；放行返回 None（之后用 ``record`` 记结果）。"""
        with self._change() as state:
            hook = self._hook(state, routine_id)
            now, tick = _now(), self._clock()
            refused: Optional[tuple] = None
            if method not in hook["methods"]:
                return ("method", 405)
            if len(data) > MAX_BYTES:
                refused = ("too_large", 413)
            elif hook.get("secret") and not hmac.compare_digest(signature(hook["secret"], data), signed_as or ""):
                refused = ("bad_signature", 401)
            else:
                hook["minute"] = [moment for moment in hook.get("minute", []) if moment > tick - 60]
                if len(hook["minute"]) >= PER_MINUTE:
                    refused = ("rate_limited", 429)
                else:
                    hook["minute"].append(tick)
                    oldest = _iso(now - timedelta(hours=KEY_HOURS))
                    hook["keys"] = {k: at for k, at in hook.get("keys", {}).items() if at > oldest}
                    if key:
                        if key in hook["keys"]:
                            refused = ("duplicate", 200)
                        else:
                            hook["keys"][key[:200]] = _iso(now)
            if refused is not None:
                self._remember(hook, refused[0], refused[1], len(data), None, False)
            return refused

    def record(self, routine_id: str, outcome: str, status: int, size: int, run_id: Optional[str], test: bool) -> None:
        with self._change() as state:
            self._remember(self._hook(state, routine_id), outcome, status, size, run_id, test)

    @staticmethod
    def _remember(hook, outcome, status, size, run_id, test) -> None:
        hook["calls"] = [*hook["calls"], {"id": uuid.uuid4().hex[:16], "at": _iso(_now()), "outcome": outcome,
                                          "status": status, "bytes": size, "runId": run_id, "test": test}][-KEEP_CALLS:]
