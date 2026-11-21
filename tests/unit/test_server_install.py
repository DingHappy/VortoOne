"""Execute installer shell functions against isolated processes and service managers."""
import json
import os
import shlex
import shutil
import socketserver
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest


INSTALLER = Path(__file__).resolve().parents[2] / "deploy" / "install.sh"


def _executable(path, body):
    path.write_text(f"#!{sys.executable}\n" + body)
    path.chmod(0o700)


def _home(tmp_path, *, mock_curl=True):
    root = tmp_path / "install"
    commands = tmp_path / "commands"
    commands.mkdir()
    (root / "home").mkdir(parents=True)
    (root / "home" / "data").write_text("before upgrade")
    for version in ("1", "2"):
        target = root / "versions" / version
        (target / "bin").mkdir(parents=True)
        (target / "bin" / "python").symlink_to(sys.executable)
        (target / ".complete").write_text(version)
        _executable(target / "bin" / "vc", """
import json, os, shutil, sys
from pathlib import Path
root = Path(os.environ['INSTALL_ROOT'])
if sys.argv[1] == 'home-backup':
    folder = root / 'backups'
    folder.mkdir(exist_ok=True)
    backup = folder / 'before upgrade'
    shutil.copyfile(root / 'home' / 'data', backup)
    print(json.dumps({'path': str(backup)}))
elif sys.argv[1] == 'home-restore':
    with (root / 'events').open('a') as out:
        out.write('restore\\n')
    if os.environ.get('FAIL_RESTORE'):
        sys.exit(1)
    shutil.copyfile(sys.argv[2], root / 'home' / 'data')
""")
    (root / "current").symlink_to("versions/1")
    _executable(commands / "uname", "print('Linux')\n")
    _executable(commands / "loginctl", "print('yes')\n")
    _executable(commands / "systemctl", """
import os, sys
from pathlib import Path
root = Path(os.environ['INSTALL_ROOT'])
args = sys.argv[1:]
version = (root / 'current').readlink().name
with (root / 'events').open('a') as out:
    out.write(' '.join(args) + ' version=' + version + '\\n')
if 'stop' in args:
    if os.environ.get('FAIL_STOP_' + ('OLD' if version == '1' else 'NEW')):
        sys.exit(1)
elif 'show' in args:
    if 'ActiveState' in args:
        print('active' if os.environ.get('STOP_RETURN_ACTIVE') else 'inactive')
    else:
        print('0')
elif version == '1' and 'restart' in args and os.environ.get('FAIL_OLD_START'):
    sys.exit(1)
elif version == '2':
    operation = os.environ.get('FAIL_OPERATION', 'restart')
    matches = (operation == 'restart' and 'restart' in args or
               operation == 'reload' and 'daemon-reload' in args or
               operation == 'timer' and 'vortoone-backup.timer' in args or
               operation == 'enable' and 'enable' in args and 'vortoone.service' in args)
    if matches:
        (root / 'home' / 'data').write_text('new version touched data')
        sys.exit(1)
""")
    if mock_curl:
        _executable(commands / "curl", """
import json, os, sys
from pathlib import Path
root = Path(os.environ['INSTALL_ROOT'])
version = (root / 'current').readlink().name
if version == '1' and os.environ.get('FAIL_OLD_CHECK'):
    sys.exit(7)
with (root / 'events').open('a') as out:
    out.write('probe ' + sys.argv[-1] + ' version=' + version + '\\n')
print(json.dumps({'version': version}))
""")
    library = tmp_path / "installer-functions.sh"
    # Source the actual functions without invoking the package-download entry point.
    library.write_text(INSTALLER.read_text().rsplit('\nmain "$@"', 1)[0] + "\n")
    env = os.environ.copy()
    env.update(INSTALL_ROOT=str(root), HOME=str(tmp_path / "fake-home"),
               XDG_CONFIG_HOME=str(tmp_path / "fake-config"),
               NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost",
               PATH=str(commands) + os.pathsep + env["PATH"])
    return root, library, env


def _shell(root, library, env, command, *, service=1, port=18888, ready_timeout=10, stop_timeout=5):
    script = f""". {shlex.quote(str(library))}
set -eu
DIR={shlex.quote(str(root))}
TARGET="$DIR/versions/2"
VERSION=2 SERVICE={service} PORT={port} STOP_TIMEOUT={stop_timeout} READY_TIMEOUT={ready_timeout}
{command}
"""
    return subprocess.run(["sh", "-c", script], env=env, capture_output=True,
                          text=True, timeout=20)


