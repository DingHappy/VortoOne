"""Check C5's launch contract without requiring a host acceptance run."""
import asyncio
from pathlib import Path
import signal
import stat
import time
from types import SimpleNamespace

import pytest

from src.sandbox import connected_command


def test_probe_argv_exposes_only_its_two_descriptors():
    outside = Path("/host-only/probe-marker")
    argv = connected_command._probe_argv(3, 9, outside, 46127)
    command_start = argv.index("--")
    options = argv[1:command_start]
    assert argv[0] == "/usr/bin/bwrap"
    assert {"--unshare-user", "--disable-userns", "--unshare-pid", "--unshare-ipc",
            "--unshare-net", "--clearenv", "--die-with-parent", "--new-session"} <= set(options)
    assert {"--bind", "--ro-bind", "--dev", "--proc", "--share-net", "--unshare-user-try"}.isdisjoint(options)
    assert options[options.index("--ro-bind-data") + 1:options.index("--ro-bind-data") + 3] == ["9", "/work/input"]
    assert options[options.index("--file") + 1:options.index("--file") + 3] == ["3", "/probe"]
    assert options[options.index("--remount-ro") + 1] == "/"
    environment_start = options.index("--clearenv")
    assert options[environment_start:environment_start + 7] == [
        "--clearenv", "--setenv", "LC_ALL", "C", "--setenv", "PWD", "/work",
    ]
    assert argv[command_start + 1:] == ["/probe", str(outside), "46127", "3", "9"]


def test_linux_probe_rejects_unsupported_hosts_before_start(monkeypatch):
    monkeypatch.setattr(connected_command.sys, "platform", "darwin")
    with pytest.raises(connected_command.IsolationUnavailable, match="Linux"):
        connected_command.check_isolation()


@pytest.mark.parametrize("owner,mode", [
    (0, 0o4755),
    (0, 0o2755),
    (0, 0o775),
    (0, 0o750),
    (1000, 0o755),
])
def test_linux_probe_rejects_untrusted_wrappers(monkeypatch, owner, mode):
    monkeypatch.setattr(connected_command.sys, "platform", "linux")
    monkeypatch.setattr(connected_command.os, "getuid", lambda: 1000)
    monkeypatch.setattr(connected_command.os, "geteuid", lambda: 1000)
    info = SimpleNamespace(st_uid=owner, st_mode=stat.S_IFREG | mode)
    monkeypatch.setattr(connected_command, "_BWRAP", SimpleNamespace(lstat=lambda: info))
    with pytest.raises(connected_command.IsolationUnavailable, match="系统文件"):
        connected_command._require_bubblewrap()


async def test_failed_bubblewrap_attempt_does_not_run_probe_directly(monkeypatch, tmp_path):
    program = tmp_path / "probe"
    input_file = tmp_path / "input"
    program.write_bytes(b"synthetic executable")
    input_file.write_bytes(b"synthetic input")
    calls = []

    async def failed(argv, **options):
        calls.append(argv)
        assert options["env"] == {}
        assert len(options["descriptors"]) == 2
        for descriptor in options["descriptors"]:
            connected_command.os.fstat(descriptor)
        return 1

    monkeypatch.setenv("VORTOONE_SANDBOX", "off")
    monkeypatch.setenv("VORTOONE_TEST_CREDENTIAL", "synthetic")
    monkeypatch.setattr(connected_command, "_run_process", failed)
    status = await connected_command._run_probe(
        program, input_file, tmp_path / "host", 46127,
        asyncio.get_running_loop().time() + 30, asyncio.Event(),
    )
    assert status == 1
    assert len(calls) == 1 and calls[0][0] == "/usr/bin/bwrap"


class _ProbeProcess:
    pid = 12345
    returncode = None

    def __init__(self):
        self.exited = asyncio.Event()
        self.waited = False

    async def wait(self):
        await self.exited.wait()
        self.waited = True
        return self.returncode

    def kill_group(self, group, signum):
        assert (group, signum) == (self.pid, signal.SIGKILL)
        self.returncode = -signum
        self.exited.set()


