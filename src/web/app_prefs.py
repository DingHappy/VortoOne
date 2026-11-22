"""新版界面里每个人自己的显示偏好：侧栏怎么排、界面用什么语言。

只是界面的摆放，不涉及权限、不进模型上下文。所有者和成员各存各的，键是调用人的身份
（``member_auth.principal`` 给的 id）；语言跟着账号走，侧栏跟着「这个人在这个家里」走。
存在 ``.vortoone/app_prefs.json``，坏了就如实报错（ValueError），不悄悄换成默认值覆盖掉。

字段和取值按界面合同（frontend/packages/client/src/api/types.ts 的 SidebarItem / SidebarPlace /
SidebarSection / SidebarView）逐项核对；不认识的值一律拒绝。
"""

from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.utils.state_dir import state_dir

_FILE = "app_prefs.json"
_LOCK = threading.Lock()

# 界面的工具与分区（types.ts 的 SidebarItem / SidebarSection）。默认顺序用「个人空间」那一套
# （sidebar-items.ts 的 PERSONAL_SIDEBAR_ITEMS / PERSONAL_SIDEBAR_SECTIONS）：这个家的 settings.mode 是 personal。
TOOLS = ("waiting", "approvals", "proposals", "routines", "drive", "calendar", "issues", "threads", "team",
         "wiki", "dashboards", "taskManager")
SECTIONS = ("tools", "agents", "channels", "people")
PLACES = ("always", "more", "auto")
AGENT_SORTS = ("workspace", "mine", "working")
DEFAULT_VIEW = {"agentSort": "workspace", "showAllAgents": True, "pins": None}

# 界面能选的语言（packages/i18n/src/locale.ts 的 LOCALES）
LOCALES = ("en", "pt-BR", "es", "fr", "de", "it", "nl", "ja", "ko", "zh-Hans", "id", "tr", "pl", "ru")
DEFAULT_LOCALE = "zh-Hans"


def _path(root: str) -> Path:
    return state_dir(root) / _FILE


def _read(root: str) -> Dict[str, Any]:
    path = _path(root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "people": {}}
    except (OSError, ValueError) as error:
        raise ValueError("界面偏好读不出来，需要先修复") from error
    if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("people"), dict):
        raise ValueError("界面偏好记录损坏，需要先修复")
    return value


def _write(root: str, value: Dict[str, Any]) -> None:
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _own(root: str, who: str) -> Dict[str, Any]:
    row = _read(root)["people"].get(who)
    return deepcopy(row) if isinstance(row, dict) else {}


def _change(root: str, who: str, key: str, value: Any) -> None:
    """改一个人的一项；value 为 None 表示回到默认（把这项删掉）。"""
    if not who:
        raise ValueError("不知道是谁的偏好")
    with _LOCK:
        data = _read(root)
        row = data["people"].setdefault(who, {})
        if value is None:
            row.pop(key, None)
        else:
            row[key] = value
        if not row:
            data["people"].pop(who, None)
        _write(root, data)


def _unique_known(items: Any, known: tuple, what: str) -> List[str]:
    if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
        raise ValueError(f"{what}必须是字符串列表")
    unknown = [item for item in items if item not in known]
    if unknown:
        raise ValueError(f"{what}里有不认识的项：{unknown}")
    if len(set(items)) != len(items):
        raise ValueError(f"{what}里有重复的项")
    return list(items)


# ── 侧栏：哪些工具放在列表里 ────────────────────────────────────────────────

def sidebar_items(root: str, who: str) -> Dict[str, str]:
    return dict(_own(root, who).get("sidebarItems") or {})


def _check_items(items: Any) -> Dict[str, str]:
    if not isinstance(items, dict):
        raise ValueError("items 必须是对象")
    _unique_known(list(items), TOOLS, "侧栏工具")
    bad = {item: place for item, place in items.items() if place not in PLACES}
    if bad:
        raise ValueError(f"侧栏位置只能是 {list(PLACES)}：{bad}")
    return dict(items)


def set_sidebar_items(root: str, who: str, items: Any) -> Dict[str, str]:
    clean = _check_items(items)
    _change(root, who, "sidebarItems", clean or None)
    return sidebar_items(root, who)


def set_sidebar_item(root: str, who: str, item: Any, place: Any) -> Dict[str, str]:
    """挪一个工具；place 为 None 表示这一项回到默认位置。"""
    if item not in TOOLS:
        raise ValueError(f"不认识的侧栏工具：{item!r}")
    if place is not None and place not in PLACES:
        raise ValueError(f"侧栏位置只能是 {list(PLACES)}")
    with _LOCK:
        data = _read(root)
        row = data["people"].setdefault(who, {})
        items = dict(row.get("sidebarItems") or {})
        if place is None:
            items.pop(item, None)
        else:
            items[item] = place
        if items:
            row["sidebarItems"] = items
        else:
            row.pop("sidebarItems", None)
        if not row:
            data["people"].pop(who, None)
        _write(root, data)
    return sidebar_items(root, who)


