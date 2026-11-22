"""完整能力的 agent（主人 2026-11-22 定）：和主助理同一套工具和规则。

- 工具来自服务登记的主助理装配，不再是登记表里的几样；派活、开小组这类会绕成派活链的除外。
- 危险操作照样过确认门，在后台任务里变成审批卡，主人批了才做。
- 成员发起的事没有完整能力：退回按成员办事级别核对过的登记工具。
- 登记里只认固定的一种写法，不能借「完整」再加私人来源、换成没有确认门的本机 CLI。
"""
import asyncio
from copy import deepcopy

import pytest

from tests.unit.staff_owner_helpers import owner_action
from src.agents.taint import reset_taint
from src.workflow.staff.registry import StaffRegistry
from src.workflow.staff.roles import FULL_GRANTS
from src.workflow.staff.service import StaffService
from src.workflow.tasks.tasks import TaskLedger, TaskRunner


@pytest.fixture(autouse=True)
def clean():
    from src.agents.staff import set_full_toolset
    reset_taint()
    yield
    reset_taint()
    set_full_toolset(None)


def _full_role(role_id='agent-full'):
    return {'id': role_id, 'title': '助手', 'brief': '你是助手。', 'grants': deepcopy(FULL_GRANTS),
            'engine': ['vortocode'], 'max_steps': 40, 'timeout_seconds': 1800}


def test_the_registry_accepts_only_the_fixed_full_grant(tmp_path):
    registry = StaffRegistry(str(tmp_path))
    assert registry.register_role(_full_role())['grants'] == FULL_GRANTS
    for change in ({'tools': ['web_search']}, {'sees': ['memory']}, {'operations': ['read', 'internal']}):
        bad = _full_role('agent-bad')
        bad['grants'] = {**bad['grants'], **change}
        with pytest.raises(ValueError, match='固定的一组'):
            registry.register_role(bad)
    cli = {**_full_role('agent-cli'), 'engine': ['claude-code']}
    with pytest.raises(ValueError, match='自有框架'):
        registry.register_role(cli)


def test_without_the_main_assistant_assembly_a_full_agent_does_not_run(tmp_path):
    from src.agents.staff import build_staff_member
    registry = StaffRegistry(str(tmp_path))
    role = registry.register_role(_full_role())
    member = registry.register_member({'id': 'helper', 'name': '助手', 'role': role['id'], 'engine': 'vortocode'})

    async def confirm(call):
        return True
    with pytest.raises(ValueError, match='常驻助理的服务'):
        build_staff_member(str(tmp_path), role, member, check=lambda: None, llm=object(), confirm_action=confirm)


def _assembly(kind, prepared, invoked):
    from src.agents.tool import PreparedToolCall, Tool

    async def prepare(args):
        frozen = deepcopy(args)
        prepared.append(frozen)

        async def apply():
            invoked.append(frozen)
            return '写好了'
        return PreparedToolCall(apply, '写入文件：' + frozen['text'], kind)

    async def nothing(_args):
        return '不该被调用'
    seen = {}

    def builder(root, ask, agent_id):
        seen['root'] = root
        tools = [Tool.prepared('host_write', '主助理的写工具', {'text': '正文'}, prepare, operation_kind=kind,
                               required_args=('text',)),
                 Tool('staff_assign', '派活', {}, nothing, operation_kind='write'),
                 Tool('team_propose', '开小组', {}, nothing, operation_kind='write'),
                 Tool('request_workspace', '切工作区', {}, nothing, operation_kind='read')]
        return tools, (lambda: 'ask'), 'external', {}
    return builder, seen


@pytest.mark.parametrize('approve', [True, False])
async def test_a_full_agent_uses_the_main_assistants_tools_and_waits_for_the_owner(tmp_path, approve):
    from src.agents.staff import build_staff_member, set_full_toolset
    prepared, invoked, names, results = [], [], [], []
    builder, seen = _assembly('write', prepared, invoked)
    set_full_toolset(builder)
    registry = StaffRegistry(str(tmp_path))
    role = registry.register_role(_full_role())
    member = registry.register_member({'id': 'helper', 'name': '助手', 'role': role['id'], 'engine': 'vortocode'})
    waiting = asyncio.Queue()

    def update(task):
        card = task.collaboration.get('approval', {})
        if card.get('status') == 'pending':
            waiting.put_nowait((task, deepcopy(card)))

    async def execute(task, work, assigned, person, check):
        async def confirm(call):
            return await service.confirm_action(task, assigned, person, call)
        agent = build_staff_member(str(tmp_path), assigned, person, check=check, llm=object(), confirm_action=confirm)
        names.extend(agent.tools)
        results.append(await agent._run_tool('host_write', {'text': '周报'}, 'build', lambda _: None))
        return '做完了'
    service = StaffService(str(tmp_path), TaskRunner(str(tmp_path), None, max_concurrent=1), execute=execute,
                           on_update=update)
    try:
        task = service.assign('owner', {'member': member['id'], 'prompt': '写周报'})
        _, card = await asyncio.wait_for(waiting.get(), 2)
        assert card['payload']['tool'] == 'host_write' and card['payload']['kind'] == 'write'
        assert TaskLedger(tmp_path).load(task.id).status == 'blocked' and not invoked
        owner_action(service, task, card, approve, reason='' if approve else '先别写')
        await service.runner.join(task.id)
    finally:
        await service.runner.shutdown()
    assert seen['root'] == str(tmp_path)
    assert 'host_write' in names
    assert not {'staff_assign', 'team_propose', 'request_workspace'} & set(names)   # 不再派活、不切工作区
    assert invoked == ([{'text': '周报'}] if approve else [])
    assert TaskLedger(tmp_path).load(task.id).status == 'done'


