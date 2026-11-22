"""C1's routes authenticate people and computers separately; no execution is available."""
import json
import os
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.gateway.computer_protocol import MAX_FRAME_BYTES, PROTOCOL_MIN, hello
from src.gateway.connected_computers import ConnectedComputers, ComputerDenied
from src.web import member_auth
from src.web.auth import COMPUTER_SELF_AUTHENTICATED, SESSION_COOKIE

OWNER = 'computer-owner-for-test'
ORIGIN = 'https://home.test'
CONNECTOR = 'a' * 32
NATIVE_TICKET = 'vcn_' + 'b' * 64


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VORTOONE_API_TOKEN', OWNER)
    monkeypatch.delenv('AUTODEV_API_TOKEN', raising=False)
    from src.web.server import app
    from src.web import app_views
    from src.web.routers import computers
    monkeypatch.setattr(computers, '_pairing_rates', computers._PairingRates())
    member_auth._EMPTY_CACHE.clear()
    root = str(tmp_path)
    return {'root': root, 'store': ConnectedComputers(root), 'app': app,
            'base': f'/api/app/organizations/{app_views.home_id(root)}'}


def browser(home, *, owner=False, cookie=False):
    headers = {'Origin': ORIGIN}
    if owner:
        headers['Authorization'] = f'Bearer {OWNER}'
    client = TestClient(home['app'], base_url=ORIGIN, headers=headers, client=('127.0.0.1', 2345))
    if cookie:
        client.cookies.set(SESSION_COOKIE, OWNER)
    return client


def paired(home, connector=CONNECTOR):
    pairing = home['store'].create_pairing(legacy_confirmed=True)
    return home['store'].pair(pairing['code'], connector, 'My computer')


def socket_client(home):
    return TestClient(home['app'], base_url=ORIGIN, client=('127.0.0.1', 2345))


def native_client(home, *, host='127.0.0.1', peer='127.0.0.1', scheme='http'):
    return TestClient(home['app'], base_url=f'{scheme}://{host}', client=(peer, 2345))


def native_request(client, ticket=NATIVE_TICKET, **kwargs):
    headers = kwargs.pop('headers', {'Authorization': 'Native ' + ticket})
    return client.post('/api/computers/native-pair' + kwargs.pop('query', ''), headers=headers,
                       **({'json': {'connector_id': CONNECTOR, 'name': 'My local computer'}} if not kwargs else kwargs))


@pytest.fixture
def native_context(monkeypatch):
    from src.gateway import computer_native
    monkeypatch.setattr(computer_native, '_pending', None)
    clock = [100.0]
    return computer_native, clock


@pytest.fixture
def native_bootstrap(home, monkeypatch, native_context):
    native, clock = native_context
    claim_native_home(home, monkeypatch)
    native.register_bootstrap(home['root'], NATIVE_TICKET, os.getppid(), clock=lambda: clock[0])
    return native_context


def claim_native_home(home, monkeypatch):
    from src.workflow.members import accounts
    from src.workflow.members.owner_account import OwnerAccount
    monkeypatch.setattr(accounts, '_SCRYPT', {'n': 16, 'r': 8, 'p': 1})
    owner = OwnerAccount(home['root'])
    token, _ = owner.claim_link()
    owner.claim(token, 'Owner', 'owner@example.test', 'native-test-owner-password')


def connect(client, grant, **kwargs):
    headers = {'Authorization': f"Bearer {grant['token']}"}
    headers.update(kwargs.pop('headers', {}))
    return client.websocket_connect(f'wss://home.test/api/computers/ws{kwargs.pop("query", "")}',
                                    headers=headers, **kwargs)


def test_public_identity_is_stable_and_cannot_claim_or_create_authority(home):
    client = browser(home)
    first = client.get('/api/computers/identity')
    second = client.get('/api/computers/identity')
    assert first.status_code == 200 and first.json() == second.json()
    assert set(first.json()) == {'home_instance_id', 'protocol_min', 'protocol_max', 'features'}
    assert first.headers['cache-control'] == 'no-store'
    assert home['store'].identity()['owner_subject'] is None
    assert client.post(home['base'] + '/computer-pairings', json={}).status_code == 401


def test_owner_pair_exchange_and_redacted_machine_list(home):
    owner = browser(home, owner=True)
    made = owner.post(home['base'] + '/computer-pairings', json={'legacy_confirmed': True})
    assert made.status_code == 201, made.text
    assert made.headers['cache-control'] == 'no-store'
    body = {'home_instance_id': made.json()['home_instance_id'], 'connector_id': CONNECTOR,
            'code': made.json()['code'], 'name': 'My computer'}
    native = socket_client(home)
    wrong = native.post('/api/computers/pair', json={**body, 'home_instance_id': 'f' * 32})
    assert wrong.status_code == 403
    answer = native.post('/api/computers/pair', json=body)
    assert answer.status_code == 200, answer.text
    token = answer.json()['token']
    rows = owner.get(home['base'] + '/machines')
    assert rows.status_code == 200 and rows.headers['cache-control'] == 'no-store'
    machine = rows.json()['machines'][0]
    assert machine['id'] == CONNECTOR and not machine['online'] and not machine['execAllowed']
    assert machine['access']['folders'] == [] and not any(machine['access']['tools'].values())
    assert token not in rows.text and 'digest' not in rows.text and 'trust_epoch' not in rows.text
    changed = owner.patch(home['base'] + '/machines/' + CONNECTOR, json={'name': 'New name'})
    assert changed.status_code == 200
    assert owner.get(home['base'] + '/machines').json()['machines'][0]['name'] == 'New name'
    assert native.post('/api/computers/pair', json=body).status_code == 403


def test_owner_cookie_requires_exact_same_origin_and_token_configuration(home, monkeypatch):
    owner = browser(home, cookie=True)
    path = home['base'] + '/computer-pairings'
    for origin in ('https://evil.test', 'http://home.test', 'https://home.test:444'):
        denied = owner.post(path, headers={'Origin': origin}, json={'legacy_confirmed': True})
        assert denied.status_code == 403 and denied.headers['cache-control'] == 'no-store'
    assert owner.post(path, json={'legacy_confirmed': True}).status_code == 201
    monkeypatch.setenv('VORTOONE_API_TOKEN', '')
    assert owner.post(path, json={'legacy_confirmed': True}).status_code == 403


