"""登录/登出/鉴权状态端点——让 token 走 httpOnly Cookie，彻底移出 URL（审计 P0#4）。

流程：前端 GET /api/auth/status 得知"需鉴权且未登录" → 弹登录框 → POST /api/auth/login {token}
→ 校验通过种 httpOnly Cookie → 之后同源请求（含 WS 握手、制品 iframe 子资源）自动带 Cookie，
token 不再出现在任何 URL / access log / referer / 分享链接里。
"""

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from src.web.auth import SESSION_COOKIE, ct_eq, get_api_token, is_authed

router = APIRouter(prefix="/api/auth", tags=["auth"])

_MAX_AGE = 30 * 24 * 3600   # 30 天


class LoginBody(BaseModel):
    token: str = ""


def set_owner_cookie(request: Request, response: Response, token: str) -> None:
    """A successful owner sign-in replaces the browser's active account."""
    from src.web.member_auth import clear_cookie

    response.set_cookie(
        SESSION_COOKIE, token,
        httponly=True,                                  # JS 读不到 → allow-scripts 制品也偷不走
        samesite="lax",                                 # 同源子资源/WS 照常带；顶层导航也可用
        secure=(request.url.scheme == "https"),
        path="/", max_age=_MAX_AGE,
    )
    clear_cookie(response)


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response):
    expected = get_api_token()
    if not expected:
        return {"ok": True, "auth_required": False}     # 未配置 token：本地开发无需登录
    # 常时比较。**这里是全站唯一免鉴权、可以随便打的 token 校验点**——中间件放行它，
    # 否则没法登录。auth.py 的 ct_eq 写着"仅当本机对外暴露时侧信道才有网络路径，故收紧"，
    # 而 97 正是 0.0.0.0 暴露的；那边收紧了，这条登录路径当时用的还是 !=。
    if not ct_eq((body.token or "").strip(), expected):
        return JSONResponse({"ok": False, "detail": "token 不正确"}, status_code=401)
    set_owner_cookie(request, response, expected)
    return {"ok": True}


@router.post("/logout")
async def logout(request: Request, response: Response):
    expected = get_api_token()
    if expected and ct_eq(request.cookies.get(SESSION_COOKIE, ""), expected):
        from src.gateway.computer_browser_preview import browser_previews
        browser_previews.end_owner_views()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/status")
async def status(request: Request):
    return {"auth_required": bool(get_api_token()), "authed": is_authed(request)}