async def test_timeout_terminates_and_reaps_probe_wrapper(monkeypatch, tmp_path):
    process = _ProbeProcess()

    async def launch(*argv, **options):
        assert argv == ("/usr/bin/bwrap",)
        assert options["start_new_session"] is True
        assert options["close_fds"] is True
        assert options["stdin"] == connected_command.subprocess.DEVNULL
        assert options["pass_fds"] == ()
        assert options["env"] == {}
        return process

    monkeypatch.setattr(connected_command.os, "killpg", process.kill_group)
    monkeypatch.setattr(connected_command.asyncio, "create_subprocess_exec", launch)
    with pytest.raises(asyncio.TimeoutError):
        await connected_command._run_process(
            ["/usr/bin/bwrap"], directory=tmp_path, env={},
            deadline=asyncio.get_running_loop().time() + 0.1, cancel=asyncio.Event(),
        )
    assert process.waited is True
    assert process.returncode == -signal.SIGKILL


@pytest.mark.parametrize("cancel_caller", [False, True])
async def test_cancellation_during_spawn_still_reaps_probe(monkeypatch, tmp_path, cancel_caller):
    process = _ProbeProcess()
    launching = asyncio.Event()
    finish_spawn = asyncio.Event()
    cancel = asyncio.Event()

    async def launch(*argv, **options):
        launching.set()
        await finish_spawn.wait()
        return process

    monkeypatch.setattr(connected_command.os, "killpg", process.kill_group)
    monkeypatch.setattr(connected_command.asyncio, "create_subprocess_exec", launch)
    work = asyncio.create_task(connected_command._run_process(
        ["/usr/bin/bwrap"], directory=tmp_path, env={},
        deadline=asyncio.get_running_loop().time() + 5, cancel=cancel,
    ))
    await launching.wait()
    if cancel_caller:
        work.cancel()
    else:
        cancel.set()
    finish_spawn.set()
    expected_error = asyncio.CancelledError if cancel_caller else connected_command._FixtureCancelled
    with pytest.raises(expected_error):
        await work
    assert process.waited is True
    assert process.returncode == -signal.SIGKILL


async def test_fixture_success_returns_only_synthetic_report(monkeypatch):
    async def passed(deadline, cancel):
        assert not cancel.is_set()
        remaining = deadline - asyncio.get_running_loop().time()
        assert 0 < remaining <= connected_command._CHECK_SECONDS
        return 0

    monkeypatch.setattr(connected_command, "_check_isolation", passed)
    result = await connected_command.run_fixture(deadline=time.time() + 1000, cancel=asyncio.Event())
    assert result.status == "succeeded"
    assert result.exit_code == 0
    assert result.log == "Linux 本机隔离探针通过。"
    assert result.report is not None and b"synthetic-boundary-check" in result.report


async def test_failed_isolation_returns_exit_status_without_report(monkeypatch):
    async def failed(deadline, cancel):
        return 24

    monkeypatch.setattr(connected_command, "_check_isolation", failed)
    result = await connected_command.run_fixture(deadline=time.time() + 5, cancel=asyncio.Event())
    assert result.status == "failed"
    assert result.exit_code == 24
    assert result.log == "能连接宿主回环监听器。"
    assert result.report is None


@pytest.mark.parametrize(("problem", "exit_code"), [
    (connected_command.IsolationUnavailable("Bubblewrap 未安装"), None),
    (OSError("socket unavailable"), None),
    (None, 1),
])
async def test_unavailable_isolation_blocks_fixture_without_report(monkeypatch, problem, exit_code):
    async def unavailable(deadline, cancel):
        if problem is not None:
            raise problem
        return exit_code

    monkeypatch.setattr(connected_command, "_check_isolation", unavailable)
    result = await connected_command.run_fixture(deadline=time.time() + 5, cancel=asyncio.Event())
    assert result.status == "blocked"
    assert result.exit_code == exit_code
    assert result.report is None


async def test_expired_or_cancelled_fixture_does_not_enter_preflight(monkeypatch):
    async def unexpected(*args):
        pytest.fail("preflight must not start")

    monkeypatch.setattr(connected_command, "_check_isolation", unexpected)
    cancel = asyncio.Event()
    expired = await connected_command.run_fixture(deadline=time.time() - 1, cancel=cancel)
    assert expired.status == "timed_out" and expired.report is None
    cancel.set()
    cancelled = await connected_command.run_fixture(deadline=time.time() + 5, cancel=cancel)
    assert cancelled.status == "cancelled" and cancelled.report is None
