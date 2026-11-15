"""Owner role conversations project and operate on the existing staff task ledger."""
from copy import deepcopy

import pytest

from tests.unit.test_staff_contract import staff_setup


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from src.gateway.main_conversation import claim_main_chat
    from src.web.server import app

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VORTOONE_API_TOKEN', 'offline-owner')
    registry, member, pool, service = staff_setup(tmp_path)
    claim_main_chat(str(tmp_path), 'sid-owner')
    monkeypatch.setattr('src.scheduling.staff_runner.get_staff_service', lambda root: service)
    client = TestClient(app)
    client.headers['Authorization'] = 'Bearer offline-owner'
    return client, registry, member, pool, service


def conversation(client, member):
    response = client.get(f"/api/staff/members/{member['id']}/conversation")
    assert response.status_code == 200, response.text
    return response.json()


def submission(snapshot, **changes):
    return {'request_id': 'a' * 32, 'prompt': 'Read the provided material',
            'acceptance': ['Return an itemized summary'], 'delivery': 'review',
            'member_revision': snapshot['member']['revision'],
            'role_revision': snapshot['role']['revision'], 'instance': snapshot['instance'],
            'identity_digest': snapshot['identity_digest'], **changes}


def test_owner_submits_once_and_sees_existing_staff_task_without_another_task_type(workspace):
    client, registry, member, pool, service = workspace
    snapshot = conversation(client, member)
    assert snapshot['tasks'] == [] and snapshot['history_complete']
    endpoint = f"/api/staff/members/{member['id']}/conversation"
    body = submission(snapshot)
    first = client.post(endpoint, json=body)
    assert first.status_code == 200, first.text
    task_id = first.json()['task']['id']
    task = service.collaboration('sid-owner').get(task_id)
    assert task.kind == 'delegation' and not task.team
    assert task.collaboration['dispatch']['member'] == member['id']
    assert task.collaboration['dispatch']['delivery'] == 'review'
    assert list(pool.jobs) == [task_id]
    replay = client.post(endpoint, json=body)
    assert replay.status_code == 200 and replay.json()['replayed']
    assert list(pool.jobs) == [task_id]
    assert conversation(client, member)['tasks'][0]['id'] == task_id
    assert client.post(endpoint, json={**body, 'prompt': 'A different request'}).status_code == 409
    assert len(pool.jobs) == 1


@pytest.mark.parametrize('credentials', ['none', 'cookie', 'wrong'])
def test_role_conversation_writes_require_explicit_owner_bearer(workspace, credentials):
    from src.web.auth import SESSION_COOKIE
    client, registry, member, pool, service = workspace
    snapshot = conversation(client, member)
    client.headers.pop('Authorization')
    if credentials == 'cookie':
        client.cookies.set(SESSION_COOKIE, 'offline-owner')
    if credentials == 'wrong':
        client.headers['Authorization'] = 'Bearer wrong'
    response = client.post(f"/api/staff/members/{member['id']}/conversation", json=submission(snapshot))
    assert response.status_code in {401, 403}
    assert not pool.jobs


@pytest.mark.parametrize('field', ['grants', 'tools', 'role', 'engine', 'model', 'owner', 'project'])
def test_role_message_cannot_change_authority(workspace, field):
    client, registry, member, pool, service = workspace
    snapshot = conversation(client, member)
    response = client.post(f"/api/staff/members/{member['id']}/conversation", json=submission(snapshot, **{field: 'injected'}))
    assert response.status_code == 422
    assert not pool.jobs


@pytest.mark.parametrize('change', [{'member_revision': 99}, {'role_revision': 99}, {'instance': 'old'}])
def test_stale_member_role_or_runtime_cannot_dispatch(workspace, change):
    client, registry, member, pool, service = workspace
    snapshot = conversation(client, member)
    response = client.post(f"/api/staff/members/{member['id']}/conversation", json=submission(snapshot, **change))
    assert response.status_code == 409
    assert not pool.jobs


