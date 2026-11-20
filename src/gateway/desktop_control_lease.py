"""The local native connector and task browser share one control lease."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import re
import secrets
import stat


@contextmanager
def desktop_lease_file(directory: Path):
    """Open the shared inode. Opening alone does not stop role operations."""
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise OSError("desktop control directory is not owned by this installation")
    fd = os.open(directory / "desktop-control.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise OSError("desktop control lease is not private")
        yield fd
    finally:
        # A native capture process may still hold this file description. An explicit
        # unlock here would let the role resume before that child has stopped.
        os.close(fd)


def claim_desktop(fd: int) -> bool:
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def desktop_revision(fd: int) -> bytes:
    value = os.pread(fd, 33, 0)
    if value and not re.fullmatch(rb"[a-f0-9]{32}", value):
        raise OSError("desktop control revision is invalid")
    return value


def mark_desktop_takeover(fd: int) -> None:
    value = secrets.token_hex(16).encode("ascii")
    if os.pwrite(fd, value, 0) != len(value):
        raise OSError("desktop control revision could not be written")
    os.ftruncate(fd, len(value))
    os.fsync(fd)
