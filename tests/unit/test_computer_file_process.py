"""Exercise the private action pipe and durable state with real child processes."""
from dataclasses import asdict
import asyncio
import json
import os
import sys
import time
import uuid

import pytest

from src.gateway import computer_file_process as child
from src.gateway.computer_file_store import ComputerFiles, FileAction
from src.gateway.computer_protocol import BINDING_FIELDS

pytestmark = pytest.mark.skipif(os.name != "posix", reason="requires private POSIX pipes and pinned directories")
BINDING = {field: format(index, "x") * 32 for index, field in enumerate(BINDING_FIELDS, 1)}


@pytest.fixture
def prepared(tmp_path):
    root = tmp_path.resolve()
    directory = root / "authorized"
    directory.mkdir()
    store = ComputerFiles(root / "file-access", BINDING)
    selected = store.grant(str(directory), "Authorized", read=True, write=True)
    revision = store.policy()["policy_revision"]

    def make(operation, content=""):
        return FileAction(uuid.uuid4().hex, selected["folder_id"], revision, operation, "child.txt",
                          store.preview(operation, selected["folder_id"], "child.txt", revision),
                          int(time.time()) + 45, "session", "turn", "call", content)

    return root, directory, store, make


async def test_actual_child_executes_a_fixed_action_without_parent_secrets(prepared, monkeypatch):
    root, directory, store, make = prepared
    monkeypatch.setenv("VORTOONE_API_TOKEN", "synthetic-parent-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-model-secret")
    spawn = asyncio.create_subprocess_exec
    children = []

    async def inspect(*args, **kwargs):
        assert args[-1] == "computer-file-action"
        assert all("child.txt" not in part and "synthetic-" not in part for part in args)
        assert kwargs["cwd"] == root
        assert set(kwargs["env"]) == {"PYTHONPATH"}
        assert kwargs["stdin"] == asyncio.subprocess.PIPE
        assert kwargs["close_fds"] is True
        process = await spawn(*args, **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", inspect)
    stop, revoked = asyncio.Event(), asyncio.Event()
    created = make("create", "内容只通过动作管道传递\n")
    receipt = await child.run_file_action(root, BINDING, created, stop=stop, revoked=revoked)
    assert receipt == {"action_id": created.action_id, "digest": created.digest, "status": "created"}
    assert children[0].pid != os.getpid() and children[0].returncode == 0
    assert (directory / created.path).read_text(encoding="utf-8") == created.content
    assert store.pending() == [receipt]
    read = make("read")
    result = await child.run_file_action(root, BINDING, read, stop=stop, revoked=revoked)
    assert result["status"] == "read" and result["text"] == created.content


@pytest.mark.parametrize("entry", ["src.runtime_entry", "desktop.runtime_entry"])
async def test_pipe_without_the_live_parent_cannot_publish(entry, prepared):
    root, directory, store, make = prepared
    action = make("create", "must not be published")
    marker = {"parent_pid": os.getpid(), "binding": BINDING, "action": asdict(action)}
    _, env = child._child_launch()

    async def send(envelope):
        process = await asyncio.create_subprocess_exec(sys.executable, "-m", entry, "computer-file-action",
                                                        cwd=root, env=env, stdin=asyncio.subprocess.PIPE,
                                                        stdout=asyncio.subprocess.PIPE)
        output, _ = await asyncio.wait_for(process.communicate((json.dumps(envelope) + "\n").encode()), 10)
        return process.returncode, output

    assert await send({**marker, "parent_pid": 1}) == (2, b"")
    assert store.pending() == []
    code, output = await send(marker)  # communicate closes stdin before the child starts work.
    assert code == 0 and json.loads(output)["status"] == "blocked"
    assert not (directory / action.path).exists()


_PARK_AFTER_START = """
from pathlib import Path
import os
import time
from src.gateway.computer_file_paths import PinnedFile
from src.gateway.computer_file_process import main

create = PinnedFile.create

def park(self, data, version, *, before_publish):
    Path('staged.pid').write_text(str(os.getpid()))
    until = time.monotonic() + 10
    while not Path('continue').exists() and time.monotonic() < until:
        time.sleep(0.01)
    create(self, data, version, before_publish=before_publish)

PinnedFile.create = park
raise SystemExit(main())
"""


def park_file_child(monkeypatch):
    launch = child._child_launch
    _, env = launch()
    monkeypatch.setattr(child, "_child_launch", lambda: ([sys.executable, "-c", _PARK_AFTER_START], env))
    spawned = asyncio.Queue()
    spawn = asyncio.create_subprocess_exec

    async def observe(*args, **kwargs):
        process = await spawn(*args, **kwargs)
        spawned.put_nowait(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", observe)
    return launch, spawned


@pytest.mark.parametrize("revoke", [True, False], ids=["revoke", "crash"])
async def test_child_staged_action_and_recovery(prepared, monkeypatch, revoke):
    root, directory, store, make = prepared
    action = make("create", "one intended effect")
    launch, spawned = park_file_child(monkeypatch)
    stop, revoked = asyncio.Event(), asyncio.Event()

    async def until(predicate):
        while not predicate():
            await asyncio.sleep(0.01)

    task = asyncio.create_task(child.run_file_action(root, BINDING, action, stop=stop, revoked=revoked))
    try:
        process = await asyncio.wait_for(spawned.get(), 3)
        staged = root / "staged.pid"
        await asyncio.wait_for(until(lambda: staged.exists() and staged.stat().st_size > 0), 3)
        assert int(staged.read_text()) == process.pid
        assert not (directory / action.path).exists()
        if revoke:
            revoked.set()
            await asyncio.wait_for(until(process.stdin.is_closing), 2)
            (root / "continue").touch()
            receipt = await asyncio.wait_for(task, 3)
            assert receipt["status"] == "blocked"
        else:
            process.kill()
            with pytest.raises(child.FileProcessUnavailable):
                await asyncio.wait_for(task, 3)
            assert store.pending() == [{"action_id": action.action_id, "digest": action.digest, "status": "unknown"}]
            monkeypatch.setattr(child, "_child_launch", launch)
            receipt = await child.run_file_action(root, BINDING, action, stop=stop, revoked=revoked)
            assert receipt["status"] == "unknown"
        assert process.returncode is not None
        assert not (directory / action.path).exists()
        assert store.pending() == [receipt]
    finally:
        revoked.set()
        (root / "continue").touch()
        await asyncio.gather(task, return_exceptions=True)
