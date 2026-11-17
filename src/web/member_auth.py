"""成员的登录会话在网页这一层怎么算数。

主人令牌过了鉴权就能用全部接口——那些接口都是按「调用的人是所有者」写的。成员的会话因此**不能**
走同一条路：它只在下面这张白名单里的接口上算数，白名单外一律 403。往白名单里加一条之前，那个接口
必须已经会分辨是谁在调（用 ``principal``），并按这个人的权限做事。

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

MEMBER_COOKIE = "vortoone_member"
_MAX_AGE = 30 * 24 * 3600
_ORG = r"/api/app/organizations/[^/]+"

# （方法，路径）——成员会话算数的全部接口。每一条的处理函数都用 principal() 分辨调用人。
MEMBER_ROUTES: Tuple[Tuple[str, "re.Pattern[str]"], ...] = tuple((method, re.compile(pattern + r"\Z")) for method, pattern in (
    ("GET", r"/api/app/me"),
    ("GET", _ORG + r"/roles"),
    ("GET", _ORG + r"/members"),
    ("GET", _ORG + r"/invitations"),
    ("POST", _ORG + r"/invitations"),
    ("DELETE", _ORG + r"/invitations/[^/]+"),
    ("POST", _ORG + r"/members/[^/]+/password-link"),
))


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


def admit(request) -> int:
    """主人令牌没通过的请求，凭成员会话能不能进。返回 0 表示放行，否则是该回的状态码。

    放行时把账号记在 ``request.state.member_account`` 上——只有这里会写它，处理函数据此知道
    调用人不是所有者。"""
    found = account(request)
    if found is None:
        return 401
    path, method = request.url.path, request.method
    if not any(method == allowed and pattern.match(path) for allowed, pattern in MEMBER_ROUTES):
        return 403
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
