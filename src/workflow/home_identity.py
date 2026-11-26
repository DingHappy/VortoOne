"""家的持久身份与电脑信任代次；不把路径、邮箱或手机凭据当作所有者身份。"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import uuid

from src.utils.state_dir import ensure_state_gitignore, state_dir

_ID = re.compile(r"[a-f0-9]{32}")
_MAX_BYTES = 1024 * 1024
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_HELD = threading.local()
IDENTITY_DIRECTORY = "home-identity"


class HomeIdentityError(RuntimeError):
    """身份无法安全读取或保存；不重建坏掉的身份。"""


def _read_json(path: Path) -> dict | None:
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_BYTES
                or (os.name != "nt" and info.st_mode & 0o077)):
            raise HomeIdentityError("家的身份文件不可用")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (not stat.S_ISREG(opened.st_mode) or opened.st_size > _MAX_BYTES
                    or (os.name != "nt" and opened.st_mode & 0o077)):
                raise HomeIdentityError("家的身份文件不可用")
            raw = handle.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            raise ValueError
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, RecursionError) as error:
        raise HomeIdentityError("家的身份文件不可用") from error


def _private_directory(path: Path) -> None:
    if path.is_symlink() or path.parent.is_symlink():
        raise HomeIdentityError("家的身份目录不可用")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or (os.name != "nt" and info.st_mode & 0o077):
        raise HomeIdentityError("家的身份目录权限过宽")


def _save_json(path: Path, value: dict) -> None:
    _private_directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def owner_identity_change(root):
    """所有者账号与身份变更共用的跨进程临界区；只供 workflow 内部组合操作。"""
    state = state_dir(root)
    directory = state / IDENTITY_DIRECTORY
    _private_directory(directory)
    key = str(directory.resolve())
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.RLock())
    with lock:
        held = getattr(_HELD, "paths", None)
        if held is None:
            held = _HELD.paths = set()
        if key in held:
            yield
            return
        fd = os.open(directory / ".lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or (os.name != "nt" and info.st_mode & 0o077):
                raise HomeIdentityError("家的身份锁不可用")
            try:
                if os.name == "nt":
                    import msvcrt
                    if not info.st_size:
                        os.write(fd, b"0")
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise HomeIdentityError("家的身份正在更新，请重试") from error
            held.add(key)
            try:
                yield
            finally:
                held.remove(key)
        finally:
            os.close(fd)


def _validate(value: dict) -> dict:
    if (set(value) != {"schema_version", "home_instance_id", "owner_subject", "ownership_state", "trust_epoch"}
            or type(value.get("schema_version")) is not int or value["schema_version"] != 1
            or not isinstance(value.get("home_instance_id"), str) or not _ID.fullmatch(value["home_instance_id"])
            or not isinstance(value.get("trust_epoch"), str) or not _ID.fullmatch(value["trust_epoch"])
            or value.get("ownership_state") not in {"legacy_candidate", "unclaimed", "claimed"}
            or (value.get("owner_subject") is not None
                and (not isinstance(value["owner_subject"], str) or not _ID.fullmatch(value["owner_subject"])))
            or (value["ownership_state"] == "claimed") != (value["owner_subject"] is not None)):
        raise HomeIdentityError("家的身份结构不可用")
    return value


def _owner(state: Path) -> dict:
    directory = state / "members"
    if directory.is_symlink():
        raise HomeIdentityError("所有者账号目录不可用")
    owner = _read_json(directory / "owner.json") or {}
    if "password" in owner:
        sealed = owner["password"]
        if (not isinstance(sealed, dict) or set(sealed) != {"salt", "hash"}
                or not isinstance(sealed["salt"], str) or not _ID.fullmatch(sealed["salt"])
                or not isinstance(sealed["hash"], str) or not re.fullmatch(r"[a-f0-9]{64}", sealed["hash"])
                or not isinstance(owner.get("name"), str) or not isinstance(owner.get("email"), str)):
            raise HomeIdentityError("所有者账号结构不可用")
    if "claim" in owner:
        claim = owner["claim"]
        if (not isinstance(claim, dict) or set(claim) != {"digest", "expires"}
                or not isinstance(claim["digest"], str) or not re.fullmatch(r"[a-f0-9]{64}", claim["digest"])
                or not isinstance(claim["expires"], str)):
            raise HomeIdentityError("所有者认领结构不可用")
    if "claim_completion" in owner:
        pending = owner["claim_completion"]
        if (not owner.get("password") or not isinstance(pending, dict)
                or set(pending) != {"digest", "expires", "admission_epoch", "subject", "trust_epoch"}
                or not isinstance(pending["digest"], str) or not re.fullmatch(r"[a-f0-9]{64}", pending["digest"])
                or not isinstance(pending["expires"], str)
                or any(not isinstance(pending[key], str) or not _ID.fullmatch(pending[key])
                       for key in ("admission_epoch", "subject", "trust_epoch"))):
            raise HomeIdentityError("所有者认领完成记录不可用")
    return owner


def _ensure(state: Path) -> dict:
    path = state / IDENTITY_DIRECTORY / "identity.json"
    current = _read_json(path)
    if current is not None:
        return _validate(current)
    legacy_directory = state / "mobile-devices"
    if legacy_directory.is_symlink():
        raise HomeIdentityError("旧家的身份目录不可用")
    legacy = _read_json(legacy_directory / "grants.json")
    if legacy is not None and (legacy.get("schema_version") != 1
            or not isinstance(legacy.get("instance_id"), str) or not _ID.fullmatch(legacy["instance_id"])
            or not isinstance(legacy.get("devices"), list)):
        raise HomeIdentityError("旧家的身份不可用")
    owner = _owner(state)
    # subject 仅在可信所有者认证/完成认领后建立，普通启动不替人认领。
    value = {"schema_version": 1, "home_instance_id": legacy["instance_id"] if legacy else uuid.uuid4().hex,
             "owner_subject": None, "ownership_state": "unclaimed" if "claim" in owner else "legacy_candidate",
             "trust_epoch": uuid.uuid4().hex}
    _save_json(path, value)
    return value


def ensure_home_identity(root) -> dict:
    """读取/迁移公共 UUID；不创建所有者或任何设备授权。"""
    state = state_dir(root)
    directory = state / IDENTITY_DIRECTORY
    # 已提交的身份是整份 fsync+replace 写入的快照。只读不得抢排他锁：配对的
    # winning 进程消费码后还要核对身份，普通并发读不能让它丢掉唯一返回的令牌。
    if directory.is_symlink() or state.is_symlink():
        raise HomeIdentityError("家的身份目录不可用")
    try:
        info = directory.lstat()
    except FileNotFoundError:
        info = None
    if info is not None:
        if not stat.S_ISDIR(info.st_mode) or (os.name != "nt" and info.st_mode & 0o077):
            raise HomeIdentityError("家的身份目录权限过宽")
        current = _read_json(directory / "identity.json")
        if current is not None:
            return dict(_validate(current))
    ensure_state_gitignore(str(root))
    with owner_identity_change(root):
        return dict(_ensure(state_dir(root)))


def initialize_unclaimed_home(root) -> dict:
    """新安装在启动服务前调用；不能把新生成的 owner-key 当作已认领。"""
    with owner_identity_change(root):
        state = state_dir(root)
        value = _ensure(state)
        if value["ownership_state"] == "claimed" or _owner(state).get("password"):
            raise HomeIdentityError("已有所有者，不能重新初始化")
        value.update(ownership_state="unclaimed", owner_subject=None, trust_epoch=uuid.uuid4().hex)
        _save_json(state / IDENTITY_DIRECTORY / "identity.json", value)
        return dict(value)


def pairing_owner(root, *, legacy_confirmed: bool = False) -> dict:
    """只供已经核验真人所有者的可信路由调用；本函数不验证登录凭据。"""
    if type(legacy_confirmed) is not bool:
        raise ValueError("需要明确的旧家确认")
    with owner_identity_change(root):
        state = state_dir(root)
        value, owner = _ensure(state), _owner(state)
        if "claim" in owner or "claim_completion" in owner:
            raise PermissionError("请先完成所有者认领")
        if value["ownership_state"] == "unclaimed":
            raise PermissionError("请先完成所有者认领")
        if value["ownership_state"] != "claimed":
            if not owner.get("password") and not legacy_confirmed:
                raise PermissionError("请明确确认这个旧家仍属于你")
            value.update(ownership_state="claimed", owner_subject=uuid.uuid4().hex)
            _save_json(state / IDENTITY_DIRECTORY / "identity.json", value)
        return dict(value)


def invalidate_computer_trust(root, reason: str, *, unclaimed: bool = False) -> dict:
    """先持久收权；旧授权即使仍在文件中也无法通过新的随机信任代次。"""
    with owner_identity_change(root):
        state = state_dir(root)
        value = _ensure(state)
        value["trust_epoch"] = uuid.uuid4().hex
        if unclaimed:
            value.update(ownership_state="unclaimed", owner_subject=None)
        _save_json(state / IDENTITY_DIRECTORY / "identity.json", value)
        return dict(value)


def complete_owner_claim(root) -> dict:
    """将已验证认领的完成记录幂等提交；不凭残留密码自动建立身份。"""
    with owner_identity_change(root):
        state = state_dir(root)
        owner = _owner(state)
        if not owner.get("password") or "claim" in owner or "claim_completion" not in owner:
            raise HomeIdentityError("所有者尚未认领")
        pending = owner["claim_completion"]
        value = _ensure(state)
        if (value["ownership_state"] == "claimed" and value["owner_subject"] == pending["subject"]
                and value["trust_epoch"] == pending["trust_epoch"]):
            # 上次可能是在 replace 已发生、目录 fsync 失败后退出；清 journal 前重新确保持久。
            _save_json(state / IDENTITY_DIRECTORY / "identity.json", value)
            return dict(value)
        if value["ownership_state"] != "unclaimed" or value["trust_epoch"] != pending["admission_epoch"]:
            raise HomeIdentityError("认领期间身份已变化，请重新认领")
        value.update(ownership_state="claimed", owner_subject=pending["subject"], trust_epoch=pending["trust_epoch"])
        _save_json(state / IDENTITY_DIRECTORY / "identity.json", value)
        return dict(value)


def prepare_replaced_home(staging_state, reason: str) -> dict:
    """解包后的暂存目录发布前调用；恢复旧摘要也不会恢复旧授权。"""
    state = Path(staging_state)
    _private_directory(state / IDENTITY_DIRECTORY)
    value = _ensure(state)
    value["trust_epoch"] = uuid.uuid4().hex
    _save_json(state / IDENTITY_DIRECTORY / "identity.json", value)
    # 恢复/搬家要求重新配对，旧派发不能在新信任代次续跑。
    for name in ("connected-computers", "computer-file-intents", "computer-job-intents"):
        computer_directory = state / name
        if computer_directory.is_symlink():
            raise HomeIdentityError("电脑状态目录不可用")
        if computer_directory.exists():
            import shutil
            try:
                shutil.rmtree(computer_directory)
            except OSError as error:
                raise HomeIdentityError("无法清理旧的电脑状态") from error
    return dict(value)
