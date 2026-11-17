"""Claude Code / Codex 当登记 agent 的执行框架（先做开发岗位）。

主人 2026-11-16 定：①用个人订阅的 agent 默认只回应所有者，可逐个打开；②不可用直接说不可用，不改用 API；
③先做开发岗位：只在一次性项目副本里读写，不执行命令、不联网，交付与测试由 VortoOne 做。
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.agents import external_workers as workers
from src.agents import team_engines
from src.agents.principal import MEMBER, OWNER, Principal
from src.workflow.members.registry import MemberRegistry, Refused
from src.workflow.staff.registry import StaffRegistry
from src.workflow.staff.roles import CLI_ENGINES, builtin_roles, builtin_team_roles
from tests.unit.test_staff_development import development, git, registered  # noqa: F401 —— 复用夹具

OWNER_P = Principal(OWNER, "owner")


# ── 岗位与登记 ────────────────────────────────────────────────────────────────

def test_the_developer_role_offers_both_clis():
    developer = next(role for role in builtin_roles() if role["id"] == "developer")
    assert developer["engine"] == ["opencode", "claude-code", "codex"]


def test_an_install_that_stored_the_old_developer_role_still_reads(tmp_path):
    registry = StaffRegistry(str(tmp_path))
    stored = builtin_roles()
    for role in stored:
        if role["id"] == "developer":
            role["engine"] = ["opencode"]                  # 老安装存的写法
            role["enabled"], role["revision"] = False, 3
    registry._write("roles.json", {"version": 1, "roles": stored})
    developer = registry.role("developer")
    assert developer["engine"] == ["opencode", "claude-code", "codex"]
    assert (developer["enabled"], developer["revision"]) == (False, 3)   # 状态和版本照旧


def test_a_tampered_builtin_is_still_refused(tmp_path):
    registry = StaffRegistry(str(tmp_path))
    stored = builtin_roles()
    for role in stored:
        if role["id"] == "developer":
            role["engine"] = ["opencode", "vortocode"]
    registry._write("roles.json", {"version": 1, "roles": stored})
    with pytest.raises(ValueError, match="内置岗位被改写"):
        registry.roles()


def test_task_teams_do_not_offer_the_clis():
    assert not set(builtin_team_roles()["developer"]["engines"]) & set(CLI_ENGINES)


@pytest.mark.parametrize("engine", CLI_ENGINES)
def test_a_cli_agent_needs_a_development_role(tmp_path, engine):
    registry = StaffRegistry(str(tmp_path))
    with pytest.raises(ValueError):                         # 研究员岗位不允许这两个框架
        registry.register_member({"id": "cli-researcher", "name": "研究", "role": "web-researcher",
                                  "engine": engine})


@pytest.mark.parametrize("engines, ok", [(["opencode"], True), (["claude-code"], True), (["codex", "opencode"], True),
                                         ([], False), (["vortocode"], False), ("codex", False)])
def test_project_roles_accept_any_allowed_subset(development, engines, ok):  # noqa: F811
    from src.workflow.staff import projects
    root, project, config, data = development
    role = projects.bind({**deepcopy(data), "engine": engines})
    if ok:
        projects.validate(role)
    else:
        with pytest.raises(ValueError):
            projects.validate(role)


# ── CLI 的参数 ────────────────────────────────────────────────────────────────

def test_claude_gets_no_commands_when_asked():
    argv = workers.build_argv("claude", cwd="/w", write=True, commands=False)
    tools = argv[argv.index("--allowedTools") + 1:]
    assert not any(tool.startswith("Bash") for tool in tools)
    assert {"Read(./**)", "Edit(./**)", "Write(./**)"} <= set(tools)
    assert any(tool.startswith("Bash") for tool in workers.build_argv("claude", cwd="/w", write=True))  # 流水线照旧


def test_codex_network_is_always_off():
    for write in (True, False):
        argv = workers.build_argv("codex", cwd="/w", write=write)
        assert "sandbox_workspace_write.network_access=false" in argv


@pytest.mark.parametrize("model", ["--dangerously-skip-permissions", "-c", "a b", "x;rm", "", "o" * 81])
def test_odd_model_names_never_reach_the_command_line(model):
    if not model:
        assert workers.model_args("claude", model) == []
        return
    with pytest.raises(ValueError):
        workers.model_args("claude", model)


def test_model_names_become_the_right_flag():
    assert workers.model_args("claude", "claude-sonnet-5-5") == ["--model", "claude-sonnet-5-5"]
    assert workers.model_args("codex", "gpt-6.1") == ["-m", "gpt-6.1"]


async def test_a_bad_model_name_starts_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(workers, "worker_binary", lambda worker: "/bin/false")

    async def never(*_args, **_kwargs):
        raise AssertionError("不该起进程")
    monkeypatch.setattr(workers.asyncio, "create_subprocess_exec", never)
    run = await workers.run_worker("claude", "go", str(tmp_path), write=True, model="--evil")
    assert run.failure_code == "invalid_request" and not run.started


# ── 一轮开发 ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("engine, worker", [("claude-code", "claude")])
async def test_one_developer_turn_runs_the_cli_locked_down(tmp_path, monkeypatch, engine, worker):
    calls = []

    async def fake(name, prompt, cwd, **options):
        calls.append((name, prompt, cwd, options))
        return workers.WorkerRun(name, final_text="改好了", exit_code=0)
    monkeypatch.setattr(workers, "worker_binary", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(workers, "run_worker", fake)
    result = await team_engines.run_developer(engine, "some-model", "补一行", timeout=60, child_id="task-1",
                                              workdir=str(tmp_path), check=lambda: None)
    assert result.text == "改好了" and not result.error
    [(name, prompt, cwd, options)] = calls
    assert name == worker and cwd == str(tmp_path) and prompt == "补一行"
    assert options["write"] is True and options["commands"] is False and options["model"] == "some-model"
    assert "不运行任何命令" in options["system"]
    assert options["execution_ref"].backend_id == worker and options["execution_ref"].task_id == "task-1"


async def test_not_installed_says_unavailable(tmp_path, monkeypatch):
    engine = "claude-code"
    monkeypatch.setattr(workers, "worker_binary", lambda name: None)
    with pytest.raises(team_engines.EngineUnavailable, match="没有装"):
        await team_engines.run_developer(engine, "", "补一行", timeout=60, child_id="task-1", workdir=str(tmp_path), check=lambda: None)


async def test_a_used_up_plan_says_so_and_nothing_else_answers(tmp_path, monkeypatch):
    async def limited(name, prompt, cwd, **options):
        return workers.WorkerRun(name, error="usage limit reached", rate_limited=True, exit_code=1)
    monkeypatch.setattr(workers, "worker_binary", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(workers, "run_worker", limited)
    result = await team_engines.run_developer("claude-code", "", "补一行", timeout=60, child_id="t",
                                              workdir=str(tmp_path), check=lambda: None)
    assert "额度用完" in result.error and result.text == ""


async def test_a_claude_code_agent_delivers_a_branch(development, monkeypatch):  # noqa: F811
    """整条路：登记 Claude Code 开发 agent → 派活 → CLI 在副本里改 → VortoOne 交付分支，主工作区不动。"""
    root, project, config, data = development
    registry, role, _opencode, pool, service = registered(development)
    member = registry.register_member({"id": "claude-dev", "name": "Claude 开发", "role": role["id"],
                                       "engine": "claude-code", "model": "claude-sonnet-5-5"})
    seen = {}

    async def fake(name, prompt, cwd, **options):
        path = Path(cwd)
        assert path != project and not (path / ".env").exists()      # 副本里没有私密文件
        seen.update(name=name, commands=options["commands"], model=options["model"])
        (path / "app.txt").write_text("original\nby claude\n")
        run = workers.WorkerRun(name, final_text="已改", exit_code=0)
        run.task_id, run.run_id, run.instance_id = (options["execution_ref"].task_id, options["execution_ref"].run_id,
                                                    options["execution_ref"].instance_id)
        return run
    monkeypatch.setattr(workers, "worker_binary", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(workers, "run_worker", fake)
    task = service.assign("owner", {"member": member["id"], "prompt": "补充说明", "acceptance": ["有改动"]})
    await pool.jobs[task.id]()
    done = service.collaboration("owner").get(task.id)
    assert done.status == "done", done.error
    assert seen == {"name": "claude", "commands": False, "model": "claude-sonnet-5-5"}
    assert git(project, "show", done.collaboration["delivery"]["branch"] + ":app.txt") == "original\nby claude"
    assert (project / "app.txt").read_text() == "original\n"


# ── 个人订阅默认只回应所有者 ──────────────────────────────────────────────────

def test_only_the_owner_opens_a_plan_to_others(tmp_path):
    members = MemberRegistry(str(tmp_path))
    assert members.plan_shared("claude-dev") is False
    with pytest.raises(Refused):
        members.share_plan(Principal(MEMBER, "m_1"), "claude-dev", True)
    members.share_plan(OWNER_P, "claude-dev", True)
    assert members.plan_shared("claude-dev") is True
    members.share_plan(OWNER_P, "claude-dev", False)
    assert members.plan_shared("claude-dev") is False
    with pytest.raises(ValueError):
        members.share_plan(OWNER_P, "../x", True)


def test_an_older_member_file_has_nothing_open(tmp_path):
    members = MemberRegistry(str(tmp_path))
    members.invite(OWNER_P)                                      # 写出一份文件
    path = members.directory / "space.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state.pop("plans_shared", None)
    path.write_text(json.dumps(state), encoding="utf-8")
    assert members.plan_shared("claude-dev") is False


@pytest.mark.parametrize("engine, shared, allowed", [
    ("vortocode", False, True), ("opencode", False, True),
    ("claude-code", False, False), ("codex", False, False), ("claude-code", True, True)])
def test_plan_allows(tmp_path, engine, shared, allowed):
    from src.workflow.staff.initiators import plan_allows
    if shared:
        MemberRegistry(str(tmp_path)).share_plan(OWNER_P, "agent-x", True)
    assert plan_allows(str(tmp_path), {"id": "agent-x", "engine": engine}) is allowed


def test_an_unreadable_member_file_keeps_plans_closed(tmp_path):
    from src.workflow.staff.initiators import plan_allows
    members = MemberRegistry(str(tmp_path))
    members.share_plan(OWNER_P, "agent-x", True)
    (members.directory / "space.json").write_text("{", encoding="utf-8")
    assert plan_allows(str(tmp_path), {"id": "agent-x", "engine": "codex"}) is False


async def test_a_member_task_on_a_closed_plan_never_runs(development, monkeypatch):  # noqa: F811
    root, project, config, data = development
    registry, role, _opencode, pool, service = registered(development)
    member = registry.register_member({"id": "claude-dev", "name": "Claude 开发", "role": role["id"],
                                       "engine": "claude-code"})

    async def never(*_args, **_kwargs):
        raise AssertionError("CLI 不该被调用")
    monkeypatch.setattr(workers, "run_worker", never)
    task = service.assign("owner", {"member": member["id"], "prompt": "改一下"}, initiator="m_1")
    with pytest.raises(ValueError, match="只回应所有者"):     # 真实执行池会把这件事记为失败
        await pool.jobs[task.id]()
    assert (project / "app.txt").read_text() == "original\n"


# ── Codex 暂不当 agent：限制不了读取范围 ──────────────────────────────────────

def test_codex_cannot_be_registered_as_an_agent(development):  # noqa: F811
    registry, role, _opencode, _pool, _service = registered(development)
    with pytest.raises(ValueError, match="限制不了读取范围"):
        registry.register_member({"id": "codex-dev", "name": "Codex 开发", "role": role["id"], "engine": "codex"})


async def test_an_existing_codex_agent_never_starts(tmp_path, monkeypatch):
    async def never(*_args, **_kwargs):
        raise AssertionError("Codex 不该被启动")
    monkeypatch.setattr(workers, "worker_binary", lambda name: "/usr/bin/codex")
    monkeypatch.setattr(workers, "run_worker", never)
    with pytest.raises(team_engines.EngineUnavailable, match="限制不了读取范围"):
        await team_engines.run_developer("codex", "", "补一行", timeout=60, child_id="t", workdir=str(tmp_path),
                                         check=lambda: None)
