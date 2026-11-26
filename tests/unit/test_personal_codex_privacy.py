"""A personal subscription stays behind the authenticated owner chat and its ledger binding."""
import pytest
from fastapi.testclient import TestClient

from src.web import app_staff, app_views
from src.workflow.home_identity import invalidate_computer_trust
from src.workflow.members.registry import MemberRegistry, Refused
from src.workflow.staff.agent_lifecycle import create_personal_codex
from src.workflow.staff.owner_confirmation import owner_route
from tests.unit.test_member_agents import OWNER_P, _finish, _join, home  # noqa: F401


@pytest.fixture
def codex_home(home, monkeypatch):  # noqa: F811
    root = home['root']
    monkeypatch.setattr('src.agents.external_workers.personal_codex_status', lambda: 'ready')
    handle = create_personal_codex(root, OWNER_P, name='Personal Codex', handle='codex-chat', project='')
    agent = app_views.member_agent_id(root, handle)
    channel = home['owner'].post(home['base'] + '/dms', json={'agentId': agent})
    assert channel.status_code == 200, channel.text
    local_owner = TestClient(home['app'], base_url='http://127.0.0.1', client=('127.0.0.1', 49152),
                             headers={'Authorization': home['owner'].headers['Authorization']})
    return {**home, 'handle': handle, 'codex': agent, 'local_owner': local_owner,
            'codex_path': home['base'] + '/channels/' + channel.json()['channelId']}


def test_only_the_owner_can_see_or_send_to_codex_even_with_stale_sharing(codex_home):
    home = codex_home
    members = MemberRegistry(home['root'])
    member = _join(home)
    handle = home['handle']
    with pytest.raises(Refused, match='仅供所有者'):
        members.share_plan(OWNER_P, handle, True)
    with pytest.raises(Refused, match='只回应所有者'):
        members.set_audience(OWNER_P, handle, {'mode': 'everyone'})

    # Data written before this feature must be ignored at read and dispatch time.
    with members._change() as state:
        state['plans_shared'][handle] = {'at': '2026-11-01T00:00:00Z'}
        state['agent_audiences'][handle] = {'mode': 'everyone', 'userIds': [], 'roleIds': []}
        state['agent_levels'][handle] = {member.member_id: 'execute'}
    assert not members.plan_shared(handle)
    assert members.audience(handle)['mode'] == 'owner'
    assert members.member_act_level_on(handle, member.member_id) == 'none'
    assert home['codex'] not in {row['id'] for row in member.get(home['base'] + '/agents').json()['agents']}
    assert member.post(home['codex_path'] + '/messages', json={'body': 'run a task'}).status_code == 404
    assert not home['pool'].jobs


async def test_direct_owner_chat_records_and_runs_the_bound_task(codex_home):
    home = codex_home
    posted = home['local_owner'].post(home['codex_path'] + '/messages', json={'body': 'answer as the personal agent'})
    assert posted.status_code == 200, posted.text
    [task_id] = home['pool'].jobs
    work = home['service'].collaboration('sid-owner')
    assert work.get(task_id).collaboration['dispatch']['personal_codex_owner']['trust_epoch']
    await _finish(home['pool'], task_id)
    assert work.get(task_id).result == '逐条交回结论'


def test_remote_and_forwarded_owner_requests_cannot_start_personal_codex(codex_home):
    home = codex_home
    path = home['codex_path'] + '/messages'
    remote = home['owner'].post(path, json={'body': 'remote task'})
    forwarded = home['local_owner'].post(path, json={'body': 'forwarded task'},
                                         headers={'X-Forwarded-For': '192.0.2.9'})
    assert remote.status_code == forwarded.status_code == 403
    assert remote.json()['error']['code'] == forwarded.json()['error']['code'] == 'local_owner_required'
    assert not home['pool'].jobs


def test_internal_agent_and_mcp_cannot_submit_private_codex_work(codex_home):
    home = codex_home
    args = {'member': home['handle'], 'prompt': 'call from another agent', 'delivery': 'notify'}
    with pytest.raises(ValueError, match='私聊'):
        home['service'].assign('sid-owner', args)
    with pytest.raises(ValueError, match='已认证'):
        home['service'].assign('sid-owner', args, owner_chat=True)
    with owner_route(home['root']):
        for origin in ({'channel': {'id': 'channel', 'thread': 'topic', 'message': 'message'}},
                       {'routine': {'id': 'routine'}}):
            with pytest.raises(ValueError, match='私聊'):
                home['service'].assign('sid-owner', args, owner_chat=True, **origin)
    with pytest.raises(ValueError, match='私聊'):
        app_staff.send_task(home['root'], home['handle'], 'call from MCP', via={'kind': 'ai_app', 'name': 'test'})
    assert not home['pool'].jobs


@pytest.mark.parametrize('changed', ['revoked', 'missing_binding', 'added_channel'])
async def test_queue_rechecks_fresh_dispatch_and_owner_trust(codex_home, changed):
    home = codex_home
    response = home['local_owner'].post(home['codex_path'] + '/messages', json={'body': 'queued private work'})
    assert response.status_code == 200, response.text
    [task_id] = home['pool'].jobs
    work = home['service'].collaboration('sid-owner')

    def alter_dispatch(task):
        dispatch = task.collaboration['dispatch']
        if changed == 'missing_binding':
            dispatch.pop('personal_codex_owner')
        else:
            dispatch['channel'] = {'id': 'injected', 'thread': 't', 'message': 'm'}

    if changed == 'revoked':
        invalidate_computer_trust(home['root'], 'offline test of owner trust invalidation')
    else:
        work.update_staff_action(task_id, 1, alter_dispatch)

    with pytest.raises(ValueError, match='私聊'):
        await _finish(home['pool'], task_id)
    assert work.get(task_id).status == 'queued'


@pytest.mark.parametrize('changed', ['home_owner', 'os_user'])
def test_a_fresh_request_cannot_use_an_agent_created_for_a_previous_owner(codex_home, monkeypatch, changed):
    home = codex_home
    if changed == 'home_owner':
        invalidate_computer_trust(home['root'], 'offline test of owner change', unclaimed=True)
    else:
        import os
        other_uid = os.geteuid() + 1
        monkeypatch.setattr('src.workflow.staff.initiators.os.geteuid', lambda: other_uid)
    response = home['local_owner'].post(home['codex_path'] + '/messages', json={'body': 'attempt a new task'})
    assert response.status_code == 409, response.text
    assert '所有者或执行账号已变化' in response.json()['error']['message']
    assert not home['pool'].jobs
