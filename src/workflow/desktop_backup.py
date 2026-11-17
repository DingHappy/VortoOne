"""恢复旧 Desktop raw tar：只原子发布家状态，保留 home 和维护锁的 inode。"""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile
import unicodedata
import uuid

from src.utils.home_maintenance import HomeMaintenanceError, home_maintenance
from src.utils.state_dir import STATE_DIRNAMES, state_dir
from src.workflow.home_identity import HomeIdentityError, prepare_replaced_home

MAX_FILES = 500_000
MAX_BYTES = 20 * 1024 ** 3
_EPHEMERAL = {"runtime.lock", "moved.json"}


class DesktopRestoreError(ValueError):
    """旧桌面包不能安全恢复；不把包里的路径或凭据放进错误正文。"""


def _checked(archive: tarfile.TarFile) -> list[tuple[tarfile.TarInfo, tuple[str, ...]]]:
    selected = []
    seen, files, parents, states = set(), set(), set(), set()
    total, count, has_state, legacy_alias = 0, 0, False, False
    for member in archive:
        count += 1
        total += member.size
        name = member.name
        relative = PurePosixPath(name)
        parts = relative.parts
        controlled_alias = (name == "home/.vortocode" and member.issym()
                            and member.linkname == ".vortoone" and member.size == 0)
        if (not parts or relative.is_absolute() or ".." in parts or "\\" in name
                or any(":" in part or any(unicodedata.category(c) in {"Cc", "Cs"} for c in part) for part in parts)
                or not (member.isfile() or member.isdir() or controlled_alias) or member.size < 0
                or count > MAX_FILES or total > MAX_BYTES):
            raise DesktopRestoreError("桌面备份包含无效成员或超出恢复上限")
        key = unicodedata.normalize("NFC", relative.as_posix()).casefold()
        ancestors = {"/".join(key.split("/")[:i]) for i in range(1, len(parts))}
        if key in seen or files.intersection(ancestors) or (member.isfile() and key in parents):
            raise DesktopRestoreError("桌面备份包含重复或冲突的路径")
        seen.add(key)
        parents.update(ancestors)
        if member.isfile() or controlled_alias:
            files.add(key)
        if controlled_alias:
            legacy_alias = True
            continue                     # 仅识别旧名兼容收据，绝不在恢复树里创建链接
        # 旧格式也备份 app data 顶层 owner-key/model.env。和原恢复一样不还原它们。
        if parts[0] != "home":
            continue
        if len(parts) == 1:
            if not member.isdir():
                raise DesktopRestoreError("桌面备份的家路径无效")
            continue
        if parts[1] in STATE_DIRNAMES:
            states.add(parts[1])
            if len(parts) >= 3 and parts[2] in _EPHEMERAL and (len(parts) != 3 or not member.isfile()):
                raise DesktopRestoreError("桌面备份的运行标记无效")
            if len(parts) >= 3 and member.isfile() and not (len(parts) == 3 and parts[2] in _EPHEMERAL):
                has_state = True
            if len(parts) == 3 and parts[2] in _EPHEMERAL:
                continue
        selected.append((member, parts[1:]))
    if len(states) != 1 or not has_state or (legacy_alias and states != {".vortoone"}):
        raise DesktopRestoreError("桌面备份没有唯一可恢复的家状态")
    return selected


def _extract(archive: tarfile.TarFile, selected, home: Path) -> None:
    home.mkdir(mode=0o700)
    for member, parts in selected:
        parent = home
        for part in parts if member.isdir() else parts[:-1]:
            parent /= part
            parent.mkdir(mode=0o700, exist_ok=True)
        if member.isdir():
            continue
        destination = home.joinpath(*parts)
        source = archive.extractfile(member)
        if source is None:
            raise DesktopRestoreError("桌面备份内容不完整")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(destination, flags, 0o600)
        copied = 0
        with os.fdopen(descriptor, "wb") as output, source:
            while chunk := source.read(1024 * 1024):
                copied += len(chunk)
                if copied > member.size:
                    raise DesktopRestoreError("桌面备份内容大小不符")
                output.write(chunk)
            if copied != member.size:
                raise DesktopRestoreError("桌面备份内容不完整")
            output.flush()
            os.fsync(output.fileno())


def _restore(data: Path, archive_path) -> dict:
    home = data / "home"
    if data.is_symlink() or home.is_symlink():
        raise DesktopRestoreError("桌面数据目录不可用")
    with tarfile.open(archive_path, "r:*") as archive:
        selected = _checked(archive)       # 坏包/无状态包在创建任何家之前拒绝
        data.mkdir(mode=0o700, parents=True, exist_ok=True)
        with home_maintenance(home, require_stopped=True):
            target = state_dir(home)
            if target.is_symlink() or (target.exists() and not target.is_dir()):
                raise DesktopRestoreError("当前家的状态目录不可用")
            staging = Path(tempfile.mkdtemp(prefix=".desktop-restore-", dir=data))
            aside = None
            extras = None
            published = False
            try:
                staged_home = staging / "home"
                _extract(archive, selected, staged_home)
                restored = state_dir(staged_home)
                prepare_replaced_home(restored, "desktop-restore")
                # state_dir 可能为旧名迁移留下受控别名；它不是用户的额外文件。
                extra_members = [p for p in staged_home.iterdir() if p.name not in STATE_DIRNAMES]
                if target.exists():
                    aside = home / f"{target.name}.before-desktop-restore-{uuid.uuid4().hex}"
                    target.rename(aside)
                restored.rename(target)
                published = True
                if extra_members:
                    extras = data / f"recovered-extras-{uuid.uuid4().hex}"
                    extras.mkdir(mode=0o700)
                    for path in extra_members:
                        path.rename(extras / path.name)
                if os.name != "nt":
                    for folder in (home, data):
                        descriptor = os.open(folder, os.O_RDONLY)
                        try:
                            os.fsync(descriptor)
                        finally:
                            os.close(descriptor)
                return {"ok": True}
            except Exception:
                # 家目录与外部维护锁始终留在原处；新 Runtime 直到回退/发布结束才能进入。
                if published and target.exists():
                    target.rename(staging / "unpublished-state")
                    published = False
                if aside is not None and not target.exists():
                    aside.rename(target)
                raise
            finally:
                shutil.rmtree(staging, ignore_errors=True)
                if extras is not None and not published:
                    shutil.rmtree(extras, ignore_errors=True)


def restore_desktop(data_dir, archive_path) -> dict:
    """仅恢复 raw tar 的家状态；额外 home 文件私有另存，顶层 App 凭据不覆盖。"""
    try:
        return _restore(Path(data_dir), archive_path)
    except (HomeIdentityError, HomeMaintenanceError, OSError, tarfile.TarError) as error:
        raise DesktopRestoreError("桌面备份无法安全恢复") from error
