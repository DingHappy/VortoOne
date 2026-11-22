"""成员的登录会话在网页这一层怎么算数。

主人令牌过了鉴权就能用全部接口——那些接口都是按「调用的人是所有者」写的。成员的会话因此**不能**
走同一条路，只认两张表：

- ``MEMBER_ROUTES``：进处理函数。往这里加一条之前，那个接口必须已经会分辨是谁在调（用
  ``principal``）并按这个人的权限做事，或者它的回答里本来就没有所有者的数据。
- ``empty_answer``（表 ``_EMPTY_ANSWERS``）：对成员一律回一个固定的空结构，**不进处理函数**。所有者的记忆、例行、技能、
  制品、通知、用量、审批这些归这里——界面拿到合法的空列表，所有者的数据不可能从这条路漏出去。

两张表之外一律 403。

- 成员会话用自己的 Cookie（``MEMBER_COOKIE``），与主人令牌的 Cookie 不同名；这里的任何凭据都
  不会让 ``auth._token_ok`` 通过。
- 同一个请求既带了主人令牌又带了成员 Cookie 时，算所有者（中间件先认主人令牌）。
- 成员会话靠 Cookie，所以写操作必须从本服务自己的页面发出（来源检查）。
- 没配置主人令牌的服务不认成员会话：那种服务上人人都是所有者，分不清谁是谁。
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional, Tuple

from src.agents.principal import MEMBER, OWNER, Principal
from src.workflow.members.channels import CHANNEL_CATEGORY_ID_PATTERN, TEAM_CHANNEL_ID_PATTERN

MEMBER_COOKIE = "vortoone_member"
_MAX_AGE = 30 * 24 * 3600
_ORG = r"/api/app/organizations/[^/]+"
_TEAM_CHANNEL = _ORG + r"/channels/" + TEAM_CHANNEL_ID_PATTERN
_CATEGORIES = _ORG + r"/channel-categories"
_CATEGORY = _CATEGORIES + r"/" + CHANNEL_CATEGORY_ID_PATTERN

# （方法，路径）——成员会话算数的全部接口。每一条的处理函数都用 principal() 分辨调用人。
MEMBER_ROUTES: Tuple[Tuple[str, "re.Pattern[str]"], ...] = tuple((method, re.compile(pattern + r"\Z")) for method, pattern in (
    ("GET", r"/api/app/me"),
    ("PATCH", r"/api/app/me"),                            # 只改调用人自己的界面语言
    ("PUT", r"/api/app/me/work-area"),                    # 本人引导里的可选填写项
    ("PATCH", _ORG + r"/me"),                             # 只改调用人在这个空间的个人时区
    # 侧栏怎么摆：每个人各存各的（web/app_prefs.py），不涉及别人的数据
    ("GET", _ORG + r"/me/sidebar(?:/order|/tools|/view)?"),
    ("PUT", _ORG + r"/me/sidebar(?:/order|/tools|/view)?"),
    ("PATCH", _ORG + r"/me/sidebar"),
    ("PUT", _ORG + r"/me/status"),
    ("GET", _ORG + r"/me/issues-view"),
    ("PUT", _ORG + r"/me/issues-view"),
    ("GET", _ORG + r"/wiki/(?:graph|pages(?:/[^/]+(?:/history|/versions/[^/]+)?)?)"),
    ("POST", _ORG + r"/wiki/import"),
    ("POST", _ORG + r"/wiki/pages"),
    ("PUT", _ORG + r"/wiki/pages/[^/]+"),
    ("DELETE", _ORG + r"/wiki/pages/[^/]+"),
    ("POST", _ORG + r"/wiki/pages/[^/]+/(?:append|rename|move|lock|pin|context)"),
    ("POST", _ORG + r"/wiki/(?:bulk/delete|trash/restore)"),
    # 任务看板：处理函数按可见范围和「编辑资料」「派活」判断（web/app_board.py）
    ("GET", _ORG + r"/search"),                           # 频道按本人已加入的、卡片按本人可见的


    ("POST", _ORG + r"/questions/[^/]+/(answer|dismiss)"),  # 只认被问的那个人


    ("GET", _ORG + r"/team-memories"),                    # 所有 agent 都读的那一份
    ("PATCH", _ORG + r"/memories/[^/]+"),                 # 只有团队记忆，处理函数里按权限核对


    ("GET", _ORG + r"/approvals"),                        # 只在替所有者代批时有内容
    ("GET", _ORG + r"/approvals/[^/]+"),
    ("POST", _ORG + r"/approvals/[^/]+/(approve|refuse)"),
    ("POST", _ORG + r"/approvals/decide"),
    ("GET", _ORG + r"/me/approval-cover"),
    ("PUT", _ORG + r"/me/approval-cover"),                # 成员这边如实拒绝
    ("DELETE", _ORG + r"/approval-covers/[^/]+"),
    ("GET", _ORG + r"/issues"),
    ("POST", _ORG + r"/issues"),
    ("GET", _ORG + r"/issues/by-number/[^/]+"),
    ("PATCH", _ORG + r"/issues/[^/]+"),
    ("DELETE", _ORG + r"/issues/[^/]+"),
    ("POST", _ORG + r"/issues/[^/]+/(?:share|comments|ask-agent)"),
    ("PUT", _ORG + r"/issues/[^/]+/(?:audience|criteria/[^/]+)"),
    ("GET", _ORG + r"/issues/[^/]+/activity"),
    ("GET", _ORG + r"/issue-(?:columns|projects)"),
    ("POST", _ORG + r"/issue-(?:columns|projects)"),
    ("PATCH", _ORG + r"/issue-(?:columns|projects)/[^/]+"),
    ("DELETE", _ORG + r"/issue-(?:columns|projects)/[^/]+"),
    # GIF：搜索和取图（团队频道里发 GIF 用）；密钥只有所有者能管
    ("GET", _ORG + r"/gifs"),
    ("GET", _ORG + r"/gifs/[A-Za-z0-9]+/media"),
    # 语音消息：问能不能发、听录音（看得到那个对话才行）、在团队频道里发；服务商和 Key 只有所有者能管
    ("GET", _ORG + r"/voice/allowed"),
    ("GET", _ORG + r"/voice/[A-Za-z0-9_-]+/audio"),
    ("POST", _TEAM_CHANNEL + r"/voice"),
    ("POST", r"/api/app/device-accounts/sign-out"),       # 只作废调用人自己的会话
    ("POST", r"/api/app/phone-sign-in"),                  # 只给调用人自己开一次性登录
    ("GET", _ORG + r"/roles"),
    ("GET", _ORG + r"/vault(?:/items/[^/]+)?"),
    ("POST", _ORG + r"/vault/items"),
    ("PATCH", _ORG + r"/vault/items/[^/]+"),
    ("DELETE", _ORG + r"/vault/items/[^/]+"),
    ("GET", _ORG + r"/routines/proposals"),
    ("POST", _ORG + r"/routines/proposals"),
    ("GET", _ORG + r"/members"),
    ("GET", _ORG + r"/invitations"),
    ("POST", _ORG + r"/invitations"),
    ("DELETE", _ORG + r"/invitations/[^/]+"),
    ("GET", _ORG + r"/invitations/all"),
    ("POST", _ORG + r"/invitations/batch"),
    ("PATCH", _ORG + r"/invitations/[^/]+"),
    ("POST", _ORG + r"/members/[^/]+/password-link"),
    # 成员和 agent：只列他用得了的 agent，私聊里只有他自己发起的事（web/app_members.py）
    ("GET", _ORG + r"/agents"),
    ("GET", _ORG + r"/team"),
    ("GET", _ORG + r"/agents/[^/]+"),
    ("GET", _ORG + r"/agents/[^/]+/(?:browser(?:/handoff)?|desktop)"),
    ("GET", _ORG + r"/agents/[^/]+/member-action-levels"),
    ("PUT", _ORG + r"/agents/[^/]+/member-action-levels/[^/]+"),
    ("GET", _ORG + r"/agents/[^/]+/member-policy"),
    ("PUT", _ORG + r"/agents/[^/]+/member-policy"),
    ("GET", _ORG + r"/agent-status"),
    ("GET", _ORG + r"/ai-status"),
    ("GET", _ORG + r"/dms"),
    ("POST", _ORG + r"/dms"),
    ("GET", _ORG + r"/channels/[^/]+/members"),
    ("GET", _ORG + r"/channels/[^/]+/messages"),
    ("POST", _ORG + r"/channels/[^/]+/messages"),
    ("GET", _ORG + r"/channels/[^/]+/active-runs"),
    ("GET", _ORG + r"/channels/[^/]+/pins"),
    ("GET", _ORG + r"/channels/[^/]+/threads"),
    ("GET", _ORG + r"/channels"),
    ("POST", _ORG + r"/channels"),
    ("GET", _ORG + r"/archived-channels"),
    ("GET", _CATEGORIES),
    ("POST", _CATEGORIES),
    ("PATCH", _CATEGORY),
    ("DELETE", _CATEGORY),
    ("PUT", _CATEGORY + r"/place"),
    ("PUT", _TEAM_CHANNEL + r"/place"),
    ("PATCH", _TEAM_CHANNEL),
    ("DELETE", _TEAM_CHANNEL),
    ("POST", _TEAM_CHANNEL + r"/(?:join|leave|read|unarchive|members)"),
    ("PUT", _TEAM_CHANNEL + r"/mute"),
    ("DELETE", _TEAM_CHANNEL + r"/mute"),
    ("DELETE", _TEAM_CHANNEL + r"/members/people/[^/]+"),
    ("GET", _TEAM_CHANNEL + r"/(?:card|roster|permissions)"),
    ("PUT", _TEAM_CHANNEL + r"/permissions"),
    ("POST", _TEAM_CHANNEL + r"/pins/[^/]+"),
    ("DELETE", _TEAM_CHANNEL + r"/pins/[^/]+"),
    ("GET", _ORG + r"/messages/[^/]+/card"),
    ("GET", _ORG + r"/messages/[^/]+/reactions"),
    ("POST", _ORG + r"/messages/[^/]+/reactions"),
    ("DELETE", _ORG + r"/messages/[^/]+/reactions"),
    ("GET", _ORG + r"/threads"),
    ("GET", _ORG + r"/threads/[^/]+"),
    ("PATCH", _ORG + r"/threads/[^/]+"),
    ("POST", _ORG + r"/threads/[^/]+/(?:close|reopen|archive|unarchive)"),
    ("POST", _ORG + r"/threads/[^/]+/read"),
    ("GET", _ORG + r"/unread-threads"),
    ("GET", _ORG + r"/overview"),
    ("GET", _ORG + r"/people"),
    # AI 应用：只列、只断开他自己的连接和个人令牌（管成员的人能看全部）；授权确认页按他本人答复
    ("GET", _ORG + r"/apps"),
    ("DELETE", _ORG + r"/apps/[^/]+"),
    ("POST", _ORG + r"/tokens"),
    ("DELETE", _ORG + r"/tokens/[^/]+"),
    ("GET", r"/api/app/authorize/[^/]+"),
    ("POST", r"/api/app/authorize/[^/]+"),
    # 回答里没有所有者的数据：空间名和时区、首页的默认版面、电脑接入引导
    ("GET", _ORG + r"/settings"),
    ("GET", _ORG + r"/dashboards/home"),
    ("GET", _ORG + r"/computer-setup"),
    # 电脑只在配对人的会话里可见。传输和本机自动配对仍使用各自的验权入口。
    ("GET", _ORG + r"/devices"),
    ("PATCH", _ORG + r"/devices/computers/[^/]+"),
    ("DELETE", _ORG + r"/devices/computers/[^/]+"),
    ("GET", _ORG + r"/machines"),
    ("PATCH", _ORG + r"/machines/[^/]+"),
    ("DELETE", _ORG + r"/machines/[^/]+"),
    ("POST", _ORG + r"/computer-pairings"),
    ("DELETE", _ORG + r"/computer-pairings/[^/]+"),
))

# 路径（GET）→ 成员看到的固定回答。
_EMPTY_ANSWERS: Tuple[Tuple[str, Any], ...] = (
    (r"/api/app/models", {"models": [], "defaultModel": ""}),
    (r"/api/app/connectors/catalog", {"connectors": [], "builtinScopes": []}),
    (_ORG + r"/memories", {"memories": []}),
    (_ORG + r"/agents/[^/]+/memories", {"memories": []}),
    (_ORG + r"/agents/[^/]+/skills", {"skills": []}),
    (_ORG + r"/skills", {"skills": []}),
    (_ORG + r"/routines", {"routines": []}),
    (_ORG + r"/notifications", {"items": [], "counts": {"all": 0, "mentions": 0, "waiting": 0, "unread": 0,
                                                       "reactions": 0}, "nextCursor": None}),
    (_ORG + r"/notifications/count", {"unread": 0, "waiting": 0}),
    (_ORG + r"/runs", {"runs": []}),
    (_ORG + r"/active-runs", {"runs": []}),
    (_ORG + r"/usage", {"usage": []}),
    (_ORG + r"/artifacts", {"artifacts": [], "nextCursor": None, "counts": {}}),
    (_ORG + r"/former-agents", {"agents": []}),          # 不在团队里的 agent 是所有者的事
)


def _empty_table() -> Tuple[Tuple["re.Pattern[str]", Any], ...]:
    """上面那张表，加上本来就回空的启动列表（routers/app_api.py 的 _EMPTY）。"""
    from src.web.routers.app_api import _EMPTY
    rows = list(_EMPTY_ANSWERS) + [(_ORG + re.escape(path), payload) for path, payload in _EMPTY.items()]
    return tuple((re.compile(pattern + r"\Z"), payload) for pattern, payload in rows)


_EMPTY_CACHE: list = []


def empty_answer(method: str, path: str) -> Optional[Any]:
    """这个请求对成员该回的固定空结构；不在表里返回 None。"""
    if method != "GET":
        return None
    if not _EMPTY_CACHE:
        _EMPTY_CACHE.extend(_empty_table())
    import json
    return next((json.loads(json.dumps(payload)) for pattern, payload in _EMPTY_CACHE if pattern.match(path)), None)


def same_origin(request) -> bool:
    from src.web.app_staff import _same_origin
    return _same_origin(request)


def account(request) -> Optional[Dict[str, Any]]:
    """这个请求带的成员会话对应的账号；没有、过期、服务没配主人令牌都返回 None。"""
    from src.web.auth import get_api_token
    from src.workflow.members.accounts import MemberAccounts
    token = request.cookies.get(MEMBER_COOKIE, "")
    if not token or not get_api_token():
        return None
    return MemberAccounts(os.getcwd()).session(token)


def member_id(found: Optional[Dict[str, Any]]) -> str:
    """账号现在是这个空间的哪个成员。登记处里已经没有他（被移除了）就不算成员。"""
    from src.workflow.members.registry import MemberRegistry
    if not found or not found.get("member"):
        return ""
    try:
        record = MemberRegistry(os.getcwd()).snapshot()["members"].get(found["member"])
    except (ValueError, OSError):
        return ""                                      # 登记读不出来：不当成成员
    return found["member"] if record is not None and record.get("account") == found["id"] else ""


def admit(request):
    """主人令牌没通过的请求，凭成员会话能不能进。返回 0 表示放行进处理函数；401/403 是该回的状态码；
    返回一个 dict 时直接把它作为回答（``empty_answer``），不进处理函数。

    放行时把账号记在 ``request.state.member_account`` 上——只有这里会写它，处理函数据此知道
    调用人不是所有者。"""
    found = account(request)
    if found is None:
        return 401
    path, method = request.url.path, request.method
    if not any(method == allowed and pattern.match(path) for allowed, pattern in MEMBER_ROUTES):
        empty = empty_answer(method, path)
        return empty if empty is not None else 403
    if not same_origin(request):
        return 403
    request.state.member_account = found
    return 0


def principal(request, root: str) -> Principal:
    """这次请求是谁发起的。

    过了主人令牌的是所有者。凭成员会话进来的（``admit`` 记下的）是那个成员；账号还没加入空间、
    或已被移除的，得到一个登记处不认识的成员——任何需要权限的事他都做不了。"""
    from src.web import app_views as views
    found = getattr(request.state, "member_account", None)
    if found is None:
        return Principal(OWNER, views.owner_id(root), root)
    return Principal(MEMBER, member_id(found) or f"account:{found['id']}", root)


def set_cookie(request, response, token: str) -> None:
    response.set_cookie(MEMBER_COOKIE, token, httponly=True, samesite="lax",
                        secure=(request.url.scheme == "https"), path="/", max_age=_MAX_AGE)


def clear_cookie(response) -> None:
    response.delete_cookie(MEMBER_COOKIE, path="/")
