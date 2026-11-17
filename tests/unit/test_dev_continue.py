"""私聊里连着交代的开发：接着上一件还没合、没删的分支改（主人 2026-11-17 定）。"""
from pathlib import Path

from src.agents import external_workers as workers
from src.agents import team_delivery
from tests.unit.test_staff_development import development, git, registered  # noqa: F401 —— 复用夹具


def _developer(development, monkeypatch, seen):  # noqa: F811
    registry, role, _opencode, pool, service = registered(development)
    member = registry.register_member({"id": "claude-dev", "name": "Claude 开发", "role": role["id"],
                                       "engine": "claude-code"})

    async def fake(name, prompt, cwd, **options):
        path = Path(cwd)
        before = (path / "app.txt").read_text()
        seen.append({"prompt": prompt, "before": before})
        (path / "app.txt").write_text(before + f"改动{len(seen)}\n")
        run = workers.WorkerRun(name, final_text=f"改了第 {len(seen)} 处", exit_code=0)
        ref = options["execution_ref"]
        run.task_id, run.run_id, run.instance_id = ref.task_id, ref.run_id, ref.instance_id
        return run
    monkeypatch.setattr(workers, "worker_binary", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(workers, "run_worker", fake)
    return member, pool, service


async def _ask(member, pool, service, prompt):
    task = service.assign("owner", {"member": member["id"], "prompt": prompt, "acceptance": ["有改动"]})
    await pool.jobs[task.id]()
    done = service.collaboration("owner").get(task.id)
    assert done.status == "done", done.error
    return done


async def test_the_second_change_builds_on_the_first_unmerged_branch(development, monkeypatch):  # noqa: F811
    _root, project, _config, _data = development
    seen = []
    member, pool, service = _developer(development, monkeypatch, seen)
    first = await _ask(member, pool, service, "按钮改成蓝色")
    second = await _ask(member, pool, service, "再把文字改成进入")
    assert seen[1]["before"] == "original\n改动1\n"                       # 副本里已经有第一件的改动
    assert "接着改" in seen[1]["prompt"] and "按钮改成蓝色" in seen[1]["prompt"]
    branch = second.collaboration["delivery"]["branch"]
    assert git(project, "show", f"{branch}:app.txt") == "original\n改动1\n改动2"
    assert second.collaboration["delivery"]["continues"] == first.collaboration["delivery"]["branch"]
    assert "合这一个就够了" in second.result
    assert (project / "app.txt").read_text() == "original\n"           # 主工作区不动


async def test_a_merged_branch_means_starting_from_the_project(development, monkeypatch):  # noqa: F811
    _root, project, _config, _data = development
    seen = []
    member, pool, service = _developer(development, monkeypatch, seen)
    first = await _ask(member, pool, service, "第一件")
    git(project, "merge", "-q", "--ff-only", first.collaboration["delivery"]["branch"])
    second = await _ask(member, pool, service, "第二件")
    assert seen[1]["before"] == "original\n改动1\n"                       # 来自项目本身，不是叠上去的
    assert "continues" not in second.collaboration["delivery"] and "接着改" not in seen[1]["prompt"]


async def test_a_deleted_branch_means_that_change_is_not_wanted(development, monkeypatch):  # noqa: F811
    _root, project, _config, _data = development
    seen = []
    member, pool, service = _developer(development, monkeypatch, seen)
    first = await _ask(member, pool, service, "第一件")
    git(project, "branch", "-D", first.collaboration["delivery"]["branch"])
    second = await _ask(member, pool, service, "第二件")
    assert seen[1]["before"] == "original\n"
    assert "continues" not in second.collaboration["delivery"]


def test_only_our_own_branches_are_continued(development):  # noqa: F811
    _root, project, _config, _data = development
    git(project, "branch", "feature-x")
    assert team_delivery.continuable(str(project), "feature-x") is False
    assert team_delivery.continuable(str(project), "vorto/team-missing-r1") is False


async def test_an_opencode_developer_continues_the_same_way(development, monkeypatch):  # noqa: F811
    from types import SimpleNamespace

    from src.agents import team_engines
    _root, project, _config, _data = development
    _registry, _role, member, pool, service = registered(development)        # 登记的是 OpenCode
    prompts = []

    async def external(engine, kind, model, prompt, **kwargs):
        path = Path(kwargs["workdir"])
        prompts.append(prompt)
        before = (path / "app.txt").read_text()
        (path / "app.txt").write_text(before + f"opencode {len(prompts)}\n")
        return SimpleNamespace(text="改好了", networked=False, error="")
    monkeypatch.setattr(team_engines, "run_external", external)
    await _ask(member, pool, service, "第一件")
    second = await _ask(member, pool, service, "第二件")
    assert git(project, "show", second.collaboration["delivery"]["branch"] + ":app.txt") == \
        "original\nopencode 1\nopencode 2"
    assert "第一件" in prompts[1] and "接着改" in prompts[1]