def join_member(home, owner, email):
    invite = owner.post(home['base'] + '/invitations', json={'email': email})
    assert invite.status_code == 200, invite.text
    token = invite.json()['url'].rsplit('/', 1)[-1]
    member = browser(home)
    signup = member.post('/api/auth/sign-up/email', headers={'x-invitation-token': token},
                         json={'email': email, 'name': email.split('@')[0], 'password': 'test-member-passphrase'})
    assert signup.status_code == 200, signup.text
    accepted = member.post(f'/api/app/invitations/{token}/accept', json={})
    assert accepted.status_code == 200, accepted.text
    return member, member.get('/api/app/me').json()['user']['id']


def exchange(home, pairing, connector, name):
    return socket_client(home).post('/api/computers/pair', json={
        'home_instance_id': pairing['home_instance_id'], 'code': pairing['code'],
        'connector_id': connector, 'name': name,
    })


def test_members_pair_independent_private_computers_with_control_only(home, monkeypatch):
    claim_native_home(home, monkeypatch)
    owner = browser(home, owner=True)
    alice, alice_id = join_member(home, owner, 'alice@example.test')
    bob, _ = join_member(home, owner, 'bob@example.test')
    path = home['base'] + '/computer-pairings'
    machines = home['base'] + '/machines'
    assert alice.get(home['base'] + '/computer-setup').json()['memberPairing'] is True
    assert alice.post(path, headers={'Origin': 'https://other.test'}, json={}).status_code == 403
    owner_code, alice_code, bob_code = (client.post(path, json={}) for client in (owner, alice, bob))
    assert all(code.status_code == 201 and code.headers['cache-control'] == 'no-store'
               for code in (owner_code, alice_code, bob_code))
    assert bob.delete(path + '/' + alice_code.json()['pairing_id']).status_code == 204
    alice_connector, bob_connector = 'b' * 32, 'c' * 32
    alice_pair = exchange(home, alice_code.json(), alice_connector, 'Alice laptop')
    assert alice_pair.status_code == 200, alice_pair.text
    grant = alice_pair.json()
    assert grant['paired_principal_id'] != home['store'].identity()['owner_subject']
    assert exchange(home, bob_code.json(), bob_connector, 'Bob laptop').status_code == 200
    assert exchange(home, owner_code.json(), CONNECTOR, 'Owner laptop').status_code == 200

    for client, only_id in ((owner, CONNECTOR), (alice, alice_connector), (bob, bob_connector)):
        listed = client.get(machines)
        assert listed.status_code == 200 and listed.headers['cache-control'] == 'no-store'
        assert [row['id'] for row in listed.json()['machines']] == [only_id]
        assert grant['token'] not in listed.text and 'digest' not in listed.text
    assert alice.get(machines).json()['machines'][0]['pairedBy']['id'] == alice_id
    assert owner.patch(machines + '/' + alice_connector, json={'name': 'Elsewhere'}).status_code == 404
    assert bob.delete(machines + '/' + alice_connector).status_code == 404
    assert alice.patch(machines + '/' + alice_connector, json={'name': 'Private laptop'}).status_code == 200

    with connect(socket_client(home), grant) as ws:
        ws.send_json(hello(grant, files=True, transfers=True, deliveries=True))
        ready = ws.receive_json()
        assert ready['protocol'] == PROTOCOL_MIN and ready['folders'] == []
        assert not any(ready['capabilities'].values())
        assert alice.get(machines).json()['machines'][0]['online']
        assert alice.delete(machines + '/' + alice_connector).status_code == 204
        assert ws.receive_json()['credentialRevoked'] is True
        ws.send_json({'type': 'ack', 'generation': ready['generation']})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    with pytest.raises(ComputerDenied):
        home['store'].authenticate(grant['token'])


def test_device_routes_project_native_pairing_and_only_rename(home):
    grant = paired(home)
    owner = browser(home, cookie=True)
    path = home['base'] + '/devices'
    device_path = path + '/computers/' + CONNECTOR
    owner_id = owner.get('/api/app/me').json()['user']['id']
    # Same-origin fetches do not need to send an Origin header with GET.
    owner.headers.pop('Origin')
    answer = owner.get(path)
    owner.headers['Origin'] = ORIGIN
    device = {
        'id': CONNECTOR, 'machineKind': 'connected', 'name': 'My computer', 'os': 'other',
        'arch': '', 'hostname': '', 'cpus': None, 'memoryGb': None, 'ownerUserId': owner_id,
        'belongsToWorkspace': False, 'spill': {'own': False, 'others': False}, 'systemMode': False,
        'audience': {'mode': 'owner', 'userIds': [], 'roleIds': []},
        'status': {'kind': 'offline', 'lastSeenAt': None}, 'maxTasks': None, 'toolchains': {},
        'lastSeenAt': None, 'mayUse': False, 'mayManage': True, 'phones': [],
    }
    assert answer.status_code == 200
    assert answer.json() == {'devices': [device], 'requests': []}
    assert answer.headers['cache-control'] == 'no-store'
    for denied in (
        {'name': 'Ignored', 'audience': {'mode': 'everyone', 'userIds': [], 'roleIds': []}},
        {'name': 'Ignored', 'belongsToWorkspace': True},
        {'name': 'Ignored', 'maxTasks': 2},
        {'name': 'Ignored', 'spill': {'others': True}},
        {'name': None},
        {},
    ):
        failed = owner.patch(device_path, json=denied)
        assert failed.status_code == 400
        assert failed.json()['error']['code'] == 'computer_rename_only'
        assert failed.headers['cache-control'] == 'no-store'
        assert owner.get(path).json() == {'devices': [device], 'requests': []}

    rename = owner.patch(device_path, json={'name': '  New device name  '})
    assert rename.status_code == 200
    assert rename.headers['cache-control'] == 'no-store'
    device['name'] = 'New device name'
    assert rename.json() == device
    assert owner.get(home['base'] + '/machines').json()['machines'][0]['name'] == device['name']

    with connect(socket_client(home), grant) as ws:
        ws.send_json(hello(grant))
        ready = ws.receive_json()
        assert ready['type'] == 'welcome'
        device['status'] = {'kind': 'unsupported', 'reason': 'paired_connector_only'}
        assert owner.get(path).json() == {'devices': [device], 'requests': []}
        removed = owner.delete(device_path)
        assert removed.status_code == 204 and not removed.content
        assert removed.headers['cache-control'] == 'no-store'
        assert ws.receive_json()['credentialRevoked'] is True
        ws.send_json({'type': 'ack', 'generation': ready['generation']})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert owner.get(path).json() == {'devices': [], 'requests': []}
    assert owner.delete(device_path).status_code == 404
    assert owner.patch(device_path, json={'name': 'Revoked'}).status_code == 404
    with pytest.raises(ComputerDenied):
        home['store'].authenticate(grant['token'])