# ── 侧栏：分区顺序、工具顺序、agent 怎么排 ───────────────────────────────────

def sidebar_order(root: str, who: str) -> List[str]:
    return list(_own(root, who).get("sidebarOrder") or SECTIONS)


def set_sidebar_order(root: str, who: str, order: Any) -> List[str]:
    """order 为 None 回到默认。必须是全部分区各一次：少一个分区界面上就找不到它了。"""
    if order is not None:
        order = _unique_known(order, SECTIONS, "侧栏分区")
        if set(order) != set(SECTIONS):
            raise ValueError(f"侧栏分区要列全：{list(SECTIONS)}")
    _change(root, who, "sidebarOrder", order)
    return sidebar_order(root, who)


def sidebar_tools(root: str, who: str) -> List[str]:
    saved = _own(root, who).get("sidebarTools")
    if not saved:
        return list(TOOLS)
    # 存下之后界面又多了工具：存过的在前，新的按默认顺序接在后面，不丢
    return list(saved) + [tool for tool in TOOLS if tool not in saved]


def set_sidebar_tools(root: str, who: str, order: Any) -> List[str]:
    if order is not None:
        order = _unique_known(order, TOOLS, "侧栏工具顺序") or None
    _change(root, who, "sidebarTools", order)
    return sidebar_tools(root, who)


def sidebar_view(root: str, who: str) -> Dict[str, Any]:
    view = dict(DEFAULT_VIEW)
    view.update(_own(root, who).get("sidebarView") or {})
    return view


def set_sidebar_view(root: str, who: str, view: Any) -> Dict[str, Any]:
    """只存排序方式和「显示全部 agent」；置顶（pins）按合同从不保存。"""
    if not isinstance(view, dict) or not {"agentSort", "showAllAgents"} <= set(view) \
            or set(view) - {"agentSort", "showAllAgents", "pins"}:
        raise ValueError("view 要有 agentSort 和 showAllAgents")
    if view["agentSort"] not in AGENT_SORTS:
        raise ValueError(f"agentSort 只能是 {list(AGENT_SORTS)}")
    if type(view["showAllAgents"]) is not bool:
        raise ValueError("showAllAgents 必须是布尔值")
    clean = {"agentSort": view["agentSort"], "showAllAgents": view["showAllAgents"]}
    _change(root, who, "sidebarView", None if clean == {k: DEFAULT_VIEW[k] for k in clean} else clean)
    return sidebar_view(root, who)


# ── 界面语言（跟着账号）────────────────────────────────────────────────────

def locale(root: str, account_key: str) -> Dict[str, Any]:
    row = _own(root, account_key)
    return {"locale": row.get("locale") or DEFAULT_LOCALE, "localeAuto": bool(row.get("localeAuto", False))}


def set_locale(root: str, account_key: str, body: Any) -> None:
    if not isinstance(body, dict) or "locale" not in body or set(body) - {"locale", "auto"}:
        raise ValueError("只能改 locale（和 auto）")
    if body["locale"] not in LOCALES:
        raise ValueError(f"不认识的语言：{body['locale']!r}")
    auto: Optional[Any] = body.get("auto", False)
    if type(auto) is not bool:
        raise ValueError("auto 必须是布尔值")
    with _LOCK:
        data = _read(root)
        row = data["people"].setdefault(account_key, {})
        row["locale"] = body["locale"]
        row["localeAuto"] = auto
        _write(root, data)


# ── 整个家的界面开关（所有者在空间设置里改）─────────────────────────────────

HOME_DEFAULTS: Dict[str, Any] = {"gifs": True, "gifRating": "g", "voiceMessages": True}
_HOME_CHECKS = {"gifs": lambda value: type(value) is bool, "gifRating": lambda value: value in ("g", "pg"),
                "voiceMessages": lambda value: type(value) is bool}


def home_settings(root: str) -> Dict[str, Any]:
    saved = _read(root).get("home")
    values = dict(HOME_DEFAULTS)
    if isinstance(saved, dict):
        values.update({key: value for key, value in saved.items() if key in HOME_DEFAULTS})
    return values


def check_home_settings(changes: Dict[str, Any]) -> None:
    for key, value in changes.items():
        if key not in _HOME_CHECKS or not _HOME_CHECKS[key](value):
            raise ValueError(f"{key} 的值无效")


def set_home_settings(root: str, changes: Dict[str, Any]) -> Dict[str, Any]:
    check_home_settings(changes)
    with _LOCK:
        data = _read(root)
        home = data.get("home") if isinstance(data.get("home"), dict) else {}
        home.update(changes)
        data["home"] = {key: value for key, value in home.items() if HOME_DEFAULTS.get(key) != value}
        if not data["home"]:
            data.pop("home")
        _write(root, data)
    return home_settings(root)