async def test_result_is_not_accepted_until_owner_reviews_exact_current_delivery(workspace):
    client, registry, member, pool, service = workspace
    snapshot = conversation(client, member)
    created = client.post(f"/api/staff/members/{member['id']}/conversation", json=submission(snapshot))
    task_id = created.json()['task']['id']
    execute, callbacks = pool.jobs[task_id]
    async with callbacks['admission']():
        await execute()
    view = conversation(client, member)['tasks'][0]
    assert view['status'] == 'done' and view['collaboration']['review'] == 'pending'
    assert view['result']
    endpoint = f"/api/staff/members/{member['id']}/tasks/{task_id}/review"
    body = {**view['confirmation'], 'verdict': 'accept', 'note': 'Checked the full result'}
    assert client.post(endpoint, json={**body, 'digest': '0' * 64}).status_code == 409
    assert service.collaboration('sid-owner').get(task_id).collaboration['review'] == 'pending'
    result = client.post(endpoint, json=body)
    assert result.status_code == 200, result.text
    assert result.json()['task']['collaboration']['review'] == 'accepted'
    assert len(pool.jobs) == 1


def test_member_proposal_only_registers_after_existing_owner_confirmation(workspace):
    from src.workflow.staff.approvals import StaffApprovals
    client, registry, member, pool, service = workspace
    snapshot = conversation(client, member)
    body = {'name': 'Second reader', 'role': snapshot['role']['id'],
            'role_revision': snapshot['role']['revision'], 'engine': 'vortocode', 'model': '',
            'instance': snapshot['instance'], 'request_id': 'b' * 32, 'role_digest': snapshot['role_digest']}
    response = client.post('/api/staff/member-proposals', json=body)
    assert response.status_code == 200, response.text
    proposal = response.json()
    assert proposal['status'] == 'pending'
    assert len(registry.members()) == 1 and not pool.jobs
    assert client.post('/api/staff/member-proposals', json=body).json()['id'] == proposal['id']
    decision = {key: proposal[key] for key in ('digest', 'nonce', 'round', 'instance')}
    approved = client.post(f"/api/staff/proposals/{proposal['id']}/decision", json={**decision, 'approve': True})
    assert approved.status_code == 200
    assert registry.member(proposal['snapshot']['data']['id'])['role'] == member['role']
    replay = client.post('/api/staff/member-proposals', json=body)
    assert replay.status_code == 200 and replay.json()['status'] == 'approved'
    assert len(StaffApprovals(registry.root).list()) == 1 and not pool.jobs


def test_other_member_cannot_see_or_review_a_task(workspace):
    client, registry, member, pool, service = workspace
    other = registry.register_member({'id': 'other', 'name': 'Other', 'role': member['role'], 'engine': 'vortocode'})
    snapshot = conversation(client, member)
    task = client.post(f"/api/staff/members/{member['id']}/conversation", json=submission(snapshot)).json()['task']
    assert conversation(client, other)['tasks'] == []
    response = client.post(f"/api/staff/members/{other['id']}/tasks/{task['id']}/review",
        json={**task['confirmation'], 'verdict': 'accept', 'note': 'Wrong member'})
    assert response.status_code == 409


def test_paused_role_stays_readable_but_cannot_receive_new_work(workspace):
    client, registry, member, pool, service = workspace
    snapshot = conversation(client, member)
    registry.set_member_status(member['id'], 'paused', member['revision'])
    assert conversation(client, member)['member']['status'] == 'paused'
    assert client.post(f"/api/staff/members/{member['id']}/conversation", json=submission(snapshot)).status_code == 409
    assert not pool.jobs


def test_even_a_record_edited_without_incrementing_revision_invalidates_the_displayed_role(workspace):
    client, registry, member, pool, service = workspace
    snapshot = conversation(client, member)
    records = deepcopy(registry._read('roles.json', {}))
    next(item for item in records['roles'] if item['id'] == member['role'])['brief'] = 'A changed duty'
    registry._write('roles.json', records)
    assert registry.role(member['role'])['revision'] == snapshot['role']['revision']
    response = client.post(f"/api/staff/members/{member['id']}/conversation", json=submission(snapshot))
    assert response.status_code == 409 and not pool.jobs


def test_lost_submission_receipt_is_read_back_by_original_id_without_execution(workspace):
    client, registry, member, pool, service = workspace
    snapshot = conversation(client, member)
    task = client.post(f"/api/staff/members/{member['id']}/conversation", json=submission(snapshot)).json()['task']
    read = client.get(f"/api/staff/members/{member['id']}/conversation", params={'request_id': 'a' * 32})
    assert read.status_code == 200
    assert read.json()['submitted_task']['id'] == task['id']
    assert list(pool.jobs) == [task['id']]