def test_device_routes_require_owner_proof_or_current_pairer(home, monkeypatch):
    claim_native_home(home, monkeypatch)
    owner = browser(home, cookie=True)
    alice, alice_id = join_member(home, owner, 'device-alice@example.test')
    bob, _ = join_member(home, owner, 'device-bob@example.test')
    owner_grant = paired(home)
    pairing = alice.post(home['base'] + '/computer-pairings', json={})
    assert pairing.status_code == 201
    alice_connector = 'c' * 32
    paired_member = exchange(home, pairing.json(), alice_connector, 'Alice device')
    assert paired_member.status_code == 200
    member_grant = paired_member.json()
    path = home['base'] + '/devices'
    alice_path = path + '/computers/' + alice_connector
    owner_path = path + '/computers/' + CONNECTOR

    assert [r['id'] for r in owner.get(path).json()['devices']] == [CONNECTOR]
    listed = alice.get(path)
    assert listed.status_code == 200
    assert listed.headers['cache-control'] == 'no-store'
    assert [(r['id'], r['ownerUserId']) for r in listed.json()['devices']] == [(alice_connector, alice_id)]
    assert bob.get(path).json() == {'devices': [], 'requests': []}
    assert owner.get(path, headers={'Origin': 'https://other.test'}).status_code == 403
    assert alice.get(path, headers={'Origin': 'https://other.test'}).status_code == 403
    assert alice.get('/api/app/organizations/unrelated/devices').status_code == 404
    assert owner.get('/api/app/organizations/unrelated/devices').status_code == 404

    assert browser(home).get(path).status_code == 401
    assert browser(home).patch(alice_path, json={'name': 'Hidden'}).status_code == 401
    assert browser(home).delete(alice_path).status_code == 401
    assert owner.patch(alice_path, json={'name': 'Hidden'}).status_code == 404
    assert owner.delete(alice_path).status_code == 404
    assert alice.patch(owner_path, json={'name': 'Hidden'}).status_code == 404
    assert alice.delete(owner_path).status_code == 404
    assert bob.patch(alice_path, json={'name': 'Hidden'}).status_code == 404
    assert bob.delete(alice_path).status_code == 404
    assert alice.patch(alice_path, json={'name': 'Hidden'}, headers={'Origin': 'https://other.test'}).status_code == 403
    assert alice.delete(alice_path, headers={'Origin': 'https://other.test'}).status_code == 403

    renamed = alice.patch(alice_path, json={'name': 'Alice laptop'})
    assert renamed.status_code == 200
    assert renamed.json()['name'] == 'Alice laptop'
    assert renamed.json()['ownerUserId'] == alice_id
    assert alice.delete(alice_path).status_code == 204
    assert alice.get(path).json() == {'devices': [], 'requests': []}
    with pytest.raises(ComputerDenied):
        home['store'].authenticate(member_grant['token'])
    assert home['store'].authenticate(owner_grant['token']) == {
        key: value for key, value in owner_grant.items() if key != 'token'
    }


def test_device_routes_hide_stale_pairings_after_trust_change(home):
    from src.workflow.home_identity import invalidate_computer_trust

    grant = paired(home)
    owner = browser(home, owner=True)
    path = home['base'] + '/devices'
    device = path + '/computers/' + CONNECTOR
    invalidate_computer_trust(home['root'], 'test restore')
    assert owner.get(path).json() == {'devices': [], 'requests': []}
    assert owner.patch(device, json={'name': 'Stale'}).status_code == 404
    assert owner.delete(device).status_code == 404
    stale, = home['store'].list()
    assert stale['name'] == 'My computer' and stale['revoked_at'] is None and not stale['active']
    with pytest.raises(ComputerDenied):
        home['store'].authenticate(grant['token'])


def test_device_revoke_does_not_close_a_replacement_credential(home, monkeypatch):
    from src.gateway.computer_connections import connections

    grant = paired(home)
    replacement = SimpleNamespace(binding={'credential_epoch': 'e' * 32})
    notified = []
    scheduled = []
    notify = connections.revoked

    def new_connection_before_notify(root, connector, *, credential_epoch=None):
        revoked, = home['store'].list()
        assert revoked['revoked_at'] is not None
        assert revoked['credential_epoch'] != grant['credential_epoch']
        notified.append(credential_epoch)
        # A home replacement can publish a new connection between the write and this call.
        with connections._lock:
            connections._current[(root, connector)] = replacement
        try:
            notify(root, connector, credential_epoch=credential_epoch)
        finally:
            with connections._lock:
                del connections._current[(root, connector)]

    monkeypatch.setattr(connections, '_schedule', scheduled.append)
    monkeypatch.setattr(connections, 'revoked', new_connection_before_notify)
    owner = browser(home, owner=True)
    response = owner.delete(home['base'] + '/devices/computers/' + CONNECTOR)
    assert response.status_code == 204
    assert notified == [grant['credential_epoch']]
    assert scheduled == []


def test_member_pairing_needs_a_claimed_home_and_current_connect_permission(home, monkeypatch):
    from src.agents.identity_groups import EVERYONE, EVERYONE_DEFAULT
    from src.agents.principal import Principal
    from src.workflow.members.registry import MemberRegistry

    monkeypatch.setattr('src.workflow.members.accounts._SCRYPT', {'n': 16, 'r': 8, 'p': 1})
    owner = browser(home, owner=True)
    member, _ = join_member(home, owner, 'pairer@example.test')
    path = home['base'] + '/computer-pairings'
    assert member.post(path, json={'legacy_confirmed': True}).status_code == 403
    assert home['store'].identity()['owner_subject'] is None
    claim_native_home(home, monkeypatch)
    pending = member.post(path, json={})
    assert pending.status_code == 201, pending.text
    MemberRegistry(home['root']).change_group(Principal('owner', 'owner'), EVERYONE,
                                              items=EVERYONE_DEFAULT - {'computers.connect_own'})
    assert member.post(path, json={}).status_code == 403
    assert exchange(home, pending.json(), 'b' * 32, 'Laptop').status_code == 403
    assert owner.post(path, json={}).status_code == 201


