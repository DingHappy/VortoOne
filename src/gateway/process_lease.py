"""Lifetime ownership on one host; never delete a held lock's inode."""
from __future__ import annotations

import os
from pathlib import Path
from src.utils.state_dir import home_state_dir, state_dir
from src.utils.home_maintenance import FileLease, HomeMaintenanceError, home_maintenance


class ProcessLease(FileLease):
    def acquire(self):
        try:
            return super().acquire()
        except HomeMaintenanceError as exc:
            raise RuntimeError("Another Runtime owns this workspace or IM channel") from exc


class RuntimeLease(ProcessLease):
    """所有 Runtime 入口共享的启动准入；在维护锁内确定目录并拿长期租约。"""
    def __init__(self, root):
        self.root = root
        # 真正的状态目录可能还要迁移，直到拿到维护锁后才确定。
        super().__init__(Path(root) / "runtime.lock")

    def acquire(self):
        if self._fd is not None:
            return self
        with home_maintenance(self.root):
            state = state_dir(self.root)
            marker = state / "moved.json"
            if marker.exists() or marker.is_symlink():
                raise RuntimeError("这个家已经搬走，这里不能再启动 Runtime")
            self.path = state / "runtime.lock"
            return super().acquire()


def channel_lease(channel: str, identity: str) -> ProcessLease:
    """Channel names and credentials never become arbitrary filesystem paths."""
    import hashlib
    if channel not in {"telegram", "dingtalk"}:
        raise ValueError("Unknown IM channel")
    directory = Path(os.getenv("VORTOONE_IM_LOCK_DIR") or
                     home_state_dir() / "channel-locks")
    digest = hashlib.sha256(str(identity).encode()).hexdigest()
    return ProcessLease(directory / f"{channel}-{digest}.lock")
