"""搬家：把一个家导出成一个包，在另一个家导入（HOME_AND_MEMBERS_DESIGN 第 3 节，H3）。

一个空间只存在一个家里；换位置是搬家，不做两个家之间的同步。

- **带上**：聊天、记忆、agent 与岗位登记、成员与身份组（密码只有哈希）、例行工作、任务台账、审计——
  也就是 ``.vortoone/`` 里除了下面这些之外的全部。
- **不带**：凭据和绑在这台电脑、这个地址上的东西——手机配对与设备钥匙、IM 配对与机器人凭据、AI 应用的连接
  （绑在旧地址上，到新家重新连）、运行锁、git 工作副本及其路径绑定、日志。模型密钥和所有者密钥本来就不在家里。
  符号链接一律不带。
- **旧的家停用**：以「搬走」导出时，旧的家里留一个标记（``moved.json``），之后那里的服务拒绝启动并说明已搬走；
  旧数据原样留着，不删。
- **导入**只进空的家；已经有数据的，要明确说替换，原来的先挪到旁边。包里每一项都核对：只能落在
  ``.vortoone/`` 里，不能是绝对路径、不能有 ``..``、不能是链接或设备文件。

包是 tar.gz：``manifest.json`` 加 ``.vortoone/`` 下的文件。
"""
from __future__ import annotations

import json
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional

from src.utils.state_dir import state_dir
from src.utils.home_maintenance import HomeMaintenanceError, home_maintenance
from src.workflow.home_identity import HomeIdentityError

FORMAT = "vortoone-home"
VERSION = 1
MARKER = "moved.json"
MAX_BYTES = 20 * 1024 ** 3                    # 包里所有文件加起来最多 20 GB
MAX_FILES = 500_000
# .vortoone/ 下不带走的顶层项
LEFT_BEHIND = frozenset({
    "runtime.lock", "worktrees", "worktree_bindings.json", "isolated_deliveries", "shadow.git", "logs",
    "ai_apps", "mobile-devices", "connected-computers", "im-pairings", "im-bots", MARKER,
})
_DATA_ROOTS = ("sessions.db", "web_sessions", "staff", "members", "tasks", "memory", "main_chat.json", "connected-computers")


class MoveError(ValueError):
    """搬家做不了；消息给人看。"""


