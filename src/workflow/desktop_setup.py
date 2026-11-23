"""Durable onboarding for the workspace created by the bundled Desktop installer."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
from unicodedata import category

from src.utils.home_maintenance import FileLease
from src.utils.state_dir import state_dir

FILE = "desktop-setup.json"
LOCK = "desktop-setup.lock"
LEGACY_FILE = ".vortoone-desktop-setup.json"
MAX_NAME = 80
_MAX_BYTES = 8192
_STEPS = {
    "team": ("purpose", "name", "business", "about", "team", "model", "invite", "done"),
    "personal": ("purpose", "about", "team", "model", "done"),
}
_INPUTS = {"purpose": "mode", "name": "name", "business": "context", "about": "workArea"}


class SetupChanged(Exception):
    """Another window has already answered this step."""


def enabled() -> bool:
    return os.environ.get("VORTOONE_BUNDLED_DESKTOP") == "1"


def _read(path: Path) -> dict | None:
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("本机引导文件无效")
            raw = stream.read(_MAX_BYTES + 1)
    except FileNotFoundError:
        return None
    if len(raw) > _MAX_BYTES:
        raise ValueError("本机引导文件过长")
    state = json.loads(raw)
    if not isinstance(state, dict):
        raise ValueError("本机引导文件无效")
    allowed = {"step", "mode", "name", "context", "workArea", "ownerDisplayName"}
    if set(state) - allowed or not all(isinstance(value, str) for value in state.values()):
        raise ValueError("本机引导数据无效")
    if state == {"step": "inactive"}:
        return state
    step, mode = state.get("step"), state.get("mode")
    if step == "purpose" and mode is None:
        return state
    if mode not in _STEPS or step not in _STEPS[mode][1:]:
        raise ValueError("本机引导进度无效")
    return state


def read(root: str) -> dict | None:
    state = _read(state_dir(root) / FILE)
    if state is None:
        state = _read(Path(root) / LEGACY_FILE)
    return None if state == {"step": "inactive"} else state


def _write(path: Path, state: dict) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{FILE}.", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(state, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def advance(root: str, step: str, answer: dict[str, str], validate_answer) -> dict | None:
    if read(root) is None:
        return None
    state_home = state_dir(root)
    with FileLease(state_home / LOCK, private=True):
        state = read(root)
        if state is None:
            return None
        if step != state["step"]:
            raise SetupChanged
        field = _INPUTS.get(step)
        if field is None:
            if answer:
                raise ValueError("此步骤无需填写额外信息")
        else:
            value = answer.get(field, "").strip()
            if set(answer) != {field} or not value or any(ord(char) < 32 and char not in "\n\t" for char in value):
                raise ValueError("请填写当前步骤所需的信息")
            if step == "purpose" and value not in _STEPS:
                raise ValueError("请选择工作区用途")
            validate_answer(field, value)
            state[field] = value
        steps = _STEPS[state["mode"]]
        state["step"] = steps[steps.index(step) + 1]
        _write(state_home / FILE, state)
        return state


def preferences(root: str) -> dict:
    """Initial answers serve as defaults for the ordinary workspace and profile settings."""
    try:
        return read(root) or {}
    except (ValueError, OSError):
        return {}


def initial_owner_name(root: str) -> str:
    """The name from the bundled installer is display metadata for token-only local owners."""
    name = preferences(root).get("ownerDisplayName", "").strip()
    return name if len(name) <= MAX_NAME and not any(category(char) == "Cc" for char in name) else ""


def rename_workspace(root: str, value: str) -> bool:
    """Return False for an ordinary workspace without a bundled setup record."""
    if read(root) is None:
        return False
    state_home = state_dir(root)
    with FileLease(state_home / LOCK, private=True):
        state = read(root)
        if state is None:
            return False
        state["name"] = value
        _write(state_home / FILE, state)
        return True


def suppress_replaced_legacy(root: str, staging: Path) -> None:
    """A legacy marker outside the replaced state directory belongs to the prior home."""
    target = staging / FILE
    if os.path.lexists(Path(root) / LEGACY_FILE) and not target.exists():
        _write(target, {"step": "inactive"})
