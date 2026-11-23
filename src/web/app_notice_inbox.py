"""The owner's Desktop inbox over the existing scheduler notice ledger."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from src.scheduling.notices import MAX_NOTICES, load_notices
from src.utils.home_maintenance import home_maintenance
from src.utils.state_dir import ensure_state_gitignore, state_dir
from src.web.app_views import _id, _iso, dm_channel_id, main_agent_id, owner_connection

NoticeFilter = Literal["all", "mentions", "waiting", "unread", "reactions", "done"]
_FILE = "logs/notice-inbox.json"
_STATUS_FIELDS = ("readAt", "doneAt", "snoozedUntil")
_LOCK = threading.Lock()


class NoticeMissing(LookupError):
    pass


class InboxUnavailable(RuntimeError):
    pass


def _format_date(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def snooze_time(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if when.tzinfo is not None:
            return _format_date(when)
    except (OverflowError, ValueError):
        pass
    raise ValueError("稍后提醒时间须为带时区的 ISO 8601 时间")


def _read_status(path):
    if path.is_symlink():
        raise InboxUnavailable("通知状态记录路径无效")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (UnicodeError, ValueError) as error:
        raise InboxUnavailable("通知状态记录损坏") from error
    if not isinstance(data, dict) or len(data) > MAX_NOTICES:
        raise InboxUnavailable("通知状态记录无效")
    for row in data.values():
        if not isinstance(row, dict) or row.keys() != set(_STATUS_FIELDS):
            raise InboxUnavailable("通知状态记录无效")
        try:
            if any(value is not None and (not isinstance(value, str) or snooze_time(value) != value)
                   for value in row.values()):
                raise ValueError
        except ValueError as error:
            raise InboxUnavailable("通知状态记录无效") from error
    return data


def _snapshot(root):
    path = state_dir(root) / _FILE
    status = _read_status(path)
    agent = main_agent_id(root)
    can_mark = owner_connection()
    items = {}
    default_reads = {}
    for row in load_notices(root, MAX_NOTICES, strict=True):
        text, stamp = str(row.get("text") or ""), _iso(row.get("ts"))
        if not text:
            continue
        recorded_id = row.get("id")
        if isinstance(recorded_id, str) and len(recorded_id) == 32 and all(c in "0123456789abcdef" for c in recorded_id):
            notice_id, default_read = _id("notice", recorded_id), None
        else:
            key = hashlib.sha256(f"{row.get('ts')}\0{row.get('source')}\0{text}".encode("utf-8", "surrogatepass"))
            notice_id, default_read = _id("notice", key.hexdigest()), stamp
        # An identical legacy entry has the same existing wire identifier.
        if notice_id not in items:
            item = {"id": notice_id, "kind": "agent_reply", "waiting": False,
                    "actors": [{"type": "agent", "id": agent}], "count": 1,
                    "source": {"channelId": dm_channel_id(agent)}, "latest": {"text": text},
                    "readAt": default_read, "doneAt": None, "snoozedUntil": None, "createdAt": stamp,
                    "updatedAt": stamp}
            item.update(status.get(notice_id, {}))
            if not can_mark and item["readAt"] is None:
                # Without an owner token the history is readable, but inbox actions are unavailable.
                item["readAt"] = stamp
            items[notice_id] = item
            default_reads[notice_id] = default_read
    return path, items, status, default_reads


def _visible(item, now: str) -> bool:
    return item["doneAt"] is None and (item["snoozedUntil"] is None or item["snoozedUntil"] <= now)


def _select(items, filter_name: NoticeFilter, now: datetime):
    stamp = _format_date(now)
    if filter_name == "done":
        oldest = _format_date(now - timedelta(days=30))
        return sorted((item for item in items if (item["doneAt"] or "") >= oldest),
                      key=lambda item: item["doneAt"], reverse=True)
    if filter_name not in {"all", "unread"}:
        # The existing ledger contains only background notices.
        return []
    return [item for item in items if _visible(item, stamp)
            and (filter_name == "all" or item["readAt"] is None)]


def _counts(items, now: datetime):
    return {"all": len(_select(items, "all", now)), "unread": len(_select(items, "unread", now)),
            "mentions": 0, "waiting": 0, "reactions": 0}


def page(root: str, filter_name: NoticeFilter, cursor: int, limit: int):
    _, items, _, _ = _snapshot(root)
    now = datetime.now(timezone.utc)
    shown = _select(items.values(), filter_name, now)
    end = cursor + limit
    return {"items": shown[cursor:end], "counts": _counts(items.values(), now),
            "nextCursor": str(end) if end < len(shown) else None}


def count(root: str):
    _, items, _, _ = _snapshot(root)
    counts = _counts(items.values(), datetime.now(timezone.utc))
    return {key: counts[key] for key in ("unread", "waiting")}


def _write(root, path, values):
    ensure_state_gitignore(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".notice-inbox-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(values, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _change(root, apply):
    with _LOCK, home_maintenance(root):
        path, items, previous, default_reads = _snapshot(root)
        now = datetime.now(timezone.utc)
        result = apply(items, now)
        changed = {key: {field: item[field] for field in _STATUS_FIELDS} for key, item in items.items()
                   if (item["readAt"], item["doneAt"], item["snoozedUntil"]) != (default_reads[key], None, None)}
        if changed != previous:
            _write(root, path, changed)
        return result


def _read(item, read: bool, now: str):
    item["readAt"] = (item["readAt"] or now) if read else None


def _done(item, done: bool, now: str):
    item["doneAt"] = (item["doneAt"] or now) if done else None
    if done:
        _read(item, True, now)


def set_status(root: str, notice_id: str, field: Literal["read", "done", "snooze"], value: bool | str | None):
    def apply(items, now):
        item = items.get(notice_id)
        if item is None:
            raise NoticeMissing("没有这条通知")
        if field == "read":
            _read(item, bool(value), _format_date(now))
        elif field == "done":
            _done(item, bool(value), _format_date(now))
        else:
            item["snoozedUntil"] = value
        return item
    return _change(root, apply)


def clear(root: str, filter_name: NoticeFilter, *, done: bool):
    def apply(items, now):
        stamp = _format_date(now)
        changed = []
        for item in _select(items.values(), filter_name, now):
            if done and not item["waiting"] and item["doneAt"] is None:
                _done(item, True, stamp)
            elif not done and item["readAt"] is None:
                _read(item, True, stamp)
            else:
                continue
            changed.append(item["id"])
        return {"ids": changed}
    return _change(root, apply)
