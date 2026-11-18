"""进程文件锁与家的短维护锁；锁文件在被交换的状态目录之外。"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import stat

from src.utils.state_dir import HOME_MAINTENANCE_FILE, ensure_home_maintenance_gitignore, state_dir

MAINTENANCE_FILE = HOME_MAINTENANCE_FILE


class HomeMaintenanceError(RuntimeError):
    """其它进程正在运行/维护这个家，或无法证明已经停机。"""


class FileLease:
    """非阻塞跨进程租约；持有期间永远不删除或替换锁 inode。"""
    def __init__(self, path, *, private=False):
        self.path = Path(path)
        self.private = private
        self._fd = None

    def acquire(self):
        if self._fd is not None:
            return self
        descriptor = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.is_symlink():
                raise HomeMaintenanceError("家的进程锁不可用")
            descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
                                 | getattr(os, "O_NONBLOCK", 0), 0o600)
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode)
                    or (self.private and os.name != "nt" and info.st_mode & 0o077)):
                raise HomeMaintenanceError("家的进程锁不可用")
            if os.name == "nt":
                import msvcrt
                if not info.st_size:
                    os.write(descriptor, b"0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, RuntimeError) as error:
            if descriptor is not None:
                os.close(descriptor)
            raise HomeMaintenanceError("这个家正在运行或维护中；请先停服务，再重试") from error
        self._fd = descriptor
        return self

    def release(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.release()


@contextmanager
def home_maintenance(root, *, require_stopped=False):
    """启动在这把锁内拿 Runtime lease；维护在里面检查并完成状态目录交换。"""
    base = Path(root).resolve()
    with FileLease(base / MAINTENANCE_FILE, private=True):
        ensure_home_maintenance_gitignore(base)
        lease = None
        try:
            if require_stopped:
                state = state_dir(base)
                if state.is_symlink():
                    raise HomeMaintenanceError("家的状态目录不能是符号链接")
                path = state / "runtime.lock"
                # 没有 Runtime 锁时不创建状态目录；外面的维护锁已阻止新 Runtime 启动。
                if path.exists() or path.is_symlink():
                    lease = FileLease(path).acquire()
            yield
        finally:
            if lease is not None:
                lease.release()
