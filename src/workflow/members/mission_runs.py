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
from src.workflow.members.registry import Missing, Refused

MAX_STEPS = 10
ACT = ("owner", "decide")
TAKE_PART = ("owner", "decide", "comment")
MAX_PLAN_ITEMS = 40
PLAN_STATUSES = ("todo", "doing", "waiting_you", "done", "dropped")
FINISHED_ITEMS = ("done", "dropped")


class WrongStatus(ValueError):
    """The mission is not in a state that allows this action."""


class BadOption(ValueError):
    """The chosen option is not one the agent offered."""


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

    # ── 谁能做什么（主人 2026-11-23 定）──
    # owner：所有者，什么都能做。decide：被批准的「决定」成员，能开始、暂停、停止、拍板、验收。
    # comment：只能表态、勾检查项。none：能看到这个频道的人，只能看和申请加入。
    def role_of(self, conn, actor, row):
        if actor.is_owner:
            return "owner" if actor.id == self.channels.owner_id else "none"
        if not _has_table(conn, "mission_participants"):
            return "none"
        found = conn.execute("SELECT role FROM mission_participants WHERE mission_id = ? AND person = ?",
                             (row["id"], actor.id)).fetchone()
        return found["role"] if found else "none"

    def _need(self, conn, actor, row, roles):
        if self.role_of(conn, actor, row) not in roles:
            raise Refused("你在这个 Mission 里没有这样做的权限")

    # ── 写：都在登记锁内核对再改 ──
    def _row(self, conn, state, actor, mission_id, roles=("owner",)):
        if actor is None:
            row = self.drafts._load(conn, mission_id)
            if row is None:
                raise Missing("没有这个任务")
            return row
        row = self.drafts._get_visible(conn, state, actor, mission_id)
        self._need(conn, actor, row, roles)
        return row

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
        with self.channels.registry._locked_state() as state:
            with self.history.database(write=True) as conn:
                row = self._row(conn, state, actor, mission_id, ACT)
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
                if _has_table(conn, "mission_decisions") and any(_open_cards(conn, mission_id)):
                    return None                         # 等人拍板或验收
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
            blocking, delivery = _open_cards(conn, mission_id) if _has_table(conn, "mission_decisions") else (0, 0)
            if delivery:
                conn.execute("UPDATE mission_runs SET status = 'awaiting_close' WHERE mission_id = ?", (mission_id,))
                return mission_id, "delivered"
            if blocking:
                return mission_id, "waiting"            # 还是进行中：有人拍板后接着做
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
        with self.channels.registry._locked_state() as state, self.history.database(write=True) as conn:
            row = self._row(conn, state, actor, mission_id, ACT)
            if row["current_status"] == "paused":
                return dict(row)
            if row["current_status"] != "active":
                raise WrongStatus("只有进行中的任务能暂停")
            self._pause(conn, mission_id, "owner")
            return dict(self.drafts._load(conn, mission_id))

    def resume(self, actor, mission_id):
        """暂停 → 进行中，重新计步；没有正在做的一步就登记下一步。返回 (草案行, 新一步的任务 id 或 None)。"""
        with self.channels.registry._locked_state() as state, self.history.database(write=True) as conn:
            row = self._row(conn, state, actor, mission_id, ACT)
            if row["current_status"] not in ("paused", "awaiting_close"):
                raise WrongStatus("只有暂停或等收尾的任务能继续")
            conn.execute("UPDATE mission_runs SET status = 'active', paused_at = NULL, pause_reason = NULL, steps = 0"
                         " WHERE mission_id = ?", (mission_id,))
            if row["current_status"] == "awaiting_close" and _has_table(conn, "mission_deliveries"):
                conn.execute("UPDATE mission_deliveries SET status = 'superseded' WHERE mission_id = ?"
                             " AND status = 'pending'", (mission_id,))      # 不验收直接继续：那份交付作废
            waiting = _has_table(conn, "mission_decisions") and _open_cards(conn, mission_id)[0]
            task_id = None if self._open_step(conn, mission_id) or waiting else self._claim(conn, mission_id, "resume")
            return dict(self.drafts._load(conn, mission_id)), task_id

    def stop(self, actor, mission_id):
        """暂停，并返回正在做的那一步的任务 id（调用方去停它）。"""
        with self.channels.registry._locked_state() as state, self.history.database(write=True) as conn:
            row = self._row(conn, state, actor, mission_id, ACT)
            if row["current_status"] not in ("active", "paused"):
                raise WrongStatus("只有进行中或暂停的任务能停止")
            self._pause(conn, mission_id, "stopped")
            step = self._mark_stop(conn, mission_id)
            return dict(self.drafts._load(conn, mission_id)), (step["task_id"] if step else None)

    def close(self, actor, mission_id, outcome, note):
        """已开始的任务收尾：标为完成或取消。返回 (草案行, 正在做的那一步的任务 id)。同样的请求重复发不报错。"""
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