def test_direct_workflow_calls_cannot_impersonate_the_owner_route(workspace):
    from src.workflow.staff.conversations import StaffConversations
    client, registry, member, pool, service = workspace
    snapshot = conversation(client, member)
    with pytest.raises(ValueError, match='主人写入路由'):
        StaffConversations(registry.root).submit(member['id'], submission(snapshot))
    assert not pool.jobs


def test_audit_failure_prevents_task_creation_and_scheduling(workspace, monkeypatch):
    client, registry, member, pool, service = workspace
    snapshot = conversation(client, member)
    monkeypatch.setattr('src.workflow.staff.conversations.append_audit', lambda *args, **kwargs: False)
    response = client.post(f"/api/staff/members/{member['id']}/conversation", json=submission(snapshot))
    assert response.status_code == 409 and not pool.jobs
    assert service.collaboration('sid-owner').list() == []


async def test_changed_delivery_and_restart_invalidate_owner_review(workspace, monkeypatch):
    from src.workflow.tasks.tasks import TaskLedger
    client, registry, member, pool, service = workspace
    snapshot = conversation(client, member)
    task_id = client.post(f"/api/staff/members/{member['id']}/conversation", json=submission(snapshot)).json()['task']['id']
    execute, callbacks = pool.jobs[task_id]
    async with callbacks['admission']():
        await execute()
    shown = conversation(client, member)['tasks'][0]
    endpoint = f"/api/staff/members/{member['id']}/tasks/{task_id}/review"
    body = {**shown['confirmation'], 'verdict': 'accept', 'note': 'Checked'}
    ledger = TaskLedger(registry.root)
    task = ledger.load(task_id); task.result = 'A different delivered result'; ledger.save(task)
    assert client.post(endpoint, json=body).status_code == 409
    fresh = conversation(client, member)['tasks'][0]
    body.update(fresh['confirmation'])
    monkeypatch.setattr('src.workflow.staff.owner_confirmation._PROCESS_INSTANCE', 'restarted')
    assert client.post(endpoint, json=body).status_code == 409
    assert ledger.load(task_id).collaboration['review'] == 'pending'


async def test_explicit_owner_followup_uses_existing_task_and_original_grants(workspace):
    client, registry, member, pool, service = workspace
    snapshot = conversation(client, member)
    task_id = client.post(f"/api/staff/members/{member['id']}/conversation", json=submission(snapshot)).json()['task']['id']
    execute, callbacks = pool.jobs[task_id]
    async with callbacks['admission']():
        await execute()
    task = conversation(client, member)['tasks'][0]
    response = client.post(f"/api/staff/members/{member['id']}/tasks/{task_id}/review",
        json={**task['confirmation'], 'verdict': 'rework', 'note': 'Add a source for each item'})
    assert response.status_code == 200, response.text
    actual = response.json()['task']
    assert actual['id'] == task_id and actual['collaboration']['round'] == 2
    assert actual['collaboration']['dispatch'] == task['collaboration']['dispatch']
    assert actual['collaboration']['messages'][-1]['body'] == 'Add a source for each item'
    assert len(service.collaboration('sid-owner').list()) == 1


def test_partial_or_unreadable_history_is_not_reported_as_complete(workspace):
    from pathlib import Path
    client, registry, member, pool, service = workspace
    path = Path(registry.root) / '.vortoone/tasks'
    path.mkdir(parents=True, exist_ok=True)
    (path / 'broken.json').write_text('not json')
    assert not conversation(client, member)['history_complete']


def test_stop_is_a_request_to_the_existing_pool_and_not_a_forged_completion(workspace, monkeypatch):
    client, registry, member, pool, service = workspace
    snapshot = conversation(client, member)
    task = client.post(f"/api/staff/members/{member['id']}/conversation", json=submission(snapshot)).json()['task']
    cancelled = []
    monkeypatch.setattr(pool, 'cancel', lambda task_id: cancelled.append(task_id) or True, raising=False)
    result = client.post(f"/api/staff/members/{member['id']}/tasks/{task['id']}/review",
        json={**task['confirmation'], 'verdict': 'stop', 'note': 'Stop this task'})
    assert result.status_code == 200 and result.json()['stop_requested']
    assert cancelled == [task['id']]
    assert result.json()['task']['status'] == 'queued'
