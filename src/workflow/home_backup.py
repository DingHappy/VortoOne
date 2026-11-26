"""家的备份与恢复（HOME_AND_MEMBERS_DESIGN 3.2）：每天一份、升级前一份，坏了能退回去。

和搬家（``home_move``）不一样：备份是给**同一个家**留的，恢复回原处，所以凭据也带上——手机配对、IM 凭据、
AI 应用的连接都在里面。它和家里的数据同样要紧：文件只给这个用户读写（0600），放在家旁边的 backups 目录。
要放到别的机器上，用加密的 ``scripts/assistant-backup.py``。

- 数据库用 SQLite 的在线备份接口拷一份一致的快照，服务开着也能备份。其余文件本来就是整个替换写入的。
- 不带：运行锁、git 工作副本、日志、搬走的标记——这些不是数据，恢复后会重新生成。
- 恢复时原来的状态目录挪到旁边（``.vortoone.before-restore-<时间>``）留着，不删。包里每一项都核对路径，
  不能落到状态目录外面、不能带链接或设备文件。恢复要先停服务。
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

from src.utils.state_dir import state_dir
from src.utils.home_maintenance import HomeMaintenanceError, home_maintenance
from src.workflow.home_identity import HomeIdentityError

FORMAT = "vortoone-backup"
VERSION = 1
PREFIX = "vortoone-backup-"
KEEP_DEFAULT = 7
# .vortoone/ 下不进备份的顶层项
NOT_DATA = frozenset({"runtime.lock", "worktrees", "worktree_bindings.json", "isolated_deliveries", "shadow.git",
                      "logs", "moved.json", "computer-file-intents", "computer-job-intents"})


class BackupError(ValueError):
    """备份或恢复做不了；消息给人看。"""


def _skipped(relative: PurePosixPath) -> bool:
    return (relative.parts[0] in NOT_DATA
            or relative.name.endswith((".lock", "-wal", "-shm", "-journal", ".tmp", ".part"))
            or relative.name.startswith(".pending-"))


def _is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _snapshot(path: Path, into: Path) -> Path:
    """正在用的 SQLite 数据库拷一份一致的快照。"""
    target = into / (path.name + ".snapshot")
    source = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        copy = sqlite3.connect(target)
        try:
            source.backup(copy)
        finally:
            copy.close()
    finally:
        source.close()
    return target


def _version() -> str:
    from importlib import metadata
    for package in ("vortoone", "vortocode"):
        try:
            return metadata.version(package)
        except metadata.PackageNotFoundError:
            continue
    return "dev"


def backups(folder: str) -> List[Path]:
    """folder 里的备份，新的在前。"""
    found = [p for p in Path(folder).glob(f"{PREFIX}*.tar.gz") if p.is_file() and not p.is_symlink()]
    return sorted(found, key=lambda p: p.name, reverse=True)


def _add_file(archive: tarfile.TarFile, source: Path, name: str) -> None:
    """大小与内容必须来自同一个打开的文件，不能在原子替换前后各读一次路径。"""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(source, flags), "rb") as handle:
        info = archive.gettarinfo(fileobj=handle, arcname=name)
        if info is None or not info.isfile():
            raise BackupError(f"备份文件不是普通文件：{name[:120]}")
        info.mode = 0o600
        archive.addfile(info, handle)


def backup(root: str, folder: str, *, keep: int = KEEP_DEFAULT, reason: str = "daily") -> Dict[str, Any]:
    """把 root 这个家备份到 folder，只留最新的 keep 份（这一份算在内）。返回这份备份的说明。"""
    if keep < 1:
        raise BackupError("至少要留一份")
    state = state_dir(root)
    if not state.is_dir():
        raise BackupError("这里还没有可以备份的家")
    destination = Path(folder)
    destination.mkdir(parents=True, exist_ok=True)
    destination.chmod(0o700)
    created = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    target = destination / f"{PREFIX}{created}-{reason[:20]}.tar.gz"
    if target.exists():
        raise BackupError("同一秒里已经有一份备份了，稍后再试")
    temporary = target.with_name(target.name + ".part")
    files = 0
    try:
        with tempfile.TemporaryDirectory() as scratch, tarfile.open(temporary, "w:gz", dereference=True) as archive:
            manifest = {"format": FORMAT, "version": VERSION, "created": created, "reason": reason,
                        "vortoone": _version()}
            data = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
            info = tarfile.TarInfo("manifest.json")
            info.size, info.mtime, info.mode = len(data), int(time.time()), 0o600
            archive.addfile(info, io.BytesIO(data))
            for path in sorted(state.rglob("*")):
                relative = PurePosixPath(path.relative_to(state).as_posix())
                if _skipped(relative) or path.is_symlink() or not path.is_file():
                    continue
                source = _snapshot(path, Path(scratch)) if _is_sqlite(path) else path
                _add_file(archive, source, f".vortoone/{relative}")
                files += 1
        temporary.chmod(0o600)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    for old in backups(folder)[keep:]:
        old.unlink(missing_ok=True)
    return {"path": str(target), "files": files, "created": created, "reason": reason}


def _checked(archive: tarfile.TarFile) -> List[tarfile.TarInfo]:
    members = []
    for member in archive.getmembers():
        if member.name == "manifest.json":
            continue
        name = PurePosixPath(member.name)
        if (name.is_absolute() or ".." in name.parts or len(name.parts) < 2 or name.parts[0] != ".vortoone"):
            raise BackupError(f"备份里有不该有的路径：{member.name[:120]}")
        if not (member.isfile() or member.isdir()):
            raise BackupError(f"备份里有链接或设备文件：{member.name[:120]}")
        if not _skipped(PurePosixPath(*name.parts[1:])):
            members.append(member)
    return members


def restore(root: str, archive_path: str) -> Dict[str, Any]:
    """把 root 这个家恢复成这份备份。原来的状态目录挪到旁边留着。调用方先停服务。"""
    try:
        with home_maintenance(root, require_stopped=True):
            return _restore(root, archive_path)
    except (HomeIdentityError, HomeMaintenanceError) as error:
        raise BackupError("恢复前无法安全核对身份或确认服务已停止；原来的家保持不变") from error


def _restore(root: str, archive_path: str) -> Dict[str, Any]:
    state = state_dir(root)
    try:
        archive = tarfile.open(archive_path, "r:gz")
    except (OSError, tarfile.TarError) as error:
        raise BackupError("打不开这份备份") from error
    with archive:
        try:
            raw = archive.extractfile("manifest.json")
            manifest = json.loads(raw.read()) if raw is not None else None
        except (KeyError, ValueError, tarfile.TarError):
            manifest = None
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
            raise BackupError("这不是 VortoOne 的备份")
        if manifest.get("version") != VERSION:
            raise BackupError("这份备份的格式这里认不得")
        members = _checked(archive)
        state.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f"{state.name}.restoring-", dir=state.parent))
        try:
            for member in members:
                relative = PurePosixPath(*PurePosixPath(member.name).parts[1:])
                destination = staging.joinpath(*relative.parts)
                parent = staging
                for part in relative.parts if member.isdir() else relative.parts[:-1]:
                    parent = parent / part
                    parent.mkdir(mode=0o700, exist_ok=True)
                if member.isdir():
                    continue
                source = archive.extractfile(member)
                if source is None:
                    raise BackupError(f"备份里的文件读不出来：{member.name[:120]}")
                with destination.open("wb") as out:
                    while chunk := source.read(1024 * 1024):
                        out.write(chunk)
                destination.chmod(0o600)
            from src.workflow import desktop_setup
            from src.workflow.home_identity import prepare_replaced_home
            prepare_replaced_home(staging, "restore")
            desktop_setup.suppress_replaced_legacy(root, staging)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    aside: Optional[Path] = None
    try:
        if state.exists():
            aside = state.with_name(f"{state.name}.before-restore-{time.time_ns()}")
            state.rename(aside)
        try:
            staging.rename(state)
        except Exception:
            if aside is not None and not state.exists():
                aside.rename(state)
            raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    if aside is not None:
        # 不进备份的那几样（日志里有所有者的通知记录、git 工作副本）原样接着用，不随恢复丢掉
        for name in sorted(NOT_DATA - {"runtime.lock", "moved.json", "computer-job-intents"}):
            if (aside / name).exists() and not (state / name).exists():
                (aside / name).rename(state / name)
    return {"restored": str(archive_path), "kept_aside": str(aside) if aside else None,
            "created": manifest.get("created")}