@pytest.mark.parametrize("operation", ["restart", "reload", "timer", "enable"])
def test_manager_start_failures_restore_old_version_and_data(tmp_path, operation):
    root, library, env = _home(tmp_path)
    env["FAIL_OPERATION"] = operation
    result = _shell(root, library, env, "upgrade 1")
    assert result.returncode == 1
    assert (root / "current").readlink() == Path("versions/1")
    assert (root / "home" / "data").read_text() == "before upgrade"
    record = json.loads((root / "upgrade.json").read_text())
    assert record["stage"] == "rolled_back" and record["failure_stage"] == "start_failed"
    assert record["ok"] is False and "启动失败" in record["reason"]
    events = (root / "events").read_text()
    assert "restore\n" in events and "restart vortoone.service version=1" in events
    assert "/api/instance version=1" in events


@pytest.mark.parametrize("condition", ["FAIL_STOP_OLD", "STOP_RETURN_ACTIVE"])
def test_upgrade_does_not_backup_or_switch_before_confirmed_stop(tmp_path, condition):
    root, library, env = _home(tmp_path)
    env[condition] = "1"
    result = _shell(root, library, env, "upgrade 1")
    assert result.returncode == 1
    assert (root / "current").readlink() == Path("versions/1")
    assert not (root / "backups").exists()
    assert json.loads((root / "upgrade.json").read_text())["stage"] == "stop_old_failed"


@pytest.mark.parametrize("failure,stage", [
    ("FAIL_STOP_NEW", "rollback_stop_failed"),
    ("FAIL_RESTORE", "rollback_restore_failed"),
])
def test_incomplete_rollback_stays_stopped_and_records_failed_stage(tmp_path, failure, stage):
    root, library, env = _home(tmp_path)
    env[failure] = "1"
    result = _shell(root, library, env, "upgrade 1")
    assert result.returncode == 1
    assert json.loads((root / "upgrade.json").read_text())["stage"] == stage
    assert (root / "home" / "data").read_text() == "new version touched data"
    events = (root / "events").read_text()
    assert "restart vortoone.service version=1" not in events
    assert "已退回" not in result.stdout + result.stderr
    if failure == "FAIL_STOP_NEW":
        assert "restore\n" not in events
        assert (root / "current").readlink() == Path("versions/2")


@pytest.mark.parametrize("failure,stage", [
    ("FAIL_OLD_START", "rollback_start_failed"), ("FAIL_OLD_CHECK", "rollback_check_failed"),
])
def test_old_service_must_restart_and_pass_check_before_claiming_rollback(tmp_path, failure, stage):
    root, library, env = _home(tmp_path)
    env[failure] = "1"
    result = _shell(root, library, env, "upgrade 1", ready_timeout=2)
    assert result.returncode == 1
    assert json.loads((root / "upgrade.json").read_text())["stage"] == stage
    assert (root / "home" / "data").read_text() == "before upgrade"
    assert "已退回" not in result.stdout + result.stderr


@pytest.mark.parametrize("failure", ["server", "backup", "stop", "query"])
def test_launchd_failure_uses_same_safe_rollback_path(tmp_path, failure):
    root, library, env = _home(tmp_path)
    commands = tmp_path / "commands"
    _executable(commands / "uname", "print('Darwin')\n")
    jobs = root / "jobs"
    jobs.mkdir()
    (jobs / "server").touch()
    _executable(commands / "launchctl", """
import os, sys
from pathlib import Path
root = Path(os.environ['INSTALL_ROOT'])
version = (root / 'current').readlink().name
args = sys.argv[1:]
operation = args[0]
name = args[-1].split('/')[-1].removesuffix('.plist').split('.')[-1]
job = root / 'jobs' / name
failure = os.environ['FAIL_LAUNCH']
if operation == 'print':
    if args[1].count('/') == 1:
        print('user domain')
    elif failure == 'query':
        sys.exit(5)
    elif job.exists():
        print('state = waiting')
    else:
        sys.exit(113)
elif operation == 'bootout':
    if failure == 'stop':
        sys.exit(1)
    job.unlink(missing_ok=True)
elif operation == 'bootstrap':
    if version == '2' and failure == name:
        (root / 'home' / 'data').write_text('new version touched data')
        sys.exit(1)
    job.touch()
""")
    env["FAIL_LAUNCH"] = failure
    result = _shell(root, library, env, "upgrade 1")
    assert result.returncode == 1
    assert (root / "current").readlink() == Path("versions/1")
    assert (root / "home" / "data").read_text() == "before upgrade"
    record = json.loads((root / "upgrade.json").read_text())
    assert record["stage"] == ("stop_old_failed" if failure in ("stop", "query") else "rolled_back")
    if failure == "query":
        assert not (root / "backups").exists()
        assert "查询退出码 5" in record["reason"]