def test_removing_member_revokes_connector_and_pending_code(home, monkeypatch):
    claim_native_home(home, monkeypatch)
    owner = browser(home, owner=True)
    member, member_id = join_member(home, owner, 'former@example.test')
    path = home['base'] + '/computer-pairings'
    first = member.post(path, json={})
    grant = exchange(home, first.json(), 'b' * 32, 'Laptop').json()
    later = member.post(path, json={}).json()
    with pytest.raises(ComputerDenied):
        home['store'].authenticate(grant['token'], owner_only=True)
    transfer = socket_client(home).get('/api/computers/transfer', headers={
        'Authorization': f"Bearer {grant['token']}", 'X-Vortoone-Transfer-Ticket': 'vct_' + 'a' * 43,
    })
    assert transfer.status_code == 403
    with connect(socket_client(home), grant) as ws:
        ws.send_json(hello(grant))
        ready = ws.receive_json()
        assert owner.delete(home['base'] + '/members/' + member_id).status_code == 204
        assert not home['store'].check(grant)
        assert ws.receive_json()['credentialRevoked'] is True
        ws.send_json({'type': 'ack', 'generation': ready['generation']})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert exchange(home, later, 'c' * 32, 'Another laptop').status_code == 403
    with pytest.raises(WebSocketDisconnect):
        with connect(socket_client(home), grant):
            pytest.fail('removed member connector connected again')


def test_pair_exchange_is_exactly_scoped_bounded_and_rate_limited(home):
    grant = paired(home)
    owner = browser(home, owner=True)
    assert owner.post('/api/computers/pair', json={}).status_code == 403
    native = socket_client(home)
    assert native.post('/api/computers/pair', content='x' * 4097).status_code == 413
    bad = {'code': 'INVALIDINPUT', 'home_instance_id': grant['home_instance_id'],
           'connector_id': 'b' * 32, 'name': 'Other'}
    responses = [native.post('/api/computers/pair', json=bad) for _ in range(12)]
    assert responses[-1].status_code == 429
    assert all(response.headers['cache-control'] == 'no-store' for response in responses)
    assert native.get('/api/computers/pair').status_code == 401
    machine = socket_client(home)
    denied = machine.get('/api/app/me', headers={'Authorization': f"Bearer {grant['token']}"})
    assert denied.status_code == 401


@pytest.mark.parametrize('extra', [
    {'headers': {'Authorization': f'Bearer {OWNER}'}},
    {'headers': {'Authorization': 'Bearer mobile_device_token'}},
    {'headers': {'Authorization': 'Bearer vca_other_scope'}},
    {'headers': {'Cookie': f'{SESSION_COOKIE}={OWNER}'}},
    {'headers': {'Origin': ORIGIN}},
    {'query': '?token=some-secret'},
])
def test_computer_socket_rejects_other_scopes_browser_cookies_and_query_tokens(home, extra):
    grant = paired(home)
    with pytest.raises(WebSocketDisconnect) as closed:
        with connect(socket_client(home), grant, **extra):
            pytest.fail('must reject before hello')
    assert closed.value.code == 1008


def test_control_socket_and_owner_revocation_with_ack(home):
    grant = paired(home)
    with connect(socket_client(home), grant) as ws:
        ws.send_json(hello(grant))
        ready = ws.receive_json()
        assert ready['type'] == 'welcome' and ready['folders'] == []
        assert not any(ready['capabilities'].values())
        generation = ready['generation']
        ws.send_json({'type': 'ping', 'generation': generation})
        assert ws.receive_json() == {'type': 'pong', 'generation': generation}
        owner = browser(home, owner=True)
        assert owner.get(home['base'] + '/machines').json()['machines'][0]['online'] is True
        assert owner.delete(home['base'] + '/machines/' + CONNECTOR).status_code == 204
        revoked = ws.receive_json()
        assert revoked == {'type': 'revoked', 'generation': generation,
                           'reason': 'authorization_revoked', 'credentialRevoked': True}
        ws.send_json({'type': 'ack', 'generation': generation})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    with pytest.raises(WebSocketDisconnect):
        with connect(socket_client(home), grant):
            pytest.fail('revoked token reconnect')


def test_revocation_without_ack_closes_within_a_bounded_wait(home, monkeypatch):
    from src.gateway import computer_connections
    monkeypatch.setattr(computer_connections, 'REVOKE_ACK_SECONDS', 0.05)
    grant = paired(home)
    with connect(socket_client(home), grant) as ws:
        ws.send_json(hello(grant))
        ws.receive_json()
        assert browser(home, owner=True).delete(home['base'] + '/machines/' + CONNECTOR).status_code == 204
        assert ws.receive_json()['type'] == 'revoked'
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 1008


def test_browser_cannot_exchange_a_pairing_code_and_bad_input_never_echoes_it(home):
    made = home['store'].create_pairing(legacy_confirmed=True)
    body = {'home_instance_id': made['home_instance_id'], 'connector_id': CONNECTOR,
            'code': made['code'], 'name': 'Other'}
    assert browser(home).post('/api/computers/pair', json=body).status_code == 403
    invalid = socket_client(home).post('/api/computers/pair', json={**body, 'unexpected': made['code']})
    assert invalid.status_code == 400 and made['code'] not in invalid.text
    valid = socket_client(home).post('/api/computers/pair', json=body)
    assert valid.status_code == 200


def test_reconnect_fences_old_generation_and_old_disconnect_keeps_new_online(home):
    grant = paired(home)
    with connect(socket_client(home), grant) as old:
        old.send_json(hello(grant))
        first = old.receive_json()
        with connect(socket_client(home), grant) as new:
            new.send_json(hello(grant))
            second = new.receive_json()
            assert first['generation'] != second['generation']
            notice = old.receive_json()
            assert notice['reason'] == 'connection_replaced' and notice['credentialRevoked'] is False
            old.send_json({'type': 'ack', 'generation': first['generation']})
            with pytest.raises(WebSocketDisconnect):
                old.receive_json()
            new.send_json({'type': 'ping', 'generation': second['generation']})
            assert new.receive_json()['type'] == 'pong'
            assert browser(home, owner=True).get(home['base'] + '/machines').json()['machines'][0]['online']


def test_cross_process_trust_change_rechecks_idle_connection(home):
    from src.workflow.home_identity import invalidate_computer_trust
    grant = paired(home)
    with connect(socket_client(home), grant) as ws:
        ws.send_json(hello(grant))
        ready = ws.receive_json()
        invalidate_computer_trust(home['root'], 'test restore')
        notice = ws.receive_json()
        assert notice['type'] == 'revoked' and notice['credentialRevoked'] is True
        ws.send_json({'type': 'ack', 'generation': ready['generation']})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


