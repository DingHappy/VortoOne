"""用本机 Claude Code 的 agent 接着上次的会话聊（参照 agentes.chat ADR 0032 的会话规则）。

- **按「agent + 发起的人」各存一个会话**：所有者一份、每位成员各一份，谁也读不到别人和这个 agent 的对话。
- **只在会话还新鲜时接着用**：上次用过不到 1 小时，且上一轮读进去不到 15 万 token；否则开新会话，
  把这个人和这个 agent 最近几件事作为开头带进去。不设上限的话，一个会话越用越长，每轮都要重读一遍。
- 例行工作不交给本机 CLI（用的是所有者的个人订阅，无人值守时不跑），所以不涉及会话。
- 同一个会话同一时刻只跑一轮：正在用的话，这一轮开一个临时的新会话，不存下来。
- CLI 的工作目录每个会话固定一个、在工作区**外面**（Claude Code 按工作目录存会话记录，接着用要回到同一个
  目录；放在工作区里它会沿上级目录读到 CLAUDE.md 之类的资料）。目录里什么都没有：这类 agent 只开联网工具。

会话记录本身由 Claude Code 存在它自己的配置目录里；这里只记会话 id 和用量。
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional

from src.utils.state_dir import state_dir

IDLE_SECONDS = 3600
MAX_INPUT_TOKENS = 150_000
CONTEXT_TOKENS = 200_000           # 交给 Claude Code 自己压缩的上限（CLAUDE_CODE_AUTO_COMPACT_WINDOW）
_FILE = "cli_sessions.json"
_LOCK = threading.RLock()
_BUSY: set = set()


@dataclass
class Session:
    key: str
    id: str
    resume: bool
    cwd: str
    keep: bool = True               # False：临时会话（原会话正忙），跑完不存


def owner_key(viewer: Optional[str]) -> str:
    """发起人在会话表里的写法：所有者是 owner，成员是他的 id。"""
    return f"member:{viewer}" if viewer else "owner"


def _session_dirs() -> Path:
    from src.agents.external_workers import worker_home
    home = worker_home()
    return home / "agent-sessions" if home is not None else Path(tempfile.gettempdir()) / "vortoone-agent-sessions"


class CliSessions:
    def __init__(self, root: str, *, clock: Callable[[], float] = time.time):
        self.root = str(root)
        self.path = state_dir(root) / "staff" / _FILE
        self.clock = clock

    def _key(self, agent: str, viewer: Optional[str]) -> str:
        return f"{agent}\0{owner_key(viewer)}"

    def _cwd(self, key: str) -> Path:
        name = hashlib.sha256(f"{Path(self.root).resolve()}\0{key}".encode()).hexdigest()[:24]
        path = _session_dirs() / name
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        return path

    def _load(self) -> Dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    @contextmanager
    def _change(self) -> Iterator[Dict[str, Any]]:
        with _LOCK:
            state = self._load()
            yield state
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f".{_FILE}.{uuid.uuid4().hex}.tmp")
            try:
                temporary.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
                temporary.chmod(0o600)
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)

    def take(self, agent: str, viewer: Optional[str]) -> Session:
        """这一轮用哪个会话。用完必须 ``release``（成功时再 ``record``）。"""
        key = self._key(agent, viewer)
        now = self.clock()
        cwd = str(self._cwd(key))
        with _LOCK:
            saved = self._load().get(key) or {}
            fresh = (saved.get("id") and now - float(saved.get("used") or 0) < IDLE_SECONDS
                     and int(saved.get("input") or 0) < MAX_INPUT_TOKENS)
            if key in _BUSY:
                return Session(key, str(uuid.uuid4()), False, cwd, keep=False)
            _BUSY.add(key)
        if fresh:
            return Session(key, str(saved["id"]), True, cwd)
        return Session(key, str(uuid.uuid4()), False, cwd)

    def record(self, session: Session, input_tokens: int) -> None:
        """这一轮成功了：记下会话和它读进去多少（下一轮据此决定接不接）。"""
        if not session.keep:
            return
        with self._change() as state:
            state[session.key] = {"id": session.id, "used": self.clock(), "input": int(input_tokens)}

    def forget(self, session: Session) -> None:
        """这个会话接不上了（记录丢了、坏了）：下一轮开新的。"""
        if not session.keep:
            return
        with self._change() as state:
            state.pop(session.key, None)

    def release(self, session: Session) -> None:
        if session.keep:
            with _LOCK:
                _BUSY.discard(session.key)


def reset_busy() -> None:
    """测试用。"""
    _BUSY.clear()
