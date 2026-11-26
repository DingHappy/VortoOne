"""Host Git operations must not run configured helpers for personal Codex."""
import subprocess

import pytest

from src.agents import team_delivery, worktree


def _git(project, *args):
    return subprocess.check_output(['git', '-C', str(project), *args], text=True).strip()


@pytest.fixture
def project(tmp_path):
    _git(tmp_path, 'init', '-q')
    _git(tmp_path, 'config', 'user.name', 'Fixture')
    _git(tmp_path, 'config', 'user.email', 'fixture@example.invalid')
    (tmp_path / 'app.txt').write_text('fixture\n')
    _git(tmp_path, 'add', 'app.txt')
    _git(tmp_path, '-c', 'commit.gpgSign=false', 'commit', '-qm', 'Fixture')
    return tmp_path


@pytest.mark.parametrize('helper', ['filter.fixture.clean', 'filter.fixture.smudge', 'filter.fixture.process',
                                    'diff.fixture.textconv', 'diff.external', 'merge.fixture.driver'])
def test_personal_checkout_refuses_a_configured_helper_before_creating_worktree(project, helper):
    _git(project, 'config', helper, '/bin/cat')
    initial = _git(project, 'worktree', 'list', '--porcelain')
    with pytest.raises(ValueError, match='外部 Git 助手'):
        team_delivery.prepare(str(project), 'task-codex', 1, run_hooks=False)
    assert _git(project, 'worktree', 'list', '--porcelain') == initial
    assert _git(project, 'config', '--get', helper) == '/bin/cat'


def test_personal_delivery_rechecks_helper_config_before_staging(project):
    path = team_delivery.prepare(str(project), 'task-codex', 1, run_hooks=False)
    try:
        initial = _git(path, 'ls-files', '--stage')
        (path / 'app.txt').write_text('changed\n')
        _git(project, 'config', 'filter.fixture.clean', '/bin/cat')
        with pytest.raises(ValueError, match='外部 Git 助手'):
            worktree.collect_diff(path, personal=True)
        assert _git(path, 'ls-files', '--stage') == initial
    finally:
        team_delivery.cleanup(str(project), path, personal=True)


def test_personal_delivery_overrides_hooks_fsmonitor_and_signing(project):
    _git(project, 'config', 'core.fsmonitor', '/usr/bin/false')
    _git(project, 'config', 'commit.gpgSign', 'true')
    _git(project, 'config', 'gpg.program', '/usr/bin/false')
    assert worktree.personal_git(project, 'config', '--get', 'core.hooksPath').stdout.strip() == '/dev/null'
    path = team_delivery.prepare(str(project), 'task-codex', 1, run_hooks=False)
    try:
        (path / 'app.txt').write_text('changed\n')
        saved = team_delivery.finish_round(str(project), path, child_id='task-codex', number=1,
                                           objective='fixture update', verify=False)
        assert not saved.get('error'), saved
        assert _git(project, 'show', f"{saved['branch']}:app.txt") == 'changed'
        assert (project / 'app.txt').read_text() == 'fixture\n'
    finally:
        team_delivery.cleanup(str(project), path, personal=True)