@pytest.mark.parametrize('mutation', ['protocol', 'identity', 'execution', 'oversize'])
def test_socket_rejects_protocol_mismatch_and_every_execution_frame(home, mutation):
    grant = paired(home)
    with connect(socket_client(home), grant) as ws:
        first = hello(grant)
        if mutation == 'protocol':
            from src.gateway.computer_protocol import PROTOCOL_MAX

            first.update(protocol_min=PROTOCOL_MAX + 1, protocol_max=PROTOCOL_MAX + 1)
        elif mutation == 'identity':
            first['home_instance_id'] = 'f' * 32
        elif mutation == 'oversize':
            ws.send_text('x' * (MAX_FRAME_BYTES + 1))
        if mutation != 'oversize':
            ws.send_json(first)
        if mutation == 'execution':
            ready = ws.receive_json()
            ws.send_json({'type': 'execute', 'generation': ready['generation']})
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 1008


def test_computer_http_exemptions_are_exact_and_general_scope_routes_exist(home):
    from tests.integration.test_server_smoke import _live_routes
    from src.gateway.workspace_scope import http_workspace_requirement
    routes = {(method, path) for path, methods in _live_routes() for method in methods}
    assert COMPUTER_SELF_AUTHENTICATED <= routes
    assert COMPUTER_SELF_AUTHENTICATED == {('GET', '/api/computers/identity'), ('POST', '/api/computers/pair'),
                                           ('POST', '/api/computers/native-pair'), ('GET', '/api/computers/transfer'),
                                           ('PUT', '/api/computers/transfer')}
    for _, path in COMPUTER_SELF_AUTHENTICATED:
        assert http_workspace_requirement(path, 'general') is None
    assert http_workspace_requirement('/api/computers/execute', 'general') == 'scratch'


@pytest.mark.parametrize('invalid', ['duplicate-hello', 'duplicate-control', 'nan-control'])
def test_socket_rejects_ambiguous_json_frames(home, invalid):
    grant = paired(home)
    with connect(socket_client(home), grant) as ws:
        if invalid == 'duplicate-hello':
            raw = json.dumps(hello(grant))
            ws.send_text(raw[:-1] + ',"type":"hello"}')
        else:
            ws.send_json(hello(grant))
            generation = ws.receive_json()['generation']
            if invalid == 'duplicate-control':
                ws.send_text('{"type":"ping","generation":"' + generation + '","type":"ping"}')
            else:
                ws.send_text('{"type":"ping","generation":"' + generation + '","extra":NaN}')
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 1008


@pytest.mark.parametrize('case', [
    'absent', 'owner', 'computer', 'mobile', 'bearer-ticket', 'wrong-length', 'uppercase', 'duplicate',
    'origin', 'empty-origin', 'cookie', 'empty-cookie', 'query', 'owner-key-header',
    'forwarded', 'forwarded-for', 'forwarded-host', 'forwarded-proto',
    'dns-host', 'localhost-host', 'remote-https', 'remote-peer', 'dns-peer', 'spoofed-peer',
])
def test_native_pair_refuses_every_scope_and_transport_substitution_before_redeem(home, monkeypatch, case):
    from src.web.routers import computers

    def forbidden(*args):
        pytest.fail('rejected network input reached native ticket redemption')

    monkeypatch.setattr(computers, 'redeem', forbidden)
    headers = {'Authorization': 'Native ' + NATIVE_TICKET}
    options = {}
    query = ''
    if case == 'absent':
        headers = {}
    elif case == 'owner':
        headers['Authorization'] = 'Bearer ' + OWNER
    elif case == 'computer':
        headers['Authorization'] = 'Bearer vcc_' + 'x' * 43
    elif case == 'mobile':
        headers['Authorization'] = 'Bearer vnd_' + 'x' * 43
    elif case == 'bearer-ticket':
        headers['Authorization'] = 'Bearer ' + NATIVE_TICKET
    elif case == 'wrong-length':
        headers['Authorization'] = 'Native ' + NATIVE_TICKET[:-1]
    elif case == 'uppercase':
        headers['Authorization'] = 'Native ' + NATIVE_TICKET.upper()
    elif case == 'duplicate':
        headers = [('Authorization', 'Native ' + NATIVE_TICKET), ('Authorization', 'Native ' + NATIVE_TICKET)]
    elif case in ('origin', 'empty-origin'):
        headers['Origin'] = '' if case == 'empty-origin' else 'http://127.0.0.1'
    elif case in ('cookie', 'empty-cookie'):
        headers['Cookie'] = '' if case == 'empty-cookie' else SESSION_COOKIE + '=' + OWNER
    elif case == 'query':
        query = '?ticket=' + NATIVE_TICKET
    elif case == 'owner-key-header':
        headers['X-API-Token'] = OWNER
    elif case.startswith('forwarded'):
        key = {'forwarded': 'Forwarded', 'forwarded-for': 'X-Forwarded-For',
               'forwarded-host': 'X-Forwarded-Host', 'forwarded-proto': 'X-Forwarded-Proto'}[case]
        headers[key] = '127.0.0.1'
    elif case == 'dns-host':
        options['host'] = 'home.test'
    elif case == 'localhost-host':
        options['host'] = 'localhost'
    elif case == 'remote-https':
        options.update(host='remote.test', peer='203.0.113.1', scheme='https')
    elif case == 'remote-peer':
        options['peer'] = '203.0.113.1'
    elif case == 'dns-peer':
        options['peer'] = 'localhost'
    elif case == 'spoofed-peer':
        options['peer'] = '203.0.113.1'
        headers['X-Forwarded-For'] = '127.0.0.1'
    denied = native_request(native_client(home, **options), headers=headers, query=query)
    assert denied.status_code == 403, denied.text
    assert denied.headers['cache-control'] == 'no-store' and denied.headers['pragma'] == 'no-cache'
    assert NATIVE_TICKET not in denied.text and OWNER not in denied.text


@pytest.mark.parametrize('body', [
    {'connector_id': 'A' * 32, 'name': 'My computer'}, {'connector_id': CONNECTOR, 'name': ''},
    {'connector_id': CONNECTOR, 'name': True}, {'connector_id': CONNECTOR, 'name': 'x' * 81},
    {'connector_id': CONNECTOR, 'name': '  '}, {'connector_id': CONNECTOR, 'name': 'bad\nname'},
    {'connector_id': CONNECTOR, 'name': '\ud800'},
    {'connector_id': CONNECTOR, 'name': 'My computer', 'legacy_confirmed': True},
    {'connector_id': CONNECTOR, 'name': 'My computer', 'token': OWNER},
])
def test_native_pair_validates_only_exact_connector_and_name_before_consumption(home, monkeypatch, body):
    from src.web.routers import computers
    monkeypatch.setattr(computers, 'redeem', lambda *args: pytest.fail('invalid body consumed ticket'))
    denied = native_request(native_client(home), content=json.dumps(body))
    assert denied.status_code == 400 and denied.headers['cache-control'] == 'no-store'
    assert NATIVE_TICKET not in denied.text and OWNER not in denied.text


