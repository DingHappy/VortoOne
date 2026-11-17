"""工具生成态在真实 Git 仓库/工作树中隐形，用户配置及锁语义保持。"""
import multiprocessing
from pathlib import Path
import subprocess

import pytest

from src.gateway.connected_computers import ConnectedComputers
from src.utils.home_maintenance import HomeMaintenanceError, MAINTENANCE_FILE, home_maintenance
from src.utils.state_dir import state_dir


def _git(root, *args):
    return subprocess.run(["git", "-c", "core.excludesfile=/dev/null", "-c", "core.hooksPath=/dev/null",
                           "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                           "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


def _repository(path):
    path.mkdir()
    _git(path, "-c", "init.defaultBranch=main", "init")
    (path / "README.md").write_text("fixture")
    (path / ".gitignore").write_text("*.user-ignore\n")
    _git(path, "add", "README.md", ".gitignore")
    _git(path, "commit", "-m", "fixture")
    return path


def _try_maintenance(root, result):
    try:
        with home_maintenance(root):
            result.put("acquired")
    except HomeMaintenanceError:
        result.put("blocked")


@pytest.mark.parametrize("worktree", [False, True])
def test_generated_identity_grants_and_lock_are_hidden_but_user_settings_are_visible(tmp_path, worktree):
    from src.workflow.home_identity import ensure_home_identity
    from src.utils.state_dir import ensure_state_gitignore
    original = _repository(tmp_path / "repository")
    root = original
    if worktree:
        root = tmp_path / "worktree"
        _git(original, "worktree", "add", "-b", "fixture-worktree", str(root))
    exclude = Path(_git(root, "rev-parse", "--git-path", "info/exclude"))
    if not exclude.is_absolute():
        exclude = root / exclude
    original_exclude = exclude.read_text() + "\n# user's own exclude\nuser-local-file"
    exclude.write_text(original_exclude)
    user_gitignore = (root / ".gitignore").read_bytes()
    ensure_home_identity(root)
    ConnectedComputers(root).create_pairing(legacy_confirmed=True)
    state = state_dir(root)
    (state / "permissions.yaml").write_text("allow: []\n")
    (state / "hooks.yaml").write_text("hooks: []\n")
    # 模拟已存在项目的旧托管清单，更新时不能吞掉用户自己的尾部规则。
    managed = state / ".gitignore"
    managed.write_text(managed.read_text().replace("home-identity/\n", "")
                       .replace("connected-computers/\n", "") + "\n*.user-owned-rule\n")
    ensure_state_gitignore(str(root))
    assert "*.user-owned-rule" in managed.read_text()
    context = multiprocessing.get_context("spawn")
    result = context.Queue()
    with home_maintenance(root):
        lock = root / MAINTENANCE_FILE
        inode = lock.stat().st_ino
        process = context.Process(target=_try_maintenance, args=(str(root), result))
        process.start()
        process.join(15)
        assert process.exitcode == 0 and result.get(timeout=2) == "blocked"
    with home_maintenance(root):
        assert lock.stat().st_ino == inode
    output = _git(root, "status", "--porcelain", "--untracked-files=all")
    assert ".vortoone/permissions.yaml" in output and ".vortoone/hooks.yaml" in output
    assert "home-identity/" not in output and "connected-computers/" not in output
    assert MAINTENANCE_FILE not in output
    assert (root / ".gitignore").read_bytes() == user_gitignore
    assert exclude.read_text().startswith(original_exclude)
    assert exclude.read_text().splitlines().count(f"/{MAINTENANCE_FILE}") == 1


def test_preexisting_lock_is_excluded_after_git_init_and_cached_without_forking_git_for_reads(tmp_path, monkeypatch):
    import src.utils.state_dir as module
    root = tmp_path / "home"
    root.mkdir()
    with home_maintenance(root):
        pass
    inode = (root / MAINTENANCE_FILE).stat().st_ino
    _git(root, "init")
    actual_git = module._git
    calls = []
    def counted(*args):
        calls.append(args)
        return actual_git(*args)
    monkeypatch.setattr(module, "_git", counted)
    for _ in range(3):
        with home_maintenance(root):
            assert (root / MAINTENANCE_FILE).stat().st_ino == inode
    assert len(calls) == 1
    exclude = root / ".git/info/exclude"
    exclude.write_text("# user's revised excludes\nkeep-me\n")
    with home_maintenance(root):
        pass
    assert exclude.read_text().startswith("# user's revised excludes\nkeep-me\n")
    assert len(calls) == 1
    exclude.unlink()
    with home_maintenance(root):
        pass
    assert f"/{MAINTENANCE_FILE}" in exclude.read_text() and len(calls) == 1


def test_exclude_symlink_does_not_modify_its_target(tmp_path):
    root = _repository(tmp_path / "repository")
    target = tmp_path / "user-rules"
    target.write_text("keep exactly\n")
    exclude = root / ".git/info/exclude"
    exclude.unlink()
    exclude.symlink_to(target)
    with home_maintenance(root):
        pass
    assert target.read_text() == "keep exactly\n" and exclude.is_symlink()
