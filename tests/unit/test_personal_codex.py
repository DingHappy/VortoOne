import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agents import external_workers as workers


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(workers, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(workers, "_personal_codex_cached_status", None)
    monkeypatch.delenv("VORTOONE_WORKER_HOME", raising=False)
    home, project = tmp_path / "personal-auth", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENAI_BASE_URL", "HTTPS_PROXY",
                "GITHUB_TOKEN", "NODE_OPTIONS"):
        monkeypatch.setenv(key, "fixture-do-not-inherit")
    binary = tmp_path / "codex"
    binary.write_text(f"#!{sys.executable}\n" + '''
import json
import os
import sys
from pathlib import Path

argv = sys.argv[1:]
assert "fixture-do-not-inherit" not in os.environ.values()
runtime = Path(__file__).parent
if argv == ["--version"]:
    print("codex-cli 0.172.0")
elif argv[0] == "login":
    assert argv[-1] == "status"
    print((runtime / "status").read_text().strip(), file=sys.stderr)
elif argv[0] == "exec":
    (runtime / "launch").write_text(json.dumps({
        "argv": argv, "stdin": sys.stdin.read(), "cwd": os.getcwd(), "home": os.environ["CODEX_HOME"],
    }))
    print(json.dumps({"type": "thread.started", "thread_id": "synthetic-thread"}))
    print(json.dumps({"type": "item.completed", "item": {"type": "command_execution",
              "command": "fixture-private-command", "aggregated_output": "fixture-sensitive-output", "exit_code": 0}}))
    if (runtime / "fail").exists():
        print("fixture-sensitive-stderr", file=sys.stderr)
        print(json.dumps({"type": "turn.failed", "error": {"message": "429 fixture-sensitive-error"}}))
        sys.exit(1)
    print(json.dumps({"type": "error", "message": "fixture-sensitive-retry"}))
    print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Synthetic answer"}}))
    if (runtime / "incomplete").exists():
        sys.exit(0)
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 6}}))
else:
    sys.exit(70)
''', encoding="utf-8")
    binary.chmod(0o700)
    (tmp_path / "status").write_text("Logged in using ChatGPT", encoding="ascii")
    monkeypatch.setattr(workers, "worker_binary", lambda name: str(binary))
    calls = []

    def isolated(binary, cwd, config, write):
        calls.append((cwd, config, write))
        return True

    monkeypatch.setattr(workers, "_personal_codex_isolated", isolated)
    return SimpleNamespace(root=tmp_path, home=home, project=project, probes=calls)


def test_subscription_status_has_short_cache_and_fails_closed_without_sandbox(fixture, monkeypatch):
    assert workers.personal_codex_status() == "ready"
    assert len(fixture.probes) == 1
    assert workers.personal_codex_status() == "ready"
    assert len(fixture.probes) == 1
    monkeypatch.setattr(workers, "_personal_codex_cached_status", None)
    monkeypatch.setattr(workers, "_personal_codex_isolated", lambda *args: False)
    assert workers.personal_codex_status() == "unsupported"


@pytest.mark.parametrize("status", ["API key configured - sk-demo", "Logged in using access token",
                                     "launch error: Logged in using ChatGPT", "Not logged in"])
async def test_other_authentication_and_unknown_status_never_launch(fixture, status):
    (fixture.root / "status").write_text(status, encoding="ascii")
    assert workers.personal_codex_status() == "signed_out"
    run = await workers.run_personal_codex("fixture only", str(fixture.project), write=True)
    assert not run.started and run.failure_code == "unavailable" and not (fixture.root / "launch").exists()
    assert status not in run.error


