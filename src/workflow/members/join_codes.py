"""凭邀请链接申请时的邮箱验证码（主人 2026-11-23 定：保留这个开关）。

开关默认关。要开：环境变量 ``VORTOONE_JOIN_EMAIL_CODES=1``，并配好发信
（``VORTOONE_SMTP_URL=smtp+starttls://用户名:密码@主机:端口``、``VORTOONE_MAIL_FROM=发件地址``）。
两样都有才算开着；只开开关、没配发信，照样当关着（发不出去的验证码不假装发了）。

- 验证码 6 位，10 分钟有效，同一个邮箱 60 秒内只能发一次、一小时最多 5 次，整个服务 10 分钟最多 30 封；
  输错 5 次作废。只存摘要，只在进程内存里（服务重启，没用掉的作废）。
- 验对了，这个邮箱在这条链接上 30 分钟内算「已证实」，申请时就不用再输。
"""
from __future__ import annotations

import hashlib
import os
import secrets
import smtplib
import threading
import time
from email.message import EmailMessage
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

CODE_SECONDS, RESEND_SECONDS, PROOF_SECONDS = 600, 60, 1800
HOURLY_PER_EMAIL, GLOBAL_WINDOW, GLOBAL_MAX, TRIES = 5, 600, 30, 5


class CodeError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 409, retry_in: Optional[int] = None):
        super().__init__(message)
        self.code, self.status, self.retry_in = code, status, retry_in


def _sender_settings() -> Optional[Tuple[str, int, str, str, bool, str]]:
    url, sender = os.environ.get("VORTOONE_SMTP_URL", ""), os.environ.get("VORTOONE_MAIL_FROM", "")
    if not url or not sender:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in ("smtp", "smtps", "smtp+starttls") or not parsed.hostname:
        return None
    port = parsed.port or (465 if parsed.scheme == "smtps" else 587)
    return (parsed.hostname, port, unquote(parsed.username or ""), unquote(parsed.password or ""),
            parsed.scheme == "smtps", sender)


def enabled() -> bool:
    """开关开着、发信也配好了。"""
    return os.environ.get("VORTOONE_JOIN_EMAIL_CODES", "") == "1" and _sender_settings() is not None


def smtp_send(to: str, subject: str, text: str) -> None:
    settings = _sender_settings()
    if settings is None:
        raise CodeError("join.codes_off", "这个家没有配好发信", 503)
    host, port, user, password, implicit, sender = settings
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = sender, to, subject
    message.set_content(text)
    client = smtplib.SMTP_SSL(host, port, timeout=15) if implicit else smtplib.SMTP(host, port, timeout=15)
    with client:
        if not implicit:
            client.starttls()
        if user:
            client.login(user, password)
        client.send_message(message)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class JoinCodes:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic, send: Callable[[str, str, str], None] = smtp_send):
        self._clock, self._send = clock, send
        self._lock = threading.Lock()
        self._codes: Dict[Tuple[str, str], Tuple[str, float, int]] = {}    # (链接, 邮箱) → (摘要, 过期, 剩余次数)
        self._sent: Dict[str, List[float]] = {}                            # 邮箱 → 发送时刻
        self._all: List[float] = []
        self._proofs: Dict[Tuple[str, str], float] = {}                    # (链接, 邮箱) → 证实到何时

    def send(self, link: str, email: str, org_name: str) -> int:
        """发一封验证码，返回多少秒后才能再发。"""
        if not enabled():
            raise CodeError("join.codes_off", "这个家没有开邮箱验证码，直接提交申请就行")
        return self.send_code(link, email, f"加入「{org_name}」的验证码",
                              "你的验证码是 {code}，10 分钟内有效。\n不是你在申请的话，忽略这封邮件就好。")

    def send_code(self, key: str, email: str, subject: str, text: str) -> int:
        """按 ``key`` 发一封验证码（``text`` 里的 ``{code}`` 换成验证码），返回多少秒后才能再发。
        频率限制和别的信共用；``check(key, email, code)`` 核对。"""
        if not enabled():
            raise CodeError("join.codes_off", "这个家没有配好发信")
        now = self._clock()
        with self._lock:
            sent = [moment for moment in self._sent.get(email, []) if moment > now - 3600]
            if sent and sent[-1] > now - RESEND_SECONDS:
                raise CodeError("join.code_wait", "刚发过一封，请稍等", 429, int(sent[-1] + RESEND_SECONDS - now) + 1)
            self._all = [moment for moment in self._all if moment > now - GLOBAL_WINDOW]
            if len(sent) >= HOURLY_PER_EMAIL or len(self._all) >= GLOBAL_MAX:
                raise CodeError("join.code_wait", "发得太多了，请过一会儿再试", 429, RESEND_SECONDS)
            code = f"{secrets.randbelow(1_000_000):06d}"
            self._codes[(key, email)] = (_digest(code), now + CODE_SECONDS, TRIES)
            self._sent[email] = [*sent, now]
            self._all.append(now)
        self._send(email, subject, text.replace("{code}", code))
        return RESEND_SECONDS

    def mail(self, email: str, subject: str, text: str) -> bool:
        """发一封别的信（找回密码），和验证码共用频率限制。超了就不发、返回 False（调用方照样回「已发」）。"""
        if not enabled():
            raise CodeError("join.codes_off", "这个家没有配好发信")
        now = self._clock()
        with self._lock:
            sent = [moment for moment in self._sent.get(email, []) if moment > now - 3600]
            self._all = [moment for moment in self._all if moment > now - GLOBAL_WINDOW]
            if (sent and sent[-1] > now - RESEND_SECONDS) or len(sent) >= HOURLY_PER_EMAIL or len(self._all) >= GLOBAL_MAX:
                return False
            self._sent[email] = [*sent, now]
            self._all.append(now)
        self._send(email, subject, text)
        return True

    def check(self, link: str, email: str, code: str) -> None:
        now = self._clock()
        with self._lock:
            found = self._codes.get((link, email))
            if found is None or found[1] <= now:
                self._codes.pop((link, email), None)
                raise CodeError("join.code_expired", "验证码过期了，请重新发一封")
            digest, expires, tries = found
            if not isinstance(code, str) or not secrets.compare_digest(digest, _digest(code.strip())):
                if tries <= 1:
                    del self._codes[(link, email)]
                    raise CodeError("join.code_expired", "输错太多次，请重新发一封")
                self._codes[(link, email)] = (digest, expires, tries - 1)
                raise CodeError("join.code_wrong", "验证码不对", 400)
            del self._codes[(link, email)]
            self._proofs[(link, email)] = now + PROOF_SECONDS

    def proven(self, link: str, email: str, code: Optional[str] = None) -> bool:
        """这个邮箱在这条链接上证实过（或者带来的 code 对得上）。"""
        if code:
            try:
                self.check(link, email, code)
            except CodeError:
                return False
        with self._lock:
            return self._proofs.get((link, email), 0) > self._clock()


CODES = JoinCodes()
