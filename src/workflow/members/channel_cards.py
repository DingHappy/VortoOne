"""团队频道里 agent 发的投票和选择卡（主人 2026-11-22 定：等 agent 进了团队频道再做）。

单独一个文件：旧版本不认识它，也就不会读写它，回滚不影响频道消息。
卡片属于一个频道、一件 agent 的事；交回回答时挂到那条消息上（``attach``），之后跟着消息显示。
谁能看、能投、能答由 TeamChannels 按频道权限判定，这里只管存取和内容校验。
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from src.workflow.members.registry import Damaged

CARD_ID_PATTERN = r"blk_[a-f0-9]{32}"
MAX_OPTIONS = 10
MAX_QUESTIONS = 4
MAX_CHOICE_OPTIONS = 6
TEXT_LIMIT = 300
OTHER_LIMIT = 1000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('poll', 'choices')),
    channel TEXT NOT NULL,
    thread TEXT NOT NULL,
    task TEXT NOT NULL,
    staff TEXT NOT NULL,
    content TEXT NOT NULL,
    created TEXT NOT NULL,
    closes TEXT,
    message TEXT
);
CREATE INDEX IF NOT EXISTS cards_by_message ON cards(message);
CREATE INDEX IF NOT EXISTS cards_by_task ON cards(task);
CREATE TABLE IF NOT EXISTS votes (
    card TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    person TEXT NOT NULL,
    option INTEGER NOT NULL,
    PRIMARY KEY(card, person, option)
);
CREATE TABLE IF NOT EXISTS answers (
    card TEXT PRIMARY KEY REFERENCES cards(id) ON DELETE CASCADE,
    person TEXT NOT NULL,
    picks TEXT NOT NULL,
    others TEXT,
    message TEXT,
    created TEXT NOT NULL
);
"""


class CardConflict(ValueError):
    """界面认得的冲突：投票已截止（poll.closed）、选择卡已经有人答了（choices.answered）。"""
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _text(value: Any, label: str, limit: int = TEXT_LIMIT) -> str:
    text = " ".join(str(value).split()) if isinstance(value, str) else ""
    if not 1 <= len(text) <= limit:
        raise ValueError(f"{label}要 1～{limit} 个字")
    return text


def poll_content(question: Any, options: Any, multiple: Any) -> Dict[str, Any]:
    if not isinstance(options, list) or not 2 <= len(options) <= MAX_OPTIONS:
        raise ValueError(f"投票要有 2～{MAX_OPTIONS} 个选项")
    labels = [_text(option, "选项", 80) for option in options]
    if len(set(labels)) != len(labels):
        raise ValueError("投票的选项不能重复")
    if not isinstance(multiple, bool):
        raise ValueError("multiple 要是真或假")
    return {"kind": "poll", "question": _text(question, "投票的问题"), "options": labels, "multiple": multiple}


def choices_content(questions: Any) -> Dict[str, Any]:
    if not isinstance(questions, list) or not 1 <= len(questions) <= MAX_QUESTIONS:
        raise ValueError(f"选择卡要有 1～{MAX_QUESTIONS} 个问题")
    shown = []
    for item in questions:
        if not isinstance(item, dict) or not isinstance(item.get("options"), list) \
                or not 2 <= len(item["options"]) <= MAX_CHOICE_OPTIONS:
            raise ValueError(f"每个问题要有 2～{MAX_CHOICE_OPTIONS} 个选项")
        options = []
        for option in item["options"]:
            label = option.get("label") if isinstance(option, dict) else option
            entry = {"label": _text(label, "选项", 80)}
            if isinstance(option, dict) and option.get("description"):
                entry["description"] = _text(option["description"], "选项说明", 200)
            options.append(entry)
        shown.append({"question": _text(item.get("question"), "问题"), "options": options})
    return {"kind": "choices", "questions": shown}