@pytest.mark.parametrize('body,status', [
    ('{"connector_id":"' + CONNECTOR + '","name":"First","name":"Second"}', 400),
    ('{"connector_id":"' + CONNECTOR + '","name":NaN}', 400),
    ('x' * 4097, 413),
])
def test_native_pair_rejects_ambiguous_or_oversized_json_before_consumption(home, monkeypatch, body, status):
    from src.web.routers import computers
    monkeypatch.setattr(computers, 'redeem', lambda *args: pytest.fail('invalid JSON consumed ticket'))
    denied = native_request(native_client(home), content=body)
    assert denied.status_code == status and denied.headers['cache-control'] == 'no-store'


def test_native_pair_requires_its_ticket_even_without_general_owner_api_token(home, monkeypatch):
    monkeypatch.setenv('VORTOONE_API_TOKEN', '')
    denied = native_request(native_client(home), headers={})
    assert denied.status_code == 403 and denied.headers['cache-control'] == 'no-store'


def test_native_ticket_is_not_a_general_owner_member_or_machine_credential(home):
    client = native_client(home)
    for method, path in [('GET', '/api/app/me'), ('GET', home['base'] + '/machines'),
                         ('GET', '/api/computers/native-pair'),
                         ('POST', '/api/computers/native-pair/'), ('POST', '/api/computers/native-pair/extra')]:
        denied = client.request(method, path, headers={'Authorization': 'Native ' + NATIVE_TICKET})
        assert denied.status_code == 401, (method, path, denied.status_code)
    with pytest.raises(WebSocketDisconnect) as denied:
        with client.websocket_connect('ws://127.0.0.1/api/computers/ws',
                                      headers={'Authorization': 'Native ' + NATIVE_TICKET}):
            pass
    assert denied.value.code == 1008


@pytest.mark.parametrize('scheme,host,peer', [
    ('http', '127.0.0.1', '127.0.0.1'), ('https', '127.0.0.1', '127.0.0.1'),
    ('http', '[::1]', '::1'), ('http', '127.0.0.1', '::ffff:127.0.0.1'),
])
def test_native_launch_ticket_pairs_once_without_exposing_secrets(home, monkeypatch, native_bootstrap,
                                                               caplog, scheme, host, peer):
    from src.gateway.computer_protocol import BINDING_FIELDS
    native, _ = native_bootstrap
    caplog.set_level('DEBUG')
    assert NATIVE_TICKET not in repr(native._pending)
    client = native_client(home, scheme=scheme, host=host, peer=peer)
    answer = native_request(client)
    assert answer.status_code == 200, answer.text
    grant = answer.json()
    assert set(grant) == set(BINDING_FIELDS) | {'token'}
    assert grant['connector_id'] == CONNECTOR and grant['token'].startswith('vcc_')
    assert all(isinstance(grant[field], str) and len(grant[field]) == 32 for field in BINDING_FIELDS)
    assert home['store'].authenticate(grant['token']) == {field: grant[field] for field in BINDING_FIELDS}
    assert answer.headers['cache-control'] == 'no-store' and answer.headers['pragma'] == 'no-cache'
    replay = native_request(client)
    assert replay.status_code == 403 and replay.headers['cache-control'] == 'no-store'
    assert native._pending is None
    assert NATIVE_TICKET not in caplog.text and grant['token'] not in caplog.text
    for path in (Path(home['store'].root) / '.vortoone').rglob('*'):
        if path.is_file():
            content = path.read_bytes()
            assert NATIVE_TICKET.encode() not in content and grant['token'].encode() not in content


def test_forged_native_ticket_does_not_consume_the_real_native_launch(home, monkeypatch, native_bootstrap):
    client = native_client(home)
    forged = native_request(client, ticket='vcn_' + 'c' * 64)
    assert forged.status_code == 403 and NATIVE_TICKET not in forged.text
    assert native_request(client).status_code == 200


@pytest.mark.parametrize('failure', ['expired', 'before-receive', 'parent-changed', 'wrong-home'])
def test_native_launch_binding_cannot_be_replayed_at_wrong_time_parent_or_home(home, monkeypatch,
                                                                           native_bootstrap, tmp_path, failure):
    native, clock = native_bootstrap
    if failure == 'expired':
        clock[0] += 60
    elif failure == 'before-receive':
        clock[0] -= 0.01
    elif failure == 'parent-changed':
        real_parent = os.getppid()
        monkeypatch.setattr(native.os, 'getppid', lambda: real_parent + 1)
    else:
        other = tmp_path / 'another-home'
        other.mkdir()
        monkeypatch.chdir(other)
    denied = native_request(native_client(home))
    assert denied.status_code == 403 and denied.headers['cache-control'] == 'no-store'
    assert denied.json()['detail']['code'] == 'computer_native_authorization_denied'
    assert home['store'].list() == []


@pytest.mark.parametrize('state', ['unclaimed', 'legacy-unconfirmed'])
def test_native_pair_does_not_claim_home_or_authorize_later_after_a_409(home, monkeypatch, native_context, state):
    from src.workflow.home_identity import initialize_unclaimed_home
    native, clock = native_context
    if state == 'unclaimed':
        initialize_unclaimed_home(home['root'])
    native.register_bootstrap(home['root'], NATIVE_TICKET, os.getppid(), clock=lambda: clock[0])
    first = native_request(native_client(home))
    assert first.status_code == 409 and first.headers['cache-control'] == 'no-store'
    assert first.json()['detail']['code'] == 'computer_owner_claim_required'
    assert native._pending is None and home['store'].list() == []
    claim_native_home(home, monkeypatch)
    second = native_request(native_client(home))
    assert second.status_code == 403 and home['store'].list() == []


def test_native_store_failure_is_redacted_and_still_consumes_correct_ticket(home, monkeypatch, native_bootstrap):
    native, _ = native_bootstrap

    def fail(*args, **kwargs):
        raise OSError('sensitive storage failure ' + NATIVE_TICKET)

    monkeypatch.setattr(ConnectedComputers, 'pair_native', fail)
    first = native_request(native_client(home))
    assert first.status_code == 503 and first.headers['cache-control'] == 'no-store'
    assert first.json()['detail']['code'] == 'computer_native_storage_unavailable'
    assert NATIVE_TICKET not in first.text and native._pending is None
    assert native_request(native_client(home)).status_code == 403


def test_native_pair_rejects_unauthenticated_attempt_without_a_registered_bootstrap(home, monkeypatch):
    from src.gateway import computer_native
    monkeypatch.setattr(computer_native, '_pending', None)
    denied = native_request(native_client(home))
    assert denied.status_code == 403 and denied.headers['cache-control'] == 'no-store'
    assert home['store'].list() == []