@pytest.mark.parametrize("write", [False, True])
async def test_subscription_launch_uses_scoped_profile_and_stdin(fixture, write):
    snapshots = []
    events = []
    run = await workers.run_personal_codex("message from fixture --trust", str(fixture.project),
                                          write=write, system="owner prompt", model="gpt-5.4",
                                          on_snapshot=snapshots.append, on_event=events.append)
    assert run.ok and run.final_text == "Synthetic answer" and run.session_id == "synthetic-thread"
    launch = json.loads((fixture.root / "launch").read_text())
    flags = launch["argv"]
    config = [value for flag, value in zip(flags, flags[1:]) if flag == "-c"]
    assert launch["stdin"] == "owner prompt\n\n---\n\nmessage from fixture --trust"
    assert launch["cwd"] == str(fixture.project.resolve()) and launch["home"] == str(fixture.home.resolve())
    assert all(flag in flags for flag in ("--ignore-user-config", "--ignore-rules", "--strict-config", "--ephemeral"))
    assert "message from fixture --trust" not in flags and "--trust" not in flags and "-s" not in flags
    assert 'model_provider="openai"' in config and 'forced_login_method="chatgpt"' in config
    assert 'approval_policy="never"' in config and 'web_search="disabled"' in config and 'mcp_servers={}' in config
    assert 'features.hooks=false' in config and 'features.skip_host_skill_discovery=true' in config
    assert fixture.probes == [(fixture.project.resolve(), flags[flags.index("-c"):flags.index("-m")], write)]
    expected = "write" if write else "read"
    profile = next(value for value in config if value.startswith("permissions."))
    assert f'"{fixture.project.resolve()}"="{expected}"' in profile
    assert f'"{fixture.project.resolve() / ".git"}"="read"' in profile
    assert f'"{fixture.home.resolve()}"="deny"' in profile and 'network={enabled=false}' in profile
    assert run.usage == {"input_tokens": 10, "output_tokens": 6}
    assert snapshots[-1].result.summary == "Synthetic answer"
    assert not any("fixture-private" in repr(event) or "fixture-sensitive" in repr(event) for event in events)


async def test_fresh_sandbox_probe_failure_or_resident_mode_never_launches(fixture, monkeypatch):
    monkeypatch.setattr(workers, "_personal_codex_isolated", lambda *args: False)
    result = await workers.run_personal_codex("fixture only", str(fixture.project), write=False)
    assert not result.started and not (fixture.root / "launch").exists()
    monkeypatch.setenv("VORTOONE_WORKER_HOME", str(fixture.root / "resident"))
    assert workers.personal_codex_status() == "unsupported"
    result = await workers.run_personal_codex("fixture only", str(fixture.project), write=False)
    assert not result.started and not (fixture.root / "launch").exists()


async def test_cli_error_keeps_rate_limit_signal_without_raw_diagnostics(fixture, caplog):
    (fixture.root / "fail").touch()
    reply = await workers.run_personal_codex("fixture only", str(fixture.project), write=True)
    assert reply.started and not reply.ok and reply.rate_limited and reply.exit_code == 1
    assert "用量" in reply.error
    assert "fixture-sensitive" not in repr(reply) and "fixture-sensitive" not in caplog.text


def test_sandbox_probe_checks_synthetic_links_and_fails_closed_on_no_effect(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    auth = tmp_path / "private-auth"
    conf = workers._personal_codex_permissions(project, auth, True)

    def fake_sandbox(argv, cwd, env):
        assert cwd == project and Path(env["CODEX_HOME"]) != auth
        visible, hidden, alias, target = map(Path, argv[-5:-1])
        assert visible.read_text() == "vortoone\n" and hidden.read_text() == "outside\n"
        assert alias.is_symlink() and alias.resolve() == hidden
        assert not target.exists()
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(workers, "_personal_codex_check", fake_sandbox)
    assert not workers._personal_codex_isolated("/test/codex", project, conf, True)
    assert list(project.iterdir()) == []


async def test_partial_reply_without_completed_turn_does_not_count_as_success(fixture):
    (fixture.root / "incomplete").touch()
    run = await workers.run_personal_codex("fixture only", str(fixture.project), write=False)
    assert run.started and not run.ok and run.failure_code == "incomplete_turn"
    assert "fixture-sensitive-retry" not in repr(run)
