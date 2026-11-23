"""Workspace profile preferences and the shared person/agent mention namespace."""
from __future__ import annotations

import json
import os
import re
import threading
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from src.agents.principal import Principal
from src.scheduling.routine_schedule import zone
from src.utils.state_dir import state_dir
from src.workflow.members.registry import MAX_MEMBERS, MemberRegistry, Refused, is_member_id

PREFERENCES_LOCK = threading.Lock()
_MAX_HANDLES_PER_PERSON = 16
_MAX_HANDLES_PER_WORKSPACE = (MAX_MEMBERS + 1) * _MAX_HANDLES_PER_PERSON
_HANDLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{1,31}")
# ASCII group mentions from frontend/packages/i18n/src/everyone.ts and system names.
_RESERVED = frozenset({"owner", "assistant", "everyone", "here", "all", "channel", "me", "admin",
                       "todos", "tous", "alle", "tutti", "iedereen", "semua", "herkes", "wszyscy"})


def _path(root: str) -> Path:
    return state_dir(root) / "app_prefs.json"


def read_preferences(root: str) -> dict[str, Any]:
    try:
        value = json.loads(_path(root).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "people": {}}
    except (OSError, ValueError) as error:
        raise ValueError("界面偏好读不出来，需要先修复") from error
    if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("people"), dict):
        raise ValueError("界面偏好记录损坏，需要先修复")
    return value


def write_preferences(root: str, value: dict[str, Any]) -> None:
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temporary, path)


def _handle_owners(value: dict[str, Any]) -> dict[str, str]:
    owners = value.get("handleOwners", {})
    if not isinstance(owners, dict) or any(not _HANDLE.fullmatch(key) or key != key.lower()
                                           or not isinstance(who, str) or not who
                                           for key, who in owners.items()):
        raise ValueError("用户名登记损坏，需要先修复")
    return owners


def check_agent_handle(root: str, handle: str) -> None:
    """Use the same reservation as profile writes while the staff registry lock is held."""
    # Legacy staff can already use system names such as "assistant"; the Desktop creator
    # applies its own reserved-name rules. Here only the human name claims are relevant.
    if handle in _handle_owners(read_preferences(root)) or is_member_id(handle):
        raise ValueError("这个用户名已留给成员，请换一个")


def _profile(who: str, row: dict[str, Any], owners: dict[str, str]) -> dict[str, str]:
    profile = {}
    handle = row.get("handle")
    if isinstance(handle, str) and owners.get(handle) == who:
        profile["handle"] = handle
    if isinstance(row.get("timezone"), str) and row["timezone"]:
        profile["timezone"] = row["timezone"]
    return profile


def profile_preferences(root: str) -> dict[str, dict[str, str]]:
    try:
        data = read_preferences(root)
    except ValueError:
        return {}  # The directory remains readable using the existing default profiles.
    try:
        owners = _handle_owners(data)
    except ValueError:
        owners = {}  # An unverified handle is never projected into the directory.
    return {who: _profile(who, row, owners) for who, row in data["people"].items() if isinstance(row, dict)}


def save_profile(root: str, actor: Principal, changes: dict[str, Any]) -> dict[str, str]:
    if not actor.is_owner and not actor.is_member:
        raise Refused("只有所有者和在册成员能改个人资料")
    if changes.keys() - {"handle", "timezone"}:
        raise ValueError("个人资料只接受用户名和时区")
    if "timezone" in changes and changes["timezone"] is not None:
        try:
            zone(changes["timezone"])
        except ValueError as error:
            raise ValueError("个人时区须使用有效的 IANA 名称，例如 Asia/Shanghai") from error
    handle = changes.get("handle")
    if "handle" in changes:
        if not isinstance(handle, str) or not _HANDLE.fullmatch(handle):
            raise ValueError("用户名须为 2 到 32 个字母、数字、- 或 _，以字母或数字开头")
        handle = handle.lower()

    from src.workflow.staff.registry import StaffRegistry
    from src.workflow.tasks.tasks import TASK_STATE_LOCK

    default = "owner" if actor.is_owner else actor.id
    membership = nullcontext() if actor.is_owner else MemberRegistry(root).while_member_active(actor)
    # The backend holds the home RuntimeLease for its lifetime. This outer thread lock is shared
    # with staff and bundle writes. Keep membership and both namespace checks stable through save.
    with TASK_STATE_LOCK, membership, PREFERENCES_LOCK:
        data = read_preferences(root)
        owners = _handle_owners(data)
        row = data["people"].setdefault(actor.id, {})
        if not isinstance(row, dict):
            raise ValueError("个人偏好记录损坏，需要先修复")
        current = _profile(actor.id, row, owners).get("handle", default)
        if handle is not None and handle != current:
            if handle != default and (handle in _RESERVED or is_member_id(handle)
                                      or handle.startswith("staff-cron-") or owners.get(handle, actor.id) != actor.id):
                raise ValueError("这个用户名已留给其他成员或系统，请换一个")
            if handle in StaffRegistry(root).reserved_member_handles():
                raise ValueError("这个用户名已由智能体使用，请换一个")
            if handle == default:
                row.pop("handle", None)
            else:
                # Keep earlier handles reserved to this person, including after removal.
                if handle not in owners:
                    if sum(who == actor.id for who in owners.values()) >= _MAX_HANDLES_PER_PERSON:
                        raise ValueError(f"每个成员最多登记 {_MAX_HANDLES_PER_PERSON} 个不同的用户名，请选用之前的用户名")
                    if len(owners) >= _MAX_HANDLES_PER_WORKSPACE:
                        raise ValueError("这个空间的用户名登记已达上限，请选用之前的用户名")
                    owners[handle] = actor.id
                    data["handleOwners"] = owners
                row["handle"] = handle
        if "timezone" in changes:
            if changes["timezone"] is None:
                row.pop("timezone", None)
            else:
                row["timezone"] = changes["timezone"]
        if not row:
            data["people"].pop(actor.id, None)
        if changes:
            write_preferences(root, data)
        return _profile(actor.id, row, owners)