# ── 决定和交付（主人 2026-11-23 定）────────────────────────────────────────────
# agent 在一步里提出：要拍板的问题（blocking 时这一步后停下等人；不阻塞且给了建议的，按建议先做，
# 等人确认或改选），以及交付（这一步后等所有者验收：完成，或提出修改再接着做）。
# 卡片跟着这一步的回答发在话题里；拍板的结果写进下一步的提示，作为资料。

MAX_CARDS_PER_STEP = 3
MAX_CHECKLIST = 20


def _text(value, limit, what, *, required=True):
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
        raise ValueError(f"{what}要在 {limit} 字以内" + ("且不能为空" if required else ""))
    return " ".join(value.split()) or None


def check_decision(args):
    if not isinstance(args, dict) or set(args) - {"question", "options", "recommendation", "why", "impact", "blocking"}:
        raise ValueError("只能有 question、options、recommendation、why、impact、blocking")
    options = args.get("options")
    if not isinstance(options, list) or not 2 <= len(options) <= 6:
        raise ValueError("要有 2～6 个选项")
    clean = [_text(option, 120, "每个选项") for option in options]
    if len(set(clean)) != len(clean):
        raise ValueError("选项不能重复")
    recommendation = _text(args.get("recommendation"), 120, "建议", required=False)
    if recommendation is not None and recommendation not in clean:
        raise ValueError("建议要是选项之一")
    blocking = args.get("blocking", True)
    if type(blocking) is not bool:
        raise ValueError("blocking 只能是 true 或 false")
    return {"question": _text(args.get("question"), 300, "问题"), "options": clean, "recommendation": recommendation,
            "why": _text(args.get("why"), 600, "理由", required=False),
            "impact": _text(args.get("impact"), 600, "影响", required=False), "blocking": blocking}


def check_delivery(args):
    if not isinstance(args, dict) or set(args) - {"summary", "checklist"}:
        raise ValueError("只能有 summary、checklist")
    checklist = args.get("checklist", [])
    if not isinstance(checklist, list) or len(checklist) > MAX_CHECKLIST:
        raise ValueError(f"检查项最多 {MAX_CHECKLIST} 条")
    return {"summary": _text(args.get("summary"), 4000, "交付说明"),
            "checklist": [{"id": f"c{index + 1}", "text": _text(item, 200, "每条检查项")}
                          for index, item in enumerate(checklist)]}


def _open_cards(conn, mission_id):
    """还在等人的：阻塞的决定、待验收的交付。"""
    blocking = conn.execute("SELECT count(*) FROM mission_decisions WHERE mission_id = ? AND status = 'pending'"
                            " AND blocking = 1 AND message_id IS NOT NULL", (mission_id,)).fetchone()[0]
    delivery = conn.execute("SELECT count(*) FROM mission_deliveries WHERE mission_id = ? AND status = 'pending'"
                            " AND message_id IS NOT NULL", (mission_id,)).fetchone()[0]
    return blocking, delivery


