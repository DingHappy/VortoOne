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
    "team": ("purpose", "name", "model", "presets", "done"),
    "personal": ("purpose", "model", "presets", "done"),
}
_LEGACY_STEPS = {
    "team": {"business", "about", "team", "invite"},
    "personal": {"about", "team"},
}


class SetupChanged(Exception):
    """Another window has already answered this step."""


class ModelRequired(Exception):
    """Setup can only choose presets with a configured model."""


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
    allowed = {"step", "mode", "name", "context", "workArea", "ownerDisplayName", "modelDeferred"}
    if set(state) - allowed or not all(isinstance(value, str) for key, value in state.items() if key != "modelDeferred"):
        raise ValueError("本机引导数据无效")
    if state == {"step": "inactive"}:
        return state
    step, mode = state.get("step"), state.get("mode")
    if "modelDeferred" in state and (state["modelDeferred"] is not True or step != "done"):
        raise ValueError("本机引导的模型状态无效")
    if step == "purpose" and mode is None:
        return state
    if mode not in _STEPS or (step not in _STEPS[mode][1:] and step not in _LEGACY_STEPS[mode]):
        raise ValueError("本机引导进度无效")
    return state


def read(root: str) -> dict | None:
    state = _read(state_dir(root) / FILE)
    if state is None:
        state = _read(Path(root) / LEGACY_FILE)
    return None if state == {"step": "inactive"} else state


def current_step(state: dict, model_configured: bool) -> str:
    """Project older, unfinished markers onto the current flow without writing them."""
    step = state["step"]
    if step in {"purpose", "done"}:
        return step
    if step == "business" or (state.get("mode") == "team" and not state.get("name", "").strip()):
        return "name"
    if step in {"about", "team", "invite"} or (step == "presets" and not model_configured):
        return "model"
    return step


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


def advance(root: str, step: str, answer: dict[str, str], model_configured, *, skip_model: bool = False) -> dict | None:
    if read(root) is None:
        return None
    state_home = state_dir(root)
    with FileLease(state_home / LOCK, private=True):
        state = read(root)
        if state is None:
            return None
        configured = model_configured()
        retesting = step == "model" and not skip_model and state.get("modelDeferred") is True
        if step != current_step(state, configured) and not retesting:
            raise SetupChanged
        if skip_model and step != "model":
            raise ValueError("只能在连接模型时选择稍后设置")
        if step == "purpose":
            if set(answer) != {"mode"} or answer["mode"] not in _STEPS:
                raise ValueError("请选择工作区用途")
            state["mode"] = answer["mode"]
        elif step == "name":
            values = {key: value.strip() for key, value in answer.items()}
            if (not values.get("name") or set(values) - {"name", "context"}
                    or any(ord(char) < 32 and char not in "\n\t" for value in values.values() for char in value)):
                raise ValueError("请填写工作区名称")
            state.update(values)
        elif answer:
            raise ValueError("此步骤无需填写额外信息")
        if step == "model" and not skip_model and not configured:
            raise ModelRequired
        if skip_model:
            state.update(step="done", modelDeferred=True)
        elif retesting:
            del state["modelDeferred"]
        else:
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