def test_native_invalid_body_does_not_burn_the_private_launch_ticket(home, monkeypatch, native_bootstrap):
    client = native_client(home)
    bad = native_request(client, json={'connector_id': CONNECTOR, 'name': 'My computer', 'owner': True})
    assert bad.status_code == 400
    assert native_request(client).status_code == 200


@pytest.mark.parametrize('changed', ['home_instance_id', 'trust_epoch'])
def test_native_ticket_cannot_follow_replaced_identity_at_the_same_path_and_failure_is_consumed(home, native_bootstrap, changed):
    from src.workflow.home_identity import ensure_home_identity, invalidate_computer_trust
    native, _ = native_bootstrap
    identity_path = Path(home['root']) / '.vortoone/home-identity/identity.json'
    original = identity_path.read_bytes()
    before = ensure_home_identity(home['root'])
    if changed == 'home_instance_id':
        identity_path.write_text(json.dumps({**before, 'home_instance_id': uuid.uuid4().hex}))
    else:
        invalidate_computer_trust(home['root'], 'test home restored after native bootstrap')
    assert ensure_home_identity(home['root'])[changed] != before[changed]
    client = native_client(home)
    first = native_request(client)
    assert first.status_code == 403 and first.headers['cache-control'] == 'no-store'
    assert first.json()['detail']['code'] == 'computer_native_authorization_denied'
    assert native._pending is None and home['store'].list() == []
    # Restoring even the original identity cannot revive a consumed launch attempt.
    identity_path.write_bytes(original)
    assert ensure_home_identity(home['root']) == before
    assert native_request(client).status_code == 403 and home['store'].list() == []


def test_native_ticket_cannot_cross_owner_reclaim_between_primitive_and_store_check(home, monkeypatch, native_bootstrap):
    from src.workflow.home_identity import ensure_home_identity
    from src.workflow.members.owner_account import OwnerAccount
    native, _ = native_bootstrap
    before = ensure_home_identity(home['root'])
    original = ConnectedComputers._pairing_identity
    raced = []

    def reclaim_before_store_read(store, **kwargs):
        assert not raced
        owner = OwnerAccount(home['root'])
        claim, _ = owner.claim_link(reset=True)
        owner.claim(claim, 'New owner', 'new-owner@example.test', 'new-native-owner-password')
        raced.append(ensure_home_identity(home['root']))
        return original(store, **kwargs)

    monkeypatch.setattr(ConnectedComputers, '_pairing_identity', reclaim_before_store_read)
    first = native_request(native_client(home))
    assert first.status_code in (403, 409) and first.headers['cache-control'] == 'no-store'
    assert len(raced) == 1
    assert raced[0]['owner_subject'] != before['owner_subject']
    assert raced[0]['trust_epoch'] != before['trust_epoch']
    assert native._pending is None and home['store'].list() == []
    assert native_request(native_client(home)).status_code == 403


@pytest.fixture
def transfer_home(home, monkeypatch):
    from src.gateway.computer_transfers import ComputerTransfers
    from src.web.routers import computers

    registry = ComputerTransfers()
    monkeypatch.setattr(computers, "computer_transfers", registry)
    monkeypatch.setenv("VORTOONE_WORKSPACE_SCOPE", "general")
    return home, registry


async def _read_staged(registry, connection, transfer_id, action_id):
    async with registry.take_staged(connection, transfer_id, action_id) as (file, handle):
        return file, handle.read()


def test_transfer_http_accepts_only_its_computer_and_consumes_ticket_once(transfer_home):
    import hashlib
    import time
    from functools import partial

    from src.gateway.computer_connections import connections
    from src.gateway.computer_protocol import TRANSFER_PATH
    from src.gateway.computer_transfers import TransferDenied, TransferFile, TransferUnavailable

    home, registry = transfer_home
    grant = paired(home)
    other = paired(home, connector="b" * 32)
    body = b"binary\x00file" * 8192
    file = TransferFile("1" * 32, "2" * 32, "3" * 32, "4" * 64, len(body), hashlib.sha256(body).hexdigest())
    client = socket_client(home)
    with connect(client, grant) as ws:
        ws.send_json(hello(grant, files=True))
        ready = ws.receive_json()
        connection = connections.file_connection(home["root"], grant["connector_id"], home["store"].identity())
        assert connection.generation == ready["generation"]
        issue = partial(registry.issue, connection, file, action_deadline=time.time() + 30)
        ticket = ws.portal.call(issue)
        with pytest.raises(TransferUnavailable):
            ws.portal.call(issue)
        headers = {"Authorization": "Bearer " + grant["token"],
                   "X-VortoOne-Transfer-Ticket": ticket["ticket"],
                   "Content-Type": "application/octet-stream"}
        for mutated in ({"Authorization": "Bearer " + other["token"]},
                        {"Authorization": "Bearer " + OWNER},
                        {"Origin": ORIGIN}, {"Cookie": "test=cookie"},
                        {"X-VortoOne-Transfer-Ticket": "vct_" + "a" * 43}):
            denied = client.put(TRANSFER_PATH, headers={**headers, **mutated}, content=body)
            assert denied.status_code == 403 and denied.headers["cache-control"] == "no-store"
        assert client.put(TRANSFER_PATH + "?ticket=test", headers=headers, content=body).status_code == 403
        assert client.post(TRANSFER_PATH, headers=headers, content=body).status_code == 401
        assert client.put(TRANSFER_PATH + "/extra", headers=headers, content=body).status_code == 409
        assert client.put(TRANSFER_PATH, headers={**headers, "Content-Encoding": "gzip"}, content=body).status_code == 400
        assert client.put(TRANSFER_PATH, headers=headers, content=body[:-1]).status_code == 403

        staged = client.put(TRANSFER_PATH, headers=headers, content=body)
        assert staged.status_code == 201
        assert staged.json() == {"transfer_id": ticket["transfer_id"], "size": len(body),
                                 "sha256": file.sha256, "status": "staged"}
        assert client.put(TRANSFER_PATH, headers=headers, content=body).status_code == 403
        with pytest.raises(TransferDenied):
            ws.portal.call(_read_staged, registry, connection, ticket["transfer_id"], "5" * 32)
        bound, received = ws.portal.call(_read_staged, registry, connection, ticket["transfer_id"], file.action_id)
        assert bound == file and received == body
        with pytest.raises(TransferDenied):
            ws.portal.call(_read_staged, registry, connection, ticket["transfer_id"], file.action_id)