class MissionCards:
    """决定和交付。写操作都在登记锁内核对（和 MissionRuns 同一把）。"""

    def __init__(self, runs: MissionRuns):
        self.runs = runs
        self.drafts = runs.drafts
        self.channels = runs.channels
        self.history = runs.history

    # ── agent 在一步里提出 ──
    def _step(self, conn, task_id):
        step = conn.execute("SELECT * FROM mission_steps WHERE task_id = ?", (task_id,)).fetchone()
        if step is None or step["finished"] is not None:
            raise WrongStatus("这一步已经结束")
        row = self.drafts._load(conn, step["mission_id"])
        if row is None or row["current_status"] not in ("active", "paused"):
            raise WrongStatus("这个任务已经收尾")
        made = conn.execute("SELECT (SELECT count(*) FROM mission_decisions WHERE task_id = ?)"
                            " + (SELECT count(*) FROM mission_deliveries WHERE task_id = ?)", (task_id, task_id)).fetchone()[0]
        if made >= MAX_CARDS_PER_STEP:
            raise ValueError(f"一步最多提 {MAX_CARDS_PER_STEP} 张卡")
        return step

    def propose_decision(self, task_id, args):
        clean = check_decision(args)
        with self.channels.registry._locked_state(), self.history.database(write=True) as conn:
            step = self._step(conn, task_id)
            status = "provisional" if not clean["blocking"] and clean["recommendation"] else "pending"
            decision_id = "decision-" + uuid.uuid4().hex
            conn.execute("INSERT INTO mission_decisions (id, mission_id, task_id, question, options, recommendation,"
                         " why, impact, blocking, status, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (decision_id, step["mission_id"], task_id, clean["question"],
                          json.dumps(clean["options"], ensure_ascii=False), clean["recommendation"], clean["why"],
                          clean["impact"], int(clean["blocking"]), status, _now()))
            return status

    def propose_delivery(self, task_id, args):
        clean = check_delivery(args)
        with self.channels.registry._locked_state(), self.history.database(write=True) as conn:
            step = self._step(conn, task_id)
            if conn.execute("SELECT 1 FROM mission_deliveries WHERE task_id = ?", (task_id,)).fetchone():
                raise ValueError("这一步已经交付过了")
            conn.execute("INSERT INTO mission_deliveries (id, mission_id, task_id, summary, checklist, status, created)"
                         " VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                         ("delivery-" + uuid.uuid4().hex, step["mission_id"], task_id, clean["summary"],
                          json.dumps(clean["checklist"], ensure_ascii=False), _now()))

    def unposted(self, task_id):
        """这一步提的、还没发到话题里的卡（发回答之后逐张发）。"""
        with self.history.database() as conn:
            if not _has_table(conn, "mission_decisions"):
                return []
            rows = [("decision", dict(row)) for row in conn.execute(
                "SELECT * FROM mission_decisions WHERE task_id = ? AND message_id IS NULL ORDER BY created", (task_id,))]
            rows += [("delivery", dict(row)) for row in conn.execute(
                "SELECT * FROM mission_deliveries WHERE task_id = ? AND message_id IS NULL ORDER BY created", (task_id,))]
            return rows

    def posted(self, kind, card_id, message_id):
        table = "mission_decisions" if kind == "decision" else "mission_deliveries"
        with self.channels.registry._locked_state(), self.history.database(write=True) as conn:
            conn.execute(f"UPDATE {table} SET message_id = ? WHERE id = ? AND message_id IS NULL", (message_id, card_id))

    # ── 读 ──
    def _decision_view(self, conn, row):
        opinions = [{"userId": self.history.person_id(o["person"]), "option": o["option"], "at": o["created"]}
                    for o in conn.execute("SELECT * FROM mission_decision_opinions WHERE decision_id = ? ORDER BY created",
                                          (row["id"],))]
        return {"id": row["id"], "missionId": row["mission_id"], "taskId": row["task_id"], "messageId": row["message_id"],
                "question": row["question"], "options": json.loads(row["options"]),
                "recommendation": row["recommendation"], "why": row["why"], "impact": row["impact"],
                "blocking": bool(row["blocking"]), "status": row["status"], "chosen": row["chosen"],
                "chosenBy": self.history.person_id(row["chosen_by"]) if row["chosen_by"] else None,
                "chosenAt": row["chosen_at"], "opinions": opinions, "createdAt": row["created"]}

    def _delivery_view(self, conn, row):
        checks = {}
        for check in conn.execute("SELECT item_id, person FROM mission_delivery_checks WHERE delivery_id = ? ORDER BY person",
                                  (row["id"],)):
            checks.setdefault(check["item_id"], []).append(self.history.person_id(check["person"]))
        return {"id": row["id"], "missionId": row["mission_id"], "taskId": row["task_id"], "messageId": row["message_id"],
                "auto": False, "summary": row["summary"], "artifactIds": [], "paths": [],
                "checklist": [{**item, "checkedBy": checks.get(item["id"], [])} for item in json.loads(row["checklist"])],
                "status": row["status"], "note": row["note"],
                "saveToWiki": bool(row["wiki_page"]) if "wiki_page" in row.keys() else False,
                "decidedBy": self.history.person_id(row["decided_by"]) if row["decided_by"] else None,
                "decidedAt": row["decided_at"], "createdAt": row["created"]}

    def decisions(self, actor, mission_id):
        with self.channels.registry._locked_state() as state, self.history.database() as conn:
            self.drafts._get_visible(conn, state, actor, mission_id)
            if not _has_table(conn, "mission_decisions"):
                return []
            return [self._decision_view(conn, row) for row in conn.execute(
                "SELECT * FROM mission_decisions WHERE mission_id = ? AND message_id IS NOT NULL ORDER BY created",
                (mission_id,))]

    def deliveries(self, actor, mission_id):
        with self.channels.registry._locked_state() as state, self.history.database() as conn:
            self.drafts._get_visible(conn, state, actor, mission_id)
            if not _has_table(conn, "mission_deliveries"):
                return []
            return [self._delivery_view(conn, row) for row in conn.execute(
                "SELECT * FROM mission_deliveries WHERE mission_id = ? AND message_id IS NOT NULL ORDER BY created",
                (mission_id,))]

    def counts(self, mission_id):
        """{decisions, provisional, deliveries}：还在等人的（已经发到话题里的）。"""
        with self.history.database() as conn:
            if not _has_table(conn, "mission_decisions"):
                return {"decisions": 0, "provisional": 0, "deliveries": 0}
            pending = dict(conn.execute("SELECT status, count(*) FROM mission_decisions WHERE mission_id = ?"
                                        " AND message_id IS NOT NULL AND status != 'taken' GROUP BY status", (mission_id,)))
            delivery = conn.execute("SELECT count(*) FROM mission_deliveries WHERE mission_id = ? AND status = 'pending'"
                                    " AND message_id IS NOT NULL", (mission_id,)).fetchone()[0]
            return {"decisions": pending.get("pending", 0), "provisional": pending.get("provisional", 0),
                    "deliveries": delivery}

    def taken_since(self, mission_id, limit=10):
        """写进下一步提示的：人拍过板的决定（最近几条）和最近一次提出修改的交付。"""
        with self.history.database() as conn:
            if not _has_table(conn, "mission_decisions"):
                return [], None
            taken = [dict(row) for row in conn.execute(
                "SELECT question, chosen, status, recommendation FROM mission_decisions WHERE mission_id = ?"
                " AND message_id IS NOT NULL ORDER BY created DESC LIMIT ?", (mission_id, limit))]
            change = conn.execute("SELECT summary, note FROM mission_deliveries WHERE mission_id = ?"
                                  " AND status = 'changes_requested' ORDER BY decided_at DESC LIMIT 1", (mission_id,)).fetchone()
            return list(reversed(taken)), (dict(change) if change else None)

    # ── 人拍板 ──
    def _decision(self, conn, state, actor, mission_id, decision_id, roles):
        row = self.drafts._get_visible(conn, state, actor, mission_id)
        self.runs._need(conn, actor, row, roles)
        decision = conn.execute("SELECT * FROM mission_decisions WHERE id = ? AND mission_id = ? AND message_id IS NOT NULL",
                                (decision_id, mission_id)).fetchone() if _has_table(conn, "mission_decisions") else None
        if decision is None:
            raise Missing("没有这个决定")
        return row, decision

    def choose(self, actor, mission_id, decision_id, option):
        """拍板。返回 (决定视图, 要不要接着派下一步)。同一选项重复点不报错。"""
        with self.channels.registry._locked_state() as state, self.history.database(write=True) as conn:
            row, decision = self._decision(conn, state, actor, mission_id, decision_id, ACT)
            if option not in json.loads(decision["options"]):
                raise BadOption("不是这个决定的选项")
            if decision["status"] == "taken":
                if decision["chosen"] != option:
                    raise WrongStatus("这个决定已经拍板了")
                return self._decision_view(conn, decision), False
            if row["current_status"] in ("done", "canceled"):
                raise WrongStatus("这个任务已经收尾")
            conn.execute("UPDATE mission_decisions SET status = 'taken', chosen = ?, chosen_by = ?, chosen_at = ?"
                         " WHERE id = ?", (option, self.channels._actor_key(actor), _now(), decision_id))
            blocking, delivery = _open_cards(conn, mission_id)
            proceed = (bool(decision["blocking"]) and row["current_status"] == "active" and not blocking
                       and not delivery and self.runs._open_step(conn, mission_id) is None)
            fresh = conn.execute("SELECT * FROM mission_decisions WHERE id = ?", (decision_id,)).fetchone()
            return self._decision_view(conn, fresh), proceed

    def opinion(self, actor, mission_id, decision_id, option):
        """表态：只记下谁倾向哪个，不拍板。再表一次是替换。"""
        with self.channels.registry._locked_state() as state, self.history.database(write=True) as conn:
            row, decision = self._decision(conn, state, actor, mission_id, decision_id, TAKE_PART)
            if option not in json.loads(decision["options"]):
                raise BadOption("不是这个决定的选项")
            if decision["status"] == "taken" or row["current_status"] in ("done", "canceled"):
                raise WrongStatus("这个决定已经拍板了")
            conn.execute("INSERT INTO mission_decision_opinions (decision_id, person, option, created) VALUES (?, ?, ?, ?)"
                         " ON CONFLICT(decision_id, person) DO UPDATE SET option = excluded.option, created = excluded.created",
                         (decision_id, self.channels._actor_key(actor), option, _now()))
            return self._decision_view(conn, decision)

    def approve_provisional(self, actor, mission_id):
        """agent 按建议先做了的决定：一次全部确认。返回确认了的。"""
        with self.channels.registry._locked_state() as state, self.history.database(write=True) as conn:
            row = self.drafts._get_visible(conn, state, actor, mission_id)
            self.runs._need(conn, actor, row, ACT)
            if row["current_status"] in ("done", "canceled"):
                raise WrongStatus("这个任务已经收尾")
            if not _has_table(conn, "mission_decisions"):
                return []
            ids = [r[0] for r in conn.execute("SELECT id FROM mission_decisions WHERE mission_id = ? AND status = 'provisional'"
                                              " AND message_id IS NOT NULL", (mission_id,))]
            key, now = self.channels._actor_key(actor), _now()
            for decision_id in ids:
                conn.execute("UPDATE mission_decisions SET status = 'taken', chosen = recommendation, chosen_by = ?,"
                             " chosen_at = ? WHERE id = ?", (key, now, decision_id))
            return [self._decision_view(conn, r) for r in conn.execute(
                f"SELECT * FROM mission_decisions WHERE id IN ({','.join('?' for _ in ids)}) ORDER BY created", ids)] if ids else []

    def _delivery(self, conn, state, actor, mission_id, delivery_id, roles):
        row = self.drafts._get_visible(conn, state, actor, mission_id)
        self.runs._need(conn, actor, row, roles)
        delivery = conn.execute("SELECT * FROM mission_deliveries WHERE id = ? AND mission_id = ? AND message_id IS NOT NULL",
                                (delivery_id, mission_id)).fetchone() if _has_table(conn, "mission_deliveries") else None
        if delivery is None:
            raise Missing("没有这份交付")
        return row, delivery

    def check(self, actor, mission_id, delivery_id, item_id, checked):
        """勾掉（或取消）一条检查项：每个人各勾各的。"""
        with self.channels.registry._locked_state() as state, self.history.database(write=True) as conn:
            _, delivery = self._delivery(conn, state, actor, mission_id, delivery_id, TAKE_PART)
            if item_id not in {item["id"] for item in json.loads(delivery["checklist"])}:
                raise Missing("没有这条检查项")
            if delivery["status"] != "pending":
                raise WrongStatus("这份交付已经有结论了")
            key = self.channels._actor_key(actor)
            if checked:
                conn.execute("INSERT OR IGNORE INTO mission_delivery_checks (delivery_id, item_id, person) VALUES (?, ?, ?)",
                             (delivery_id, item_id, key))
            else:
                conn.execute("DELETE FROM mission_delivery_checks WHERE delivery_id = ? AND item_id = ? AND person = ?",
                             (delivery_id, item_id, key))
            return self._delivery_view(conn, delivery)

    def conclude(self, actor, mission_id, delivery_id, outcome, note):
        """验收：accepted（Mission 完成）或 changes_requested（Mission 回到进行中，接着做）。
        返回 (交付视图, 要不要接着派下一步, 要停下的那一步的任务 id)。同样的结论重复发不报错。"""
        with self.channels.registry._locked_state() as state, self.history.database(write=True) as conn:
            row, delivery = self._delivery(conn, state, actor, mission_id, delivery_id, ACT)
            if delivery["status"] != "pending":
                if (delivery["status"], delivery["note"]) != (outcome, note):
                    raise WrongStatus("这份交付已经有结论了")
                return self._delivery_view(conn, delivery), False, None
            if row["current_status"] in ("done", "canceled"):
                raise WrongStatus("这个任务已经收尾")
            now = _now()
            conn.execute("UPDATE mission_deliveries SET status = ?, note = ?, decided_by = ?, decided_at = ? WHERE id = ?",
                         (outcome, note, self.channels._actor_key(actor), now, delivery_id))
            proceed = False
            if outcome == "accepted":
                step = self.runs._mark_stop(conn, mission_id)
                conn.execute("UPDATE mission_runs SET status = 'done', closed_at = ?, close_note = ? WHERE mission_id = ?",
                             (now, note, mission_id))
                stop = step["task_id"] if step else None
            else:
                conn.execute("UPDATE mission_runs SET status = 'active', paused_at = NULL, pause_reason = NULL, steps = 0"
                             " WHERE mission_id = ?", (mission_id,))
                blocking, _ = _open_cards(conn, mission_id)
                proceed = not blocking and self.runs._open_step(conn, mission_id) is None
                stop = None
            fresh = conn.execute("SELECT * FROM mission_deliveries WHERE id = ?", (delivery_id,)).fetchone()
            return self._delivery_view(conn, fresh), proceed, stop


    def mark_saved(self, delivery_id, page_id):
        """验收时存成了一页 Wiki。"""
        with self.channels.registry._locked_state(), self.history.database(write=True) as conn:
            conn.execute("UPDATE mission_deliveries SET wiki_page = ? WHERE id = ? AND wiki_page IS NULL",
                         (page_id, delivery_id))

    def wiki_page_of(self, mission_id):
        with self.history.database() as conn:
            if conn is None or not _has_table(conn, "mission_deliveries"):
                return None
            if "wiki_page" not in {row[1] for row in conn.execute("PRAGMA table_info(mission_deliveries)")}:
                return None
            found = conn.execute("SELECT wiki_page FROM mission_deliveries WHERE mission_id = ? AND wiki_page IS NOT NULL"
                                 " ORDER BY created DESC LIMIT 1", (mission_id,)).fetchone()
            return found[0] if found else None


