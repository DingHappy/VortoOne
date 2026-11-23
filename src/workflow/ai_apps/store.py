"""AI 应用接入的存取：登记的应用、等人确认的授权请求、授权码、连接（grant）和令牌。

- 一份 JSON（``.vortoone/ai_apps/state.json``），文件锁加进程内锁；所有改动在锁里读-改-写。
- 授权码和令牌只存 sha256 摘要：文件泄露也换不回能用的凭据（随机 32 字节，摘要足够）。
- **一个连接属于一个人**（所有者或某位成员）和一个应用：同一个应用在两台电脑上连，共用这个连接；
  断开就全部失效。个人令牌也是一个连接（``kind = token``），只有一枚令牌。
- 授权码 120 秒内有效、只能换一次；同一个码再来一次，说明被人截了，它换出的令牌全部作废。
- 续期令牌每用一次就换新的（90 天，滑动）。用过的旧续期令牌 60 秒内又来、且它换出的新令牌还没用过，
  当作应用的重试，再发一对；除此之外旧令牌再出现就把这一整串作废。
- 谁能看、能断开哪个连接，由调用方（web 层）按权限决定后传进来；本模块只认「这个连接是谁的」。
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple
from urllib.parse import urlencode, urlsplit

from src.utils.state_dir import state_dir
from src.workflow.ai_apps import clients as C

SCOPES = ("read", "write", "manage")
# 第一阶段发现文档只公布这两档：「创建和管理」的工具还没有
ADVERTISED_SCOPES = ("read", "write")
REQUEST_SECONDS = 30 * 60
CODE_SECONDS = 120
REFRESH_SECONDS = 90 * 86400
REFRESH_GRACE = 60
TOKENS_PER_PERSON = 10
TOKEN_NAME_CHARS = 60
STATE_CHARS = 4096
MAX_CLIENTS = 2000
MAX_OPEN_REQUESTS = 500
TOKEN_DAYS = (30, 90, 365)
_TOUCH_EVERY = 60
# 按地址登记的应用：说明文件存 5 分钟；再取失败时，一天内的旧副本照用；取失败的 20 秒内不再试（只在进程内）。
# 同时最多取 3 份，其余等最多 3 秒，还轮不到就当这次「忙」。
DOCUMENT_FRESH, DOCUMENT_STALE, DOCUMENT_RETRY = 300, 86400, 20
_DOCUMENT_FAILURES: Dict[str, float] = {}
_DOCUMENT_SLOTS = threading.BoundedSemaphore(3)
_FILE = "state.json"
_MAX_BYTES = 8 * 1024 * 1024
_LOCK = threading.RLock()


class _Kept(Exception):
    """改动要保存，但这次请求仍然失败：带着要抛给调用方的错误。"""

    def __init__(self, error: Exception):
        super().__init__(str(error))
        self.error = error


class Damaged(ValueError):
    """状态文件读不出来：停下，不当成「什么都没有」。"""


class OAuthError(Exception):
    """令牌端点的错误：status 是 HTTP 状态码，error 是 OAuth 错误码。"""

    def __init__(self, status: int, error: str, description: str = ""):
        super().__init__(description or error)
        self.status, self.error, self.description = status, error, description


class ConsentError(Exception):
    """确认页拿不到或不能再答这个请求。code 对应界面的错误状态；status 是 HTTP 状态码。"""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


class StartError(Exception):
    """授权请求本身不成立。page 为界面的 ``?erro=`` 状态。"""

    def __init__(self, page: str):
        super().__init__(page)
        self.page = page


def digest(secret: str) -> str:
    return hashlib.sha256(str(secret or "").encode("utf-8", "surrogatepass")).hexdigest()


def _secret(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(32)


def scopes_for(access: str) -> List[str]:
    """一档权限带上它下面的几档：write 带 read，manage 带 write 和 read。"""
    if access not in SCOPES:
        raise ValueError("权限只能是 read、write 或 manage")
    return list(SCOPES[:SCOPES.index(access) + 1])


def access_of(scopes: List[str]) -> str:
    return next((s for s in reversed(SCOPES) if s in scopes), "read")


def parse_scopes(raw: Any) -> List[str]:
    """请求里的 scope（空格分隔）：不认识的丢掉，都没有就是公布的全部；取最宽的一档往下带。"""
    asked = set(raw.split()) if isinstance(raw, str) else set()
    known = [s for s in ADVERTISED_SCOPES if s in asked]
    return scopes_for(access_of(known)) if known else list(ADVERTISED_SCOPES)


def normalize_resource(raw: str) -> Optional[str]:
    """RFC 8707 的 resource，按应用的各种写法比较：主机小写、去掉默认端口、去掉一个结尾斜杠。"""
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return None
    if not parts.scheme or not parts.hostname or parts.query or parts.fragment:
        return None
    default = {"https": 443, "http": 80}.get(parts.scheme)
    shown = f":{port}" if port and port != default else ""
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    return f"{parts.scheme}://{host}{shown}{parts.path.rstrip('/') if parts.path != '/' else ''}"


def pkce_ok(verifier: Any, challenge: str) -> bool:
    if not isinstance(verifier, str) or not 43 <= len(verifier) <= 128:
        return False
    computed = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii", "ignore")).digest())
    return secrets.compare_digest(computed.decode().rstrip("="), str(challenge))


def _iso(moment: Optional[float]) -> Optional[str]:
    if moment is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(moment))


def fetch_document(url: str) -> bytes:
    """取应用的说明文件：只走 https、目的地必须是公网地址（防内网访问）、不跟重定向、3 秒、64 KB。"""
    from src.agents.web_fetch import fetch_bytes
    # 带请求头时 fetch_bytes 不跟重定向：说明文件必须就在 client_id 那个地址上
    return fetch_bytes(url, max_bytes=C.DOCUMENT_BYTES, timeout=3, headers={"Accept": "application/json"})


def reset_document_failures() -> None:
    """测试用。"""
    _DOCUMENT_FAILURES.clear()


class AiApps:
    def __init__(self, root: str, *, clock: Callable[[], float] = time.time,
                 fetch: Optional[Callable[[str], bytes]] = None):
        self.root = str(root)
        self.directory = state_dir(root) / "ai_apps"
        self.clock = clock
        self.fetch = fetch

    # ── 存取 ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _fresh() -> Dict[str, Any]:
        return {"version": 1, "clients": {}, "requests": {}, "codes": {}, "grants": {}, "tokens": {}}

    def _load(self) -> Dict[str, Any]:
        path = self.directory / _FILE
        if self.directory.is_symlink():
            raise Damaged("AI 应用目录不能是符号链接")
        if not path.exists():
            return self._fresh()
        if path.is_symlink() or path.stat().st_size > _MAX_BYTES:
            raise Damaged("AI 应用状态文件无效或过大")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeError, ValueError) as error:
            raise Damaged("AI 应用状态文件损坏") from error
        if not isinstance(value, dict) or value.get("version") != 1 \
                or any(not isinstance(value.get(key), dict) for key in self._fresh() if key != "version"):
            raise Damaged("AI 应用状态文件结构无效")
        return value

    def _save(self, state: Dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / _FILE
        if path.is_symlink():
            raise Damaged("AI 应用状态文件不能是符号链接")
        encoded = json.dumps(state, ensure_ascii=False, indent=1) + "\n"
        if len(encoded.encode("utf-8")) > _MAX_BYTES:
            raise ValueError("AI 应用状态文件过大，未写入")
        temporary = self.directory / f".{_FILE}.{uuid.uuid4().hex}.tmp"
        try:
            temporary.write_text(encoded, encoding="utf-8")
            temporary.chmod(0o600)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def _change(self) -> Iterator[Dict[str, Any]]:
        """锁里读-改-写。块里抛 ``_Kept`` 时照样保存（例如作废了被盗用的令牌，再拒绝这次请求），
        然后把它带的错误抛出去；其它异常不保存。"""
        with _LOCK:
            handle = None
            try:
                import fcntl
                self.directory.mkdir(parents=True, exist_ok=True)
                handle = open(self.directory / ".state.lock", "a+")   # noqa: SIM115 —— 锁要持有到写完
                fcntl.flock(handle, fcntl.LOCK_EX)
            except (ImportError, OSError):
                handle = None
            try:
                state = self._load()
                self._prune(state)
                try:
                    yield state
                except _Kept as kept:
                    self._save(state)
                    raise kept.error from None
                self._save(state)
            finally:
                if handle is not None:
                    handle.close()

    def _read(self) -> Dict[str, Any]:
        with _LOCK:
            return self._load()

    def _prune(self, state: Dict[str, Any]) -> None:
        """过期一天以上的请求、过期的授权码、过期或作废 30 天以上的令牌，顺手清掉。"""
        now = self.clock()
        for key in [k for k, r in state["requests"].items() if r["expires"] + 86400 < now]:
            del state["requests"][key]
        for key in [k for k, c in state["codes"].items() if c["expires"] + 600 < now]:
            del state["codes"][key]
        for key in [k for k, t in state["tokens"].items()
                    if (t.get("revoked") or t["expires"] or now) + 30 * 86400 < now]:
            del state["tokens"][key]

    # ── 登记（动态注册） ──────────────────────────────────────────────────────

    def register(self, body: Any) -> Dict[str, Any]:
        checked = C.check_registration(body)
        now = self.clock()
        with self._change() as state:
            if len(state["clients"]) >= MAX_CLIENTS:
                raise C.InvalidClient("Too many registered apps on this server")
            client_id = _secret("vcl_")[:28]
            state["clients"][client_id] = {**checked, "created": now, "last_used": now}
        return {"client_id": client_id, "client_id_issued_at": int(now), "client_name": checked["name"],
                "redirect_uris": checked["redirect_uris"], "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]}

    # ── 按地址登记的应用 ──────────────────────────────────────────────────────

    def _refresh_document(self, client_id: str) -> None:
        """client_id 是说明文件的地址时，必要的话去取一份、核对后存下。在锁外取：取一次可能要几秒。"""
        url = C.cimd_url(client_id)
        if url is None:
            return
        now = self.clock()
        known = self._read()["clients"].get(client_id)
        if known is not None and known.get("kind") == "cimd" and now - known.get("fetched", 0) < DOCUMENT_FRESH:
            return
        failed = _DOCUMENT_FAILURES.get(client_id)
        if failed is not None and now - failed < DOCUMENT_RETRY:
            return
        if not _DOCUMENT_SLOTS.acquire(timeout=3):
            return                                      # 忙：有一天内的旧副本就用旧的
        try:
            document = C.parse_document(client_id, (self.fetch or fetch_document)(url))
        except Exception:  # noqa: BLE001 —— 取不到、不是 JSON、内容不对：都当这次没取到
            if len(_DOCUMENT_FAILURES) > 10000:
                _DOCUMENT_FAILURES.clear()
            _DOCUMENT_FAILURES[client_id] = now
            return
        finally:
            _DOCUMENT_SLOTS.release()
        _DOCUMENT_FAILURES.pop(client_id, None)
        with self._change() as state:
            previous = state["clients"].get(client_id)
            if previous is None and len(state["clients"]) >= MAX_CLIENTS:
                return
            state["clients"][client_id] = {**document, "kind": "cimd", "fetched": now,
                                           "created": (previous or {}).get("created", now),
                                           "last_used": (previous or {}).get("last_used", now)}

    def _usable_client(self, state: Dict[str, Any], client_id: str) -> Optional[Dict[str, Any]]:
        client = state["clients"].get(client_id)
        if client is not None and client.get("kind") == "cimd" \
                and self.clock() - client.get("fetched", 0) >= DOCUMENT_STALE:
            return None                                 # 一天都没能重新取到：不再认
        return client

    # ── 授权请求与确认 ────────────────────────────────────────────────────────

    def start(self, query: Dict[str, Any], resource: str) -> str:
        """``/api/oauth/authorize`` 的参数 → 请求 id（确认页凭它取内容）。不成立抛 StartError。

        参数错误一律停在我们自己的错误页，不发回应用：任何人都能登记任意 https 回调地址，往回发
        错误等于让本服务替人跳转（RFC 9700 §4.11.2）。"""
        def text(key: str) -> str:
            value = query.get(key)
            return value if isinstance(value, str) else ""
        self._refresh_document(text("client_id"))
        now = self.clock()
        with self._change() as state:
            client = self._usable_client(state, text("client_id"))
            if client is None:
                raise StartError("app_desconhecido")
            redirect = text("redirect_uri")
            if not redirect and len(client["redirect_uris"]) == 1:
                redirect = client["redirect_uris"][0]
            if not C.redirect_matches(client["redirect_uris"], redirect):
                raise StartError("endereco_de_retorno")
            asked = text("resource")
            if (text("response_type") != "code" or not text("code_challenge")
                    or text("code_challenge_method") != "S256" or len(text("state")) > STATE_CHARS
                    or (asked and normalize_resource(asked) != normalize_resource(resource))):
                raise StartError("pedido_invalido")
            open_requests = [r for r in state["requests"].values() if r["expires"] > now and not r["answered"]]
            if len(open_requests) >= MAX_OPEN_REQUESTS:
                raise StartError("muitas_tentativas")
            request_id = secrets.token_urlsafe(24)
            state["requests"][request_id] = {
                "client": text("client_id"), "redirect_uri": redirect, "state": text("state"),
                "challenge": text("code_challenge"), "scopes": parse_scopes(query.get("scope")),
                "created": now, "expires": now + REQUEST_SECONDS, "answered": False}
            client["last_used"] = now
        return request_id

    def _open_request(self, state: Dict[str, Any], request_id: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        request = state["requests"].get(str(request_id or ""))
        if request is None:
            raise ConsentError(404, "expirado", "没有这个授权请求")
        if request["answered"]:
            raise ConsentError(409, "expirado", "这个授权请求已经答复过")
        if request["expires"] <= self.clock():
            raise ConsentError(410, "expirado", "这个授权请求已过期")
        client = state["clients"].get(request["client"])
        if client is None:
            raise ConsentError(404, "expirado", "应用的登记已不在")
        return request, client

    def consent(self, request_id: str) -> Dict[str, Any]:
        """确认页要显示的东西（不含账号和空间，由 web 层补）。"""
        with _LOCK:
            state = self._load()
            request, client = self._open_request(state, request_id)
        return {"id": request_id, "expiresAt": _iso(request["expires"]),
                "access": {"requested": list(request["scopes"])},
                "client": {"name": client["name"],
                           **C.display(request["redirect_uri"], client["name"], request["client"])}}

    def recent_local(self, seconds: int = 120) -> List[Dict[str, Any]]:
        """最近 seconds 秒里、还没答复的本机应用（回调回到这台电脑：本机回环或 Cursor）的授权请求，新的在前。

        给 Desktop 外壳用：它在自己已登录的窗口里打开确认页。只列回调回到本机的请求——授权码只会回到
        这台电脑，别人发起的请求弹到所有者面前，误点「允许」也拿不走码；https 回调的请求不列。"""
        now = self.clock()
        rows = []
        for request_id, request in self._read()["requests"].items():
            if request["answered"] or request["expires"] <= now or now - request["created"] > seconds:
                continue
            if C.display(request["redirect_uri"], "")["displayKind"] not in ("loopback", "app_scheme"):
                continue
            rows.append({"id": request_id, "createdAt": _iso(request["created"])})
        return sorted(rows, key=lambda row: row["createdAt"], reverse=True)

    def decide(self, request_id: str, person: str, *, allow: bool, access: str = "write",
               issuer: str = "") -> Tuple[str, Optional[str]]:
        """主人或成员在确认页上的答复 → (浏览器回到应用的地址, 连接 id 或 None)。一个请求只答一次。"""
        now = self.clock()
        with self._change() as state:
            request, client = self._open_request(state, request_id)
            request["answered"] = True
            reply: Dict[str, str] = {}
            grant_id = None
            if not allow:
                reply["error"] = "access_denied"
            else:
                if access not in request["scopes"]:
                    raise ConsentError(400, "pedido_invalido", "应用没有请求这档权限")
                scopes = scopes_for(access)
                grant_id = self._grant_for(state, person, request["client"], client, request["redirect_uri"],
                                           scopes, now)
                code = _secret("vcc_")
                state["codes"][digest(code)] = {
                    "client": request["client"], "redirect_uri": request["redirect_uri"],
                    "challenge": request["challenge"], "grant": grant_id, "scopes": scopes,
                    "access_seconds": C.access_seconds(request["redirect_uri"]),
                    "expires": now + CODE_SECONDS, "used": False, "family": uuid.uuid4().hex}
                reply["code"] = code
            if request["state"]:
                reply["state"] = request["state"]
            if issuer:
                reply["iss"] = issuer
        separator = "&" if "?" in request["redirect_uri"] else "?"
        return request["redirect_uri"] + separator + urlencode(reply), grant_id

    @staticmethod
    def _grant_for(state, person, client_id, client, redirect_uri, scopes, now) -> str:
        """这个人和这个应用的连接：已有就沿用（权限取较宽的，连接时间往前挪），断开过的重新启用。"""
        shown = C.display(redirect_uri, client["name"], client_id)
        for grant_id, grant in state["grants"].items():
            if grant["kind"] == "oauth" and grant["person"] == person and grant["client"] == client_id:
                if grant.get("revoked"):
                    grant.update(revoked=None, scopes=scopes, last_used=None)
                else:
                    grant["scopes"] = sorted(set(grant["scopes"]) | set(scopes), key=SCOPES.index)
                grant.update(connected=now, name=client["name"], display=shown)
                return grant_id
        grant_id = "app_" + uuid.uuid4().hex[:20]
        state["grants"][grant_id] = {"kind": "oauth", "person": person, "client": client_id,
                                     "name": client["name"], "display": shown, "scopes": scopes,
                                     "connected": now, "last_used": None, "revoked": None, "expires": None}
        return grant_id

    # ── 令牌端点 ──────────────────────────────────────────────────────────────

    def _issue(self, state, grant_id: str, family: str, scopes: List[str], seconds: int, now: float,
               parent: Optional[str] = None) -> Dict[str, Any]:
        access, refresh = _secret("vca_"), _secret("vcf_")
        common = {"grant": grant_id, "family": family, "scopes": scopes, "revoked": None}
        state["tokens"][digest(access)] = {**common, "type": "access", "expires": now + seconds}
        state["tokens"][digest(refresh)] = {**common, "type": "refresh", "expires": now + REFRESH_SECONDS,
                                            "used": None, "children": [], "seconds": seconds}
        if parent is not None:
            state["tokens"][parent]["children"].append(digest(refresh))
        return {"access_token": access, "token_type": "Bearer", "expires_in": seconds,
                "refresh_token": refresh, "scope": " ".join(scopes)}

    def _revoke_family(self, state, family: str, now: float) -> None:
        for token in state["tokens"].values():
            if token["family"] == family and not token.get("revoked"):
                token["revoked"] = now

    def exchange(self, form: Dict[str, Any]) -> Dict[str, Any]:
        """授权码换令牌。"""
        now = self.clock()
        with self._change() as state:
            code = state["codes"].get(digest(str(form.get("code") or "")))
            if code is None or code["expires"] <= now:
                raise OAuthError(400, "invalid_grant", "The code is unknown or expired")
            if code["used"]:
                self._revoke_family(state, code["family"], now)        # 码被重放：它换出的全部作废
                raise _Kept(OAuthError(400, "invalid_grant", "The code was already used"))
            if form.get("client_id") != code["client"]:
                raise OAuthError(400, "invalid_grant", "The code belongs to another client")
            if form.get("redirect_uri", code["redirect_uri"]) != code["redirect_uri"]:
                raise OAuthError(400, "invalid_grant", "redirect_uri does not match")
            if not pkce_ok(form.get("code_verifier"), code["challenge"]):
                raise OAuthError(400, "invalid_grant", "PKCE verification failed")
            code["used"] = True
            grant = state["grants"].get(code["grant"])
            if grant is None or grant.get("revoked"):
                raise OAuthError(400, "invalid_grant", "The connection was removed")
            return self._issue(state, code["grant"], code["family"], code["scopes"], code["access_seconds"], now)

    def refresh(self, form: Dict[str, Any]) -> Dict[str, Any]:
        now = self.clock()
        with self._change() as state:
            key = digest(str(form.get("refresh_token") or ""))
            token = state["tokens"].get(key)
            if token is None or token["type"] != "refresh" or token.get("revoked") or token["expires"] <= now:
                raise OAuthError(400, "invalid_grant", "The refresh token is unknown, expired or revoked")
            grant = state["grants"].get(token["grant"])
            if grant is None or grant.get("revoked") or form.get("client_id", grant["client"]) != grant["client"]:
                raise OAuthError(400, "invalid_grant", "The connection was removed")
            if token["used"] is not None:
                children_used = any((state["tokens"].get(child) or {}).get("used") is not None
                                    for child in token["children"])
                if now - token["used"] > REFRESH_GRACE or children_used:
                    self._revoke_family(state, token["family"], now)    # 旧令牌又来：当作被盗
                    raise _Kept(OAuthError(400, "invalid_grant", "The refresh token was already used"))
            else:
                token["used"] = now
                for sibling_parent in state["tokens"].values():           # 兄弟对里第一个续期的，作废其余
                    if key in sibling_parent.get("children", ()):
                        for other in sibling_parent["children"]:
                            if other != key and (state["tokens"].get(other) or {}).get("used") is None:
                                state["tokens"][other]["revoked"] = now
            scopes = [s for s in token["scopes"] if s in grant["scopes"]]
            return self._issue(state, token["grant"], token["family"], scopes, token["seconds"], now, parent=key)

    def revoke_token(self, secret: str) -> None:
        """``/api/oauth/revoke``：作废这枚令牌所在的一整串；认不出来也不报错（RFC 7009）。"""
        now = self.clock()
        with self._change() as state:
            token = state["tokens"].get(digest(secret))
            if token is not None and token["type"] != "personal":
                self._revoke_family(state, token["family"], now)

    # ── 调用 /mcp 时认人 ──────────────────────────────────────────────────────

    def authenticate(self, bearer: str) -> Optional[Dict[str, Any]]:
        """``Authorization: Bearer`` 的值 → 连接信息 {grant, person, scopes, name, display, kind}；不认得返回 None。"""
        if not isinstance(bearer, str) or not bearer.startswith(("vca_", "vcp_")):
            return None
        now = self.clock()
        with _LOCK:
            state = self._load()
        token = state["tokens"].get(digest(bearer))
        if token is None or token["type"] not in ("access", "personal") or token.get("revoked") \
                or (token["expires"] is not None and token["expires"] <= now):
            return None
        grant = state["grants"].get(token["grant"])
        if grant is None or grant.get("revoked") or (grant.get("expires") and grant["expires"] <= now):
            return None
        if not grant.get("last_used") or now - grant["last_used"] > _TOUCH_EVERY:
            self._touch(token["grant"], now)
        return {"grant": token["grant"], "person": grant["person"], "kind": grant["kind"],
                "scopes": [s for s in token["scopes"] if s in grant["scopes"]],
                "name": grant["name"], "display": grant.get("display") or {}}

    def _touch(self, grant_id: str, now: float) -> None:
        try:
            with self._change() as state:
                grant = state["grants"].get(grant_id)
                if grant is not None:
                    grant["last_used"] = now
        except (OSError, ValueError):
            pass                                       # 记不下「最近使用」不影响这次调用

    # ── 设置页：列出、断开、个人令牌 ──────────────────────────────────────────

    def grants(self, person: Optional[str] = None) -> List[Dict[str, Any]]:
        """还有效的连接（含个人令牌），person 为 None 时是全部。"""
        now = self.clock()
        rows = []
        for grant_id, grant in self._read()["grants"].items():
            if grant.get("revoked") or (grant.get("expires") and grant["expires"] <= now):
                continue
            if person is not None and grant["person"] != person:
                continue
            rows.append({"id": grant_id, **grant})
        return sorted(rows, key=lambda row: row["connected"], reverse=True)

    def grant(self, grant_id: str) -> Optional[Dict[str, Any]]:
        found = self._read()["grants"].get(str(grant_id or ""))
        return None if found is None or found.get("revoked") else {"id": grant_id, **found}

    def disconnect(self, grant_id: str, kind: Optional[str] = None) -> bool:
        """断开一个连接（或作废一枚个人令牌）：它的令牌全部失效。没有或已断开返回 False。"""
        now = self.clock()
        with self._change() as state:
            grant = state["grants"].get(str(grant_id or ""))
            if grant is None or grant.get("revoked") or (kind and grant["kind"] != kind):
                return False
            grant["revoked"] = now
            for token in state["tokens"].values():
                if token["grant"] == grant_id and not token.get("revoked"):
                    token["revoked"] = now
        return True

    def disconnect_person(self, person: str) -> int:
        """这个人的所有连接和个人令牌全部作废（被移出空间、重置密码时）。返回断开的个数。"""
        if not person:
            return 0
        now = self.clock()
        with self._change() as state:
            mine = {grant_id for grant_id, grant in state["grants"].items() if grant["person"] == person}
            active = {grant_id for grant_id in mine if not state["grants"][grant_id].get("revoked")}
            for grant_id in active:
                state["grants"][grant_id]["revoked"] = now
            for token in state["tokens"].values():
                if token["grant"] in mine and not token.get("revoked"):
                    token["revoked"] = now
            # Reconnecting may reuse a grant ID. Its unused old authorization codes must stay invalid.
            for key in [key for key, code in state["codes"].items() if code["grant"] in mine]:
                del state["codes"][key]
        return len(active)

    def create_token(self, person: str, name: Any, access: str,
                     days: Optional[int]) -> Tuple[Dict[str, Any], str]:
        """个人令牌：返回 (连接, 令牌原文)。原文只在这里出现一次。"""
        clean = C.clean_name(name)[:TOKEN_NAME_CHARS]
        if not clean:
            raise ValueError("令牌要有名字")
        if days is not None and days not in TOKEN_DAYS:
            raise ValueError("有效期只能是 30、90、365 天或不过期")
        scopes = scopes_for(access)
        now = self.clock()
        expires = now + days * 86400 if days else None
        secret = _secret("vcp_")
        with self._change() as state:
            mine = [g for g in state["grants"].values() if g["kind"] == "token" and g["person"] == person
                    and not g.get("revoked") and not (g.get("expires") and g["expires"] <= now)]
            if len(mine) >= TOKENS_PER_PERSON:
                raise OverflowError("token_limit")
            grant_id = "tok_" + uuid.uuid4().hex[:20]
            grant = {"kind": "token", "person": person, "client": None, "name": clean, "display": {},
                     "scopes": scopes, "connected": now, "last_used": None, "revoked": None, "expires": expires}
            state["grants"][grant_id] = grant
            state["tokens"][digest(secret)] = {"grant": grant_id, "family": grant_id, "type": "personal",
                                               "scopes": scopes, "expires": expires, "revoked": None}
        return {"id": grant_id, **grant}, secret


def iso(moment: Optional[float]) -> Optional[str]:
    return _iso(moment)
