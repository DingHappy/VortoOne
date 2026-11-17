"""AI 应用发给主助理的话：作为主要聊天的又一个入口（Desktop、IM、手机 App 之后）。

- 只有所有者的连接能到主助理（``principal.may_address_main_assistant``）：它连着所有者的个人记忆和电脑。
- 回合用 IM 那一档装配（``kind="im"``）：**输入本身就是外部内容**，每回合从污点态起步，消息在历史里带出处
  标记；这一阶段应用那头问不到人（``confirm=None``），要确认的动作一律不做，回复里说明要回 VortoOne 操作。
- 经 ``run_external_turn(source="ai_app")`` 写进主要聊天，Desktop 同一段对话里看得到，和其它入口轮流跑。
- 回合只在内存里：应用凭 ``run_id`` 等回复；服务重启后旧 run_id 认不出来，回复照样留在主要聊天里。
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

MAX_TEXT = 4000
_KEEP_FINISHED = 3600.0
_MAX_TURNS = 64


@dataclass
class MainTurn:
    rid: str
    grant: str
    person: str
    state: str = "running"            # running / done / failed
    reply: str = ""
    error: str = ""
    started: float = field(default_factory=time.time)
    finished: float = 0.0
    done: asyncio.Event = field(default_factory=asyncio.Event, repr=False)


class MainTurns:
    def __init__(self, root: str):
        self.root = root
        self.turns: Dict[str, MainTurn] = {}
        self._agent: Any = None

    def _build(self) -> Any:
        from src.gateway.agent_session import build_session
        return build_session(self.root, kind="im", confirm=None, can_ask_human=False,
                             workspace_scope="general", with_dev=False)

    def _prune(self) -> None:
        now = time.time()
        for rid, turn in list(self.turns.items()):
            if turn.finished and now - turn.finished > _KEEP_FINISHED:
                self.turns.pop(rid, None)

    async def start(self, *, grant: str, person: str, app_name: str, text: str) -> MainTurn:
        from src.web import app_views as views
        from src.web.app_bridge import main_chat_bridge
        if not views.model_connected():
            raise ValueError("还没有连接模型，主助理没法回复")
        self._prune()
        if sum(1 for t in self.turns.values() if t.state == "running") >= 4:
            raise ValueError("主助理这里排队的事太多，稍后再发")
        if len(self.turns) >= _MAX_TURNS:
            raise ValueError("进行中的回合太多，稍后再发")
        await main_chat_bridge(self.root)              # 主要聊天还没登记时，先登记（与界面发消息同一条路）
        turn = MainTurn("aiapp-" + secrets.token_hex(8), grant, person)
        self.turns[turn.rid] = turn
        # 记进主要聊天的原话带上来源：Desktop 里看得出这句不是在电脑上打的
        shown = f"（经 AI 应用「{app_name}」发来）\n{text.strip()}"
        asyncio.get_running_loop().create_task(self._run(turn, shown))
        return turn

    async def _run(self, turn: MainTurn, text: str) -> None:
        from src.gateway.main_conversation import run_external_turn
        if self._agent is None:
            self._agent = self._build()
        agent = self._agent

        async def run(history: list):
            agent.history = list(history)
            reply = await agent.run_turn(text, mode="build")
            return list(agent.history), str(reply or "")

        try:
            reply = await run_external_turn(self.root, source="ai_app", user_text=text, rid=turn.rid,
                                            run=run, agent=agent)
            if reply is None:
                turn.state, turn.error = "failed", "主要聊天还没开通：请先在 VortoOne 里打开一次主助理的对话"
            else:
                turn.state, turn.reply = "done", reply
        except Exception as error:  # noqa: BLE001 —— 失败要告诉应用，不能静默
            busy = type(error).__name__ == "MainConversationBusy"
            turn.state = "failed"
            turn.error = str(error)[:200] if busy else "这一轮没有完成，可能已有部分操作，请在 VortoOne 里核对"
            if not busy:
                logger.warning("AI 应用的主助理回合失败：%s", type(error).__name__)
        finally:
            turn.finished = time.time()
            turn.done.set()

    def get(self, rid: str, person: str) -> Optional[MainTurn]:
        turn = self.turns.get(rid) if isinstance(rid, str) else None
        return turn if turn is not None and turn.person == person else None


_TURNS: Dict[str, MainTurns] = {}


def main_turns(root: str) -> MainTurns:
    if root not in _TURNS:
        _TURNS[root] = MainTurns(root)
    return _TURNS[root]


def reset() -> None:
    """测试用。"""
    _TURNS.clear()
