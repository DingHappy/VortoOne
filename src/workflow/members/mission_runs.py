"""Mission 开始以后：状态、每一步、计划版本（主人 2026-11-23 定）。

- 只有原生 agent 的 Mission，在频道话题里一步接一步自动推进；结果整个频道都看得到。
- 停下来的时候：这一步失败、被打断（「恢复后自动接着做」没开）、agent 没列计划、一次开始累计做满
  ``MAX_STEPS`` 步；计划全部做完就等所有者收尾（awaiting_close）。
- 同一时刻一个 Mission 只有一步在做：先在这里登记一步（同一把登记锁内核对），再派活、再把任务 id 记回来。
- 草案本身（mission_drafts）不改：执行状态记在旁边的表里，旧版本读到的仍是原来的草案。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from src.workflow.members.channel_history import _stamp
from src.workflow.members.mission_drafts import MissionDrafts, _has_table
from src.workflow.members.registry import Missing

MAX_STEPS = 10
MAX_PLAN_ITEMS = 40
PLAN_STATUSES = ("todo", "doing", "waiting_you", "done", "dropped")
FINISHED_ITEMS = ("done", "dropped")


class WrongStatus(ValueError):
    """The mission is not in a state that allows this action."""


def _now():
    return _stamp(datetime.now(timezone.utc))


def check_plan(items):
    """agent 交来的计划：1～40 项，每项一个标题和状态（可带一句备注）。不合格整份拒绝。"""
    if not isinstance(items, list) or not 0 < len(items) <= MAX_PLAN_ITEMS:
        raise ValueError(f"计划要有 1～{MAX_PLAN_ITEMS} 项")
    clean = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or set(item) - {"title", "status", "note"}:
            raise ValueError("每一项只能有 title、status、note")
        title, status, note = item.get("title"), item.get("status", "todo"), item.get("note")
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError("每一项要有 200 字以内的标题")
        if status not in PLAN_STATUSES:
            raise ValueError("状态只能是 " + "、".join(PLAN_STATUSES))
        if note is not None and (not isinstance(note, str) or len(note) > 300):
            raise ValueError("备注要在 300 字以内")
        row = {"id": f"p{index + 1}", "title": " ".join(title.split()), "status": status}
        if note and note.strip():
            row["note"] = " ".join(note.split())
        clean.append(row)
    return clean


class MissionRuns:
    """Mission 开始以后的状态。所有者的核对沿用草案（MissionDrafts）。"""

    def __init__(self, drafts: MissionDrafts):
        self.drafts = drafts
        self.channels = drafts.channels
        self.history = drafts.channels.history

    # ── 读 ──
    def get(self, actor, mission_id):
        return self.drafts.get(actor, mission_id)

    @staticmethod
    def _plans(conn, mission_id):
        if not _has_table(conn, "mission_plans"):
            return []
        rows = conn.execute("SELECT * FROM mission_plans WHERE mission_id = ? ORDER BY version DESC LIMIT 20",
                            (mission_id,))
        return [{"version": row["version"], "items": json.loads(row["items"]), "source": "agent",
                 "taskId": row["task_id"], "createdAt": row["created"]} for row in rows]

    @staticmethod
    def _steps(conn, mission_id):
        if not _has_table(conn, "mission_steps"):
            return []
        return [dict(row) for row in conn.execute(
            "SELECT * FROM mission_steps WHERE mission_id = ? ORDER BY number", (mission_id,))]

    def details(self, actor, mission_id):
        """草案行 + 计划历史（新到旧）+ 每一步。"""
        with self.channels.registry._locked_state() as state, self.history.database() as conn:
            row = dict(self.drafts._get_visible(conn, state, actor, mission_id))
            return row, self._plans(conn, mission_id), self._steps(conn, mission_id)

    def latest(self, mission_ids):
        """列表用：每个 Mission 的最新计划和还没收尾的步（一次读完）。"""
        found = {mission_id: {"plan": None, "steps": []} for mission_id in mission_ids}
        with self.history.database() as conn:
            for mission_id in mission_ids:
                plans = self._plans(conn, mission_id)
                found[mission_id] = {"plan": plans[0] if plans else None,
                                     "steps": [step for step in self._steps(conn, mission_id) if step["finished"] is None]}
        return found

    def load(self, mission_id):
        """不核对看的人：只给已经核对过的内部调用。"""
        with self.history.database() as conn:
            row = self.drafts._load(conn, mission_id)
            if row is None:
                raise Missing("没有这个任务")
            return dict(row)

    def open_steps(self, mission_id):
        with self.history.database() as conn:
            return [step for step in self._steps(conn, mission_id) if step["finished"] is None]

    def plan_of(self, mission_id):
        """不核对看的人：只给已经核对过的内部调用（派下一步时写提示）。"""
        with self.history.database() as conn:
            plans = self._plans(conn, mission_id)
            return plans[0]["items"] if plans else []

    def by_task(self, task_id):
        """这件任务是哪个 Mission 的哪一步（不是就 None）。"""
        with self.history.database() as conn:
            if not _has_table(conn, "mission_steps"):
                return None
            row = conn.execute("SELECT * FROM mission_steps WHERE task_id = ?", (task_id,)).fetchone()
            return dict(row) if row else None

    def active_ids(self):
        """所有还在进行或暂停、可能有一步没收尾的 Mission（重启时补收尾用）。"""
        with self.history.database() as conn:
            if not _has_table(conn, "mission_runs"):
                return []
            return [row[0] for row in conn.execute(
                "SELECT DISTINCT step.mission_id FROM mission_steps AS step"
                " WHERE step.finished IS NULL")]

    # ── 写：都在登记锁内核对再改 ──
    def _row(self, conn, state, actor, mission_id):
        if actor is None:
            row = self.drafts._load(conn, mission_id)
            if row is None:
                raise Missing("没有这个任务")
            return row
        return self.drafts._get_visible(conn, state, actor, mission_id)

    def _open_step(self, conn, mission_id):
        return conn.execute("SELECT * FROM mission_steps WHERE mission_id = ? AND finished IS NULL",
                            (mission_id,)).fetchone()

    def _mark_stop(self, conn, mission_id):
        """正在做的那一步记下「是人要停的」，再交给执行池去停。返回那一步（没有就 None）。"""
        step = self._open_step(conn, mission_id)
        if step is not None:
            conn.execute("UPDATE mission_steps SET stop_requested = 1 WHERE mission_id = ? AND number = ?",
                         (mission_id, step["number"]))
        return step

    def request_step_stop(self, task_id):
        """从别处（比如频道的「正在运行」）停 Mission 的一步：同样记下是人要停的，并暂停 Mission。"""
        with self.channels.registry._locked_state(), self.history.database(write=True) as conn:
            if not _has_table(conn, "mission_steps"):
                return
            step = conn.execute("SELECT * FROM mission_steps WHERE task_id = ? AND finished IS NULL",
                                (task_id,)).fetchone()
            if step is None:
                return
            conn.execute("UPDATE mission_steps SET stop_requested = 1 WHERE task_id = ?", (task_id,))
            self._pause(conn, step["mission_id"], "stopped")

    def _claim(self, conn, mission_id, trigger):
        """登记一步，连任务 id 一起先定好：任务哪怕立刻结束，回调也认得出是这一步。返回任务 id。"""
        number = conn.execute("SELECT COALESCE(MAX(number), 0) + 1 FROM mission_steps WHERE mission_id = ?",
                              (mission_id,)).fetchone()[0]
        task_id = "task-" + uuid.uuid4().hex
        conn.execute("INSERT INTO mission_steps (mission_id, number, trigger, task_id, created) VALUES (?, ?, ?, ?, ?)",
                     (mission_id, number, trigger, task_id, _now()))
        conn.execute("UPDATE mission_runs SET steps = steps + 1 WHERE mission_id = ?", (mission_id,))
        return task_id

    def start(self, actor, mission_id):
        """草案 → 进行中，并登记第一步。返回 (草案行, 第一步的任务 id)。"""
        self.drafts._owner(actor)
        with self.channels.registry._locked_state() as state:
            with self.history.database(write=True) as conn:
                row = self._row(conn, state, actor, mission_id)
                if row["current_status"] != "proposed":
                    raise WrongStatus("只有还没开始的草案能开始")
                conn.execute("INSERT INTO mission_runs (mission_id, status, started_at) VALUES (?, 'active', ?)",
                             (mission_id, _now()))
                task_id = self._claim(conn, mission_id, "start")
                return dict(self.drafts._load(conn, mission_id)), task_id

    def claim_next(self, mission_id, trigger):
        """进行中、没有在做的一步、没做满：登记下一步并返回它的任务 id；做满了就暂停并返回 None。"""
        with self.channels.registry._locked_state():
            with self.history.database(write=True) as conn:
                row = self.drafts._load(conn, mission_id)
                if row is None or row["current_status"] != "active" or self._open_step(conn, mission_id):
                    return None
                if row["run_steps"] >= MAX_STEPS:
                    self._pause(conn, mission_id, "step_limit")
                    return None
                return self._claim(conn, mission_id, trigger)

    def abandon(self, task_id, reason):
        """这一步没派出去：记成没开始并暂停，等人处理。"""
        with self.channels.registry._locked_state(), self.history.database(write=True) as conn:
            step = conn.execute("SELECT * FROM mission_steps WHERE task_id = ? AND finished IS NULL",
                                (task_id,)).fetchone()
            if step is None:
                return
            conn.execute("UPDATE mission_steps SET finished = ?, outcome = 'not_started' WHERE task_id = ?",
                         (_now(), task_id))
            row = self.drafts._load(conn, step["mission_id"])
            if row is not None and row["current_status"] == "active":
                self._pause(conn, step["mission_id"], reason)

    @staticmethod
    def _pause(conn, mission_id, reason):
        conn.execute("UPDATE mission_runs SET status = 'paused', paused_at = ?, pause_reason = ?"
                     " WHERE mission_id = ? AND status = 'active'", (_now(), reason, mission_id))

    def finish(self, task_id, outcome):
        """一步结束了：记下结果，决定下一步。返回 (mission_id, 下一步怎么办) 或 None（不是 Mission 的步、已收过尾）。

        下一步怎么办：continue（接着派）、resume（被打断、按设置重新做这一步）、
        以及停下的原因 failed / interrupted / no_plan / step_limit / planned_done / stopped / closed。"""
        with self.channels.registry._locked_state(), self.history.database(write=True) as conn:
            if not _has_table(conn, "mission_steps"):
                return None
            step = conn.execute("SELECT * FROM mission_steps WHERE task_id = ?", (task_id,)).fetchone()
            if step is None or step["finished"] is not None:
                return None
            mission_id = step["mission_id"]
            if outcome == "cancelled" and not step["stop_requested"]:
                # 没人要停它：是服务关闭时执行池取消的，和重启打断一样处理
                outcome = "interrupted"
            conn.execute("UPDATE mission_steps SET finished = ?, outcome = ? WHERE mission_id = ? AND number = ?",
                         (_now(), outcome, mission_id, step["number"]))
            row = self.drafts._load(conn, mission_id)
            if row["current_status"] in ("done", "canceled"):
                return mission_id, "closed"
            if row["current_status"] != "active":
                return mission_id, "stopped"
            if outcome == "cancelled":
                # 人停的这一步收尾前，所有者已经点了「继续」（状态又是进行中）：接着做下一步
                return mission_id, "continue"
            if outcome == "interrupted":
                if row["resume_tasks"]:
                    return mission_id, "resume"
                self._pause(conn, mission_id, "interrupted")
                return mission_id, "interrupted"
            if outcome != "done":
                self._pause(conn, mission_id, "failed")
                return mission_id, "failed"
            plans = self._plans(conn, mission_id)
            if not plans:
                self._pause(conn, mission_id, "no_plan")
                return mission_id, "no_plan"
            if all(item["status"] in FINISHED_ITEMS for item in plans[0]["items"]):
                conn.execute("UPDATE mission_runs SET status = 'awaiting_close' WHERE mission_id = ?", (mission_id,))
                return mission_id, "planned_done"
            if row["run_steps"] >= MAX_STEPS:
                self._pause(conn, mission_id, "step_limit")
                return mission_id, "step_limit"
            return mission_id, "continue"

    def set_plan(self, task_id, items):
        """正在做的那一步交来新计划（只有这一步自己能改，Mission 还没收尾）。返回新版本号。"""
        clean = check_plan(items)
        with self.channels.registry._locked_state(), self.history.database(write=True) as conn:
            step = conn.execute("SELECT * FROM mission_steps WHERE task_id = ?", (task_id,)).fetchone()
            if step is None or step["finished"] is not None:
                raise WrongStatus("这一步已经结束，不能再改计划")
            row = self.drafts._load(conn, step["mission_id"])
            if row is None or row["current_status"] not in ("active", "paused"):
                raise WrongStatus("这个任务已经收尾")
            version = conn.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM mission_plans WHERE mission_id = ?",
                                   (step["mission_id"],)).fetchone()[0]
            conn.execute("INSERT INTO mission_plans (mission_id, version, items, task_id, created) VALUES (?, ?, ?, ?, ?)",
                         (step["mission_id"], version, json.dumps(clean, ensure_ascii=False), task_id, _now()))
            return version

    def pause(self, actor, mission_id):
        """不再派下一步；正在做的这一步做完为止。"""
        self.drafts._owner(actor)
        with self.channels.registry._locked_state() as state, self.history.database(write=True) as conn:
            row = self._row(conn, state, actor, mission_id)
            if row["current_status"] == "paused":
                return dict(row)
            if row["current_status"] != "active":
                raise WrongStatus("只有进行中的任务能暂停")
            self._pause(conn, mission_id, "owner")
            return dict(self.drafts._load(conn, mission_id))

    def resume(self, actor, mission_id):
        """暂停 → 进行中，重新计步；没有正在做的一步就登记下一步。返回 (草案行, 新一步的任务 id 或 None)。"""
        self.drafts._owner(actor)
        with self.channels.registry._locked_state() as state, self.history.database(write=True) as conn:
            row = self._row(conn, state, actor, mission_id)
            if row["current_status"] not in ("paused", "awaiting_close"):
                raise WrongStatus("只有暂停或等收尾的任务能继续")
            conn.execute("UPDATE mission_runs SET status = 'active', paused_at = NULL, pause_reason = NULL, steps = 0"
                         " WHERE mission_id = ?", (mission_id,))
            task_id = None if self._open_step(conn, mission_id) else self._claim(conn, mission_id, "resume")
            return dict(self.drafts._load(conn, mission_id)), task_id

    def stop(self, actor, mission_id):
        """暂停，并返回正在做的那一步的任务 id（调用方去停它）。"""
        self.drafts._owner(actor)
        with self.channels.registry._locked_state() as state, self.history.database(write=True) as conn:
            row = self._row(conn, state, actor, mission_id)
            if row["current_status"] not in ("active", "paused"):
                raise WrongStatus("只有进行中或暂停的任务能停止")
            self._pause(conn, mission_id, "stopped")
            step = self._mark_stop(conn, mission_id)
            return dict(self.drafts._load(conn, mission_id)), (step["task_id"] if step else None)

    def close(self, actor, mission_id, outcome, note):
        """已开始的任务收尾：标为完成或取消。返回 (草案行, 正在做的那一步的任务 id)。同样的请求重复发不报错。"""
        self.drafts._owner(actor)
        with self.channels.registry._locked_state() as state, self.history.database(write=True) as conn:
            row = self._row(conn, state, actor, mission_id)
            if row["started_at"] is None:
                raise WrongStatus("还没开始的草案请直接取消")
            if row["current_status"] in ("done", "canceled"):
                if (row["current_status"], row["note"]) != (outcome, note):
                    raise WrongStatus("这个任务已经收尾")
                return dict(row), None
            step = self._mark_stop(conn, mission_id)
            conn.execute("UPDATE mission_runs SET status = ?, closed_at = ?, close_note = ? WHERE mission_id = ?",
                         (outcome, _now(), note, mission_id))
            return dict(self.drafts._load(conn, mission_id)), (step["task_id"] if step else None)

    def set_resume_tasks(self, actor, mission_id, on):
        self.drafts._owner(actor)
        with self.channels.registry._locked_state() as state, self.history.database(write=True) as conn:
            row = self._row(conn, state, actor, mission_id)
            if row["started_at"] is None:
                raise WrongStatus("开始以后才能设置")
            if row["current_status"] in ("done", "canceled"):
                raise WrongStatus("这个任务已经收尾")
            conn.execute("UPDATE mission_runs SET resume_tasks = ? WHERE mission_id = ?", (int(bool(on)), mission_id))
            return dict(self.drafts._load(conn, mission_id))


def runs_for(root):
    """执行回调和 agent 工具用：不核对看的人（调用方是已经核对过、正在做这一步的任务）。"""
    from src.workflow.members.channels import TeamChannels
    return MissionRuns(MissionDrafts(TeamChannels(root, "")))
