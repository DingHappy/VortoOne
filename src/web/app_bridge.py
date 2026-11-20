"""新版界面与主要聊天之间的桥。

桥在会话那一侧就是又一个附着的客户端（见 routers/realtime.py 末尾的接入点）：它收到会话事件，
换成界面的对话事件发给已订阅的界面连接；界面发来的消息、停止和审批决定，由它按原协议交回会话。
回合怎么跑、要不要确认、能不能放行，全部仍由会话与内核决定——桥不执行、不代答、不放宽范围。

每个家一座桥，进程内常驻：界面连接全断开时回合照常进行（回合属于会话，不属于某条连接）。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, Optional, Set

from src.gateway import app_protocol as F
from src.gateway import protocol as P
from src.web import app_views as views

logger = logging.getLogger(__name__)

_BRIDGES: Dict[str, "MainChatBridge"] = {}
_LOCKS: Dict[str, asyncio.Lock] = {}


class NoModel(RuntimeError):
    """还没有连接模型：不开回合，也不把这条消息记成已发出。"""


class MainChatBridge:
    def __init__(self, root: str, key: str) -> None:
        self.root, self.key = root, key
        self.query_params = {"sid": key[len("sid-"):]}       # 会话侧只认这一项来定位会话
        self.channel = views.dm_channel_id(views.main_agent_id(root))
        self.sockets: Set[Any] = set()
        self.announced: Set[str] = set()
        self.runs: Dict[str, Dict[str, Any]] = {}             # 进行中的回合：rid → 界面要的运行状态
        self.client_ids: Dict[str, str] = {}                  # rid → 界面发消息时带的 clientId
        self.confirms: Dict[str, Dict[str, Any]] = {}         # 等主人决定的确认：id → 整理前的记录
        self.decisions: Dict[str, bool] = {}                  # 主人经界面作出的决定：id → 是否批准

    # ── 界面连接 ──────────────────────────────────────────────────────────────

    def subscribe(self, socket) -> None:
        self.sockets.add(socket)

    def unsubscribe(self, socket) -> None:
        self.sockets.discard(socket)

    async def _emit(self, kind: str, **fields: Any) -> None:
        event = F.channel_event(self.channel, kind, **fields)
        for socket in list(self.sockets):
            try:
                await socket.send_json(F.make_app_frame(F.EVENT, channelId=self.channel, event=event))
            except Exception:  # noqa: BLE001 —— 一条界面连接断了不影响会话和其它连接
                self.sockets.discard(socket)

    # ── 会话 → 界面 ───────────────────────────────────────────────────────────

    async def send_json(self, event: Dict[str, Any]) -> None:
        """会话事件流把本段会话的每个事件交到这里。任何翻译错误都不能传回会话。"""
        try:
            await self._translate(event)
        except Exception:  # noqa: BLE001
            logger.warning("界面事件翻译失败: %s", event.get("type"), exc_info=True)

    def _run_fields(self, rid: str) -> Dict[str, Any]:
        return {"threadId": views.message_id(rid, "user"), "runId": rid, "agentId": views.main_agent_id(self.root)}

    async def _start_run(self, rid: str) -> None:
        if rid in self.runs:
            return
        started = views.now_iso()
        self.runs[rid] = {**self._run_fields(rid), "channelId": self.channel, "status": "running",
                          "triggerMessageId": views.message_id(rid, "user"), "startedAt": started,
                          "createdAt": started}
        await self._emit("agent.run", **self._run_fields(rid), status="running",
                         triggerMessageId=views.message_id(rid, "user"), startedAt=started)

    async def _finish_run(self, rid: str, status: str, error: Optional[str] = None) -> None:
        if self.runs.pop(rid, None) is None:
            return
        self.client_ids.pop(rid, None)
        await self._emit("run.finished", **self._run_fields(rid), status=status, error=error)

    async def _translate(self, event: Dict[str, Any]) -> None:
        kind = event.get("type")
        rid = str(event.get("rid") or "")
        if kind == P.AGENT_CONFIRM:
            await self._confirm_opened(event, rid)
            return
        if kind == P.AGENT_CONFIRM_CLOSED:
            await self._confirm_closed(event)
            return
        if rid and kind in {P.AGENT_PHASE, P.AGENT_SAY, P.AGENT_TOOL, P.AGENT_STREAM, P.AGENT_EMIT}:
            await self._start_run(rid)
        if rid and kind == P.AGENT_TOOL:
            running = event.get("status") == "running"
            await self._emit("agent.tool", **self._run_fields(rid),
                             tool=str(event.get("name") or "tool") if running else None,
                             toolUseId=str(event.get("id") or "") or None,
                             detail=str(event.get("summary") or "")[:200] or None if running else None,
                             failed=None if running or event.get("status") == "succeeded" else True)
        elif rid and kind == P.AGENT_STREAM and not self.runs[rid].get("typing"):
            self.runs[rid]["typing"] = True
            await self._emit("agent.typing", **self._run_fields(rid))
        if kind in {P.AGENT_HISTORY, P.AGENT_EMIT, P.AGENT_QUEUE, P.AGENT_DONE, P.AGENT_CANCELLED, P.AGENT_ERROR}:
            await self.announce_new_messages()
        if rid and kind == P.AGENT_ERROR:
            await self._finish_run(rid, "failed", str(event.get("text") or "")[:500] or None)
        elif rid and kind == P.AGENT_CANCELLED:
            await self._finish_run(rid, "canceled")
        elif rid and kind == P.AGENT_DONE:
            await self._finish_run(rid, "done")

    async def announce_new_messages(self) -> None:
        for message in views.main_messages(self.root, limit=400):
            if message["id"] in self.announced:
                continue
            self.announced.add(message["id"])
            rid = message.get("agentRunId")
            if message["author"]["type"] == "person":
                rid = next((r for r in self.client_ids if views.message_id(r, "user") == message["id"]), None)
                if rid:
                    message = {**message, "clientId": self.client_ids[rid]}
            await self._emit("message.created", messageId=message["id"], message=message,
                             agentRunId=message.get("agentRunId"),
                             authorUserId=message["author"]["id"] if message["author"]["type"] == "person" else None)

    async def _confirm_opened(self, event: Dict[str, Any], rid: str) -> None:
        cid = str(event.get("id") or "")
        if not cid or cid in self.confirms:      # 重连回放会再发一次同一个确认
            return
        pending = self.pending(cid)
        if pending is None:
            return
        self.confirms[cid] = {**pending, "rid": rid or None}
        card = views.approval_card(self.root, self.confirms[cid])
        self.announced.add(card["id"])
        await self._emit("message.created", messageId=card["id"], message=card, agentRunId=rid or None)
        await self._emit("approval.created", approval=views.approval(self.root, self.confirms[cid]))

    async def _confirm_closed(self, event: Dict[str, Any]) -> None:
        cid = str(event.get("id") or "")
        pending = self.confirms.pop(cid, None)
        if pending is None:
            return
        decision = self.decisions.pop(cid, None)
        reason = event.get("reason")
        if reason == "timeout":
            status = "expired"
        elif reason == "answered" and decision is not None:
            status = "approved" if decision else "refused"
        elif reason == "answered":
            status = "canceled"      # 在别的端答的：这里不知道结果，不猜是批准还是拒绝
        else:
            status = "canceled"
        await self._emit("approval.decided",
                         approval=views.approval(self.root, pending, status=status, decided=decision is not None))

    # ── 界面 → 会话 ───────────────────────────────────────────────────────────

    async def post(self, body: str, client_id: Optional[str], *, file_owner: bool = False,
                   local_browser: bool = False, paired_browser: bool = False) -> Dict[str, Any]:
        from src.gateway.computer_browser_preview import browser_previews
        from src.gateway.computer_file_authority import OwnerTurn
        from src.gateway.task_browser import task_browser_grant
        from src.web.routers import realtime
        if not views.model_connected():
            raise NoModel()
        rid = uuid.uuid4().hex
        if client_id:
            self.client_ids[rid] = client_id
        message = views.owner_message(self.root, rid, body, client_id=client_id)
        browser_grant = task_browser_grant(self.root) if local_browser else None
        paired_epoch = browser_previews.owner_epoch() if paired_browser else None
        file_identity = None
        if file_owner:
            from src.gateway.connected_computers import ConnectedComputers, ComputerUnavailable
            try:
                identity = await asyncio.to_thread(ConnectedComputers(self.root).identity)
                if identity.get("ownership_state") == "claimed":
                    file_identity = identity["home_instance_id"], identity["owner_subject"], identity["trust_epoch"]
            except (ComputerUnavailable, OSError):
                pass  # Ordinary chat can proceed when the home identity cannot be checked.
        event = {"type": P.AGENT, "text": body, "rid": rid, "mode": "plan"}
        if file_identity is not None or browser_grant is not None:
            await realtime.client_message(self, event, owner_turn=OwnerTurn(
                self.root, self.key, rid, file_identity=file_identity, task_browser=browser_grant,
                paired_browser_epoch=paired_epoch if file_identity is not None else None,
            ))
        else:
            await realtime.client_message(self, event)
        # 应答和实时/重读用同一条记录，不能用接收请求的时刻覆盖它的时间与顺序。
        recorded = next((row for row in views.main_messages(self.root, limit=400)
                         if row["id"] == message["id"]), None)
        return {**(recorded or message), **({"clientId": client_id} if client_id else {})}

    async def stop(self, rid: str) -> bool:
        from src.web.routers import realtime
        if rid not in self.runs:
            return False
        await realtime.client_message(self, {"type": P.AGENT_CANCEL})
        return True

    async def decide(self, cid: str, approve: bool, *, file_owner: bool = False,
                     browser_owner: bool = False) -> Optional[Dict[str, Any]]:
        """主人的决定按原协议交回会话，范围固定为「仅这一次」。返回决定后的审批；确认已不在等则 None。"""
        from src.web.routers import realtime
        pending = self.pending(cid)
        if pending is None or (realtime.requires_owner_confirmation(cid) and not file_owner):
            return None
        paired = self.paired_browser_task(cid)
        if pending.get("paired_browser") and paired is None:
            return None
        if paired is not None:
            from src.gateway.paired_browser_task import PairedBrowserNotReady

            if not browser_owner:
                return None
            if approve:
                preview = paired.preview()
                if preview is not None and preview.controlling:
                    raise PairedBrowserNotReady("请先交还页面控制，再确认继续")
                if (preview is None or not await preview.verify_site()
                        or paired.preview() is not preview or preview.controlling or self.pending(cid) is None):
                    raise PairedBrowserNotReady()
        self.decisions[cid] = approve
        event = {"type": P.AGENT_CONFIRM_RESPONSE, "id": cid, "ok": approve, "scope": "once"}
        if file_owner:
            await realtime.client_message(self, event, owner_confirmation=True)
        else:
            await realtime.client_message(self, event)
        return views.approval(self.root, pending, status="approved" if approve else "refused", decided=True)

    def pending(self, cid: str) -> Optional[Dict[str, Any]]:
        return next((item for item in self.pending_confirmations() if item["id"] == cid), None)

    def website_frame(self, cid: str):
        from src.web.routers.realtime import pending_website_frame
        row = self.confirms.get(cid)
        if row is not None and row.get("rid") in self.runs:
            return pending_website_frame(self.root, self.key, cid)
        return None

    def paired_browser_task(self, cid: str):
        from src.web.routers.realtime import pending_paired_browser
        row = self.confirms.get(cid)
        if row is not None and row.get("rid") in self.runs:
            return pending_paired_browser(self.root, self.key, cid)
        return None

    def pending_confirmations(self) -> list[Dict[str, Any]]:
        """以会话侧为准：只有它还在等的确认才算待处理。"""
        from src.web.routers.realtime import pending_confirmations
        rows = []
        for item in pending_confirmations(self.key):
            rows.append({**item, "rid": (self.confirms.get(item["id"]) or {}).get("rid")})
        return rows

    def active_runs(self) -> list[Dict[str, Any]]:
        waiting = {item.get("rid") for item in self.pending_confirmations()}
        return [{key: value for key, value in {**run, "status": "waiting_approval" if rid in waiting
                                               else run["status"]}.items() if key != "typing"}
                for rid, run in self.runs.items()]


async def main_chat_bridge(root: str) -> MainChatBridge:
    """取这座家的桥；第一次用时登记主要聊天（还没有的话）并附着到会话上。"""
    async with _LOCKS.setdefault(root, asyncio.Lock()):
        bridge = _BRIDGES.get(root)
        if bridge is not None:
            return bridge
        from src.gateway.main_conversation import claim_main_chat
        from src.web.routers import realtime
        key = views.main_chat_key(root) or claim_main_chat(root, "sid-app" + uuid.uuid4().hex[:24])
        bridge = MainChatBridge(root, key)
        await realtime.attach_client(bridge)
        bridge.announced = {message["id"] for message in views.main_messages(root, limit=400)}
        _BRIDGES[root] = bridge
        return bridge


def existing_bridge(root: str) -> Optional[MainChatBridge]:
    return _BRIDGES.get(root)


def reset_bridges() -> None:
    """测试用：丢掉进程内的桥。"""
    _BRIDGES.clear()
    _LOCKS.clear()