def test_a_member_never_gets_the_full_abilities(tmp_path):
    from src.workflow.staff.initiators import role_for_member
    registry = StaffRegistry(str(tmp_path))
    role = registry.register_role(_full_role())
    narrowed = role_for_member(registry, role, 'execute')
    grants = narrowed['grants']
    assert 'full' not in grants and grants['sees'] == [] and grants['project'] is False
    assert not {'deliver', 'interact', 'irreversible'} & set(grants['operations'])
    catalog = registry.tool_catalog()
    private = {'save_memory', 'mail_list', 'mail_read', 'calendar_events', 'recent_notices', 'write_file', 'read_file'}
    assert set(grants['tools']) <= set(catalog) and not private & set(grants['tools'])
    registry.validate_role(narrowed)          # 收窄后的是一份普通的、校验得过的岗位


def test_a_member_cannot_assemble_a_full_agent_directly(tmp_path):
    from src.agents.principal import MEMBER, Principal
    from src.agents.staff import build_staff_member, set_full_toolset
    builder, _ = _assembly('write', [], [])
    set_full_toolset(builder)
    registry = StaffRegistry(str(tmp_path))
    role = registry.register_role(_full_role())
    member = registry.register_member({'id': 'helper', 'name': '助手', 'role': role['id'], 'engine': 'vortocode'})

    async def confirm(call):
        return True
    with pytest.raises(ValueError, match='成员发起'):
        build_staff_member(str(tmp_path), role, member, check=lambda: None, llm=object(), confirm_action=confirm,
                           principal=Principal(MEMBER, 'm1', str(tmp_path)))


def test_the_approval_card_still_checks_the_operation_kind(tmp_path):
    """完整能力不列工具，但审批照样只认岗位里的操作类别：任务内部协作这类不在里面。"""
    from src.workflow.staff.actions import StaffActions
    registry = StaffRegistry(str(tmp_path))
    role = registry.register_role(_full_role())
    member = registry.register_member({'id': 'helper', 'name': '助手', 'role': role['id'], 'engine': 'vortocode'})
    service = StaffService(str(tmp_path), TaskRunner(str(tmp_path), None, max_concurrent=1), execute=None)
    actions = service.actions if hasattr(service, 'actions') else StaffActions(service)
    call = {'tool': 'anything', 'args': {}, 'kind': 'internal', 'message': '内部'}
    with pytest.raises(ValueError, match='不在登记岗位权限内'):
        asyncio.run(actions.request(None, role, member, call, None))


def test_the_service_hands_agents_the_main_assistants_assembly(tmp_path, monkeypatch):
    """真的装配一次：通用工作区里主助理有的（联网、记忆、制品）agent 也有。"""
    from src.agents.staff import _full_tools, set_full_toolset
    from src.web.agent_toolset import main_assistant_toolset
    monkeypatch.setattr('src.gateway.workspace_scope.current_workspace_scope', lambda: 'general')
    monkeypatch.delenv('VORTOONE_DESKTOP_SIDECAR', raising=False)
    monkeypatch.delenv('VORTOCODE_API_TOKEN', raising=False)
    set_full_toolset(main_assistant_toolset)

    async def ask(message, kind):
        return False
    tools, trust_level, profile, _session = _full_tools(str(tmp_path), ask, 'helper')
    names = {tool.name for tool in tools}
    assert {'web_search', 'web_fetch', 'save_memory'} <= names
    assert not any(name.startswith(('staff_', 'team_', 'routine_')) for name in names)
    assert not {'update_plan', 'request_build', 'request_workspace'} & names
    assert profile == 'external' and callable(trust_level)


def test_handing_work_to_a_full_agent_asks_as_a_write():
    """派活给完整能力的 agent 按「写」问：它自己的危险步骤另外逐次问，派活不标成不可撤销。"""
    from src.agents.staff import operation
    assert operation(_full_role()) == 'write'
    assert operation({'grants': {'operations': ['read']}}) == 'read'
