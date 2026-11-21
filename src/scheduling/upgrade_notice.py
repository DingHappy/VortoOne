"""把安装命令记下的升级结果告诉所有者（HOME_AND_MEMBERS_DESIGN 3.2「退回时在 Desktop 里说明原因」）。

安装命令（deploy/install.sh）每次升级都把结果写进 ``<安装目录>/upgrade.json``，并在 run.sh 里用
``VORTOONE_UPGRADE_FILE`` 告诉服务它在哪。服务启动时读一次：没报过的结果写进通知台账（界面的通知里就看得到），
每个结果只报一次（按它的时间记在 ``.vortoone/upgrade-notice.json``）。读不出来就什么都不做，不影响启动。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from src.utils.state_dir import state_dir

ENV = "VORTOONE_UPGRADE_FILE"
_SEEN = "upgrade-notice.json"


def _text(result: dict) -> Optional[str]:
    to, frm = str(result.get("to") or "")[:40], str(result.get("from") or "")[:40]
    if result.get("ok") is True:
        return f"VortoOne 已从 {frm} 升级到 {to}。" if frm and to else None
    if result.get("ok") is False:
        reason = str(result.get("reason") or "原因不明")[:300]
        backup = str(result.get("backup") or "")[:300]
        return (f"VortoOne 升级到 {to} 没有成功，已经退回 {frm}，数据也回到了升级前。原因：{reason}。"
                + (f"升级前的备份在 {backup}。" if backup else ""))
    return None


def report(root: str, path: Optional[str] = None) -> Optional[str]:
    """有没报过的升级结果就写进通知台账，返回写进去的那句话；没有返回 None。"""
    source = path or os.getenv(ENV, "").strip()
    if not source:
        return None
    try:
        result = json.loads(Path(source).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(result, dict) or not result.get("at"):
        return None
    seen_path = state_dir(root) / _SEEN
    try:
        seen = json.loads(seen_path.read_text(encoding="utf-8")).get("at")
    except (OSError, ValueError, AttributeError):
        seen = None
    if seen == result["at"]:
        return None
    text = _text(result)
    if text is None:
        return None
    from src.scheduling.notices import record_notice
    record_notice(root, text, source="upgrade")
    try:
        seen_path.parent.mkdir(parents=True, exist_ok=True)
        seen_path.write_text(json.dumps({"at": result["at"]}), encoding="utf-8")
    except OSError:
        pass
    return text
