"""新版界面的语音消息：语音转文字的服务商、录音的存放与回放、上传表单的解析。

- 服务商：所有者在设置里选一家并填 Key（存 provider_keys.json，0600，只回末 4 位）。
  OpenAI 格式的四家（openai / groq / deepinfra / local）和 ElevenLabs 真去转写；
  AssemblyAI 与 Soniox 是先上传再轮询的接口，还没接，选它们会被拒绝。
- 录音：存 ``.vortoone/voice/``（0600），30 天后过期；回放前核对看的人能不能读那个对话。
- 转写出来的文字是消息正文，给 agent 看；人看到的是语音条（meta.voice）。录音本身不进模型上下文。
- 上传表单用标准库解析（不引入 python-multipart），先限大小再解析。
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from email.parser import BytesParser
from email.policy import HTTP
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from src.utils.state_dir import state_dir
from src.web import provider_keys

PROVIDERS = ("assemblyai", "elevenlabs", "soniox", "groq", "openai", "local", "deepinfra")
_OPENAI_FORMAT = {"openai": ("https://api.openai.com/v1", "whisper-1"),
                  "groq": ("https://api.groq.com/openai/v1", "whisper-large-v3-turbo"),
                  "deepinfra": ("https://api.deepinfra.com/v1/openai", "openai/whisper-large-v3-turbo"),
                  "local": ("", "whisper-1")}
_ELEVENLABS = "https://api.elevenlabs.io/v1"
_NOT_YET = ("assemblyai", "soniox")

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_MS, MIN_MS, PEAKS = 5 * 60_000, 700, 48
KEEP_DAYS = 30
_CLIP = re.compile(r"[A-Za-z0-9_-]{16,64}")
_LOCK = threading.Lock()
# 收的录音格式：按文件头认，不信声明的类型
_AUDIO_TYPES = {"webm": "audio/webm", "mp4": "audio/mp4", "ogg": "audio/ogg", "wav": "audio/wav",
                "mpeg": "audio/mpeg"}


class VoiceError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


# ── 服务商与密钥 ──────────────────────────────────────────────────────────────

def _saved(root: str) -> Tuple[Optional[Dict[str, Any]], bool]:
    data, readable = provider_keys.read(root)
    if not readable:
        return None, False
    speech = data.get("speech")
    if speech is None:
        return None, True
    if (not isinstance(speech, dict) or speech.get("provider") not in PROVIDERS
            or not all(isinstance(speech.get(key, ""), str) for key in ("apiKey", "baseUrl", "model"))):
        return None, False
    return speech, True


def key_view(root: str) -> Dict[str, Any]:
    speech, readable = _saved(root)
    if not readable:
        return {"provider": None, "hint": None, "state": "invalid", "envProviders": []}
    if speech is None:
        return {"provider": None, "hint": None, "state": "off", "envProviders": []}
    hint = speech["apiKey"][-4:] if speech.get("apiKey") else _host(speech.get("baseUrl", ""))
    return {"provider": speech["provider"], "hint": hint or None, "state": "ok", "envProviders": []}


def available(root: str) -> bool:
    return key_view(root)["state"] == "ok"


def _host(url: str) -> str:
    return urlsplit(url).netloc if url else ""


def check_input(body: Any) -> Dict[str, str]:
    """界面的 SpeechKeyInput → 要存的记录。"""
    if not isinstance(body, dict) or set(body) - {"provider", "apiKey", "baseUrl", "model"}:
        raise VoiceError(400, "invalid", "只能给 provider、apiKey、baseUrl、model")
    provider = body.get("provider")
    if provider not in PROVIDERS:
        raise VoiceError(400, "invalid", "不认识的语音服务")
    if provider in _NOT_YET:
        raise VoiceError(400, "voice.provider_unsupported", "这台服务还不支持这家语音服务，可以选 OpenAI、Groq、"
                                                             "DeepInfra、ElevenLabs 或本地服务")
    clean = {"provider": provider}
    for key in ("apiKey", "baseUrl", "model"):
        value = body.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > 500):
            raise VoiceError(400, "invalid", f"{key} 无效")
        if value and value.strip():
            clean[key] = value.strip()
    if provider == "local":
        parts = urlsplit(clean.get("baseUrl", ""))
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username:
            raise VoiceError(400, "invalid", "本地服务要给 http(s) 地址")
        clean["baseUrl"] = clean["baseUrl"].rstrip("/")
    elif not clean.get("apiKey"):
        raise VoiceError(400, "invalid", "要填这家服务的 API Key")
    elif "baseUrl" in clean:
        raise VoiceError(400, "invalid", "只有本地服务能指定地址")
    return clean


def save(root: str, record: Dict[str, str]) -> None:
    provider_keys.update(root, lambda data: data.__setitem__("speech", record))


def remove(root: str) -> None:
    provider_keys.update(root, lambda data: data.pop("speech", None))


def _endpoint(record: Dict[str, str]) -> Tuple[str, str, Dict[str, str]]:
    """转写地址、模型、请求头。"""
    provider = record["provider"]
    key = record.get("apiKey", "")
    if provider == "elevenlabs":
        return _ELEVENLABS + "/speech-to-text", record.get("model") or "scribe_v1", {"xi-api-key": key}
    base, model = _OPENAI_FORMAT[provider]
    base = record.get("baseUrl") or base
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    return base + "/audio/transcriptions", record.get("model") or model, headers


async def _http(method: str, url: str, *, headers: Dict[str, str], form: Any = None,
                timeout: float = 60) -> Tuple[int, Any]:
    import aiohttp
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            async with session.request(method, url, headers=headers, data=form, allow_redirects=False) as response:
                try:
                    payload = await response.json(content_type=None)
                except Exception:  # noqa: BLE001 —— 不是 JSON 也只看状态码
                    payload = None
                return response.status, payload
    except Exception as error:  # noqa: BLE001
        raise VoiceError(502, "voice.unavailable", "连不上语音服务") from error


async def verify(record: Dict[str, str]) -> None:
    """不花钱地试一下 Key：OpenAI 格式查模型列表，ElevenLabs 查账号。"""
    url, _model, headers = _endpoint(record)
    probe = (_ELEVENLABS + "/user") if record["provider"] == "elevenlabs" else url.rsplit("/audio/", 1)[0] + "/models"
    status, _payload = await _http("GET", probe, headers=headers, timeout=15)
    if status in (401, 403):
        raise VoiceError(400, "voice.key_invalid", "语音服务不认这个 Key")
    if record["provider"] != "local" and status >= 400:
        raise VoiceError(502, "voice.unavailable", f"语音服务返回了 {status}")


async def transcribe(root: str, audio: bytes, content_type: str, language: str = "") -> str:
    import aiohttp
    record, _readable = _saved(root)
    if record is None:
        raise VoiceError(422, "voice.off", "这台服务还没有设置语音转文字")
    url, model, headers = _endpoint(record)
    form = aiohttp.FormData()
    form.add_field("file", audio, filename="voice." + _extension(content_type), content_type=content_type)
    form.add_field("model_id" if record["provider"] == "elevenlabs" else "model", model)
    if re.fullmatch(r"[a-z]{2}", language or ""):
        form.add_field("language_code" if record["provider"] == "elevenlabs" else "language", language)
    status, payload = await _http("POST", url, headers=headers, form=form, timeout=120)
    if status in (401, 403):
        raise VoiceError(502, "voice.key_invalid", "语音服务不认这个 Key，请所有者在设置里重新填")
    if status >= 400 or not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
        raise VoiceError(502, "voice.unavailable", f"语音服务没能转写（{status}）")
    return " ".join(payload["text"].split())[:20000]


# ── 上传表单 ─────────────────────────────────────────────────────────────────

def parse_form(content_type: str, body: bytes) -> Tuple[Dict[str, str], Optional[Tuple[bytes, str]]]:
    """multipart/form-data → (文字字段, (文件字节, 文件声明的类型))。"""
    if not content_type.lower().startswith("multipart/form-data") or "boundary=" not in content_type:
        raise VoiceError(400, "invalid", "要用 multipart/form-data 上传")
    message = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n"
                                                  + body)
    if not message.is_multipart():
        raise VoiceError(400, "invalid", "上传的表单无效")
    fields: Dict[str, str] = {}
    upload = None
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not isinstance(name, str):
            continue
        payload = part.get_payload(decode=True) or b""
        if name == "file":
            upload = (payload, part.get_content_type())
        elif len(payload) <= 8192:
            fields[name] = payload.decode("utf-8", "replace")
    return fields, upload


def sniff(audio: bytes) -> Optional[str]:
    if audio.startswith(b"\x1a\x45\xdf\xa3"):
        return _AUDIO_TYPES["webm"]
    if audio[4:8] == b"ftyp":
        return _AUDIO_TYPES["mp4"]
    if audio.startswith(b"OggS"):
        return _AUDIO_TYPES["ogg"]
    if audio.startswith(b"RIFF") and audio[8:12] == b"WAVE":
        return _AUDIO_TYPES["wav"]
    if audio.startswith(b"ID3") or audio[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return _AUDIO_TYPES["mpeg"]
    return None


def _extension(content_type: str) -> str:
    return {"audio/webm": "webm", "audio/mp4": "m4a", "audio/ogg": "ogg", "audio/wav": "wav",
            "audio/mpeg": "mp3"}.get(content_type, "bin")


def check_fields(fields: Dict[str, str]) -> Dict[str, Any]:
    try:
        duration = int(float(fields.get("durationMs", "")))
        peaks = json.loads(fields.get("peaks") or "[]")
    except (ValueError, TypeError) as error:
        raise VoiceError(400, "invalid", "录音的时长或波形无效") from error
    if not MIN_MS <= duration <= MAX_MS:
        raise VoiceError(400, "voice.length", "录音要在 0.7 秒到 5 分钟之间")
    if not isinstance(peaks, list) or len(peaks) > PEAKS or any(
            type(p) not in (int, float) or not 0 <= p <= 1 for p in peaks):
        raise VoiceError(400, "invalid", "波形无效")
    client = fields.get("clientId", "").strip()
    if not client or len(client) > 120:
        raise VoiceError(400, "invalid", "缺 clientId")
    return {"durationMs": duration, "peaks": [round(float(p), 3) for p in peaks], "clientId": client,
            "threadId": fields.get("threadId") or None, "replyTo": fields.get("replyTo") or None,
            "language": (fields.get("language") or "")[:2].lower()}


# ── 录音的存放 ────────────────────────────────────────────────────────────────

def _dir(root: str) -> Path:
    return state_dir(root) / "voice"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def _write(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def store_clip(root: str, audio: bytes, content_type: str, *, channel: str, scope: str, author: str,
               client: str) -> Dict[str, Any]:
    folder = _dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    _forget_expired(root)
    clip_id = secrets.token_urlsafe(18)
    expires = _now() + timedelta(days=KEEP_DAYS)
    record = {"id": clip_id, "channel": channel, "scope": scope, "author": author, "client": client,
              "contentType": content_type, "createdAt": _stamp(_now()), "expiresAt": _stamp(expires)}
    _write(folder / f"{clip_id}.audio", audio)
    _write(folder / f"{clip_id}.json", json.dumps(record).encode("utf-8"))
    return record


def clip(root: str, clip_id: str) -> Optional[Dict[str, Any]]:
    if not _CLIP.fullmatch(clip_id or ""):
        return None
    try:
        record = json.loads((_dir(root) / f"{clip_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) and record.get("id") == clip_id else None


def expired(record: Dict[str, Any]) -> bool:
    try:
        return datetime.fromisoformat(str(record["expiresAt"]).replace("Z", "+00:00")) <= _now()
    except (KeyError, ValueError):
        return True


def audio(root: str, record: Dict[str, Any]) -> Optional[bytes]:
    try:
        return (_dir(root) / f"{record['id']}.audio").read_bytes()
    except OSError:
        return None


def find_clip(root: str, *, channel: str, author: str, client: str) -> Optional[Dict[str, Any]]:
    """同一个人在同一个对话里用同一个 clientId 发过的录音（界面重试时用）。"""
    for path in _dir(root).glob("*.json"):
        if path.name == "main_index.json":
            continue
        record = clip(root, path.stem)
        if record and (record.get("channel"), record.get("author"), record.get("client")) == (channel, author, client):
            return record
    return None


def drop_clip(root: str, clip_id: str) -> None:
    for suffix in (".audio", ".json"):
        try:
            (_dir(root) / f"{clip_id}{suffix}").unlink()
        except OSError:
            pass


def _forget_expired(root: str) -> None:
    """过期的录音删掉音频，留下记录（回放回 410 而不是 404）。"""
    folder = _dir(root)
    for path in folder.glob("*.json"):
        record = clip(root, path.stem) if path.name != "main_index.json" else None
        if record and expired(record):
            try:
                (folder / f"{record['id']}.audio").unlink()
            except OSError:
                pass


def voice_meta(record: Dict[str, Any], fields: Dict[str, Any], *, no_words: bool = False) -> Dict[str, Any]:
    meta = {"clipId": record["id"], "durationMs": fields["durationMs"], "peaks": fields["peaks"],
            "expiresAt": record["expiresAt"]}
    if no_words:
        meta["noWords"] = True
    return meta


# ── 主助理私聊里的语音条：消息 id → meta.voice ──────────────────────────────────
# 主要聊天的记录是会话本身（只有文字），语音条另记一份，读的时候按消息 id 附上。

def _index_path(root: str) -> Path:
    return _dir(root) / "main_index.json"


def main_voices(root: str) -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads(_index_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def remember_main_voice(root: str, message_id: str, meta: Dict[str, Any]) -> None:
    with _LOCK:
        data = main_voices(root)
        data[message_id] = meta
        path = _index_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.replace(tmp, path)


def attach_main_voices(root: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    voices = main_voices(root)
    if not voices:
        return rows
    for row in rows:
        found = voices.get(row.get("id"))
        if found:
            row["meta"] = {**(row.get("meta") or {}), "voice": found}
    return rows

