"""Run one fixed file action in a child without connector secrets or a network client."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import select
import stat
import sys
import time

from src.gateway.computer_file_store import ACTION_SECONDS, ComputerFiles, FileAction, FileStateUnavailable
from src.gateway.computer_file_wire import action_value, bounded_frame, object_fields, result_value
from src.gateway.computer_protocol import MAX_FRAME_BYTES, binding_fields, decode_frame

_COMMAND = "computer-file-action"
_REAP_SECONDS = 0.75


class FileProcessUnavailable(RuntimeError):
    """No verified result reached the process that dispatched this action."""


def _child_launch() -> tuple[list[str], dict[str, str]]:
    if getattr(sys, "frozen", False):
        return [sys.executable, _COMMAND], {"PYINSTALLER_RESET_ENVIRONMENT": "1"}
    root = str(Path(__file__).resolve().parents[2])
    return [sys.executable, "-m", "src.runtime_entry", _COMMAND], {"PYTHONPATH": root}


async def _reap(process) -> None:
    process.stdin.close()  # EOF is a fail-closed cancellation signal before publication.
    try:
        await asyncio.wait_for(process.wait(), _REAP_SECONDS)
        return
    except asyncio.TimeoutError:
        pass
    try:
        process.kill()  # Refer to the child we launched, never a PID stored on disk.
    except ProcessLookupError:
        pass
    await process.wait()


async def run_file_action(directory: Path, binding: dict, action: FileAction, *,
                          stop: asyncio.Event, revoked: asyncio.Event) -> dict:
    limit = action.deadline - time.time()
    if not 0 < limit <= ACTION_SECONDS or stop.is_set() or revoked.is_set():
        raise FileProcessUnavailable("本机动作已停止或过期")
    envelope = bounded_frame({"parent_pid": os.getpid(), "binding": binding_fields(binding), "action": asdict(action)})
    payload = (json.dumps(envelope, ensure_ascii=True) + "\n").encode("ascii")
    command, env = _child_launch()
    deadline = asyncio.get_running_loop().time() + limit
    try:
        process = await asyncio.create_subprocess_exec(*command, cwd=directory, env=env,
                                                        stdin=asyncio.subprocess.PIPE,
                                                        stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.DEVNULL, close_fds=True,
                                                        limit=MAX_FRAME_BYTES + 1)
    except (ValueError, OSError) as error:
        raise FileProcessUnavailable("无法启动本机文件进程") from error

    async def exchange():
        if stop.is_set() or revoked.is_set() or asyncio.get_running_loop().time() >= deadline:
            raise FileProcessUnavailable("本机动作已停止或过期")
        process.stdin.write(payload)
        await process.stdin.drain()
        raw = await process.stdout.readline()
        if not raw.endswith(b"\n"):
            raise FileProcessUnavailable("本机文件进程未返回回执")
        return result_value(decode_frame(raw[:-1]), action)

    exchange_task = asyncio.create_task(exchange())
    signals = [asyncio.create_task(event.wait()) for event in (stop, revoked)]
    try:
        done, _ = await asyncio.wait([exchange_task, *signals],
                                     timeout=max(0, deadline - asyncio.get_running_loop().time()),
                                     return_when=asyncio.FIRST_COMPLETED)
        if exchange_task in done:
            return exchange_task.result()
        process.stdin.close()
        return await asyncio.wait_for(asyncio.shield(exchange_task), _REAP_SECONDS)
    except (ValueError, OSError, asyncio.TimeoutError) as error:
        raise FileProcessUnavailable("本机文件动作结果未能核对") from error
    finally:
        for task in (exchange_task, *signals):
            task.cancel()
        await asyncio.gather(exchange_task, *signals, return_exceptions=True)
        await _reap(process)


def _read_envelope(descriptor: int) -> dict:
    raw = bytearray()
    while len(raw) <= MAX_FRAME_BYTES:
        data = os.read(descriptor, min(8192, MAX_FRAME_BYTES + 1 - len(raw)))
        if not data:
            break
        raw.extend(data)
        if b"\n" in data and raw.endswith(b"\n") and raw.count(b"\n") == 1:
            envelope = decode_frame(bytes(raw[:-1]))
            return object_fields(envelope, ("parent_pid", "binding", "action"))
        if b"\n" in data:
            break
    raise ValueError("invalid private file action frame")


def main() -> int:
    try:
        descriptor = sys.stdin.fileno()
        if os.name != "posix" or not stat.S_ISFIFO(os.fstat(descriptor).st_mode):
            return 2
        envelope = _read_envelope(descriptor)
        parent = envelope["parent_pid"]
        if type(parent) is not int or parent <= 1 or parent != os.getppid():
            return 2
        binding = binding_fields(envelope["binding"])
        object_fields(envelope["binding"], binding)
        action = action_value(envelope["action"])

        def active():
            # Native parent holds the writing end. Any additional data, EOF,
            # or parent death fences the action at its next pre-effect check.
            return os.getppid() == parent and not select.select([descriptor], [], [], 0)[0]

        result = ComputerFiles(Path.cwd() / "file-access", binding).execute(action, active=active)
        print(json.dumps(bounded_frame(result), ensure_ascii=True), flush=True)
        return 0
    except (FileStateUnavailable, OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return 2
