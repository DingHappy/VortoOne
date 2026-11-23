"""所有者在新版界面里填的第三方服务密钥（GIPHY、语音转文字）：一个 0600 的文件。

存在 ``.vortoone/provider_keys.json``；整个 ``.vortoone`` 对 agent 的文件工具是敏感路径。
密钥从不回给界面。读不出来的记录如实报告为「无效」，下一次保存整个换掉。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Tuple

from src.utils.state_dir import state_dir

_FILE = "provider_keys.json"
_LOCK = threading.Lock()


def path(root: str) -> Path:
    return state_dir(root) / _FILE


def read(root: str) -> Tuple[Dict[str, Any], bool]:
    """全部记录，以及文件是否读得出来。"""
    try:
        data = json.loads(path(root).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, True
    except (OSError, ValueError):
        return {}, False
    return (data, True) if isinstance(data, dict) else ({}, False)


def update(root: str, change: Callable[[Dict[str, Any]], None]) -> None:
    target = path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        data, _readable = read(root)
        change(data)
        tmp = target.with_name(target.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
