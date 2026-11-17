"""用本机 Claude Code 的 agent 接着上次的会话聊：会话按人分开、只在新鲜时接、上下文交给 CLI 压缩。"""
import pytest

from src.agents import external_workers as workers
from src.agents import team_engines
from src.workflow.staff import cli_sessions as CS
from tests.unit.test_agent_cli_engines import _general_service, session_dirs  # noqa: F401 —— 复用夹具

SID = "123e4567-e89b-12d3-a456-426614174000"


# ── 命令行 ────────────────────────────────────────────────────────────────────

def test_without_a_session_nothing_is_kept():
    argv = workers.build_argv("claude", cwd="/w", write=False, only_tools=())
    assert "--no-session-persistence" in argv and "--resume" not in argv and "--session-id" not in argv


def test_a_new_session_is_named_and_an_old_one_resumed():
    new = workers.build_argv("claude", cwd="/w", write=False, only_tools=(), session=(SID, False))
    assert new[new.index("--session-id") + 1] == SID and "--no-session-persistence" not in new
    old = workers.build_argv("claude", cwd="/w", write=False, only_tools=("WebSearch",), session=(SID, True))
    assert old[old.index("--resume") + 1] == SID
    assert old[old.index("--tools") + 1] == "WebSearch"          # 接着聊不改变能用的工具


@pytest.mark.parametrize("bad", ["--dangerously-skip-permissions", "../x", "", SID + "x"])
def test_a_session_id_must_look_like_one(bad):
    with pytest.raises(ValueError):
        workers.build_argv("claude", cwd="/w", write=False, only_tools=(), session=(bad, True))


def test_codex_never_resumes():
    with pytest.raises(ValueError):
        workers.build_argv("codex", cwd="/w", write=False, session=(SID, True))


class _Stop(Exception):
    pass


