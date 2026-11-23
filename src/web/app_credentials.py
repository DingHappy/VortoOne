"""新版界面里的「AI 密钥」：所有者自己加的模型端点（OpenAI 兼容），以及服务配置里的那一个。

- 存在 ``.vortoone/model_credentials.json``（0600）；密钥从不回给界面，只回末 4 位。
- 每个有效的端点登记成一个供应商（``VORTOONE_PROVIDER_<ID>_BASE/_KEY``，见 ``llm/providers.py``），
  agent 的模型写成 ``custom:<密钥 id>:<模型>`` 就走这个端点；key 只跟着自己的端点走，不借给别家。
  这些环境变量在子进程里会被剥掉（``sandbox/policy.py``）。
- 设为默认：这个家默认的模型端点换成它（主助理和没选模型的 agent 都用它），原来服务配置里的
  ``OPENAI_API_BASE/KEY``、``DEFAULT_MODEL`` 记下来，取消默认或删掉它时换回去。
- 服务配置里的那一个（``.env``）列成只读的一行 ``server``：改它要去服务的配置里改。
- 现在只接 OpenAI 兼容的端点：各家官方的 key 请按「自定义端点」填它们的 OpenAI 兼容地址；
  Anthropic Messages 格式的端点本服务的模型客户端不会说，如实拒绝。
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.utils.state_dir import state_dir

_FILE = "model_credentials.json"
_LOCK = threading.RLock()
SERVER_ID = "server"
_ENV_PREFIX = "VORTOONE_PROVIDER_"
_ENV_TAG = "CRED_"                  # 只动自己登记的那些，不碰主人自己在 .env 里登记的供应商
_ID = re.compile(r"cred-[0-9a-f]{12}")
MAX_MODELS, MAX_LABEL, MAX_KEY, MAX_URL = 50, 80, 400, 300
CHECK_TIMEOUT = 20.0
# 服务配置里原来的默认端点：设为默认前记下，换回去时用。None 表示还没换过。
_SERVER_ENV: Optional[Dict[str, Optional[str]]] = None
_DEFAULT_KEYS = ("OPENAI_API_BASE", "OPENAI_API_KEY", "DEFAULT_MODEL")


class CredentialError(ValueError):
    """界面上要说明的拒绝：code 给界面分辨，消息给人看。"""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


def _path(root: str) -> Path:
    return state_dir(root) / _FILE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(root: str) -> Dict[str, Any]:
    try:
        data = json.loads(_path(root).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"credentials": [], "default": None}
    except (OSError, ValueError) as error:
        raise CredentialError("credentials.unreadable", "AI 密钥记录读不出来，没有改动", 409) from error
    if not isinstance(data, dict) or not isinstance(data.get("credentials"), list):
        raise CredentialError("credentials.unreadable", "AI 密钥记录格式不对，没有改动", 409)
    return data


def _write(root: str, data: Dict[str, Any]) -> None:
    target = _path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


# ── 校验 ─────────────────────────────────────────────────────────────────────

def _text(value: Any, label: str, limit: int, *, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
        raise CredentialError("invalid", f"{label}要填，且不超过 {limit} 个字")
    return value.strip()


def _base_url(value: Any) -> str:
    text = _text(value, "地址", MAX_URL)
    from urllib.parse import urlsplit
    parsed = urlsplit(text)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password \
            or parsed.query or parsed.fragment:
        raise CredentialError("invalid", "地址要是 http(s)://主机/路径 的形式，不能带账号、参数")
    return text.rstrip("/")


def _style(value: Any) -> str:
    if value != "openai":
        raise CredentialError("endpoint.style_unsupported",
                              "现在只接 OpenAI 兼容的端点（Chat Completions）；Anthropic Messages 格式的还不支持")
    return "openai"


def _prices(value: Any) -> Optional[Dict[str, float]]:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"input", "output", "cacheRead", "cacheWrite"} \
            or not {"input", "output"} <= set(value):
        raise CredentialError("invalid", "价格要有输入和输出（每百万 token 美元）")
    out = {}
    for key, number in value.items():
        if type(number) not in (int, float) or number < 0 or number > 10000:
            raise CredentialError("invalid", "价格要是 0～10000 之间的数")
        out[key] = float(number)
    return out


def _models(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list) or not value or len(value) > MAX_MODELS:
        raise CredentialError("invalid", f"至少选一个模型，最多 {MAX_MODELS} 个")
    rows, seen = [], set()
    for item in value:
        if not isinstance(item, dict) or set(item) - {"id", "label", "prices", "check"}:
            raise CredentialError("invalid", "模型只能有 id、名字和价格")
        model = _text(item.get("id"), "模型 id", 160)
        if model in seen or ":" in model and model.startswith("custom:"):
            raise CredentialError("invalid", "模型 id 不能重复")
        seen.add(model)
        row: Dict[str, Any] = {"id": model}
        label = _text(item.get("label"), "模型名字", MAX_LABEL, required=False)
        if label:
            row["label"] = label
        prices = _prices(item.get("prices"))
        if prices is not None:
            row["prices"] = prices
        rows.append(row)
    return rows


def _key(value: Any) -> str:
    key = _text(value, "密钥", MAX_KEY)
    if any(ch.isspace() for ch in key):
        raise CredentialError("invalid", "密钥里不能有空格或换行")
    return key


# ── 和端点说话 ───────────────────────────────────────────────────────────────

def _headers(key: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {key}"} if key else {}


async def list_models(base_url: str, key: str) -> Tuple[List[Dict[str, str]], str]:
    """（模型列表, 错误）。端点不给列表就如实说没有。"""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=CHECK_TIMEOUT, follow_redirects=False) as client:
            response = await client.get(f"{base_url}/models", headers=_headers(key))
        if response.status_code >= 400:
            return [], f"端点回了 {response.status_code}"
        data = response.json()
    except (httpx.HTTPError, ValueError) as error:
        return [], f"连不上这个端点：{type(error).__name__}"
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return [], "端点没有给出模型列表"
    models = []
    for item in items[:200]:
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip():
            models.append({"id": item["id"].strip()[:160]})
    return models, "" if models else "端点没有列出模型"


_PROBE_TOOL = {"type": "function", "function": {
    "name": "report_ready", "description": "Report that you are ready.",
    "parameters": {"type": "object", "properties": {"ready": {"type": "boolean"}}, "required": ["ready"]}}}


async def check_model(base_url: str, key: str, model: str) -> Dict[str, Any]:
    """一次真的小调用：调了工具 ok，只说话 no_tools，出错 failed（带端点的原话）。agent 靠调工具干活。"""
    import httpx
    body = {"model": model, "max_tokens": 32, "tools": [_PROBE_TOOL],
            "messages": [{"role": "user", "content": "Call report_ready with ready=true."}]}
    try:
        async with httpx.AsyncClient(timeout=CHECK_TIMEOUT, follow_redirects=False) as client:
            response = await client.post(f"{base_url}/chat/completions", headers=_headers(key), json=body)
        if response.status_code >= 400:
            detail = response.text[:200].strip()
            return {"model": model, "check": "failed", "error": f"{response.status_code} {detail}".strip()}
        data = response.json()
        message = ((data.get("choices") or [{}])[0] or {}).get("message") or {}
    except (httpx.HTTPError, ValueError, AttributeError, IndexError, TypeError) as error:
        return {"model": model, "check": "failed", "error": f"调用失败：{type(error).__name__}"}
    return {"model": model, "check": "ok" if message.get("tool_calls") else "no_tools"}


# ── 视图 ─────────────────────────────────────────────────────────────────────

def _hint(key: str) -> str:
    return key[-4:] if len(key) >= 8 else ""


def _server() -> Optional[Dict[str, Any]]:
    """服务配置里的那一个：有 key 才列。设为默认换过之后，列的是原来那份。"""
    env = _SERVER_ENV if _SERVER_ENV is not None else {k: os.getenv(k) for k in _DEFAULT_KEYS}
    key = (env.get("OPENAI_API_KEY") or "").strip()
    if not key:
        return None
    from src.llm.client import DEFAULT_LLM_BASE_URL
    base = (env.get("OPENAI_API_BASE") or DEFAULT_LLM_BASE_URL).rstrip("/")
    model = (env.get("DEFAULT_MODEL") or os.getenv("OPENAI_MODEL") or "").strip()
    return {"id": SERVER_ID, "provider": "custom", "label": "服务配置的模型", "key": key,
            "endpoint": {"style": "openai", "baseURL": base, "models": [{"id": model}] if model else []},
            "createdAt": datetime.fromtimestamp(0, timezone.utc).isoformat(), "revokedAt": None, "server": True}


def _view(item: Dict[str, Any], used_by: List[Dict[str, str]]) -> Dict[str, Any]:
    from src.web.app_views import _iso
    return {"id": item["id"], "provider": "custom", "label": item["label"], "keyHint": _hint(item["key"]),
            "workspaceIdHint": None,
            "endpoint": {"style": "openai", "baseURL": item["endpoint"]["baseURL"],
                         "models": [dict(m) for m in item["endpoint"]["models"]]},
            "usedBy": used_by, "copies": 0, "createdAt": _iso(item["createdAt"]),
            "revokedAt": _iso(item["revokedAt"]) if item.get("revokedAt") else None}


def _used_by(root: str, credential_id: str) -> List[Dict[str, str]]:
    """哪些 agent 的模型写的是这个端点上的（custom:<id>:…）。主助理用默认的那个，不在这里列。"""
    from src.web import app_views as views
    prefix = f"custom:{credential_id}:"
    return [{"id": a["id"], "name": a["name"]} for a in views.agents(root) if str(a.get("model") or "").startswith(prefix)]


def listing(root: str) -> List[Dict[str, Any]]:
    with _LOCK:
        data = _read(root)
    rows = [_view(item, _used_by(root, item["id"])) for item in data["credentials"]]
    server = _server()
    if server is not None:
        rows.insert(0, _view(server, []))
    return rows


def _find(data: Dict[str, Any], credential_id: str) -> Dict[str, Any]:
    if credential_id == SERVER_ID:
        raise CredentialError("credential.server", "这是服务配置里的模型，要在服务的配置（.env）里改", 409)
    item = next((x for x in data["credentials"] if x["id"] == credential_id), None)
    if item is None:
        raise CredentialError("not_found", "没有这个 AI 密钥", 404)
    return item


def _active(item: Dict[str, Any]) -> bool:
    return not item.get("revokedAt")


def best_model(models: List[Dict[str, Any]]) -> Optional[str]:
    """和界面同一个挑法：调过工具的优先，没测过的其次，只会说话的再次，失败的最后。"""
    rank = {"ok": 0, None: 1, "no_tools": 2, "failed": 3}
    ordered = sorted(models, key=lambda m: rank.get(m.get("check"), 1))
    return ordered[0]["id"] if ordered else None


# ── 让它真的生效 ─────────────────────────────────────────────────────────────

def _env_name(credential_id: str) -> str:
    return _ENV_PREFIX + _ENV_TAG + credential_id[5:].upper()


def provider_name(credential_id: str) -> str:
    """登记成的供应商名（llm/providers 把环境变量里的名字转小写）。"""
    return (_ENV_TAG + credential_id[5:]).lower()


def _registered() -> Dict[str, Tuple[str, str]]:
    """进程环境里现在登记着的端点：名字 → （地址, key）。"""
    names = [k[:-5] for k in os.environ if k.startswith(_ENV_PREFIX + _ENV_TAG) and k.endswith("_BASE")]
    return {name: (os.environ[name + "_BASE"], os.environ.get(name + "_KEY", "")) for name in names}


def apply(root: str) -> None:
    """把记录里的端点登记进进程环境：每个有效的一个供应商；默认的那个换掉服务的默认端点。"""
    global _SERVER_ENV
    from src.llm import endpoints
    with _LOCK, endpoints.LOCK:     # 整批一起改：任何客户端都读不到「新地址 + 旧 key」
        try:
            data = _read(root)
        except CredentialError:
            return              # 读不出来就保持现状，不拿半份记录去改模型端点
        wanted = {}
        for item in data["credentials"]:
            if _active(item):
                wanted[_env_name(item["id"])] = item
        before = _registered()
        default_before = tuple(os.environ.get(k) for k in _DEFAULT_KEYS)
        for name in [k for k in os.environ if k.startswith(_ENV_PREFIX + _ENV_TAG)]:
            if name.endswith("_BASE") and name[:-5] not in wanted or name.endswith("_KEY") and name[:-4] not in wanted:
                os.environ.pop(name, None)
        for name, item in wanted.items():
            os.environ[name + "_BASE"] = item["endpoint"]["baseURL"]
            os.environ[name + "_KEY"] = item["key"]
        default = next((x for x in data["credentials"] if x["id"] == data.get("default") and _active(x)), None)
        if default is not None:
            if _SERVER_ENV is None:
                _SERVER_ENV = {k: os.environ.get(k) for k in _DEFAULT_KEYS}
            os.environ["OPENAI_API_BASE"] = default["endpoint"]["baseURL"]
            os.environ["OPENAI_API_KEY"] = default["key"]
            model = best_model(default["endpoint"]["models"])
            if model:
                os.environ["DEFAULT_MODEL"] = model
        elif _SERVER_ENV is not None:
            for key, value in _SERVER_ENV.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            _SERVER_ENV = None
        after = _registered()
        active = (os.environ.get("OPENAI_API_BASE", ""), os.environ.get("OPENAI_API_KEY", ""))
        endpoints.replaced([(pair, after.get(name)) for name, pair in before.items() if after.get(name) != pair],
                           [*after.values(), active])
        if tuple(os.environ.get(k) for k in _DEFAULT_KEYS) != default_before:
            endpoints.default_changed()
    from src.llm.providers import reset_cache
    reset_cache()


# ── 改动 ─────────────────────────────────────────────────────────────────────

async def endpoint_models(body: Dict[str, Any]) -> Dict[str, Any]:
    _style(body.get("style"))
    base = _base_url(body.get("baseURL"))
    key = _key(body["apiKey"]) if body.get("apiKey") else ""
    models, error = await list_models(base, key)
    return {"baseURL": base, "models": models, **({"error": error} if error else {})}


async def add(root: str, body: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(body, dict) or set(body) - {"provider", "label", "apiKey", "workspaceId", "organizationId",
                                                  "projectId", "endpoint"}:
        raise CredentialError("invalid", "只能填供应商、名字、密钥和端点")
    if body.get("provider") != "custom":
        raise CredentialError("provider.unsupported",
                              "现在只接自定义端点：各家官方的 key 请选「自定义端点」，填它的 OpenAI 兼容地址和模型")
    if any(body.get(name) for name in ("workspaceId", "organizationId", "projectId")):
        raise CredentialError("invalid", "自定义端点不用填工作区、组织或项目")
    endpoint = body.get("endpoint")
    if not isinstance(endpoint, dict) or set(endpoint) - {"style", "baseURL", "models"}:
        raise CredentialError("invalid", "自定义端点要有格式、地址和模型")
    _style(endpoint.get("style"))
    base, models = _base_url(endpoint.get("baseURL")), _models(endpoint.get("models"))
    key, label = _key(body.get("apiKey")), _text(body.get("label"), "名字", MAX_LABEL)
    checks = [await check_model(base, key, m["id"]) for m in models]
    if all(c["check"] == "failed" for c in checks):
        raise CredentialError("credential.refused", "这个端点没接受这把密钥：" + (checks[0].get("error") or ""))
    for row, result in zip(models, checks):
        row["check"] = result["check"]
    with _LOCK:
        data = _read(root)
        same = next((x for x in data["credentials"] if _active(x) and x["key"] == key
                     and x["endpoint"]["baseURL"] == base), None)
        if same is not None:            # 同一把 key、同一个地址：更新那一个，不加副本
            same.update(label=label, endpoint={"style": "openai", "baseURL": base, "models": models})
            item, reused = same, True
        else:
            item = {"id": "cred-" + uuid.uuid4().hex[:12], "label": label, "key": key,
                    "endpoint": {"style": "openai", "baseURL": base, "models": models},
                    "createdAt": _now(), "revokedAt": None}
            data["credentials"].append(item)
            reused = False
        _write(root, data)
    apply(root)
    return {**_view(item, _used_by(root, item["id"])), "check": "ok" if any(c["check"] == "ok" for c in checks)
            else "unchecked", "reused": reused, "checks": checks}


async def set_models(root: str, credential_id: str, models_in: Any) -> Dict[str, Any]:
    with _LOCK:
        item = dict(_find(_read(root), credential_id))
    models = _models(models_in)
    checks = [await check_model(item["endpoint"]["baseURL"], item["key"], m["id"]) for m in models]
    for row, result in zip(models, checks):
        row["check"] = result["check"]
    with _LOCK:
        data = _read(root)
        current = _find(data, credential_id)
        if current["key"] != item["key"]:
            raise CredentialError("credential.changed", "测试期间密钥换过了，请再试一次", 409)
        current["endpoint"]["models"] = models
        _write(root, data)
    apply(root)
    return {"models": models, "checks": checks}


async def rotate(root: str, credential_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(body, dict) or set(body) - {"apiKey", "label"}:
        raise CredentialError("invalid", "只能换密钥和名字")
    key = _key(body.get("apiKey"))
    with _LOCK:
        item = dict(_find(_read(root), credential_id))
    model = best_model(item["endpoint"]["models"])
    result = await check_model(item["endpoint"]["baseURL"], key, model) if model else {"check": "failed"}
    if result["check"] == "failed":
        raise CredentialError("credential.refused", "这个端点没接受新密钥：" + str(result.get("error") or ""))
    with _LOCK:
        data = _read(root)
        current = _find(data, credential_id)
        current["key"] = key
        if body.get("label"):
            current["label"] = _text(body["label"], "名字", MAX_LABEL)
        _write(root, data)
        view = _view(current, _used_by(root, credential_id))
    apply(root)
    return view


async def check(root: str, credential_id: str) -> Dict[str, str]:
    with _LOCK:
        data = _read(root)
        item = _server() if credential_id == SERVER_ID else dict(_find(data, credential_id))
    if item is None:
        raise CredentialError("not_found", "没有这个 AI 密钥", 404)
    model = best_model(item["endpoint"]["models"])
    if not model:
        return {"check": "unchecked"}
    result = await check_model(item["endpoint"]["baseURL"], item["key"], model)
    if result["check"] == "failed":
        raise CredentialError("credential.refused", "这个端点没接受这把密钥：" + str(result.get("error") or ""))
    return {"check": "ok" if result["check"] == "ok" else "unchecked"}


def make_default(root: str, credential_id: str) -> Dict[str, Any]:
    """设为默认。服务配置的那一行设为默认，就是换回服务配置。"""
    with _LOCK:
        data = _read(root)
        if credential_id == SERVER_ID:
            server = _server()
            if server is None:
                raise CredentialError("not_found", "服务配置里没有模型", 404)
            data["default"] = None
            _write(root, data)
            view = _view(server, [])
        else:
            item = _find(data, credential_id)
            if not _active(item):
                raise CredentialError("credential.revoked", "这把密钥已经停用", 409)
            if not best_model(item["endpoint"]["models"]):
                raise CredentialError("invalid", "这个端点还没有模型，先选模型")
            data["default"] = credential_id
            _write(root, data)
            view = _view(item, _used_by(root, credential_id))
    apply(root)
    return view


def remove(root: str, credential_id: str, *, purge: bool, release: bool) -> None:
    """停用（purge 就彻底删掉）。是默认的、或有 agent 在用，要 release 说明看过后果才动。"""
    with _LOCK:
        data = _read(root)
        item = _find(data, credential_id)
        users = _used_by(root, credential_id)
        if (data.get("default") == credential_id or users) and not release:
            who = "、".join(u["name"] for u in users)
            raise CredentialError("credential.in_use", ("它是默认的模型端点" if data.get("default") == credential_id
                                                        else f"{who} 在用它") + "；确认后再删", 409)
        if data.get("default") == credential_id:
            data["default"] = None
        if purge:
            data["credentials"] = [x for x in data["credentials"] if x["id"] != credential_id]
        else:
            item["revokedAt"] = item.get("revokedAt") or _now()
        _write(root, data)
    apply(root)


def remove_copies(root: str, credential_id: str) -> Dict[str, int]:
    """同一把 key、同一个地址保存时就更新原来那个，不会有副本。"""
    with _LOCK:
        if credential_id != SERVER_ID:
            _find(_read(root), credential_id)
    return {"removed": 0}


def default_id(root: str) -> Optional[str]:
    try:
        data = _read(root)
    except CredentialError:
        return None
    return data.get("default")


def models(root: str) -> Dict[str, Any]:
    """界面的模型列表：每个有效端点的模型（id 是 custom:<密钥>:<模型>），默认是默认端点挑出来的那个。"""
    try:
        data = _read(root)
    except CredentialError:
        data = {"credentials": [], "default": None}
    rows = []
    for item in data["credentials"]:
        if not _active(item):
            continue
        for model in item["endpoint"]["models"]:
            prices = model.get("prices")
            rows.append({"id": f"custom:{item['id']}:{model['id']}", "label": model.get("label") or model["id"],
                         "provider": "custom", "inputUsdPerMTok": (prices or {}).get("input", 0.0),
                         "outputUsdPerMTok": (prices or {}).get("output", 0.0), "available": True,
                         "endpoint": {"credentialId": item["id"], "name": model["id"]},
                         **({} if prices else {"unpriced": True})})
    default = next((x for x in data["credentials"] if x["id"] == data.get("default") and _active(x)), None)
    best = best_model(default["endpoint"]["models"]) if default else None
    return {"models": rows, "defaultModel": f"custom:{default['id']}:{best}" if default and best else ""}
