"""agent 提议改团队背景，所有者批准（主人 2026-11-24 定：成员和 agent 写的内容一律按资料处理，据此可以开放）。

团队背景会放在每个 agent 每次对话的最前面，所以 agent 不能直接改：它只能提一份完整的新版本，
所有者看着前后对比决定（可以先改几个字再采纳）。读过外部内容的那一轮提的，记上一笔给所有者看。

- 每条记下提议时的背景原文（``before``）。背景后来改过，这条就「过时」，不能再采纳。
- 待决定的最多 ``MAX_PENDING`` 条，同样的新版本只记一条；一共留最近 ``KEEP`` 条。
- 谁让 agent 提的（成员，或所有者自己）记在 ``initiator``：成员只看得到自己发起的，可以「请所有者看看」一次。
"""
from __future__ import annotations

import json
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from src.utils.state_dir import state_dir

MAX_PENDING, KEEP = 20, 200
MAX_TITLE, MAX_WHY = 80, 600
_FILE = "context_proposals.json"
_LOCK = threading.RLock()


class ProposalError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.status = code, status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ContextProposals:
    def __init__(self, root: str):
        self.root = str(root)
        self.directory = state_dir(root) / "members"

    def _load(self) -> Dict[str, Any]:
        path = self.directory / _FILE
        if not path.exists():
            return {"version": 1, "proposals": []}
        if path.is_symlink() or path.stat().st_size > 4 * 1024 * 1024:
            raise ProposalError("unavailable", "提议记录无效或过大", 503)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeError, ValueError) as error:
            raise ProposalError("unavailable", "提议记录损坏", 503) from error
        if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("proposals"), list):
            raise ProposalError("unavailable", "提议记录结构无效", 503)
        return value

    def _save(self, state: Dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / _FILE
        if path.is_symlink():
            raise ProposalError("unavailable", "提议记录不能是符号链接", 503)
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

    def list(self) -> List[Dict[str, Any]]:
        with _LOCK:
            return [dict(item) for item in self._load()["proposals"]]

    def get(self, proposal_id: str) -> Dict[str, Any]:
        found = next((item for item in self.list() if item["id"] == proposal_id), None)
        if found is None:
            raise ProposalError("not_found", "没有这条提议", 404)
        return found

    # ── agent 提 ──────────────────────────────────────────────────────────────

    def propose(self, *, by: str, initiator: Optional[str], task: str, title: Any, why: Any, after: Any,
                tainted: bool) -> Dict[str, Any]:
        from src.memory.assistant_profile import MAX_CONTEXT, load_home_context

        if not isinstance(after, str) or not after.strip() or len(after.strip()) > MAX_CONTEXT:
            raise ValueError(f"新的团队背景要写完整的一份，1～{MAX_CONTEXT} 字")
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > MAX_TITLE:
            raise ValueError(f"标题要 1～{MAX_TITLE} 字")
        if why is not None and (not isinstance(why, str) or len(why) > MAX_WHY):
            raise ValueError(f"理由最多 {MAX_WHY} 字")
        before = load_home_context(self.root)
        text = after.replace("\0", "").strip()
        if text == before.strip():
            raise ValueError("和现在的团队背景一样，不用提")
        with self._change() as state:
            pending = [item for item in state["proposals"] if item["status"] == "pending"]
            same = next((item for item in pending if item["after"] == text and item["before"] == before), None)
            if same is not None:
                return dict(same)
            if len(pending) >= MAX_PENDING:
                raise ValueError(f"待所有者决定的提议已经有 {MAX_PENDING} 条，先等等")
            item = {"id": "prop-" + uuid.uuid4().hex[:16], "target": "org_context", "by": str(by),
                    "initiator": initiator, "task": str(task), "title": title.strip(), "why": (why or "").strip(),
                    "before": before, "after": text, "tainted": bool(tainted), "status": "pending",
                    "askedAt": None, "createdAt": _now(), "decidedBy": None, "decidedAt": None}
            state["proposals"] = [*state["proposals"], item][-KEEP:]
            return dict(item)

    # ── 人决定 ────────────────────────────────────────────────────────────────

    @staticmethod
    def stale(item: Dict[str, Any], current: str) -> bool:
        return item["status"] == "pending" and item["before"] != current

    def _pending(self, state: Dict[str, Any], proposal_id: str) -> Dict[str, Any]:
        item = next((row for row in state["proposals"] if row["id"] == proposal_id), None)
        if item is None:
            raise ProposalError("not_found", "没有这条提议", 404)
        if item["status"] != "pending":
            raise ProposalError("proposal.decided", "这条提议已经决定过了")
        return item

    def accept(self, proposal_id: str, decided_by: str, final_text: Optional[str] = None) -> Dict[str, Any]:
        """采纳：团队背景换成提议的新版本（或所有者改过的 ``final_text``）。背景后来改过的不能采纳。"""
        from src.memory.assistant_profile import MAX_CONTEXT, load_home_context, save_home_context

        if final_text is not None and (not isinstance(final_text, str) or not final_text.strip()
                                       or len(final_text.strip()) > MAX_CONTEXT):
            raise ProposalError("invalid", f"团队背景要 1～{MAX_CONTEXT} 字", 400)
        with self._change() as state:
            item = self._pending(state, proposal_id)
            if self.stale(item, load_home_context(self.root)):
                raise ProposalError("proposal.stale", "团队背景在这条提议之后改过了，不能再采纳")
            text = (final_text if final_text is not None else item["after"]).replace("\0", "").strip()
            save_home_context(self.root, text)
            item.update(status="accepted", decidedBy=decided_by, decidedAt=_now(), after=text)
            return dict(item)

    def refuse(self, proposal_id: str, decided_by: str) -> Dict[str, Any]:
        with self._change() as state:
            item = self._pending(state, proposal_id)
            item.update(status="refused", decidedBy=decided_by, decidedAt=_now())
            return dict(item)

    def ask(self, proposal_id: str, member_id: str) -> Dict[str, Any]:
        """发起的成员请所有者看看（一次）。"""
        with self._change() as state:
            item = self._pending(state, proposal_id)
            if item["initiator"] != member_id:
                raise ProposalError("not_found", "没有这条提议", 404)
            if not item["askedAt"]:
                item["askedAt"] = _now()
            return dict(item)
