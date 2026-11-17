"""agent 在界面上的样子和状态：外观、建立时间、是否归档、新建时从哪个现成角色开始。

这些不是权限，不进岗位和专员的登记（那两样要所有者批准、带版本）；这里只存界面需要的展示信息。
权限、引擎和模型仍以 ``StaffRegistry`` 为准，谁能和它对话以成员登记处的受众为准。

- 外观只收界面认识的取值（``engine.d.ts`` 的颜色、眼睛、配饰，颜色另可 ``#rrggbb``）。
- 归档 = 专员暂停、从团队里隐藏，可以恢复；彻底删除 = 专员撤掉（终态），这里记 ``deleted``。
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Mapping, Optional

from src.utils.state_dir import state_dir

COLORS = frozenset({"sky", "indigo", "violet", "rose", "amber", "green", "teal", "graphite"})
EYES = frozenset({"round", "dots", "wide", "happy", "sleepy"})
ACCESSORIES = frozenset({"none", "glasses", "hat", "cap", "headphones", "bow", "crown", "antenna", "headset",
                         "beanie", "party", "flower", "scarf"})
SETUP_ROLES = frozenset({"dev", "ops", "qa", "pm", "support", "sales", "marketing", "content", "data", "finance",
                         "people", "research", "assistant"})
SETUP_STEPS = frozenset({"ai", "apps", "where", "who", "how"})
_HEX = re.compile(r"#[0-9a-fA-F]{6}")
_FILE = "agent_profiles.json"
_LOCK = threading.RLock()


def check_look(value: Any) -> Dict[str, str]:
    """界面送来的外观 → 存下的外观；不认识的取值报错，不悄悄丢掉。"""
    if value is None:
        return {}
    if not isinstance(value, Mapping) or set(value) - {"color", "eyes", "accessory"}:
        raise ValueError("外观只有颜色、眼睛、配饰三项")
    look: Dict[str, str] = {}
    color, eyes, accessory = value.get("color"), value.get("eyes"), value.get("accessory")
    if color is not None:
        if not isinstance(color, str) or not (color in COLORS or _HEX.fullmatch(color)):
            raise ValueError("颜色不认识")
        look["color"] = color
    if eyes is not None:
        if eyes not in EYES:
            raise ValueError("眼睛样式不认识")
        look["eyes"] = str(eyes)
    if accessory is not None:
        if accessory not in ACCESSORIES:
            raise ValueError("配饰不认识")
        look["accessory"] = str(accessory)
    return look


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AgentProfiles:
    def __init__(self, root: str):
        self.path = state_dir(root) / "staff" / _FILE

    def _load(self) -> Dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def all(self) -> Dict[str, Dict[str, Any]]:
        with _LOCK:
            return {key: dict(item) for key, item in self._load().items() if isinstance(item, dict)}

    def get(self, member_id: str) -> Dict[str, Any]:
        return self.all().get(str(member_id), {})

    @contextmanager
    def _change(self) -> Iterator[Dict[str, Any]]:
        with _LOCK:
            state = self._load()
            yield state
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f".{_FILE}.{uuid.uuid4().hex}.tmp")
            try:
                temporary.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
                temporary.chmod(0o600)
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)

    def create(self, member_id: str, *, look: Mapping[str, str], setup: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
        """setup：新建时的清单（已由调用方整理好：role 是现成角色或 None，另可有 hello、starter、apps）。"""
        if setup is not None and setup.get("role") is not None and setup["role"] not in SETUP_ROLES:
            raise ValueError("现成角色不认识")
        with self._change() as state:
            state[str(member_id)] = {"appearance": dict(look), "created": _now(), "archived": False,
                                     "deleted": False,
                                     "setup": {**dict(setup), "confirmed": []} if setup is not None else None}
            return dict(state[str(member_id)])

    def update(self, member_id: str, **change: Any) -> Dict[str, Any]:
        with self._change() as state:
            item = state.setdefault(str(member_id), {"created": _now()})
            item.update(change)
            return dict(item)

    def confirm_step(self, member_id: str, step: str) -> Dict[str, Any]:
        if step not in SETUP_STEPS:
            raise ValueError("没有这一步")
        with self._change() as state:
            item = state.setdefault(str(member_id), {"created": _now()})
            setup = item.get("setup") or {"role": None, "confirmed": []}
            if step not in setup["confirmed"]:
                setup["confirmed"].append(step)
            item["setup"] = setup
            return dict(item)
