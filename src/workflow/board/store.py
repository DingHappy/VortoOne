"""看板的存储：``.vortoone/board.sqlite3``。

只存数据与做形状核对；谁能看、谁能改由调用方（web/app_board.py）按成员登记处的规矩判断。
每张卡片有空间内递增的编号（#1、#2…）。看板列固定属于五个阶段之一，卡片移到哪一列就取那一列的阶段。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from src.utils.state_dir import state_dir

STAGES = ("todo", "in_progress", "in_review", "done", "canceled")
PRIORITIES = ("low", "normal", "high")
MAX_TITLE, MAX_DESCRIPTION, MAX_CRITERIA, MAX_CRITERION = 200, 20000, 30, 500
MAX_LABELS, MAX_LABEL, MAX_COMMENT = 20, 40, 10000
_LOCK = threading.RLock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS issues (
    id TEXT PRIMARY KEY,
    number INTEGER NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    column_id TEXT,
    board_rank REAL NOT NULL DEFAULT 0,
    priority TEXT NOT NULL DEFAULT 'normal',
    project_id TEXT,
    assignee TEXT,
    labels TEXT NOT NULL DEFAULT '[]',
    due_date TEXT,
    criteria TEXT NOT NULL DEFAULT '[]',
    created_by TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '{"type": "manual"}',
    channel_id TEXT,
    audience TEXT NOT NULL DEFAULT '{"mode": "everyone", "userIds": [], "channelId": null}',
    github_url TEXT,
    parent_id TEXT,
    created TEXT NOT NULL,
    updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activity (
    id TEXT PRIMARY KEY,
    issue_id TEXT NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    author TEXT NOT NULL,
    text TEXT NOT NULL,
    event TEXT,
    params TEXT,
    auto INTEGER NOT NULL DEFAULT 0,
    created TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS activity_of_issue ON activity(issue_id, created);
CREATE TABLE IF NOT EXISTS columns (
    id TEXT PRIMARY KEY,
    stage TEXT NOT NULL,
    name TEXT,
    position INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    color TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0,
    created TEXT NOT NULL
);
"""


class BoardError(ValueError):
    """形状不对或引用不存在。"""