@pytest.mark.parametrize("failure", ["truncated", "oversized", "wrong-digest", "revoked", "expired", "disk"])
def test_failed_transfer_discards_stage_and_burns_claimed_ticket(transfer_home, monkeypatch, failure):
    import asyncio
    import hashlib
    import time
    from functools import partial

    from src.gateway.computer_connections import connections
    from src.gateway import computer_transfers as transfer_module
    from src.gateway.computer_transfers import TransferDenied, TransferFile, TransferUnavailable

    home, registry = transfer_home
    grant = paired(home)
    body = b"file bytes"
    file = TransferFile("1" * 32, "2" * 32, "3" * 32, "4" * 64, len(body), hashlib.sha256(body).hexdigest())
    client = socket_client(home)
    with connect(client, grant) as ws:
        ws.send_json(hello(grant, files=True))
        ws.receive_json()
        connection = connections.file_connection(home["root"], grant["connector_id"], home["store"].identity())
        deadline = time.time() + (0.25 if failure == "expired" else 20)
        ticket = ws.portal.call(partial(registry.issue, connection, file, action_deadline=deadline))
        if failure == "disk":
            from types import SimpleNamespace
            monkeypatch.setattr(transfer_module.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))

        async def chunks():
            if failure == "revoked":
                yield body
                await asyncio.to_thread(home["store"].revoke, grant["connector_id"])
            elif failure == "expired":
                await asyncio.sleep(0.4)
                yield body
            elif failure == "truncated":
                yield body[:-1]
            elif failure == "oversized":
                yield body + b"x"
            else:
                yield b"x" * len(body)

        with pytest.raises(TransferUnavailable if failure == "disk" else TransferDenied):
            ws.portal.call(registry.receive, home["root"], grant, ticket["ticket"], len(body), chunks())
        with pytest.raises(TransferDenied):
            ws.portal.call(_read_staged, registry, connection, ticket["transfer_id"], file.action_id)
        with pytest.raises(TransferDenied):
            ws.portal.call(registry.receive, home["root"], grant, ticket["ticket"], len(body), chunks())
        spool = Path(home["root"]) / ".vortoone" / "computer-file-intents"
        assert not spool.exists() or not list(spool.iterdir())


def test_transfer_stream_has_one_receiver_and_rejects_stale_generation(transfer_home):
    import hashlib
    import time
    from functools import partial

    from src.gateway.computer_connections import connections
    from src.gateway.computer_transfers import TransferDenied, TransferFile

    home, registry = transfer_home
    grant = paired(home)
    body = b"chunks across one request"
    file = TransferFile("1" * 32, "2" * 32, "3" * 32, "4" * 64, len(body), hashlib.sha256(body).hexdigest())
    client = socket_client(home)

    def open_generation(ws):
        ws.send_json(hello(grant, files=True))
        ws.receive_json()
        return connections.file_connection(home["root"], grant["connector_id"], home["store"].identity())

    with connect(client, grant) as first:
        initial = open_generation(first)
        stale = first.portal.call(partial(registry.issue, initial, file, action_deadline=time.time() + 30))
        with connect(client, grant) as second:
            current = open_generation(second)
            assert current.generation != initial.generation

            async def unneeded_data():
                pytest.fail("the receiver must reject a stale or already-claimed ticket before reading")
                yield body

            with pytest.raises(TransferDenied):
                second.portal.call(registry.receive, home["root"], grant, stale["ticket"], len(body), unneeded_data())
            ticket = second.portal.call(partial(registry.issue, current, file, action_deadline=time.time() + 30))

            async def chunks():
                yield body[:4]
                with pytest.raises(TransferDenied):
                    await registry.receive(home["root"], grant, ticket["ticket"], len(body), unneeded_data())
                yield body[4:]

            receipt = second.portal.call(registry.receive, home["root"], grant, ticket["ticket"], len(body), chunks())
            assert receipt["transfer_id"] == ticket["transfer_id"]
            bound, received = second.portal.call(_read_staged, registry, current, ticket["transfer_id"], file.action_id)
            assert bound == file and received == body


@pytest.mark.parametrize("body", [b"", b"binary\x00file" * 40000])
def test_delivery_ticket_is_single_use_directional_and_bound_to_its_computer(transfer_home, body):
    import hashlib
    import time
    from functools import partial

    from src.gateway.computer_connections import connections
    from src.gateway.computer_protocol import DELIVERY_PROTOCOL, TRANSFER_PATH
    from src.gateway.computer_transfers import TransferFile, private_spool

    home, registry = transfer_home
    grant = paired(home)
    other = paired(home, connector="b" * 32)
    file = TransferFile("1" * 32, "2" * 32, "3" * 32, "4" * 64, len(body), hashlib.sha256(body).hexdigest())
    client = socket_client(home)
    with connect(client, grant) as ws, private_spool(home["root"], len(body)) as source:
        source.write(body)
        ws.send_json(hello(grant, deliveries=True))
        assert ws.receive_json()["protocol"] == DELIVERY_PROTOCOL
        connection = connections.file_connection(home["root"], grant["connector_id"], home["store"].identity())
        incoming = ws.portal.call(partial(registry.issue, connection, file, action_deadline=time.time() + 30))
        headers = {"Authorization": "Bearer " + grant["token"], "X-VortoOne-Transfer-Ticket": incoming["ticket"]}
        assert client.get(TRANSFER_PATH, headers=headers).status_code == 403
        ws.portal.call(registry.abort, connection, incoming["transfer_id"])

        ticket = ws.portal.call(partial(registry.issue, connection, file, action_deadline=time.time() + 30,
                                        source=source))
        headers["X-VortoOne-Transfer-Ticket"] = ticket["ticket"]
        assert client.get(TRANSFER_PATH, headers={**headers, "Authorization": "Bearer " + other["token"]}).status_code == 403
        assert client.get(TRANSFER_PATH, headers={**headers, "Authorization": "Bearer " + OWNER}).status_code == 403
        assert client.get(TRANSFER_PATH, headers={**headers, "Range": "bytes=0-5"}).status_code == 400
        assert client.put(TRANSFER_PATH, headers={**headers, "Content-Type": "application/octet-stream",
                                                  "Content-Length": str(len(body))}, content=body).status_code == 403
        downloaded = client.get(TRANSFER_PATH, headers=headers)
        assert downloaded.status_code == 200 and downloaded.content == body
        assert downloaded.headers["content-length"] == str(len(body))
        assert downloaded.headers["cache-control"] == "no-store"
        assert client.get(TRANSFER_PATH, headers=headers).status_code == 403
