"""新版 Desktop 的搜索：团队频道里的消息和任务看板的卡片，各按调用人能看到的范围。

按子串找（不分 ASCII 大小写），不做相关度排序：「最相关」和「最新」都按新的在前，``score`` 一律 1。
现在找不到的（如实不返回，不猜）：
- 主要聊天和与 agent 的私聊：记录在会话存储与任务台账里，这一版没有接
- Wiki、文件、记忆、agent 做过的事
- 语音消息（没有文字）；「提到了谁」「卡片」「文件」筛选：频道记录里没有这些标记，带上就不返回消息
- 「来自 agent」：团队频道里只有人发的消息
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from src.web import app_board

KINDS = ("message", "issue")
ALL_KINDS = frozenset({"message", "context_item", "issue", "file", "wiki", "action"})
HAS = frozenset({"link", "file", "image", "card"})
SORTS = frozenset({"relevance", "newest", "oldest"})
MAX_LIMIT = 50
DEFAULT_LIMIT = 20
MAX_QUERY = 200
_SNIPPET = 160
_LINK = re.compile(r"https?://", re.IGNORECASE)


class SearchQueryError(ValueError):
    pass


def _at(value: str) -> Optional[datetime]:
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return moment if moment.tzinfo is not None else None


def _list(value: str) -> List[str]:
    return [part for part in (value or "").split(",") if part]


def snippet(text: str, needle: str) -> str:
    """命中处前后各留一段；整段不长就原样给。"""
    flat = " ".join(text.split())
    if len(flat) <= _SNIPPET:
        return flat
    at = max(flat.casefold().find(needle.casefold()), 0)
    start = max(0, min(at - _SNIPPET // 3, len(flat) - _SNIPPET))
    return ("…" if start else "") + flat[start:start + _SNIPPET] + ("…" if start + _SNIPPET < len(flat) else "")


def _message_hits(service, actor, needle: str, filters: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    if filters["author"] == "agent" or filters["fromAgents"] or filters["mentionsUsers"] \
            or filters["mentionsAgents"] or set(filters["has"]) & {"file", "card"}:
        return []
    found = service.search(actor, needle, channel_ids=set(filters["in"]) or None, after=filters["after"],
                           before=filters["before"], oldest=filters["sort"] == "oldest", limit=limit * 4)
    hits = []
    for item in found:
        message = item["message"]
        media = (message.get("meta") or {}).get("media")
        if filters["fromUsers"] and message["author"]["id"] not in filters["fromUsers"]:
            continue
        if filters["pinned"] and not message.get("pinned"):
            continue
        if "link" in filters["has"] and not _LINK.search(message["body"]):
            continue
        if "image" in filters["has"] and not media:
            continue
        hit = {"kind": "message", "sourceId": message["id"], "channelIds": [message["channelId"]],
               "title": item["channelName"], "snippet": snippet(message["body"], needle), "score": 1,
               "createdAt": message["createdAt"],
               "author": {"type": "person", "id": message["author"]["id"], "name": item["authorName"]},
               "messageId": message["id"], "threadId": message["threadId"], "threadTitle": item["threadTitle"]}
        if media:
            hit["mediaKind"] = "gif"
        hits.append(hit)
        if len(hits) >= limit:
            break
    return hits


def _issue_hits(root: str, actor, needle: str, filters: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    # 卡片不在频道里，也不是谁「发」的：带了频道、发送人、提到、附件、置顶这些消息筛选就不返回卡片
    if any(filters[key] for key in ("in", "fromUsers", "fromAgents", "mentionsUsers", "mentionsAgents", "has",
                                    "pinned", "author")):
        return []
    rows = []
    for issue in app_board.listing(root, actor, q=needle):
        created = issue.get("createdAt") or ""
        moment = _at(created)
        if (filters["after"] or filters["before"]) and moment is None:
            continue
        if (filters["after"] and moment < _at(filters["after"])) or \
                (filters["before"] and moment >= _at(filters["before"])):
            continue
        text = issue["title"] if needle.casefold() in issue["title"].casefold() else issue["description"]
        rows.append({"kind": "issue", "sourceId": issue["id"], "channelIds": [],
                     "title": f"#{issue['number']} {issue['title']}", "snippet": snippet(text, needle), "score": 1,
                     "createdAt": created})
    rows.sort(key=lambda row: row["createdAt"], reverse=filters["sort"] != "oldest")
    return rows[:limit]


def parse(*, q: str, kinds: str = "", limit: Optional[int] = None, **raw: str) -> Dict[str, Any]:
    needle = (q or "").strip()
    if not needle:
        raise SearchQueryError("要搜的文字不能为空")
    if len(needle) > MAX_QUERY:
        raise SearchQueryError(f"要搜的文字最多 {MAX_QUERY} 个字")
    wanted = _list(kinds)
    if not set(wanted) <= ALL_KINDS:
        raise SearchQueryError("kinds 里有不认识的种类")
    if limit is not None and not 1 <= limit <= MAX_LIMIT:
        raise SearchQueryError(f"limit 要在 1 到 {MAX_LIMIT} 之间")
    filters = {key: _list(raw.get(key, "")) for key in ("in", "fromUsers", "fromAgents", "mentionsUsers",
                                                         "mentionsAgents", "has")}
    if not set(filters["has"]) <= HAS:
        raise SearchQueryError("has 只能是 link、file、image、card")
    author = raw.get("author") or ""
    if author not in {"", "person", "agent"}:
        raise SearchQueryError("author 只能是 person 或 agent")
    sort = raw.get("sort") or "relevance"
    if sort not in SORTS:
        raise SearchQueryError("sort 只能是 relevance、newest 或 oldest")
    for key in ("after", "before"):
        if raw.get(key) and _at(raw[key]) is None:
            raise SearchQueryError(f"{key} 要是带时区的 ISO 时间")
    filters.update(author=author, sort=sort, pinned=raw.get("pinned") == "true",
                   after=raw.get("after") or "", before=raw.get("before") or "")
    return {"needle": needle, "kinds": [kind for kind in KINDS if not wanted or kind in wanted],
            "limit": limit or DEFAULT_LIMIT, "filters": filters}


def query(root: str, service, actor, parsed: Dict[str, Any]) -> List[Dict[str, Any]]:
    needle, limit, filters = parsed["needle"], parsed["limit"], parsed["filters"]
    groups: Iterable[List[Dict[str, Any]]] = [
        _message_hits(service, actor, needle, filters, limit) if kind == "message"
        else _issue_hits(root, actor, needle, filters, limit) for kind in parsed["kinds"]]
    hits = [hit for group in groups for hit in group]
    hits.sort(key=lambda hit: hit["createdAt"], reverse=filters["sort"] != "oldest")
    return hits[:limit]
