"""Fixed local Linux isolation check for connected computers."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
from typing import Literal

_BWRAP = Path("/usr/bin/bwrap")
_CC = Path("/usr/bin/cc")
_PROBE_SOURCE = Path(__file__).with_name("connected_command_probe.c")
_PROBE_INPUT = b"vortoone-isolation-probe\n"
_WORK_BYTES = 1024 * 1024
_MAX_PROGRAM_BYTES = 16 * 1024 * 1024
_COMPILE_SECONDS = 20
_PROBE_SECONDS = 5
_CHECK_SECONDS = _COMPILE_SECONDS + _PROBE_SECONDS
_REPORT = (
    b'<testsuite name="vortoone-isolation-fixture" tests="1" failures="0">'
    b'<testcase name="synthetic-boundary-check"/></testsuite>\n'
)
_FAILURES = {
    20: "沙箱内的输入或临时目录不可用。",
    21: "只读输入或沙箱根目录可写。",
    22: "可访问宿主文件。",
    23: "存在未预期的环境变量或文件描述符。",
    24: "能连接宿主回环监听器。",
    25: "无法判断宿主网络边界。",
}


class IsolationUnavailable(RuntimeError):
    """The probe could not establish the required OS isolation."""


class _FixtureCancelled(Exception):
    pass


@dataclass(frozen=True)
class FixtureRun:
    status: Literal["succeeded", "failed", "blocked", "cancelled", "timed_out"]
    exit_code: int | None
    log: str
    report: bytes | None = None


def _require_bubblewrap() -> None:
    if sys.platform != "linux":
        raise IsolationUnavailable("此隔离探针目前支持 Linux。")
    if os.getuid() == 0 or os.geteuid() != os.getuid():
        raise IsolationUnavailable("请用普通本机用户运行隔离探针。")
    try:
        info = _BWRAP.lstat()
    except OSError as error:
        raise IsolationUnavailable("找不到系统安装的 Bubblewrap。") from error
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
            or info.st_mode & (stat.S_ISUID | stat.S_ISGID | 0o022)
            or not info.st_mode & stat.S_IXOTH):
        raise IsolationUnavailable("Bubblewrap 必须是普通用户可执行的系统文件。")


def _probe_argv(program_fd: int, input_fd: int, outside: Path, port: int) -> list[str]:
    return [
        str(_BWRAP),
        "--unshare-user", "--disable-userns",
        "--unshare-pid", "--unshare-ipc", "--unshare-net", "--unshare-uts",
        "--cap-drop", "ALL", "--die-with-parent", "--new-session",
        "--clearenv", "--setenv", "LC_ALL", "C", "--setenv", "PWD", "/work",
        "--size", str(_WORK_BYTES), "--perms", "0700", "--tmpfs", "/work",
        "--perms", "0400", "--ro-bind-data", str(input_fd), "/work/input",
        "--perms", "0555", "--file", str(program_fd), "/probe",
        "--remount-ro", "/", "--chdir", "/work",
        "--", "/probe", str(outside), str(port), str(program_fd), str(input_fd),
    ]


async def _wait_for_exit(
    process: asyncio.subprocess.Process, cancel: asyncio.Event, deadline: float
) -> int:
    done = asyncio.create_task(process.wait())
    cancelled = asyncio.create_task(cancel.wait())
    try:
        loop = asyncio.get_running_loop()
        remaining = max(0, deadline - loop.time())
        await asyncio.wait((done, cancelled), return_when=asyncio.FIRST_COMPLETED, timeout=remaining)
        if cancel.is_set():
            raise _FixtureCancelled
        if done.done() and loop.time() < deadline:
            return done.result()
        raise asyncio.TimeoutError
    finally:
        for task in (done, cancelled):
            task.cancel()
        await asyncio.gather(done, cancelled, return_exceptions=True)


async def _stop_process_group(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    await process.wait()


async def _run_process(
    argv: list[str],
    *,
    directory: Path,
    env: dict[str, str],
    deadline: float,
    cancel: asyncio.Event,
    descriptors: tuple[int, ...] = (),
) -> int:
    if cancel.is_set():
        raise _FixtureCancelled
    if deadline <= asyncio.get_running_loop().time():
        raise asyncio.TimeoutError
    launch = asyncio.create_task(asyncio.create_subprocess_exec(
        *argv, cwd=directory, env=env, close_fds=True, pass_fds=descriptors,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    ))
    try:
        process = await asyncio.shield(launch)
    except asyncio.CancelledError:
        # If our caller is cancelled during spawn, we still own the child.
        try:
            process = await launch
        except OSError:
            pass
        else:
            await _stop_process_group(process)
        raise
    try:
        return await _wait_for_exit(process, cancel, deadline)
    finally:
        await _stop_process_group(process)


async def _compile_probe(directory: Path, deadline: float, cancel: asyncio.Event) -> Path:
    program = directory / "probe"
    try:
        status = await _run_process(
            [str(_CC), "-static", "-O2", "-Wall", "-Werror", str(_PROBE_SOURCE), "-o", str(program)],
            directory=directory, env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
            deadline=min(deadline, asyncio.get_running_loop().time() + _COMPILE_SECONDS),
            cancel=cancel,
        )
        if status != 0:
            raise IsolationUnavailable("无法编译固定隔离探针。需要本机 C 编译器与静态运行库。")
        if not 0 < program.stat().st_size <= _MAX_PROGRAM_BYTES:
            raise IsolationUnavailable("隔离探针程序大小无效。")
    except asyncio.TimeoutError:
        raise
    except OSError as error:
        raise IsolationUnavailable("无法编译固定隔离探针。需要本机 C 编译器与静态运行库。") from error
    return program


async def _run_probe(
    program: Path, source: Path, outside: Path, port: int, deadline: float, cancel: asyncio.Event
) -> int:
    with program.open("rb") as executable, source.open("rb") as contents:
        descriptors = (executable.fileno(), contents.fileno())
        argv = _probe_argv(*descriptors, outside, port)
        # These descriptors carry only the fixed executable and synthetic input.
        return await _run_process(
            argv, directory=program.parent, env={}, descriptors=descriptors,
            deadline=min(deadline, asyncio.get_running_loop().time() + _PROBE_SECONDS),
            cancel=cancel,
        )


async def _check_isolation(deadline: float, cancel: asyncio.Event) -> int:
    _require_bubblewrap()
    with tempfile.TemporaryDirectory(prefix="vortoone-command-isolation-") as scratch:
        directory = Path(scratch).resolve()
        program = await _compile_probe(directory, deadline, cancel)
        source = directory / "input"
        source.write_bytes(_PROBE_INPUT)
        outside = directory / "host-only"
        outside.write_bytes(_PROBE_INPUT)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(1)
            address = listener.getsockname()
            with socket.create_connection(address, timeout=1):
                connected, _ = listener.accept()
                connected.close()
            status = await _run_probe(program, source, outside, address[1], deadline, cancel)
        if outside.read_bytes() != _PROBE_INPUT or source.read_bytes() != _PROBE_INPUT:
            raise IsolationUnavailable("探针检测到宿主文件被更改。")
    return status


async def run_fixture(*, deadline: float, cancel: asyncio.Event) -> FixtureRun:
    """Run a fixed synthetic test, with a Unix-epoch deadline and explicit stop signal."""
    if cancel.is_set():
        return FixtureRun("cancelled", None, "本机隔离探针已取消。")
    if type(deadline) not in (float, int) or not 0 < deadline < 2**53:
        return FixtureRun("failed", None, "探针期限无效。")
    remaining = min(deadline - time.time(), _CHECK_SECONDS)
    if remaining <= 0:
        return FixtureRun("timed_out", None, "本机隔离探针已超时。")
    monotonic_deadline = asyncio.get_running_loop().time() + remaining
    try:
        status = await _check_isolation(monotonic_deadline, cancel)
    except _FixtureCancelled:
        return FixtureRun("cancelled", None, "本机隔离探针已取消。")
    except asyncio.TimeoutError:
        return FixtureRun("timed_out", None, "本机隔离探针已超时。")
    except IsolationUnavailable as error:
        return FixtureRun("blocked", None, str(error))
    except OSError:
        return FixtureRun("blocked", None, "无法完成本机隔离探针。")
    if status in _FAILURES:
        return FixtureRun("failed", status, _FAILURES[status])
    if status != 0:
        return FixtureRun("blocked", status, "Bubblewrap 未能运行固定隔离探针。")
    return FixtureRun("succeeded", 0, "Linux 本机隔离探针通过。", _REPORT)


def check_isolation() -> None:
    """Synchronous local preflight. It never changes capability state."""
    result = asyncio.run(run_fixture(deadline=time.time() + _CHECK_SECONDS, cancel=asyncio.Event()))
    if result.status != "succeeded":
        raise IsolationUnavailable(result.log)


def main() -> int:
    try:
        if len(sys.argv) != 1:
            raise IsolationUnavailable("隔离探针无需参数。")
        check_isolation()
    except IsolationUnavailable as error:
        print(error, file=sys.stderr)
        return 1
    print("Linux 本机隔离探针通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
