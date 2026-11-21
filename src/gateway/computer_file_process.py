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

from src.gateway.computer_file_store import ACTION_SECONDS, ComputerFiles, FileAction, FileDelivery, FileStateUnavailable
from src.gateway.computer_file_wire import action_value, bounded_frame, object_fields, result_value
from src.gateway.computer_protocol import MAX_FRAME_BYTES, TRANSFER_SECONDS, binding_fields, decode_frame

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
                          stop: asyncio.Event, revoked: asyncio.Event,
                          disconnected: asyncio.Event | None = None, source: int | None = None) -> dict:
    deliveries = isinstance(action, FileDelivery)
    if deliveries != (source is not None):
        raise FileProcessUnavailable("本机动作缺少传输暂存")
    limit = action.deadline - time.time()
    maximum = TRANSFER_SECONDS if deliveries else ACTION_SECONDS
    events = (stop, revoked) if disconnected is None else (stop, revoked, disconnected)
    if not 0 < limit <= maximum or any(event.is_set() for event in events):
        raise FileProcessUnavailable("本机动作已停止或过期")
    envelope = {"parent_pid": os.getpid(), "binding": binding_fields(binding), "action": asdict(action)}
    if source is not None:
        envelope["source_fd"] = source
    payload = (json.dumps(bounded_frame(envelope), ensure_ascii=True) + "\n").encode("ascii")
    command, env = _child_launch()
    deadline = asyncio.get_running_loop().time() + limit
    try:
        process = await asyncio.create_subprocess_exec(*command, cwd=directory, env=env,
                                                        stdin=asyncio.subprocess.PIPE,
                                                        stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.DEVNULL, close_fds=True,
                                                        pass_fds=() if source is None else (source,),
                                                        limit=MAX_FRAME_BYTES + 1)
    except (ValueError, OSError) as error:
        raise FileProcessUnavailable("无法启动本机文件进程") from error

    async def exchange():
        if any(event.is_set() for event in events) or asyncio.get_running_loop().time() >= deadline:
            raise FileProcessUnavailable("本机动作已停止或过期")
        process.stdin.write(payload)
        await process.stdin.drain()
        raw = await process.stdout.readline()
        if not raw.endswith(b"\n"):
            raise FileProcessUnavailable("本机文件进程未返回回执")
        return result_value(decode_frame(raw[:-1]), action)

    exchange_task = asyncio.create_task(exchange())
    signals = [asyncio.create_task(event.wait()) for event in events]
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
            keys = {"parent_pid", "binding", "action"}
            return object_fields(envelope, keys | {"source_fd"} if "source_fd" in envelope else keys)
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
        delivery = "source_fd" in envelope
        action = action_value(envelope["action"], delivery=delivery)

        def active():
            # Native parent holds the writing end. Any additional data, EOF,
            # or parent death fences the action at its next pre-effect check.
            return os.getppid() == parent and not select.select([descriptor], [], [], 0)[0]

        store = ComputerFiles(Path.cwd() / "file-access", binding)
        if delivery:
            source = envelope["source_fd"]
            if type(source) is not int or source < 3:
                return 2
            info = os.fstat(source)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 0 or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077 or info.st_size != action.size):
                return 2
            result = store.execute_delivery(action, source, active=active)
        else:
            result = store.execute(action, active=active)
        print(json.dumps(bounded_frame(result), ensure_ascii=True), flush=True)
        return 0
    except (FileStateUnavailable, OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return 2
