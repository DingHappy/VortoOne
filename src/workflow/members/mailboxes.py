"""团队邮箱（主人 2026-11-24 定）：所有者接进来的 IMAP 邮箱，和每个 agent 在每个邮箱上能做什么。

- agent 只能读（搜索、读信、看文件夹）和往草稿箱放草稿；发信、回复、转发、删除、整理都没有。
- 只有所有者自己发起的事（私聊、所有者派的活、所有者批准的例行工作）里，agent 才拿得到读邮箱的工具；
  成员发起的、频道里的一律不给（见 agents/tools/team_mail.py）。
- 授权码（客户端专用密码）只存在这台服务器上（0600），不回给界面、不进日志或工具结果。
- 授权按 agent 的内部 id（专员 id）记；界面上的 agent id 由 web 层换算。
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from src.utils.state_dir import state_dir

_FILE = "mailboxes.json"
_LOCK = threading.RLock()
MAX_ACCOUNTS, MAX_ALIASES, MAX_RULES = 20, 20, 50
# 这里有的动作；发信、回复、转发、删除、整理没有
ACTIONS = ("search", "read", "folders", "draft", "organize", "send", "reply", "forward", "delete")
READ_ACTIONS = ("search", "read", "folders")
_ADDRESS = re.compile(r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{1,253}\.[A-Za-z]{2,24}")
_RULE = re.compile(r"(?:[A-Za-z0-9._%+\-]{1,64})?@[A-Za-z0-9.\-]{1,253}\.[A-Za-z]{2,24}")
_HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?")

# 常见邮箱（IMAP 要开「客户端专用密码 / 授权码」）
PRESETS: List[Dict[str, Any]] = [
    {"id": "icloud", "label": "iCloud", "imapHost": "imap.mail.me.com", "imapPort": 993,
     "smtpHost": "smtp.mail.me.com", "smtpPort": 587, "username": "address",
     "appPasswordUrl": "https://account.apple.com/account/manage"},
    {"id": "qq", "label": "QQ 邮箱", "imapHost": "imap.qq.com", "imapPort": 993,
     "smtpHost": "smtp.qq.com", "smtpPort": 465, "username": "address", "appPasswordUrl": None},
    {"id": "163", "label": "网易 163", "imapHost": "imap.163.com", "imapPort": 993,
     "smtpHost": "smtp.163.com", "smtpPort": 465, "username": "address", "appPasswordUrl": None},
    {"id": "126", "label": "网易 126", "imapHost": "imap.126.com", "imapPort": 993,
     "smtpHost": "smtp.126.com", "smtpPort": 465, "username": "address", "appPasswordUrl": None},
    {"id": "aliyun", "label": "阿里邮箱", "imapHost": "imap.aliyun.com", "imapPort": 993,
     "smtpHost": "smtp.aliyun.com", "smtpPort": 465, "username": "address", "appPasswordUrl": None},
    {"id": "yahoo", "label": "Yahoo", "imapHost": "imap.mail.yahoo.com", "imapPort": 993,
     "smtpHost": "smtp.mail.yahoo.com", "smtpPort": 465, "username": "address", "appPasswordUrl": None},
]


class MailboxError(RuntimeError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _address(value: Any, label: str = "邮箱地址") -> str:
    if not isinstance(value, str) or not _ADDRESS.fullmatch(value.strip()):
        raise MailboxError(400, "invalid", f"{label}无效")
    return value.strip().lower()


def _rules(value: Any, label: str) -> List[str]:
    if not isinstance(value, list) or len(value) > MAX_RULES or any(
            not isinstance(item, str) or not _RULE.fullmatch(item.strip()) for item in value):
        raise MailboxError(400, "invalid", f"{label}要是邮箱地址或 @域名，最多 {MAX_RULES} 条")
    return list(dict.fromkeys(item.strip().lower() for item in value))


def default_policy() -> Dict[str, Any]:
    return {"folders": [], "senders": [], "windowDays": None, "allowedRecipients": [], "dailySendCap": 0,
            "sendWithoutAsking": False, "fromAddress": None}


def check_policy(value: Any, account: Dict[str, Any]) -> Dict[str, Any]:
    """agent 在这个邮箱上的规则。和发信有关的几项这里没有发信，只接受「不发」的取值。"""
    if not isinstance(value, dict) or set(value) - set(default_policy()):
        raise MailboxError(400, "invalid", "规则只能有 folders、senders、windowDays、allowedRecipients、dailySendCap、"
                                           "sendWithoutAsking、fromAddress")
    policy = {**default_policy(), **value}
    folders = policy["folders"]
    if not isinstance(folders, list) or len(folders) > MAX_RULES or any(
            not isinstance(item, str) or not item.strip() or len(item) > 300 for item in folders):
        raise MailboxError(400, "invalid", "文件夹要是 1～300 字的名字")
    window = policy["windowDays"]
    if window is not None and (type(window) is not int or not 1 <= window <= 3650):
        raise MailboxError(400, "invalid", "windowDays 要是 1～3650 天，或不限（null）")
    if policy["sendWithoutAsking"] is not False or _rules(policy["allowedRecipients"], "收件人") \
            or type(policy["dailySendCap"]) is not int or policy["dailySendCap"] < 0:
        raise MailboxError(422, "mail.send_unavailable", "这里的 agent 不发信，只能读信和放草稿：发信的规则不能设")
    sender = policy["fromAddress"]
    if sender is not None and _address(sender, "发件地址") not in (account["address"], *account["aliases"]):
        raise MailboxError(400, "invalid", "发件地址要是这个邮箱自己的地址或别名")
    return {"folders": list(dict.fromkeys(item.strip() for item in folders)),
            "senders": _rules(policy["senders"], "发件人"), "windowDays": window, "allowedRecipients": [],
            "dailySendCap": policy["dailySendCap"], "sendWithoutAsking": False,
            "fromAddress": sender.strip().lower() if sender else None}


class Mailboxes:
    def __init__(self, root: str):
        self.root = str(root)
        self.directory = state_dir(root) / "members"

    def _load(self) -> Dict[str, Any]:
        path = self.directory / _FILE
        if not path.exists():
            return {"version": 1, "accounts": {}, "grants": {}}
        if path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
            raise MailboxError(503, "unavailable", "邮箱登记无效或过大")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeError, ValueError) as error:
            raise MailboxError(503, "unavailable", "邮箱登记损坏") from error
        if (not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("accounts"), dict)
                or not isinstance(value.get("grants"), dict)):
            raise MailboxError(503, "unavailable", "邮箱登记结构无效")
        return value

    def _save(self, state: Dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / _FILE
        if path.is_symlink():
            raise MailboxError(503, "unavailable", "邮箱登记不能是符号链接")
        temporary = self.directory / f".{_FILE}.{uuid.uuid4().hex}.tmp"
        try:
            temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.chmod(0o600)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def _change(self) -> Iterator[Dict[str, Any]]:
        with _LOCK:
            state = self._load()
            yield state
            self._save(state)

    # ── 邮箱 ──────────────────────────────────────────────────────────────────

    @staticmethod
    def connection(data: Dict[str, Any]) -> Dict[str, Any]:
        """界面填的 → 登录用的（预设补服务器）。不碰存储，好先测再存。"""
        if not isinstance(data, dict) or set(data) - {"preset", "address", "password", "label", "username",
                                                      "imapHost", "imapPort", "smtpHost", "smtpPort"}:
            raise MailboxError(400, "invalid", "邮箱信息里有不认识的字段")
        address = _address(data.get("address"))
        password = data.get("password")
        if not isinstance(password, str) or not password.strip() or len(password) > 256:
            raise MailboxError(400, "invalid", "要填邮箱的客户端专用密码（授权码）")
        preset = next((item for item in PRESETS if item["id"] == data.get("preset")), None)
        if preset is None and data.get("preset") not in ("custom", "other", None):
            raise MailboxError(400, "invalid", "不认识这个邮箱类型")
        host = data.get("imapHost") or (preset or {}).get("imapHost")
        port = data.get("imapPort") or (preset or {}).get("imapPort") or 993
        if not isinstance(host, str) or not _HOST.fullmatch(host) or "." not in host:
            raise MailboxError(400, "invalid", "要填 IMAP 服务器地址")
        if type(port) is not int or not 1 <= port <= 65535:
            raise MailboxError(400, "invalid", "IMAP 端口无效")
        username = data.get("username")
        if username is None:
            username = address.partition("@")[0] if (preset or {}).get("username") == "local" else address
        if not isinstance(username, str) or not username.strip() or len(username) > 254:
            raise MailboxError(400, "invalid", "登录名无效")
        label = data.get("label")
        if label is not None and (not isinstance(label, str) or len(label.strip()) > 80):
            raise MailboxError(400, "invalid", "名字最多 80 字")
        return {"address": address, "password": password.strip(), "username": username.strip(),
                "imapHost": host.lower(), "imapPort": port, "preset": preset["id"] if preset else None,
                "label": (label or "").strip() or address}

    def add(self, connection: Dict[str, Any], owner_id: str) -> Dict[str, Any]:
        with self._change() as state:
            if len(state["accounts"]) >= MAX_ACCOUNTS:
                raise MailboxError(409, "mail.too_many", f"最多接 {MAX_ACCOUNTS} 个邮箱")
            if any(item["address"] == connection["address"] for item in state["accounts"].values()):
                raise MailboxError(409, "mail.already_connected", "这个邮箱已经接过了")
            account_id = "mailbox-" + uuid.uuid4().hex[:16]
            state["accounts"][account_id] = {**connection, "id": account_id, "aliases": [], "defaultFrom": None,
                                             "offActions": [], "askActions": [], "createdAt": _now(),
                                             "owner": owner_id, "status": "active", "lastError": None}
            return dict(state["accounts"][account_id])

    def get(self, account_id: str) -> Dict[str, Any]:
        with _LOCK:
            found = self._load()["accounts"].get(account_id)
        if found is None:
            raise MailboxError(404, "not_found", "没有这个邮箱")
        return dict(found)

    def accounts(self) -> List[Dict[str, Any]]:
        with _LOCK:
            state = self._load()
        return sorted((dict(item) for item in state["accounts"].values()), key=lambda item: item["createdAt"])

    def update(self, account_id: str, change: Dict[str, Any], *, password: Optional[str] = None) -> Dict[str, Any]:
        if not isinstance(change, dict) or set(change) - {"label", "password", "offActions", "askActions", "aliases",
                                                          "defaultFrom"}:
            raise MailboxError(400, "invalid", "只能改名字、授权码、动作开关、别名和默认发件地址")
        with self._change() as state:
            account = state["accounts"].get(account_id)
            if account is None:
                raise MailboxError(404, "not_found", "没有这个邮箱")
            if "label" in change:
                label = change["label"]
                if not isinstance(label, str) or not label.strip() or len(label.strip()) > 80:
                    raise MailboxError(400, "invalid", "名字要 1～80 字")
                account["label"] = label.strip()
            for key in ("offActions", "askActions"):
                if key in change:
                    value = change[key]
                    if not isinstance(value, list) or any(item not in ACTIONS for item in value):
                        raise MailboxError(400, "invalid", f"{key} 只能是 {', '.join(ACTIONS)} 里的几个")
                    account[key] = [item for item in ACTIONS if item in value]
            if "aliases" in change:
                aliases = change["aliases"]
                if not isinstance(aliases, list) or len(aliases) > MAX_ALIASES:
                    raise MailboxError(400, "invalid", f"别名最多 {MAX_ALIASES} 个")
                account["aliases"] = [item for item in dict.fromkeys(_address(a, "别名") for a in aliases)
                                      if item != account["address"]]
                if account["defaultFrom"] not in (None, *account["aliases"]):
                    account["defaultFrom"] = None
            if "defaultFrom" in change:
                sender = change["defaultFrom"]
                sender = None if sender is None else _address(sender, "默认发件地址")
                if sender not in (None, account["address"], *account["aliases"]):
                    raise MailboxError(400, "invalid", "默认发件地址要是这个邮箱自己的地址或别名")
                account["defaultFrom"] = None if sender == account["address"] else sender
            if password is not None:
                account.update(password=password, status="active", lastError=None)
            return dict(account)

    def mark(self, account_id: str, *, error: Optional[str]) -> None:
        """登录失败了：标成要重新连接（换授权码后恢复）。"""
        with self._change() as state:
            account = state["accounts"].get(account_id)
            if account is not None:
                account.update(status="needs_reconnect" if error else "active", lastError=error)

    def remove(self, account_id: str) -> None:
        with self._change() as state:
            if state["accounts"].pop(account_id, None) is None:
                raise MailboxError(404, "not_found", "没有这个邮箱")
            state["grants"].pop(account_id, None)

    # ── agent 的授权 ──────────────────────────────────────────────────────────

    def grants_of(self, staff_id: str) -> Dict[str, Dict[str, Any]]:
        """这个 agent 在各个邮箱上的授权：{邮箱 id: {level, policy}}。没授权的不在里面。"""
        with _LOCK:
            state = self._load()
        return {account_id: dict(grants[staff_id]) for account_id, grants in state["grants"].items()
                if staff_id in grants and account_id in state["accounts"]}

    def agents_on(self, account_id: str) -> Dict[str, Dict[str, Any]]:
        with _LOCK:
            return dict(self._load()["grants"].get(account_id) or {})

    def grant(self, account_id: str, staff_id: str, level: Any, can_delete: Any, policy: Any) -> Dict[str, Any]:
        if level not in ("none", "read", "read_write"):
            raise MailboxError(400, "invalid", "级别只能是 none、read、read_write")
        if can_delete is not False:
            raise MailboxError(422, "mail.delete_unavailable", "这里的 agent 不删信")
        with self._change() as state:
            account = state["accounts"].get(account_id)
            if account is None:
                raise MailboxError(404, "not_found", "没有这个邮箱")
            grants = state["grants"].setdefault(account_id, {})
            if level == "none":
                grants.pop(staff_id, None)
                return {"level": "none", "policy": check_policy(policy, account)}
            grants[staff_id] = {"level": level, "policy": check_policy(policy, account)}
            return dict(grants[staff_id])

    def forget_agent(self, staff_id: str) -> None:
        with self._change() as state:
            for grants in state["grants"].values():
                grants.pop(staff_id, None)
