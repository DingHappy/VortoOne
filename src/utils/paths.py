"""路径围栏——**全仓唯一一份**。一切文件操作都必须先过它，把路径钉死在工作目录内。

## 为什么收到这里

2026-09-11 的依赖图体检发现：这个函数**全仓写了三份**，而且**三份不等价**：

    src/web/auth.resolve_within         空串 → 返回 base 本身；**不捕 OSError**
    src/agents/tools/files._resolve_within   空串 → None；额外 lstrip("@")；捕 OSError
    src/memory/rewind._resolve_within        空串 → 返回 base；捕 OSError（rewind 已随 TUI 删除）

而 rewind 那份的注释写着"契约与 src/web/auth.resolve_within 相同"——**那句话是假的**。

CLAUDE.md 的原话是"安全规则**上收到内核一处**，别在每个端各写一遍（那正是「加一端漏一端」
的历史病根）"。路径围栏正是这样一条规则，却写了三份、还漂移了。

## 取最严的那套语义

- **空串 → None**。`resolve_within(base, "")` 原先有两份返回 base 目录本身，那是个真空子：
  调用方写的是 `if resolve_within(...) is None: 拒绝`，空串于是**过了闸**，然后拿着一个
  目录路径往下走。
- **OSError → None**。`Path.resolve()` 在软链环（ELOOP）、超长路径上会抛 OSError。
  web 那份不捕，异常直接穿出围栏——一个安全判定函数**抛异常而不是拒绝**，是 fail-open 的形状。
- `@` 前缀的剥离是 **agents 层的书写约定**（`@src/x.py`），不属于围栏本身，留在调用方做。
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any, Optional

from src.utils.state_dir import STATE_DIRNAMES


def resolve_within(base: Any, rel: Any) -> Optional[Path]:
    """把 `rel` 解析进 `base` 并做边界校验；越界一律返回 None（**不抛异常**）。

    拦得住：绝对路径（`base / "/etc/passwd"` 语义上丢掉 base → 落到根 → relative_to 抛）、
    `..` 上跳、软链逃逸（resolve() 之后再判）、空串、以及解析过程本身出错。

    用 `relative_to` 做**组件级**判断，而不是字符串 startswith——后者会把 `/x/proj-secrets`
    误判成在 `/x/proj` 内。
    """
    text = str(rel or "").strip()
    if not text:
        return None
    try:
        root = Path(base).resolve()
        target = (root / text).resolve()
        target.relative_to(root)
    except (ValueError, OSError):
        return None
    return target


_SAFE_ENV_TEMPLATES = {
    ".env.example",
    ".env.sample",
    ".env.template",
    ".env.defaults",
}
_SENSITIVE_NAMES = {
    ".env",
    ".envrc",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".git-credentials",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "credentials.json",
    "client_secret.json",
    "service-account.json",
}
_SENSITIVE_DIRS = {".ssh", ".aws", ".docker", ".gnupg", ".kube", "secrets"}
_SENSITIVE_STATE_DIRS = set(STATE_DIRNAMES)          # .vortoone 与改名前留下的 .vortocode 链接
_SENSITIVE_STATE_FILES = {"cli_session.json", "sessions.db", "sessions.db-shm", "sessions.db-wal"}
_SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".keystore"}


def _raw_sensitive_repo_path(path: object) -> bool:
    raw = str(path or "").strip().replace("\\", "/").lstrip("@/")
    if not raw:
        return False
    parts = [part.lower() for part in PurePosixPath(raw).parts if part not in {"", "."}]
    if not parts:
        return False
    name = parts[-1]
    if name in _SAFE_ENV_TEMPLATES:
        return False
    if (
        name in _SENSITIVE_NAMES
        or (name.startswith(".env.") and name not in _SAFE_ENV_TEMPLATES)
        or name.startswith("secrets.")
    ):
        return True
    if any(part in _SENSITIVE_DIRS or part in _SENSITIVE_STATE_DIRS for part in parts):
        return True
    if name in _SENSITIVE_STATE_FILES:
        return True
    return any(name.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES)


def is_sensitive_repo_path(path: object, repo_root: str | None = None) -> bool:
    """Return whether a raw or resolved repository path can hold credentials/state."""
    if _raw_sensitive_repo_path(path):
        return True
    if not repo_root:
        return False
    raw = str(path or "").strip().lstrip("@")
    if not raw:
        return False
    try:
        root = Path(repo_root).resolve()
        resolved = (root / raw).resolve()
        rel = resolved.relative_to(root).as_posix()
    except (OSError, ValueError):
        return False
    return _raw_sensitive_repo_path(rel)