async def test_the_context_limit_reaches_claude_code_and_wins_over_personal_settings(tmp_path, monkeypatch):
    seen = {}

    async def spawn(*argv, **options):
        seen.update(argv=argv, env=options["env"])
        raise _Stop()
    monkeypatch.setattr(workers, "worker_binary", lambda worker: "/usr/bin/claude")
    monkeypatch.setattr(workers, "user_config", lambda worker: ([], {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "900000"}))
    monkeypatch.setattr(workers.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(_Stop):
        await workers.run_worker("claude", "hi", str(tmp_path), write=False, commands=False, only_tools=(),
                                 session=(SID, True), context_tokens=200_000)
    assert seen["env"]["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "200000"
    assert "--resume" in seen["argv"]


@pytest.mark.parametrize("worker, tokens", [("claude", 1000), ("claude", 20_000_000), ("codex", 200_000)])
async def test_an_odd_context_limit_starts_nothing(tmp_path, monkeypatch, worker, tokens):
    async def never(*_a, **_k):
        raise AssertionError("不该启动")
    monkeypatch.setattr(workers, "worker_binary", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(workers.asyncio, "create_subprocess_exec", never)
    run = await workers.run_worker(worker, "hi", str(tmp_path), write=False, context_tokens=tokens)
    assert run.failure_code == "invalid_request"


# ── 会话表 ────────────────────────────────────────────────────────────────────

def _store(tmp_path, clock):
    return CS.CliSessions(str(tmp_path), clock=lambda: clock[0])


def test_a_fresh_session_is_resumed_and_a_stale_or_long_one_is_not(tmp_path):
    clock = [1000.0]
    store = _store(tmp_path, clock)
    first = store.take("researcher", None)
    assert first.resume is False and first.keep
    store.record(first, 20_000)
    store.release(first)
    again = store.take("researcher", None)
    assert (again.id, again.resume, again.cwd) == (first.id, True, first.cwd)
    store.record(again, CS.MAX_INPUT_TOKENS)                 # 上一轮读了 15 万：下一轮开新的
    store.release(again)
    long = store.take("researcher", None)
    assert long.resume is False and long.id != first.id
    store.record(long, 1000)
    store.release(long)
    clock[0] += CS.IDLE_SECONDS                               # 一小时没用：开新的
    assert store.take("researcher", None).resume is False


def test_each_person_has_their_own_session_with_each_agent(tmp_path):
    store = _store(tmp_path, [1000.0])
    rows = {}
    for agent, viewer in [("a", None), ("a", "m_1"), ("a", "m_2"), ("b", None)]:
        session = store.take(agent, viewer)
        store.record(session, 10)
        store.release(session)
        rows[(agent, viewer)] = session
    assert len({s.id for s in rows.values()}) == 4 and len({s.cwd for s in rows.values()}) == 4
    assert store.take("a", "m_1").id == rows[("a", "m_1")].id


def test_a_session_in_use_is_not_shared(tmp_path):
    store = _store(tmp_path, [1000.0])
    busy = store.take("a", None)
    other = store.take("a", None)
    assert other.keep is False and other.id != busy.id
    store.record(other, 10)                                    # 临时会话不存
    store.release(busy)
    assert store.take("a", None).resume is False


def test_the_folder_lives_outside_the_workspace_and_is_empty(tmp_path):
    session = _store(tmp_path / "home", [1.0]).take("a", None)
    assert not str(session.cwd).startswith(str(tmp_path / "home")) and list(__import__("pathlib").Path(session.cwd).iterdir()) == []


# ── 走一遍执行 ────────────────────────────────────────────────────────────────

async def _run(service, pool, member, prompt, **extra):
    task = service.assign("owner", {"member": member["id"], "prompt": prompt}, **extra)
    await pool.jobs[task.id]()
    return service.collaboration("owner").get(task.id)


async def test_the_second_message_continues_the_first_conversation(tmp_path, monkeypatch):
    member, pool, service, seen = _general_service(tmp_path, monkeypatch)
    sessions = []

    async def fake(engine, model, prompt, **options):
        sessions.append((options["session"].id, options["session"].resume, prompt, options["context_tokens"]))
        return team_engines.CliGeneralResult(text=f"答复{len(sessions)}", networked=True, input_tokens=5000)
    monkeypatch.setattr(team_engines, "run_cli_general", fake)
    assert (await _run(service, pool, member, "第一件")).status == "done"
    assert (await _run(service, pool, member, "第二件")).status == "done"
    (first, new, p1, limit), (second, resumed, p2, _) = sessions
    assert new is False and resumed is True and first == second and limit == CS.CONTEXT_TOKENS
    assert "之前的对话" not in p1 and "之前的对话" not in p2         # 接着聊：不用再把之前的话塞进去


async def test_a_new_session_brings_the_recent_conversation(tmp_path, monkeypatch):
    member, pool, service, seen = _general_service(tmp_path, monkeypatch)
    await _run(service, pool, member, "查一下 X 的版本")
    CS.CliSessions(str(tmp_path)).forget(CS.Session(f"{member['id']}\0owner", "", False, ""))
    await _run(service, pool, member, "那 Y 呢")
    assert seen["session"].resume is False
    assert "【之前的对话" in seen["prompt"] and "查一下 X 的版本" in seen["prompt"] and "带来源的结论" in seen["prompt"]


async def test_a_member_never_sees_the_owners_conversation(tmp_path, monkeypatch):
    from src.agents.principal import OWNER, Principal
    from src.workflow.members.registry import MemberRegistry
    member, pool, service, seen = _general_service(tmp_path, monkeypatch)
    await _run(service, pool, member, "所有者的私事")
    owner_session = seen["session"].id
    members = MemberRegistry(str(tmp_path))
    _, token = members.invite(Principal(OWNER, "owner"))
    who = members.accept_invitation(token, "小王")
    members.share_plan(Principal(OWNER, "owner"), member["id"], True)
    await _run(service, pool, member, "成员的问题", initiator=who)
    assert seen["session"].id != owner_session and "所有者的私事" not in seen["prompt"]


async def test_a_lost_session_is_replaced_once(tmp_path, monkeypatch):
    member, pool, service, seen = _general_service(tmp_path, monkeypatch)
    await _run(service, pool, member, "第一件")
    calls = []

    async def fake(engine, model, prompt, **options):
        calls.append((options["session"].id, options["session"].resume, prompt))
        if options["session"].resume:
            return team_engines.CliGeneralResult(error="No conversation found with session ID", lost_session=True)
        return team_engines.CliGeneralResult(text="重新开始也答上了", input_tokens=10)
    monkeypatch.setattr(team_engines, "run_cli_general", fake)
    done = await _run(service, pool, member, "第二件")
    assert done.status == "done" and "重新开始也答上了" in done.result
    (lost, resumed, _), (fresh, new, prompt) = calls
    assert resumed is True and new is False and fresh != lost and "第一件" in prompt
    assert CS.CliSessions(str(tmp_path)).take(member["id"], None).id == fresh


def test_a_missing_session_is_recognised_from_claude_codes_own_answer():
    """Claude Code 2.1 接不上会话时的真实输出：原因只在 errors 里。"""
    obj = {"type": "result", "subtype": "error_during_execution", "is_error": True, "num_turns": 0,
           "session_id": SID, "errors": [f"No conversation found with session ID: {SID}"], "usage": {}}
    run = workers.WorkerRun("claude")
    for event in workers.parse_claude_event(obj):
        workers.fold_event(run, event)
    assert "No conversation found" in run.error
    assert team_engines._lost_session(run, CS.Session("k", SID, True, "/w")) is True
    assert team_engines._lost_session(run, CS.Session("k", SID, False, "/w")) is False