class ChannelCards:
    def __init__(self, directory: Path):
        self.path = Path(directory) / "channel-cards.sqlite3"

    @contextmanager
    def database(self, *, write: bool = False) -> Iterator[Optional[sqlite3.Connection]]:
        if self.path.is_symlink():
            raise Damaged("频道卡片记录不能是符号链接")
        if not write and not self.path.exists():
            yield None
            return
        if write and not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            os.close(fd)
        try:
            mode = "rw" if write else "ro"
            with closing(sqlite3.connect(self.path.resolve().as_uri() + f"?mode={mode}", uri=True)) as conn, conn:
                conn.row_factory = sqlite3.Row
                if write:
                    conn.execute("PRAGMA foreign_keys = ON")
                    conn.executescript("BEGIN;" + _SCHEMA + "COMMIT;")
                yield conn
        except sqlite3.Error as error:
            raise Damaged("频道卡片记录不可用") from error

    def create(self, kind: str, *, channel: str, thread: str, task: str, staff: str,
               content: Dict[str, Any], hours: Optional[int] = None) -> str:
        if kind == "poll":
            content = poll_content(content.get("question"), content.get("options"), content.get("multiple", False))
            if type(hours) is not int or not 1 <= hours <= 168:
                raise ValueError("投票的截止时间要在 1～168 小时之内")
        elif kind == "choices":
            content = choices_content(content.get("questions"))
        else:
            raise ValueError("不认识的卡片")
        card = "blk_" + uuid.uuid4().hex
        now = _now()
        with self.database(write=True) as conn:
            if conn.execute("SELECT count(*) FROM cards WHERE task = ?", (task,)).fetchone()[0] >= 3:
                raise ValueError("一件事最多发 3 张卡片")
            conn.execute("INSERT INTO cards(id, kind, channel, thread, task, staff, content, created, closes) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (card, kind, channel, thread, task, staff, json.dumps(content, ensure_ascii=False), _stamp(now),
                          _stamp(now + timedelta(hours=hours)) if kind == "poll" else None))
        return card

    def attach(self, task: str, message: str) -> None:
        """回答发出去了：这件事的卡片挂到那条消息上（只挂一次）。"""
        with self.database(write=True) as conn:
            conn.execute("UPDATE cards SET message = ? WHERE task = ? AND message IS NULL", (message, task))

    def by_message(self, messages: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        found: Dict[str, List[Dict[str, Any]]] = {}
        if not messages:
            return found
        with self.database() as conn:
            if conn is None:
                return found
            marks = ",".join("?" * len(messages))
            for row in conn.execute(f"SELECT * FROM cards WHERE message IN ({marks}) ORDER BY created", messages):
                found.setdefault(row["message"], []).append(self._card(row))
        return found

    def get(self, card: str) -> Optional[Dict[str, Any]]:
        with self.database() as conn:
            row = conn.execute("SELECT * FROM cards WHERE id = ?", (card,)).fetchone() if conn is not None else None
            return self._card(row) if row is not None else None

    @staticmethod
    def _card(row) -> Dict[str, Any]:
        return {"id": row["id"], "kind": row["kind"], "channel": row["channel"], "thread": row["thread"],
                "task": row["task"], "staff": row["staff"], "content": json.loads(row["content"]),
                "created": row["created"], "closes": row["closes"], "message": row["message"]}

    def votes(self, card: str) -> List[Dict[str, Any]]:
        with self.database() as conn:
            if conn is None:
                return []
            return [{"person": row["person"], "option": row["option"]}
                    for row in conn.execute("SELECT person, option FROM votes WHERE card = ? ORDER BY rowid", (card,))]

    def set_votes(self, card: str, person: str, options: List[int]) -> None:
        with self.database(write=True) as conn:
            conn.execute("DELETE FROM votes WHERE card = ? AND person = ?", (card, person))
            conn.executemany("INSERT INTO votes(card, person, option) VALUES (?, ?, ?)",
                             [(card, person, option) for option in options])

    def answer(self, card: str) -> Optional[Dict[str, Any]]:
        with self.database() as conn:
            row = conn.execute("SELECT * FROM answers WHERE card = ?", (card,)).fetchone() if conn is not None else None
        if row is None:
            return None
        return {"person": row["person"], "picks": json.loads(row["picks"]),
                "others": json.loads(row["others"]) if row["others"] else None,
                "message": row["message"], "created": row["created"]}

    def claim_answer(self, card: str, person: str, picks: List[int], others: Optional[List[Optional[str]]]) -> bool:
        """先答的算：已经有人答过返回 False。"""
        with self.database(write=True) as conn:
            cursor = conn.execute("INSERT OR IGNORE INTO answers(card, person, picks, others, created) VALUES (?, ?, ?, ?, ?)",
                                  (card, person, json.dumps(picks), json.dumps(others) if others else None, _stamp(_now())))
            return cursor.rowcount == 1

    def answer_message(self, card: str, message: str) -> None:
        with self.database(write=True) as conn:
            conn.execute("UPDATE answers SET message = ? WHERE card = ?", (message, card))
