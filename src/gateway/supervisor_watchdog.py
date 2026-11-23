"""监护进程看门狗——监护它的进程没了，runtime 就自己退出。

Desktop 正常退出时会 `RunEvent::Exit` → 终止 runtime 进程组，这条路是通的。问题出在**非正常
退出**：强杀（`kill -9` / 活动监视器强制退出 / 开发时 `tmux kill-session`）、崩溃、宿主机注销。
此时 runtime 还活着，继续占着端口、持着一个能调模型、能跑命令的 agent，而界面上再没有它的入口
——2026-09-17 的真机诊断里就遗留了两对这样的孤儿进程，直到手工 kill。

做法：Desktop 启动 runtime 时把自己的 pid 写进 ``VORTOONE_SUPERVISOR_PID``，runtime 定期看
一眼那个进程还在不在，不在就退出。没有这个环境变量（`vc server` 手工起、systemd 托管、CI）
时看门狗完全不启动——那些场景的生命周期本来就不归 Desktop 管。

Unix 使用 `os.kill(pid, 0)` 探活。Windows 用只具备同步权限的进程句柄，因为 Windows 的
`os.kill(pid, 0)` 会终止目标进程。无法探测时保守地当进程还在。
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import logging
import os
from ctypes import wintypes
from typing import Optional

logger = logging.getLogger(__name__)

ENV_VAR = "VORTOONE_SUPERVISOR_PID"
_DEFAULT_INTERVAL = 5.0
_WINDOWS = os.name == "nt"
_SYNCHRONIZE = 0x00100000
_ERROR_INVALID_PARAMETER = 87
_WAIT_OBJECT_0 = 0


def supervisor_pid(env: Optional[dict] = None) -> Optional[int]:
    """环境里申报的监护进程 pid；没有/非法/是自己 → None（即不启动看门狗）。"""
    raw = (env or os.environ).get(ENV_VAR, "")
    try:
        pid = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if pid <= 1 or pid == os.getpid():
        return None
    return pid


def _windows_process_alive(pid: int) -> bool:
    if not 1 < pid <= 0xFFFFFFFF:
        return False
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    wait = kernel.WaitForSingleObject
    wait.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    wait.restype = wintypes.DWORD
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    handle = open_process(_SYNCHRONIZE, False, pid)
    if not handle:
        return ctypes.get_last_error() != _ERROR_INVALID_PARAMETER
    try:
        return wait(handle, 0) != _WAIT_OBJECT_0
    finally:
        close(handle)


def process_alive(pid: int) -> bool:
    """用本平台的只读探活接口检查监护进程。"""
    if _WINDOWS:
        return _windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # 存在，只是不归我管（换用户跑的）——当活着，别误杀自己
    except OSError:
        return True          # 平台异常按"还在"处理：漏判只是多活一会儿，误判会打断正在跑的活
    return True


async def watch_supervisor(pid: int, interval: float = _DEFAULT_INTERVAL,
                           on_gone=None) -> None:
    """轮询监护进程；它消失后调用 ``on_gone``（默认退出本进程）。"""
    while True:
        await asyncio.sleep(interval)
        if process_alive(pid):
            continue
        logger.warning("监护进程 %s 已退出，runtime 随之退出（避免留下孤儿）", pid)
        if on_gone is not None:
            on_gone()
        else:
            os._exit(0)      # 直接退：此时没有人在等这个 runtime，正常 shutdown 可能被回合卡住
        return


def start_watchdog(interval: float = _DEFAULT_INTERVAL) -> Optional[asyncio.Task]:
    """在当前事件循环里挂上看门狗；没申报监护进程则返回 None（不启动）。"""
    pid = supervisor_pid()
    if pid is None:
        return None
    task = asyncio.get_running_loop().create_task(watch_supervisor(pid, interval))
    with contextlib.suppress(Exception):
        task.set_name("supervisor-watchdog")
    logger.info("看门狗已启动：监护进程 pid=%s，每 %.0f 秒探活一次", pid, interval)
    return task
