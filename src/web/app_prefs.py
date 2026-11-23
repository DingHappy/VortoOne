"""新版界面的显示偏好：侧栏、界面语言、个人资料和团队图。

只是界面展示，不涉及权限、不进模型上下文。个人偏好按调用人的身份保存
（``member_auth.principal`` 给的 id）；语言跟着账号走，侧栏、用户名和时区跟着「这个人在这个家里」走。
团队图的汇报连线属于工作区，只有所有者可以编辑。
存在 ``.vortoone/app_prefs.json``，坏了就如实报错（ValueError），不悄悄换成默认值覆盖掉。

字段和取值按界面合同（frontend/packages/client/src/api/types.ts 的 SidebarItem / SidebarPlace /
SidebarSection / SidebarView）逐项核对；不认识的值一律拒绝。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unicodedata import category

from src.workflow.members.profile_preferences import PREFERENCES_LOCK as _LOCK
from src.workflow.members.profile_preferences import read_preferences as _read
from src.workflow.members.profile_preferences import write_preferences as _write

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
AVAILABILITIES = ("auto", "away", "busy", "out", "hidden")


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
        if not isinstance(row, dict):
            raise ValueError("个人偏好记录损坏，需要先修复")
        if value is None:
            row.pop(key, None)
        else:
            row[key] = value
        if not row:
            data["people"].pop(who, None)
        _write(root, data)


def person_value(root: str, who: str, key: str) -> Any:
    """一个人的一项偏好（没有就是 None）。"""
    return _own(root, who).get(key)


def set_person_value(root: str, who: str, key: str, value: Any) -> None:
    _change(root, who, key, value)


def _status_line(value: Any) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"text", "emoji", "until"}:
        raise ValueError("状态要包含文字，可附表情和到期时间")
    text, emoji, until = value.get("text"), value.get("emoji"), value.get("until")
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 100 or _has_control(text):
        raise ValueError("状态文字须为 1 到 100 个可显示字符")
    if emoji is not None and (not isinstance(emoji, str) or not 1 <= len(emoji) <= 32 or _has_control(emoji)):
        raise ValueError("状态表情格式不正确")
    if until is not None:
        if not isinstance(until, str) or len(until) > 40:
            raise ValueError("状态到期时间须为带时区的 ISO 时间")
        try:
            instant = datetime.fromisoformat(until[:-1] + "+00:00" if until.endswith("Z") else until)
        except ValueError as error:
            raise ValueError("状态到期时间须为带时区的 ISO 时间") from error
        if instant.tzinfo is None or "T" not in until:
            raise ValueError("状态到期时间须为带时区的 ISO 时间")
        if instant <= datetime.now(timezone.utc):
            return None
    return {"text": text.strip(), "emoji": emoji, "until": until}


def _has_control(text: str) -> bool:
    return any(category(char) in {"Cc", "Cs"} for char in text)


def status_preferences(root: str) -> Dict[str, Optional[Dict[str, Any]]]:
    return {who: {"availability": row.get("availability"), "status": row.get("status")} if isinstance(row, dict) else None
            for who, row in _read(root)["people"].items()}


def status_line(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        return _status_line(row.get("status"))
    except ValueError:
        return None


def set_status(root: str, who: str, body: Dict[str, Any]) -> Dict[str, Any]:
    if not body or set(body) - {"availability", "status"}:
        raise ValueError("只能更新可用状态或状态文字")
    if "availability" in body and body["availability"] not in AVAILABILITIES:
        raise ValueError("未识别的可用状态")
    line = _status_line(body.get("status"))
    with _LOCK:
        data = _read(root)
        row = data["people"].setdefault(who, {})
        if not isinstance(row, dict):
            raise ValueError("界面偏好记录损坏，需要先修复")
        if "availability" in body:
            row["availability"] = body["availability"]
        if "status" in body:
            if line is None:
                row.pop("status", None)
            else:
                row["status"] = line
        _write(root, data)
        return {"availability": row.get("availability"), "status": row.get("status")}


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


# ── 账号偏好 ────────────────────────────────────────────────────────────────

# frontend/packages/client/src/lib/work-area.ts 的 WORK_AREA_ANSWERS
WORK_AREAS = ("dev", "marketing", "sales", "finance", "ops", "support", "other", "skipped")


def account_preferences(root: str, account_key: str) -> Dict[str, Any]:
    row = _own(root, account_key)
    area = row.get("workArea")
    return {"locale": row.get("locale") or DEFAULT_LOCALE, "localeAuto": bool(row.get("localeAuto", False)),
            "workArea": area if area in WORK_AREAS else None}


def set_work_area(root: str, account_key: str, body: Dict[str, Any]) -> None:
    if set(body) != {"area"} or body["area"] not in WORK_AREAS:
        raise ValueError("请选择引导中的工作领域或跳过")
    _change(root, account_key, "workArea", body["area"])


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


# ── 团队图的汇报关系 ────────────────────────────────────────────────────────

def _agent_lines(data: Dict[str, Any]) -> Dict[str, str]:
    saved = data.get("agentLines", {})
    if not isinstance(saved, dict) or any(not key or not isinstance(value, str) or not value
                                          for key, value in saved.items()):
        raise ValueError("团队图的汇报关系记录无效")
    return dict(saved)


def agent_lines(root: str) -> Dict[str, str]:
    return _agent_lines(_read(root))


def set_agent_lines(root: str, changes: Dict[str, Optional[str]]) -> Dict[str, Optional[str]]:
    """保存经过名册校验的批量更改，返回每一条实际更改前的汇报对象。"""
    with _LOCK:
        data = _read(root)
        lines = _agent_lines(data)
        previous = {}
        for agent, supervisor in changes.items():
            if lines.get(agent) == supervisor:
                continue
            previous[agent] = lines.get(agent)
            if supervisor is None:
                lines.pop(agent, None)
            else:
                lines[agent] = supervisor
        # 与客户端契约相同，一条线最多三位智能体，不能把自己或下属当作上级。
        for agent in lines:
            seen = {agent}
            supervisor = lines.get(agent)
            while supervisor is not None:
                if supervisor in seen:
                    raise ValueError("团队图的汇报关系不能形成环")
                seen.add(supervisor)
                if len(seen) > 3:
                    raise ValueError("团队图的汇报链最多三位智能体")
                supervisor = lines.get(supervisor)
        if previous:
            if lines:
                data["agentLines"] = lines
            else:
                data.pop("agentLines", None)
            _write(root, data)
    return previous


# ── 看板怎么看（每个人自己的）─────────────────────────────────────────────────

ISSUES_VIEW_DEFAULT: Dict[str, Any] = {
    "layout": "board", "group": "stage", "sort": "manual",
    "fields": {"project": True, "assignee": True, "priority": True, "due": True, "criteria": True,
               "pullRequest": True, "label": True},
    "assignees": [], "priorities": [], "showCanceled": False}
_VIEW_CHOICES = {"layout": ("board", "list"), "group": ("stage", "assignee", "priority"),
                 "sort": ("manual", "priority", "due")}


def issues_view(root: str, who: str) -> Dict[str, Any]:
    saved = _own(root, who).get("issuesView") or {}
    value = deepcopy(ISSUES_VIEW_DEFAULT)
    value.update({key: item for key, item in saved.items() if key != "fields"})
    value["fields"].update(saved.get("fields") or {})
    return value


def set_issues_view(root: str, who: str, view: Any) -> Dict[str, Any]:
    if not isinstance(view, dict) or set(view) - set(ISSUES_VIEW_DEFAULT):
        raise ValueError("看板视图里有不认识的项")
    for key, choices in _VIEW_CHOICES.items():
        if key in view and view[key] not in choices:
            raise ValueError(f"{key} 只能是 {list(choices)}")
    fields = view.get("fields", {})
    if not isinstance(fields, dict) or set(fields) - set(ISSUES_VIEW_DEFAULT["fields"]) or any(
            type(value) is not bool for value in fields.values()):
        raise ValueError("fields 无效")
    for key in ("assignees", "priorities"):
        if key in view and (not isinstance(view[key], list) or len(view[key]) > 100
                            or any(not isinstance(item, str) or len(item) > 140 for item in view[key])):
            raise ValueError(f"{key} 无效")
    if "showCanceled" in view and type(view["showCanceled"]) is not bool:
        raise ValueError("showCanceled 要是布尔值")
    _change(root, who, "issuesView", deepcopy(view))
    return issues_view(root, who)


# ── 主人在界面上新建、还在等核对的例行工作提议 ───────────────────────────────
# 新界面的核对页只处理成员提交的和这里记下的；助理替主人提的照旧不在这里核对（#963 定的边界）。

def ui_routine_proposals(root: str) -> set:
    saved = _read(root).get("routineProposals")
    return {item for item in saved if isinstance(item, str)} if isinstance(saved, list) else set()


def remember_ui_routine_proposal(root: str, proposal_id: str) -> None:
    with _LOCK:
        data = _read(root)
        saved = [item for item in data.get("routineProposals") or [] if isinstance(item, str)]
        data["routineProposals"] = (saved + [proposal_id])[-200:]
        _write(root, data)


# ── 每个人在这个空间的显示方式 ──────────────────────────────────────────────────

_CHAT_STYLES = ("comfortable", "left", "bubbles", "compact")
_CHAT_TONES = ("light", "strong")
_CHAT_CHOICES = {
    "channelStyle": _CHAT_STYLES, "dmStyle": _CHAT_STYLES,
    "channelTone": _CHAT_TONES, "dmTone": _CHAT_TONES,
    "openAt": ("latest", "read"), "textSize": ("xs", "sm", "md", "lg", "xl"),
}
_DEFAULT_START = {"page": "home", "channelId": None}


def workspace_display(root: str, who: str) -> Dict[str, Any]:
    saved = _own(root, who)
    start = dict(_DEFAULT_START)
    chosen = saved.get("start")
    if isinstance(chosen, dict):
        page, channel_id = chosen.get("page"), chosen.get("channelId")
        if page == "last":
            start["page"] = "last"
        if page == "channel" and isinstance(channel_id, str) and channel_id:
            start = {"page": "channel", "channelId": channel_id}
    chat = saved.get("chatPrefs")
    if isinstance(chat, dict):
        chat = {
            key: value for key, value in chat.items()
            if key in _CHAT_CHOICES and value in _CHAT_CHOICES[key]
        }
    else:
        chat = {}
    return {"showActivity": saved.get("showActivity") is not False, "chatPrefs": chat, "start": start}


def set_show_activity(root: str, who: str, show: bool) -> Dict[str, bool]:
    _change(root, who, "showActivity", None if show else False)
    return {"show": show}


def set_start(root: str, who: str, page: str, channel: str | None) -> Dict[str, Any]:
    start = {"page": page, "channelId": channel}
    _change(root, who, "start", None if start == _DEFAULT_START else start)
    return start


def set_chat_look(root: str, who: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    if not patch or any(key not in _CHAT_CHOICES or val not in _CHAT_CHOICES[key] for key, val in patch.items()):
        raise ValueError("请选择支持的聊天外观设置")
    with _LOCK:
        data = _read(root)
        row = data["people"].setdefault(who, {})
        chat = row.get("chatPrefs", {})
        if not isinstance(chat, dict):
            raise ValueError("聊天外观记录损坏，需要先修复")
        chat.update(patch)
        row["chatPrefs"] = chat
        _write(root, data)
    return chat
