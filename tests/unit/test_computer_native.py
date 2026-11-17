"""Private launcher tickets do not turn owner/API credentials into native pairing."""
import json
import os
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.gateway import computer_native as native
from src.gateway.connected_computers import ConnectedComputers
from src.workflow.members.owner_account import OwnerAccount

TICKET = 'vcn_' + '1' * 64
REPO = Path(__file__).resolve().parents[2]


def claimed(root):
    account = OwnerAccount(str(root))
    token, _ = account.claim_link()
    account.claim(token, 'Test owner', 'owner@example.test', 'Synthetic-long-password-123')


@pytest.fixture(autouse=True)
def isolated_ticket(monkeypatch):
    monkeypatch.setattr(native, '_pending', None)


def test_native_ticket_is_hashed_bound_and_consumed_once_without_human_window_changes(tmp_path):
    claimed(tmp_path)
    store = ConnectedComputers(tmp_path)
    window = store.create_pairing()
    native.register_bootstrap(tmp_path, TICKET, os.getppid())
    assert TICKET not in repr(native._pending)
    with pytest.raises(native.ComputerNativeDenied):
        native.redeem(tmp_path, 'vcn_' + '2' * 64, 'a' * 32, 'This computer')
    with pytest.raises(native.ComputerNativeDenied):
        native.redeem(tmp_path / 'other', TICKET, 'a' * 32, 'This computer')
    grant = native.redeem(tmp_path, TICKET, 'a' * 32, 'This computer')
    assert store.authenticate(grant['token'])['connector_id'] == 'a' * 32
    with pytest.raises(native.ComputerNativeDenied):
        native.redeem(tmp_path, TICKET, 'b' * 32, 'This computer')
    assert store.pair(window['code'], 'b' * 32, 'Manual computer')
    assert all(TICKET not in file.read_text() for file in tmp_path.rglob('*.json'))


@pytest.mark.parametrize('elapsed', [-1, 60, 61])
def test_native_ticket_has_fixed_monotonic_deadline(tmp_path, elapsed):
    clock = [100.0]
    native.register_bootstrap(tmp_path, TICKET, os.getppid(), clock=lambda: clock[0])
    clock[0] += elapsed
    with pytest.raises(native.ComputerNativeDenied):
        native.redeem(tmp_path, TICKET, 'a' * 32, 'This computer')


def test_native_ticket_checks_actual_parent_at_registration_and_redemption(tmp_path, monkeypatch):
    actual = os.getppid()
    with pytest.raises(native.ComputerNativeDenied):
        native.register_bootstrap(tmp_path, TICKET, actual + 1)
    native.register_bootstrap(tmp_path, TICKET, actual)
    monkeypatch.setattr(native.os, 'getppid', lambda: actual + 1)
    with pytest.raises(native.ComputerNativeDenied):
        native.redeem(tmp_path, TICKET, 'a' * 32, 'This computer')


def test_unclaimed_native_attempt_is_consumed_and_cannot_later_gain_owner_authority(tmp_path):
    native.register_bootstrap(tmp_path, TICKET, os.getppid())
    with pytest.raises(native.ComputerNativeUnclaimed):
        native.redeem(tmp_path, TICKET, 'a' * 32, 'This computer')
    claimed(tmp_path)
    with pytest.raises(native.ComputerNativeDenied):
        native.redeem(tmp_path, TICKET, 'a' * 32, 'This computer')
    assert ConnectedComputers(tmp_path).list() == []


def test_native_persist_failure_never_reuses_launch_ticket(tmp_path, monkeypatch):
    claimed(tmp_path)
    native.register_bootstrap(tmp_path, TICKET, os.getppid())
    def failed(*args, **kwargs):
        raise OSError('synthetic disk failure')
    monkeypatch.setattr(ConnectedComputers, 'pair_native', failed)
    with pytest.raises(native.ComputerNativeUnavailable):
        native.redeem(tmp_path, TICKET, 'a' * 32, 'This computer')
    with pytest.raises(native.ComputerNativeDenied):
        native.redeem(tmp_path, TICKET, 'a' * 32, 'This computer')


def test_concurrent_native_redemptions_have_only_one_grant(tmp_path):
    claimed(tmp_path)
    native.register_bootstrap(tmp_path, TICKET, os.getppid())
    def attempt(index):
        try:
            return native.redeem(tmp_path, TICKET, format(index, 'x') * 32, 'This computer')
        except native.ComputerNativeDenied:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        grants = list(pool.map(attempt, (1, 2)))
    assert sum(grant is not None for grant in grants) == 1
    assert len(ConnectedComputers(tmp_path).list()) == 1


@pytest.mark.parametrize('entry', ['src.runtime_entry', 'desktop.runtime_entry'])
@pytest.mark.parametrize('valid_parent', [True, False])
def test_real_server_entries_receive_private_stdin_and_validate_parent_before_listening(tmp_path, entry, valid_parent):
    claimed(tmp_path)
    program = '''
import importlib, json, os, uvicorn
from src.gateway import computer_native as native
def listener(*args, **kwargs):
    grant=native.redeem(os.getcwd(), 'vcn_'+'1'*64, 'b'*32, 'This computer')
    print('NATIVE_RESULT:'+json.dumps({'home':grant['home_instance_id'],'connector':grant['connector_id']}))
uvicorn.run=listener
importlib.import_module(ENTRY).main(['server','--port','18123','--native-computer-control'])
'''.replace('ENTRY', repr(entry))
    env = {'PATH': os.environ.get('PATH', ''), 'HOME': str(tmp_path / 'isolated-home'),
           'PYTHONPATH': str(REPO), 'VORTOONE_STATE_DIR_MIGRATION': 'off',
           'VORTOONE_API_TOKEN': 'synthetic-owner'}
    frame = {'ticket': TICKET, 'parent_pid': os.getpid() + (0 if valid_parent else 1)}
    result = subprocess.run([sys.executable, '-c', program], input=json.dumps(frame)+'\n',
                            text=True, capture_output=True, cwd=tmp_path, env=env, timeout=15)
    assert TICKET not in result.stdout + result.stderr
    if valid_parent:
        assert result.returncode == 0, result.stderr
        assert 'NATIVE_RESULT:' in result.stdout
        assert len(ConnectedComputers(tmp_path).list()) == 1
    else:
        assert result.returncode != 0
        assert 'NATIVE_RESULT:' not in result.stdout
        assert ConnectedComputers(tmp_path).list() == []