@pytest.mark.parametrize("query_status,expected_return", [(113, 0), (5, 1)])
def test_launchd_only_treats_explicit_unregistered_status_as_absent(tmp_path, query_status, expected_return):
    root, library, env = _home(tmp_path)
    _executable(tmp_path / "commands" / "launchctl", f"""
import sys
if sys.argv[1] != 'print':
    raise RuntimeError('must not unload an unverified job')
sys.exit(0 if sys.argv[2].count('/') == 1 else {query_status})
""")
    result = _shell(root, library, env, "if bootout_job com.vorto.vortoone.server; then exit 0; else echo \"$WHY\"; exit 1; fi")
    assert result.returncode == expected_return
    if query_status == 5:
        assert "查询退出码 5" in result.stdout


@pytest.mark.parametrize("ignore_term", [False, True])
def test_background_stop_keeps_pid_when_process_refuses_to_exit(tmp_path, ignore_term):
    root, library, env = _home(tmp_path)
    worker = subprocess.Popen([sys.executable, "-c", f"""
import signal, time
signal.signal(signal.SIGTERM, signal.SIG_IGN if {ignore_term!r} else signal.SIG_DFL)
print('ready', flush=True)
time.sleep(60)
"""], stdout=subprocess.PIPE, text=True)
    try:
        assert worker.stdout.readline().strip() == "ready"
        (root / "server.pid").write_text(str(worker.pid))
        result = _shell(root, library, env, "stop_server", service=2,
                        stop_timeout=1 if ignore_term else 5)
        if ignore_term:
            assert result.returncode != 0 and worker.poll() is None
            assert (root / "server.pid").read_text() == str(worker.pid)
        else:
            assert result.returncode == 0
            assert not (root / "server.pid").exists()
            worker.wait(timeout=2)
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.wait()
        worker.stdout.close()


@contextmanager
def _health_server(hang_path, delay_health=0):
    finish = threading.Event()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            path = self.rfile.readline().decode().split()[1]
            if path == hang_path:
                finish.wait(8)
                return
            if path == "/api/health/quick":
                finish.wait(delay_health)
            body = b'{"version":"2"}'
            self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode()
                             + b"\r\nConnection: close\r\n\r\n" + body)

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True

    with Server(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address[1]
        finally:
            finish.set()
            server.shutdown()
            thread.join(timeout=2)


@pytest.mark.parametrize("hang_path,delay_health", [
    ("/api/health/quick", 0), ("/api/instance", 0), ("/api/instance", 1.2),
])
def test_self_check_bounds_silent_health_and_version_requests(tmp_path, hang_path, delay_health):
    if not shutil.which("curl"):
        pytest.skip("curl is required by the installer")
    root, library, env = _home(tmp_path, mock_curl=False)
    with _health_server(hang_path, delay_health) as port:
        started = time.monotonic()
        result = _shell(root, library, env, "if self_check; then exit 0; else echo \"$WHY\"; exit 1; fi",
                        service=0, port=port, ready_timeout=2)
        elapsed = time.monotonic() - started
    assert result.returncode == 1
    assert elapsed < 4.5
    assert "应答" in result.stdout or "版本自检请求" in result.stdout


def test_loopback_self_check_does_not_use_users_http_proxy(tmp_path):
    if not shutil.which("curl"):
        pytest.skip("curl is required by the installer")
    root, library, env = _home(tmp_path, mock_curl=False)
    env.update(NO_PROXY="", no_proxy="", http_proxy="http://127.0.0.1:1",
               ALL_PROXY="http://127.0.0.1:1", all_proxy="http://127.0.0.1:1")
    with _health_server("") as port:
        result = _shell(root, library, env, "self_check", service=0, port=port)
    assert result.returncode == 0, result.stdout + result.stderr