def cards_for(root):
    return MissionCards(runs_for(root))


# ── 参与者、加入申请、建议（主人 2026-11-23 定）──────────────────────────────────────
# 能看到这个频道的人可以申请加入；所有者批准时给「决定」或「评论」身份，也可以直接加人、改身份、移出。
# 在 Mission 话题里，没有拍板权的人（不是所有者也不是「决定」成员）说的话记成建议：
# 有拍板权的人采纳之前，它不进 agent 的提示；采纳后交给 agent，谢绝的永远不进。

class MissionPeople:
    def __init__(self, runs: MissionRuns):
        self.runs = runs
        self.drafts = runs.drafts
        self.channels = runs.channels
        self.history = runs.history

    def _name(self, state, person):
        from src.workflow.members.registry import MemberRegistry
        if person == "owner":
            return self.channels.owner_name
        try:
            return MemberRegistry._member(state, person)["name"]
        except (Missing, KeyError, ValueError):
            return "已离开的成员"

    def _in_channel(self, state, row, person):
        channel = state["channels"].get(row["channel_id"]) or {}
        return person in (channel.get("joined") or {})

    def visible(self, actor, mission_id):
        """看得到这个 Mission 的人（所有者，或能读这个频道的成员）：返回 (草案行, 身份, 自己有没有待处理的加入申请)。"""
        with self.channels.registry._locked_state() as state, self.history.database() as conn:
            row = dict(self.drafts._get_visible(conn, state, actor, mission_id))
            role = self.runs.role_of(conn, actor, row)
            asked = bool(not actor.is_owner and _has_table(conn, "mission_join_requests") and conn.execute(
                "SELECT 1 FROM mission_join_requests WHERE mission_id = ? AND person = ? AND status = 'pending'",
                (mission_id, actor.id)).fetchone())
            return row, role, asked

    def participants(self, actor, mission_id):
        with self.channels.registry._locked_state() as state, self.history.database() as conn:
            row = self.drafts._get_visible(conn, state, actor, mission_id)
            rows = conn.execute("SELECT * FROM mission_participants WHERE mission_id = ? ORDER BY created",
                                (mission_id,)) if _has_table(conn, "mission_participants") else []
            return {"owner": {"userId": self.history.person_id(row["owner"]), "name": self.channels.owner_name},
                    "participants": [{"userId": self.history.person_id(p["person"]), "name": self._name(state, p["person"]),
                                      "role": p["role"],
                                      "addedBy": self.history.person_id(p["added_by"]) if p["added_by"] else None,
                                      "createdAt": p["created"]} for p in rows]}

    def set_participant(self, actor, mission_id, person, role, *, create):
        """所有者加人或改身份。person 是成员 id；他要在这个频道里。create：只加新的（已在就报错）。"""
        if role not in ("decide", "comment"):
            raise BadOption("身份只能是 decide 或 comment")
        with self.channels.registry._locked_state() as state, self.history.database(write=True) as conn:
            row = self.runs._row(conn, state, actor, mission_id)
            if row["current_status"] in ("done", "canceled"):
                raise WrongStatus("这个任务已经收尾")
            if person == "owner" or not self._in_channel(state, row, person):
                raise Missing("这个人不在 Mission 所在的频道里")
            existing = conn.execute("SELECT 1 FROM mission_participants WHERE mission_id = ? AND person = ?",
                                    (mission_id, person)).fetchone()
            if create and existing:
                raise WrongStatus("这个人已经在这个 Mission 里了")
            if not create and not existing:
                raise Missing("这个人不在这个 Mission 里")
            conn.execute("INSERT INTO mission_participants (mission_id, person, role, added_by, created) VALUES (?, ?, ?, ?, ?)"
                         " ON CONFLICT(mission_id, person) DO UPDATE SET role = excluded.role",
                         (mission_id, person, role, self.channels._actor_key(actor), _now()))
            conn.execute("UPDATE mission_join_requests SET status = 'accepted', role = ?, decided_by = ?, decided_at = ?"
                         " WHERE mission_id = ? AND person = ? AND status = 'pending'",
                         (role, self.channels._actor_key(actor), _now(), mission_id, person))
        return self.participants(actor, mission_id)

    def remove_participant(self, actor, mission_id, person):
        with self.channels.registry._locked_state() as state, self.history.database(write=True) as conn:
            self.runs._row(conn, state, actor, mission_id)
            gone = conn.execute("DELETE FROM mission_participants WHERE mission_id = ? AND person = ?",
                                (mission_id, person)).rowcount if _has_table(conn, "mission_participants") else 0
            if not gone:
                raise Missing("这个人不在这个 Mission 里")

    # ── 加入申请 ──
    def _request_view(self, state, row):
        return {"id": f"{row['mission_id']}:{row['person']}", "missionId": row["mission_id"],
                "userId": self.history.person_id(row["person"]), "name": self._name(state, row["person"]),
                "status": row["status"], "role": row["role"],
                "decidedBy": self.history.person_id(row["decided_by"]) if row["decided_by"] else None,
                "decidedAt": row["decided_at"], "createdAt": row["created"]}

    def join_requests(self, actor, mission_id):
        """所有者看到全部待处理的；成员只看到自己的。"""
        with self.channels.registry._locked_state() as state, self.history.database() as conn:
            self.drafts._get_visible(conn, state, actor, mission_id)
            if not _has_table(conn, "mission_join_requests"):
                return []
            if self.runs.role_of(conn, actor, {"id": mission_id}) == "owner":
                rows = conn.execute("SELECT * FROM mission_join_requests WHERE mission_id = ? AND status = 'pending'"
                                    " ORDER BY created", (mission_id,))
            else:
                rows = conn.execute("SELECT * FROM mission_join_requests WHERE mission_id = ? AND person = ?",
                                    (mission_id, actor.id))
            return [self._request_view(state, r) for r in rows]

    def request_join(self, actor, mission_id):
        """能看到这个频道、还不在这个 Mission 里的成员申请加入。再申请一次不报错；被谢绝后可以再申请。"""
        if actor.is_owner:
            raise WrongStatus("所有者不用申请")
        with self.channels.registry._locked_state() as state, self.history.database(write=True) as conn:
            row = self.drafts._get_visible(conn, state, actor, mission_id)
            if row["current_status"] in ("done", "canceled"):
                raise WrongStatus("这个任务已经收尾")
            if self.runs.role_of(conn, actor, row) != "none":
                raise WrongStatus("你已经在这个 Mission 里了")
            conn.execute("INSERT INTO mission_join_requests (mission_id, person, status, created) VALUES (?, ?, 'pending', ?)"
                         " ON CONFLICT(mission_id, person) DO UPDATE SET status = 'pending', role = NULL,"
                         " decided_by = NULL, decided_at = NULL, created = excluded.created"
                         " WHERE mission_join_requests.status != 'pending'", (mission_id, actor.id, _now()))
            found = conn.execute("SELECT * FROM mission_join_requests WHERE mission_id = ? AND person = ?",
                                 (mission_id, actor.id)).fetchone()
            return self._request_view(state, found)

    def decide_join(self, actor, mission_id, person, outcome, role):
        """所有者答复加入申请：accept 时给身份（默认评论），decline 谢绝。"""
        with self.channels.registry._locked_state() as state, self.history.database() as conn:
            self.runs._row(conn, state, actor, mission_id)
            found = conn.execute("SELECT * FROM mission_join_requests WHERE mission_id = ? AND person = ?",
                                 (mission_id, person)).fetchone() if _has_table(conn, "mission_join_requests") else None
            if found is None:
                raise Missing("没有这份加入申请")
        if outcome == "accept":
            if found["status"] == "declined":
                raise WrongStatus("这份申请已经谢绝了")
            self.set_participant(actor, mission_id, person, role or "comment",
                                 create=not self._is_participant(mission_id, person))
        else:
            with self.channels.registry._locked_state() as state, self.history.database(write=True) as conn:
                self.runs._row(conn, state, actor, mission_id)
                current = conn.execute("SELECT status FROM mission_join_requests WHERE mission_id = ? AND person = ?",
                                       (mission_id, person)).fetchone()
                if current["status"] == "accepted":
                    raise WrongStatus("这份申请已经同意了")
                conn.execute("UPDATE mission_join_requests SET status = 'declined', decided_by = ?, decided_at = ?"
                             " WHERE mission_id = ? AND person = ? AND status = 'pending'",
                             (self.channels._actor_key(actor), _now(), mission_id, person))
        with self.channels.registry._locked_state() as state, self.history.database() as conn:
            found = conn.execute("SELECT * FROM mission_join_requests WHERE mission_id = ? AND person = ?",
                                 (mission_id, person)).fetchone()
            return self._request_view(state, found)

    def _is_participant(self, mission_id, person):
        with self.history.database() as conn:
            return bool(_has_table(conn, "mission_participants") and conn.execute(
                "SELECT 1 FROM mission_participants WHERE mission_id = ? AND person = ?", (mission_id, person)).fetchone())

    # ── 建议 ──
    def note_message(self, actor, mission_id, message_id):
        """有人在 Mission 话题里说了一句：没有拍板权的记成待采纳的建议。返回 True 表示记成了建议。"""
        with self.channels.registry._locked_state(), self.history.database(write=True) as conn:
            row = self.drafts._load(conn, mission_id)
            if row is None or row["current_status"] in ("done", "canceled"):
                return False
            if self.runs.role_of(conn, actor, row) in ACT:
                return False
            conn.execute("INSERT OR IGNORE INTO mission_suggestions (message_id, mission_id, status, created)"
                         " VALUES (?, ?, 'pending', ?)", (message_id, mission_id, _now()))
            return True

    def decide_suggestion(self, actor, mission_id, message_id, outcome):
        """有拍板权的人采纳或谢绝。返回 (结果, 要不要接着派下一步)。同样的答复重复发不报错。"""
        status = {"accept": "accepted", "decline": "declined"}[outcome]
        with self.channels.registry._locked_state() as state, self.history.database(write=True) as conn:
            row = self.runs._row(conn, state, actor, mission_id, ACT)
            found = conn.execute("SELECT * FROM mission_suggestions WHERE message_id = ? AND mission_id = ?",
                                 (message_id, mission_id)).fetchone() if _has_table(conn, "mission_suggestions") else None
            if found is None:
                raise Missing("没有这条建议")
            if found["status"] != "pending":
                if found["status"] != status:
                    raise WrongStatus("这条建议已经答复过了")
                return {"messageId": message_id, "status": status}, False
            conn.execute("UPDATE mission_suggestions SET status = ?, decided_by = ?, decided_at = ? WHERE message_id = ?",
                         (status, self.channels._actor_key(actor), _now(), message_id))
            proceed = (status == "accepted" and row["current_status"] == "active"
                       and self.runs._open_step(conn, mission_id) is None and not any(_open_cards(conn, mission_id)))
            return {"messageId": message_id, "status": status}, proceed

    def hidden_messages(self, mission_id):
        """还没被采纳的建议（不进 agent 的提示）。"""
        with self.history.database() as conn:
            if not _has_table(conn, "mission_suggestions"):
                return set()
            return {r[0] for r in conn.execute("SELECT message_id FROM mission_suggestions WHERE mission_id = ?"
                                               " AND status != 'accepted'", (mission_id,))}

    def counts(self, actor, mission_id):
        """给界面的：待采纳的建议、待处理的加入申请（所有者才看得到后者）。"""
        with self.history.database() as conn:
            if not _has_table(conn, "mission_suggestions"):
                return {"suggestions": 0, "joinRequests": 0}
            suggestions = conn.execute("SELECT count(*) FROM mission_suggestions WHERE mission_id = ? AND status = 'pending'",
                                       (mission_id,)).fetchone()[0]
            joins = conn.execute("SELECT count(*) FROM mission_join_requests WHERE mission_id = ? AND status = 'pending'",
                                 (mission_id,)).fetchone()[0] if actor.is_owner else 0
            return {"suggestions": suggestions, "joinRequests": joins}


def people_for(root):
    return MissionPeople(runs_for(root))