def moved(root: str) -> Optional[Dict[str, Any]]:
    """这个家搬走了没有：搬走了返回标记内容（何时、导出到哪），没有返回 None。"""
    try:
        value = json.loads((state_dir(root) / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else {"at": None}


def _left_behind(relative: PurePosixPath) -> bool:
    return relative.parts[0] in LEFT_BEHIND or relative.name.endswith(".lock")


def export_home(root: str, destination: str, *, moving: bool = False) -> Dict[str, Any]:
    """把这个家导出到 destination（一个还不存在的 .tar.gz）。moving：导出后旧的家停用。"""
    try:
        with home_maintenance(root, require_stopped=moving):
            return _export_home(root, destination, moving=moving)
    except (HomeIdentityError, HomeMaintenanceError) as error:
        raise MoveError("搬家前无法安全核对身份或确认服务已停止") from error


def _export_home(root: str, destination: str, *, moving: bool) -> Dict[str, Any]:
    state = state_dir(root)
    if not state.is_dir():
        raise MoveError("这里还没有可以搬的家")
    if moved(root) is not None:
        raise MoveError("这个家已经搬走了")
    target = Path(destination)
    if target.exists():
        raise MoveError("目标文件已经存在，换个名字")
    if not target.name.endswith(".tar.gz"):
        raise MoveError("搬家包的文件名要以 .tar.gz 结尾")
    from src.workflow.home_identity import ensure_home_identity
    ensure_home_identity(root)          # 先保留旧手机目录里的公共 UUID，再留下手机凭据
    files: List[Path] = []
    skipped: set = set()
    total = 0
    for path in sorted(state.rglob("*")):
        relative = PurePosixPath(path.relative_to(state).as_posix())
        if _left_behind(relative):
            skipped.add(relative.parts[0])
            continue
        if path.is_symlink():
            skipped.add(str(relative))
            continue
        if path.is_file():
            files.append(path)
            total += path.stat().st_size
    if total > MAX_BYTES or len(files) > MAX_FILES:
        raise MoveError("这个家太大，没法打成一个搬家包")
    manifest = {"format": FORMAT, "version": VERSION, "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "files": len(files), "bytes": total, "left_behind": sorted(skipped)}
    temporary = target.with_name(target.name + ".part")
    try:
        with tarfile.open(temporary, "w:gz") as archive:
            data = json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8")
            info = tarfile.TarInfo("manifest.json")
            info.size, info.mtime, info.mode = len(data), int(time.time()), 0o600
            import io
            archive.addfile(info, io.BytesIO(data))
            for path in files:
                archive.add(path, arcname=f".vortoone/{path.relative_to(state).as_posix()}", recursive=False)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    if moving:
        from src.workflow.home_identity import invalidate_computer_trust
        invalidate_computer_trust(root, "move-out")
        (state / MARKER).write_text(json.dumps({"at": manifest["created"], "package": str(target)},
                                               ensure_ascii=False), encoding="utf-8")
    return {**manifest, "path": str(target), "moving": moving}


def _has_data(state: Path) -> bool:
    return any((state / name).exists() for name in _DATA_ROOTS)


def _checked_members(archive: tarfile.TarFile) -> List[tarfile.TarInfo]:
    members, total = [], 0
    for member in archive.getmembers():
        name = PurePosixPath(member.name)
        if member.name == "manifest.json" or (member.isdir() and member.name.rstrip("/") == ".vortoone"):
            continue
        if (name.is_absolute() or ".." in name.parts or not name.parts or name.parts[0] != ".vortoone"
                or len(name.parts) < 2):
            raise MoveError(f"搬家包里有不该有的路径：{member.name[:120]}")
        if not (member.isfile() or member.isdir()):
            raise MoveError(f"搬家包里有链接或设备文件：{member.name[:120]}")
        if _left_behind(PurePosixPath(*name.parts[1:])):
            continue                                  # 不该带过来的，即使包里有也不放
        total += member.size
        if total > MAX_BYTES or len(members) >= MAX_FILES:
            raise MoveError("搬家包太大")
        members.append(member)
    return members


def import_home(root: str, package: str, *, replace: bool = False) -> Dict[str, Any]:
    """把搬家包导入到 root 这个家。已有数据时要 replace=True，原来的挪到旁边（不删）。"""
    try:
        with home_maintenance(root, require_stopped=True):
            return _import_home(root, package, replace=replace)
    except (HomeIdentityError, HomeMaintenanceError) as error:
        raise MoveError("导入前无法安全核对身份或确认服务已停止；原来的家保持不变") from error


def _import_home(root: str, package: str, *, replace: bool) -> Dict[str, Any]:
    state = state_dir(root)
    try:
        archive = tarfile.open(package, "r:gz")
    except (OSError, tarfile.TarError) as error:
        raise MoveError("打不开这个搬家包") from error
    with archive:
        try:
            raw = archive.extractfile("manifest.json")
            manifest = json.loads(raw.read()) if raw is not None else None
        except (KeyError, ValueError, tarfile.TarError):
            manifest = None
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
            raise MoveError("这不是 VortoOne 的搬家包")
        if manifest.get("version") != VERSION:
            raise MoveError("搬家包的版本这里认不得，先把这里的 VortoOne 升级到最新")
        members = _checked_members(archive)
        aside = None
        had_data = state.exists() and _has_data(state)
        if had_data and not replace:
            raise MoveError("这个家里已经有数据；确定要用搬家包替换，就选替换（原来的会挪到旁边留着）")
        state.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f"{state.name}.importing-", dir=state.parent))
        try:
            for member in members:
                relative = PurePosixPath(*PurePosixPath(member.name).parts[1:])
                destination = staging.joinpath(*relative.parts)
                if member.isdir():
                    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
                    continue
                parent = staging
                for part in relative.parts[:-1]:
                    parent = parent / part
                    parent.mkdir(mode=0o700, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise MoveError(f"搬家包里的文件读不出来：{member.name[:120]}")
                with destination.open("wb") as out:
                    while chunk := source.read(1024 * 1024):
                        out.write(chunk)
                destination.chmod(0o600)
            from src.workflow.home_identity import prepare_replaced_home
            prepare_replaced_home(staging, "move-in")
            if state.exists():
                aside = state.with_name(f"{state.name}.before-import-{time.time_ns()}")
                state.rename(aside)
            # 原来已有、但没数据的状态目录（只有锁和 .gitignore 之类）：并进来，不丢它的锁文件
            if aside is not None and not had_data:
                for leftover in aside.iterdir():
                    if not (staging / leftover.name).exists():
                        leftover.rename(staging / leftover.name)
            staging.rename(state)
        except Exception:
            import shutil
            shutil.rmtree(staging, ignore_errors=True)
            if aside is not None and not state.exists():
                aside.rename(state)
            raise
    return {"files": len([m for m in members if m.isfile()]), "replaced": bool(had_data),
            "kept_aside": str(aside) if aside else None, "created": manifest.get("created")}