class NotFound(LookupError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _actor(value: Any) -> Dict[str, str]:
    if (not isinstance(value, dict) or set(value) != {"type", "id"} or value["type"] not in ("person", "agent")
            or not isinstance(value["id"], str) or not value["id"] or len(value["id"]) > 120):
        raise BoardError("负责人要写成 {type: person|agent, id}")
    return {"type": value["type"], "id": value["id"]}


def _text(value: Any, limit: int, what: str, *, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or "\0" in value:
        raise BoardError(f"{what}无效或超过 {limit} 字")
    clean = value.strip()
    if required and not clean:
        raise BoardError(f"{what}不能为空")
    return clean


def _labels(value: Any) -> List[str]:
    if not isinstance(value, list) or len(value) > MAX_LABELS:
        raise BoardError(f"标签最多 {MAX_LABELS} 个")
    return list(dict.fromkeys(_text(item, MAX_LABEL, "标签", required=True) for item in value))


def _due(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as error:
        raise BoardError("截止日期要写成 YYYY-MM-DD") from error


def _url(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    text = _text(value, 500, "链接")
    if not text.startswith("https://github.com/"):
        raise BoardError("只能链接到 GitHub")
    return text


class Board:
    def __init__(self, root: str):
        self.path = state_dir(root) / "board.sqlite3"

    # ── 连接 ──────────────────────────────────────────────────────────────────
    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        path = Path(self.path)
        if path.is_symlink():
            raise BoardError("看板记录不能是符号链接")
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600))
        with _LOCK, closing(sqlite3.connect(path)) as conn, conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.executescript("BEGIN;" + _SCHEMA + "COMMIT;")
            if conn.execute("SELECT count(*) FROM columns").fetchone()[0] == 0:
                conn.executemany("INSERT INTO columns(id, stage, name, position) VALUES(?, ?, NULL, ?)",
                                 [(f"col-{stage}", stage, index) for index, stage in enumerate(STAGES)])
            yield conn

    # ── 读 ────────────────────────────────────────────────────────────────────
    @staticmethod
    def _row(row: sqlite3.Row) -> Dict[str, Any]:
        return {"id": row["id"], "number": row["number"], "title": row["title"], "description": row["description"],
                "status": row["status"], "columnId": row["column_id"], "boardRank": row["board_rank"],
                "priority": row["priority"], "projectId": row["project_id"],
                "assignee": json.loads(row["assignee"]) if row["assignee"] else None,
                "labels": json.loads(row["labels"]), "dueDate": row["due_date"],
                "criteria": json.loads(row["criteria"]), "createdBy": json.loads(row["created_by"]),
                "source": json.loads(row["source"]), "channelId": row["channel_id"],
                "audience": json.loads(row["audience"]), "githubUrl": row["github_url"],
                "parentIssueId": row["parent_id"], "createdAt": row["created"], "updatedAt": row["updated"]}

    def issues(self) -> List[Dict[str, Any]]:
        with self._db() as conn:
            return [self._row(row) for row in conn.execute("SELECT * FROM issues ORDER BY board_rank, number")]

    def issue(self, issue_id: str) -> Dict[str, Any]:
        with self._db() as conn:
            row = conn.execute("SELECT * FROM issues WHERE id = ?", (issue_id,)).fetchone()
        if row is None:
            raise NotFound("没有这张卡片")
        return self._row(row)

    @contextmanager
    def _locked_audience(self) -> Iterator[sqlite3.Connection]:
        with self._db() as conn:
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            yield conn

    @contextmanager
    def locked_issue(self, issue_id: str) -> Iterator[Dict[str, Any]]:
        """Keep an issue's audience stable through an operation in a related store."""
        with self._locked_audience() as conn:
            row = conn.execute("SELECT * FROM issues WHERE id = ?", (issue_id,)).fetchone()
            if row is None:
                raise NotFound("没有这张卡片")
            yield self._row(row)

    @contextmanager
    def locked_issues(self, ids: set[str]) -> Iterator[Dict[str, Dict[str, Any]]]:
        """Snapshot several current issue audiences while projecting related state."""
        keys = sorted(ids)
        with self._locked_audience() as conn:
            issues = {}
            for offset in range(0, len(keys), 200):
                batch = keys[offset:offset + 200]
                markers = ", ".join("?" for _ in batch)
                for row in conn.execute(f"SELECT * FROM issues WHERE id IN ({markers})", batch):
                    issues[row["id"]] = self._row(row)
            yield issues

    def by_number(self, number: int) -> Dict[str, Any]:
        with self._db() as conn:
            row = conn.execute("SELECT * FROM issues WHERE number = ?", (number,)).fetchone()
        if row is None:
            raise NotFound("没有这张卡片")
        return self._row(row)

    def children(self, issue_id: str) -> List[Dict[str, Any]]:
        with self._db() as conn:
            return [self._row(row) for row in conn.execute(
                "SELECT * FROM issues WHERE parent_id = ? ORDER BY number", (issue_id,))]

    def columns(self) -> List[Dict[str, Any]]:
        with self._db() as conn:
            return [{"id": row["id"], "stage": row["stage"], "name": row["name"], "position": row["position"]}
                    for row in conn.execute("SELECT * FROM columns ORDER BY position, id")]

    def projects(self) -> List[Dict[str, Any]]:
        with self._db() as conn:
            return [{"id": row["id"], "name": row["name"], "color": row["color"], "archived": bool(row["archived"])}
                    for row in conn.execute("SELECT * FROM projects ORDER BY archived, name")]

    def activity(self, issue_id: str) -> List[Dict[str, Any]]:
        self.issue(issue_id)
        with self._db() as conn:
            return [{"id": row["id"], "kind": row["kind"], "author": json.loads(row["author"]), "text": row["text"],
                     **({"event": row["event"], "textCode": row["event"],
                         "textParams": json.loads(row["params"] or "{}")} if row["event"] else {}),
                     **({"auto": True} if row["auto"] else {}), "createdAt": row["created"]}
                    for row in conn.execute("SELECT * FROM activity WHERE issue_id = ? ORDER BY created, id",
                                            (issue_id,))]

    # ── 写：卡片 ──────────────────────────────────────────────────────────────
    def _column_for(self, conn, stage: str) -> str:
        row = conn.execute("SELECT id FROM columns WHERE stage = ? ORDER BY position LIMIT 1", (stage,)).fetchone()
        return row["id"] if row else f"col-{stage}"

    def _event(self, conn, issue_id: str, author: Dict[str, str], event: str, text: str,
               params: Optional[Dict[str, str]] = None, *, auto: bool = False) -> None:
        conn.execute("INSERT INTO activity(id, issue_id, kind, author, text, event, params, auto, created) "
                     "VALUES(?, ?, 'event', ?, ?, ?, ?, ?, ?)",
                     (uuid.uuid4().hex, issue_id, json.dumps(author), text, event, json.dumps(params or {}),
                      int(auto), _now()))

    def _check_project(self, conn, project_id: Any) -> Optional[str]:
        if project_id is None:
            return None
        if not isinstance(project_id, str) or conn.execute(
                "SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
            raise BoardError("没有这个项目")
        return project_id

    @staticmethod
    def _criteria(items: Any, previous: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        if not isinstance(items, list) or len(items) > MAX_CRITERIA:
            raise BoardError(f"验收项最多 {MAX_CRITERIA} 条")
        kept = {item["id"]: item for item in previous or []}
        result = []
        for item in items:
            if isinstance(item, str):
                item = {"text": item}
            if not isinstance(item, dict) or set(item) - {"id", "text"}:
                raise BoardError("验收项要写成文字")
            text = _text(item.get("text"), MAX_CRITERION, "验收项", required=True)
            old = kept.get(item.get("id")) if item.get("id") else None
            result.append({"id": old["id"], "text": text, "done": old["done"], "doneBy": old.get("doneBy")}
                          if old else {"id": uuid.uuid4().hex[:12], "text": text, "done": False, "doneBy": None})
        return result

    def create(self, author: Dict[str, str], body: Dict[str, Any], *, audience: Dict[str, Any]) -> Dict[str, Any]:
        title = _text(body.get("title"), MAX_TITLE, "标题", required=True)
        description = _text(body.get("description") or "", MAX_DESCRIPTION, "说明")
        priority = body.get("priority") or "normal"
        if priority not in PRIORITIES:
            raise BoardError("优先级只能是 low、normal 或 high")
        assignee = _actor(body["assignee"]) if body.get("assignee") else None
        with self._db() as conn:
            number = conn.execute("SELECT coalesce(max(number), 0) + 1 FROM issues").fetchone()[0]
            issue_id, now = uuid.uuid4().hex, _now()
            rank = conn.execute("SELECT coalesce(max(board_rank), 0) + 1 FROM issues").fetchone()[0]
            conn.execute(
                "INSERT INTO issues(id, number, title, description, status, column_id, board_rank, priority, project_id,"
                " assignee, labels, due_date, criteria, created_by, audience, github_url, parent_id, created, updated)"
                " VALUES(?, ?, ?, ?, 'todo', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (issue_id, number, title, description, self._column_for(conn, "todo"), rank, priority,
                 self._check_project(conn, body.get("projectId")), json.dumps(assignee) if assignee else None,
                 json.dumps(_labels(body.get("labels") or [])), _due(body.get("dueDate")),
                 json.dumps(self._criteria(body.get("criteria") or [])), json.dumps(author), json.dumps(audience),
                 _url(body.get("githubUrl")), body.get("parentIssueId"), now, now))
            self._event(conn, issue_id, author, "created", "created this task")
        return self.issue(issue_id)

    def update(self, issue_id: str, author: Dict[str, str], patch: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {"title", "description", "status", "priority", "assignee", "dueDate", "labels", "boardRank",
                   "githubUrl", "columnId", "projectId", "criteria"}
        if set(patch) - allowed:
            raise BoardError(f"这些字段这里不能改：{sorted(set(patch) - allowed)}")
        current = self.issue(issue_id)
        changes: Dict[str, Any] = {}
        events: List[tuple] = []
        with self._db() as conn:
            if "title" in patch:
                changes["title"] = _text(patch["title"], MAX_TITLE, "标题", required=True)
            if "description" in patch:
                changes["description"] = _text(patch["description"] or "", MAX_DESCRIPTION, "说明")
            if "priority" in patch:
                if patch["priority"] not in PRIORITIES:
                    raise BoardError("优先级只能是 low、normal 或 high")
                changes["priority"] = patch["priority"]
            if "assignee" in patch:
                assignee = _actor(patch["assignee"]) if patch["assignee"] else None
                changes["assignee"] = json.dumps(assignee) if assignee else None
                if assignee != current["assignee"]:
                    events.append(("assigned" if assignee else "unassigned",
                                   "assigned" if assignee else "removed the assignee", assignee or {}))
            if "dueDate" in patch:
                changes["due_date"] = _due(patch["dueDate"])
            if "labels" in patch:
                changes["labels"] = json.dumps(_labels(patch["labels"]))
            if "boardRank" in patch:
                if type(patch["boardRank"]) not in (int, float):
                    raise BoardError("boardRank 要是数字")
                changes["board_rank"] = float(patch["boardRank"])
            if "githubUrl" in patch:
                changes["github_url"] = _url(patch["githubUrl"])
            if "projectId" in patch:
                changes["project_id"] = self._check_project(conn, patch["projectId"])
            if "criteria" in patch:
                changes["criteria"] = json.dumps(self._criteria(patch["criteria"], current["criteria"]))
            stage = None
            if "columnId" in patch:
                row = conn.execute("SELECT stage FROM columns WHERE id = ?", (patch["columnId"],)).fetchone()
                if row is None:
                    raise BoardError("没有这一列")
                changes["column_id"], stage = patch["columnId"], row["stage"]
            if "status" in patch:
                if patch["status"] not in STAGES:
                    raise BoardError("状态无效")
                if stage is not None and stage != patch["status"]:
                    raise BoardError("列和状态不一致")
                stage = patch["status"]
                if "columnId" not in patch:
                    changes["column_id"] = self._column_for(conn, stage)
            if stage is not None:
                changes["status"] = stage
                if stage != current["status"]:
                    events.append(("status_changed", f"moved it to {stage}", {"from": current["status"], "to": stage}))
            if not changes:
                return current
            changes["updated"] = _now()
            assignments = ", ".join(f"{key} = ?" for key in changes)
            conn.execute(f"UPDATE issues SET {assignments} WHERE id = ?", (*changes.values(), issue_id))
            for event, text, params in events:
                self._event(conn, issue_id, author, event, text,
                            {key: str(value) for key, value in params.items()})
        return self.issue(issue_id)

    def set_audience(self, issue_id: str, audience: Dict[str, Any]) -> Dict[str, Any]:
        self.issue(issue_id)
        with self._db() as conn:
            conn.execute("UPDATE issues SET audience = ?, updated = ? WHERE id = ?",
                         (json.dumps(audience), _now(), issue_id))
        return self.issue(issue_id)

    def set_criterion(self, issue_id: str, criterion_id: str, done: bool, author: Dict[str, str]) -> Dict[str, Any]:
        if type(done) is not bool:
            raise BoardError("done 要是布尔值")
        current = self.issue(issue_id)
        criteria = current["criteria"]
        found = next((item for item in criteria if item["id"] == criterion_id), None)
        if found is None:
            raise NotFound("没有这条验收项")
        found["done"], found["doneBy"] = done, author if done else None
        with self._db() as conn:
            conn.execute("UPDATE issues SET criteria = ?, updated = ? WHERE id = ?",
                         (json.dumps(criteria), _now(), issue_id))
        return self.issue(issue_id)

    def delete(self, issue_id: str) -> None:
        self.issue(issue_id)
        with self._db() as conn:
            conn.execute("UPDATE issues SET parent_id = NULL WHERE parent_id = ?", (issue_id,))
            conn.execute("DELETE FROM issues WHERE id = ?", (issue_id,))

    def comment(self, issue_id: str, author: Dict[str, str], text: Any) -> Dict[str, Any]:
        body = _text(text, MAX_COMMENT, "评论", required=True)
        self.issue(issue_id)
        comment_id = uuid.uuid4().hex
        with self._db() as conn:
            conn.execute("INSERT INTO activity(id, issue_id, kind, author, text, created) "
                         "VALUES(?, ?, 'comment', ?, ?, ?)", (comment_id, issue_id, json.dumps(author), body, _now()))
            conn.execute("UPDATE issues SET updated = ? WHERE id = ?", (_now(), issue_id))
        return next(item for item in self.activity(issue_id) if item["id"] == comment_id)

    def record(self, issue_id: str, author: Dict[str, str], event: str, text: str,
               params: Optional[Dict[str, str]] = None, *, auto: bool = False) -> None:
        with self._db() as conn:
            self._event(conn, issue_id, author, event, text, params, auto=auto)

    # ── 写：列与项目 ─────────────────────────────────────────────────────────
    def add_column(self, body: Dict[str, Any]) -> List[Dict[str, Any]]:
        if body.get("stage") not in STAGES:
            raise BoardError("要选这一列属于哪个阶段")
        name = _text(body.get("name") or "", 60, "列名") or None
        with self._db() as conn:
            position = conn.execute("SELECT coalesce(max(position), -1) + 1 FROM columns").fetchone()[0]
            if conn.execute("SELECT count(*) FROM columns").fetchone()[0] >= 30:
                raise BoardError("看板最多 30 列")
            conn.execute("INSERT INTO columns(id, stage, name, position) VALUES(?, ?, ?, ?)",
                         ("col-" + uuid.uuid4().hex[:10], body["stage"], name, position))
        return self.columns()

    def update_column(self, column_id: str, patch: Dict[str, Any]) -> List[Dict[str, Any]]:
        if set(patch) - {"name", "position"}:
            raise BoardError("列只能改名字和位置")
        with self._db() as conn:
            if conn.execute("SELECT 1 FROM columns WHERE id = ?", (column_id,)).fetchone() is None:
                raise NotFound("没有这一列")
            if "name" in patch:
                name = _text(patch["name"] or "", 60, "列名") or None
                conn.execute("UPDATE columns SET name = ? WHERE id = ?", (name, column_id))
            if "position" in patch:
                if type(patch["position"]) is not int or patch["position"] < 0:
                    raise BoardError("位置要是非负整数")
                ids = [row["id"] for row in conn.execute("SELECT id FROM columns ORDER BY position, id")]
                ids.remove(column_id)
                ids.insert(min(patch["position"], len(ids)), column_id)
                conn.executemany("UPDATE columns SET position = ? WHERE id = ?",
                                 [(index, cid) for index, cid in enumerate(ids)])
        return self.columns()

    def delete_column(self, column_id: str, move_to: Optional[str]) -> List[Dict[str, Any]]:
        with self._db() as conn:
            row = conn.execute("SELECT stage FROM columns WHERE id = ?", (column_id,)).fetchone()
            if row is None:
                raise NotFound("没有这一列")
            same_stage = conn.execute("SELECT id FROM columns WHERE stage = ? AND id != ? ORDER BY position",
                                      (row["stage"], column_id)).fetchall()
            if not same_stage:
                raise BoardError("每个阶段至少留一列")
            target = move_to or same_stage[0]["id"]
            moved = conn.execute("SELECT stage FROM columns WHERE id = ?", (target,)).fetchone()
            if moved is None or target == column_id:
                raise BoardError("没有要挪去的那一列")
            conn.execute("UPDATE issues SET column_id = ?, status = ? WHERE column_id = ?",
                         (target, moved["stage"], column_id))
            conn.execute("DELETE FROM columns WHERE id = ?", (column_id,))
        return self.columns()

    def create_project(self, body: Dict[str, Any]) -> Dict[str, Any]:
        name = _text(body.get("name"), 80, "项目名", required=True)
        color = body.get("color") or "gray"
        if not isinstance(color, str) or len(color) > 20:
            raise BoardError("颜色无效")
        project_id = "proj-" + uuid.uuid4().hex[:10]
        with self._db() as conn:
            conn.execute("INSERT INTO projects(id, name, color, archived, created) VALUES(?, ?, ?, 0, ?)",
                         (project_id, name, color, _now()))
        return next(item for item in self.projects() if item["id"] == project_id)

    def update_project(self, project_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        if set(patch) - {"name", "color", "archived"}:
            raise BoardError("项目只能改名字、颜色和归档")
        with self._db() as conn:
            if conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
                raise NotFound("没有这个项目")
            if "name" in patch:
                conn.execute("UPDATE projects SET name = ? WHERE id = ?",
                             (_text(patch["name"], 80, "项目名", required=True), project_id))
            if "color" in patch:
                if not isinstance(patch["color"], str) or len(patch["color"]) > 20:
                    raise BoardError("颜色无效")
                conn.execute("UPDATE projects SET color = ? WHERE id = ?", (patch["color"], project_id))
            if "archived" in patch:
                if type(patch["archived"]) is not bool:
                    raise BoardError("archived 要是布尔值")
                conn.execute("UPDATE projects SET archived = ? WHERE id = ?", (int(patch["archived"]), project_id))
        return next(item for item in self.projects() if item["id"] == project_id)

    def delete_project(self, project_id: str) -> None:
        with self._db() as conn:
            if conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
                raise NotFound("没有这个项目")
            conn.execute("UPDATE issues SET project_id = NULL WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
