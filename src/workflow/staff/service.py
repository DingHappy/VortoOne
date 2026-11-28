"""Single-specialist dispatch on CollaborationService and the existing execution pool."""
from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
from contextlib import asynccontextmanager, AsyncExitStack
from contextvars import ContextVar
from copy import deepcopy

from src.workflow.staff.registry import StaffRegistry
from src.workflow.tasks.collaboration import CollaborationService
from src.workflow.tasks.tasks import TASK_STATE_LOCK, TaskLedger, is_private_channel_draft

_BUDGET: ContextVar[object | None] = ContextVar('staff_active_budget', default=None)


class _ActiveBudget:
    def __init__(self, task_id, seconds):
        self.task_id, self.remaining = task_id, seconds
        self.loop = asyncio.get_running_loop()
        self.task = asyncio.current_task()
        self.handle, self.started, self.expired = None, None, False

    def pause(self):
        if self.handle is not None:
            self.handle.cancel()
            self.handle = None
        if self.started is not None:
            self.remaining -= max(0, self.loop.time() - self.started)
            self.started = None

    def resume(self):
        if self.started is not None:
            return
        self.started = self.loop.time()
        def expire():
            self.expired = True
            self.task.cancel()
        self.handle = self.loop.call_later(max(0, self.remaining), expire)


class StaffService:
    def __init__(self, root, runner, *, execute, on_update=None):
        self.root = str(root)
        self.runner = runner
        self.execute = execute
        self.on_update = on_update
        self.registry = StaffRegistry(root)
        self._slots = {}
        self._department_slots = {}
        self._budget_changed = asyncio.Event()
        self._loop = None
        from src.workflow.staff.budgets import StaffBudgets
        self.budgets = StaffBudgets(root)
        from src.workflow.staff.actions import StaffActions
        self.actions = StaffActions(self)

    def collaboration(self, owner):
        def updated(task):
            if task.collaboration.get('dispatch', {}).get('routine'):
                from src.workflow.staff.routines import Routines
                Routines(self.root).settle(task)
            notify = getattr(self.runner, 'notify_existing', None)
            if callable(notify):
                notify(task)
            if self.on_update is not None:
                self.on_update(task)
        return CollaborationService(self.root, owner, on_update=updated)

    def wake_budgets(self):
        if self._loop is not None:
            async def wake():
                self._budget_changed.set()
                for condition, _ in self._department_slots.values():
                    async with condition:
                        condition.notify_all()
            self._loop.call_soon_threadsafe(lambda: self._loop.create_task(wake()))

    def configure_budgets(self, data, revision):
        value = self.budgets.configure(data, revision)
        self.wake_budgets()
        return value

    def recover_quotas(self):
        for task in TaskLedger(self.root).list():
            if task.status == 'blocked' and task.collaboration.get('quota', {}).get('status') == 'waiting':
                def interrupt(current):
                    current.status = 'interrupted'
                    current.collaboration['quota']['status'] = 'interrupted'
                    current.error = '服务重启中断额度等待；请先核对历史，再显式返工或重新派活。'
                self.collaboration(task.owner_session).update_staff_action(task.id, task.collaboration['round'], interrupt)

    def recover(self):
        """Only durably unchanged, never-started work may re-enter the original pool."""
        from src.workflow.staff.recovery import never_started
        from src.workflow.staff.team_roster import is_managed
        with TASK_STATE_LOCK:
            for task in TaskLedger(self.root).list():
                if not is_managed(task) or task.status not in {'queued', 'running', 'blocked'}:
                    continue
                if getattr(self.runner, 'is_active', lambda _: False)(task.id):
                    continue
                try:
                    if not never_started(task):
                        raise ValueError('没有持久证据证明一步都没执行')
                    member, role = self.execution_identity(task)
                    dispatch = task.collaboration['dispatch']
                    if member['revision'] != dispatch['member_revision'] or role['revision'] != dispatch['role_revision']:
                        raise ValueError('专员或岗位版本已变化')
                    if member['department'] != dispatch.get('department', '') or role['id'] != dispatch['role']:
                        raise ValueError('原部门或岗位身份已变化，旧任务不能跨边界恢复')
                    self.check(member, role, task=task)
                    if dispatch['source'] == 'staff':
                        self.schedule(task, member, role)
                except Exception as error:  # noqa: BLE001 - uncertain work stays visible for owner reconciliation.
                    reason = f'重启后等待主人核对：{type(error).__name__}: {str(error)[:300]}'
                    def interrupt(current, reason=reason):
                        current.status = 'interrupted'
                        current.error = reason
                    CollaborationService(self.root, task.owner_session).update_staff_action(task.id, task.collaboration['round'], interrupt)

    def record_usage(self, task, meter, *, complete=True):
        def update(current):
            previous = current.collaboration.get('usage') or {'tokens': 0, 'calls': 0, 'complete': True}
            current.collaboration['usage'] = {'tokens': previous['tokens'] + meter['total_tokens'],
                'calls': previous['calls'] + meter['calls'], 'complete': previous['complete'] and complete,
                'day': self.budgets.today().isoformat()}
        self.collaboration(task.owner_session).update_staff_action(task.id, task.collaboration['round'], update)

    async def wait_for_budget(self, task, role, member):
        self.check(member, role, task=task)
        reason = self.budgets.status(task, member)
        if reason is None:
            return
        budget = _BUDGET.get()
        if budget is None or budget.task_id != task.id:
            raise ValueError('只有所属专员执行可以等待额度')
        self._loop = asyncio.get_running_loop()
        work = self.collaboration(task.owner_session)
        def waiting(current):
            if current.status != 'running' and not (current.status == 'blocked' and current.collaboration.get('quota', {}).get('status') == 'waiting'):
                raise ValueError('任务不在运行中，不能等待额度')
            current.status = 'blocked'
            current.collaboration['quota'] = {**reason, 'status': 'waiting'}
        budget.pause()
        try:
            while True:
                async with self.runner.waiting_for_owner(task.id):
                    work.update_staff_action(task.id, task.collaboration['round'], waiting)
                    while True:
                        self._budget_changed.clear()
                        self.check(member, role, task=task)
                        reason = self.budgets.status(task, member)
                        if reason is None:
                            break
                        now = datetime.now().astimezone()
                        tomorrow = datetime.combine(now.date() + timedelta(days=1), time.min).astimezone()
                        try:
                            await asyncio.wait_for(self._budget_changed.wait(), max(1, (tomorrow - now).total_seconds()))
                        except asyncio.TimeoutError:
                            pass
                self.check(member, role, task=task)
                # A peer request may settle while this worker waits to re-enter the pool.
                reason = self.budgets.status(task, member)
                if reason is None:
                    break
            def resume(current):
                if current.status != 'blocked' or current.collaboration.get('quota', {}).get('status') != 'waiting':
                    raise ValueError('额度等待已失效')
                current.status = 'running'
                current.collaboration['quota']['status'] = 'resumed'
            work.update_staff_action(task.id, task.collaboration['round'], resume)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            from src.agents.tool import ToolTurnYield
            raise ToolTurnYield('额度等待已中断，未继续执行，请核对任务。') from error
        finally:
            budget.resume()

    def assignment(self, args):
        if not isinstance(args, dict) or set(args) - {'member', 'prompt', 'acceptance', 'delivery'}:
            raise ValueError('派活只接受任务字段，不允许权限或其它字段')
        if not isinstance(args.get('prompt'), str) or not args['prompt'].strip() or len(args['prompt']) > 4000:
            raise ValueError('任务原文须为 1～4000 字符')
        acceptance = args.get('acceptance', [])
        if (not isinstance(acceptance, list) or len(acceptance) > 20
                or any(not isinstance(x, str) or not x.strip() or len(x) > 500 for x in acceptance)):
            raise ValueError('验收标准须为最多 20 项、每项最多 500 字符的非空文字')
        if args.get('delivery', 'review') not in {'review', 'notify'}:
            raise ValueError('交付方式只能是 review 或 notify')
        member = self.registry.member(args.get('member'))
        # Validate persisted records, including engine/source constraints, before use.
        member = self.registry.validate_member(member)
        assigned = self.registry.role(member['role'])
        if member['status'] != 'active' or not assigned['enabled']:
            raise ValueError('专员或岗位已停用')
        self._check_department(member, assigned)
        return member, assigned

    def _check_department(self, member, role):
        if not member['department']:
            return
        from src.workflow.staff.departments import Departments
        from src.workflow.staff.notes import StaffNotes
        department = self.registry.department(member['department'])
        if department['status'] != 'active':
            raise ValueError('部门已暂停或撤掉')
        Departments(self.registry).check_notes(department, StaffNotes(self.root).snapshot(department['id']))

    @staticmethod
    def executable(role):
        if role['grants']['project']:
            if not role.get('project_binding'):
                raise ValueError('未绑定的登记项目副本尚未接入专员执行；不会读写主工作区')
            from src.workflow.staff.projects import check
            check(role)
        if any(source.startswith('files:') for source in role['grants']['sees']):
            raise ValueError('登记目录来源尚未接入；不会用主工作区代替')

    async def confirm_action(self, task, role, member, call):
        budget = _BUDGET.get()
        if budget is None or budget.task_id != task.id:
            raise ValueError('只有执行中的所属专员任务可以提出审批')
        return await self.actions.request(task, role, member, call, budget)

    def check(self, member, role, *, task=None):
        if member['engine'] == 'codex' and task is not None:
            from src.workflow.staff.initiators import require_personal_codex_dispatch
            require_personal_codex_dispatch(self.root, task, member, role)
        if task is not None and task.collaboration.get('dispatch', {}).get('routine'):
            from src.workflow.staff.routines import Routines
            Routines(self.root).check_task(task)
        if member.get('team_task'):
            from src.workflow.tasks.team import TeamService
            from src.workflow.staff.team_roster import check
            check(self.root, TeamService(self.root).get(member['team_task']), member, role)
            return
        latest = self.registry.member(member['id'])
        current = self.registry.role(role['id'])
        if latest != member or current != role or latest['status'] != 'active' or not current['enabled']:
            raise ValueError('专员或岗位已变化，请重新读取并批准派活')
        self.executable(current)
        self._check_department(latest, current)
        if is_private_channel_draft(task):
            self._check_channel_draft(task, latest)

    def _check_channel_draft(self, task, member):
        from src.agents.principal import OWNER, Principal
        from src.workflow.home_identity import ensure_home_identity
        from src.workflow.members.channels import TeamChannels
        from src.workflow.members.registry import Refused
        from src.workflow.staff.initiators import initiator_of, level_on

        source = task.collaboration['dispatch']['channel']
        actor = initiator_of(task)
        if actor is None:
            if source.get('ownerIdentity') != ensure_home_identity(self.root):
                raise Refused('工作区所有者已变化')
            actor = Principal(OWNER, source['actorId'])
        elif actor.id != source.get('actorId'):
            raise Refused('频道草稿的发起人不匹配')
        channel_id = source['id']
        channels = TeamChannels(self.root, actor.id)
        card = channels.card(actor, channel_id)
        if (not card['can']['post'] or not channels.can_call_agents(actor, channel_id)
                or member['id'] not in channels.agents_here(actor, channel_id)
                or level_on(self.root, member['id'], actor) == 'none'):
            raise Refused('频道或智能体的办事权限已变化')

    def execution_identity(self, task):
        if task.collaboration['dispatch'].get('routine'):
            from src.workflow.staff.routines import Routines
            Routines(self.root).check_task(task)
        if task.collaboration['dispatch']['source'] == 'team':
            from src.workflow.tasks.team import TeamService
            return TeamService(self.root).execution_identity(task.id)
        return self.assignment({'member': task.collaboration['dispatch']['member'], 'prompt': task.prompt})

    @asynccontextmanager
    async def department_admission(self, department):
        if not department:
            yield
            return
        condition, count = self._department_slots.setdefault(department, (asyncio.Condition(), [0]))
        async with condition:
            await condition.wait_for(lambda: count[0] < self.registry.department(department)['limits']['parallel'])
            count[0] += 1
        try:
            yield
        finally:
            async with condition:
                count[0] -= 1
                condition.notify_all()

    @asynccontextmanager
    async def coordinator_admission(self, root):
        lead = root.team['coordinator'].get('member_snapshot')
        if lead:
            condition, count = self._slots.setdefault(lead['id'], (asyncio.Condition(), [0]))
            async with condition:
                await condition.wait_for(lambda: count[0] < lead['limits']['parallel'])
                count[0] += 1
        try:
            async with self.department_admission(root.team.get('department', '')):
                yield
        finally:
            if lead:
                async with condition:
                    count[0] -= 1
                    condition.notify_all()

    def assign_channel_draft(self, owner, args, *, task_id, initiator, channel, audit):
        """Register a confirmed private draft once, after the authorization audit is durable."""
        with TASK_STATE_LOCK:
            existing = TaskLedger(self.root).load(task_id)
            if existing is not None:
                dispatch = (existing.collaboration or {}).get('dispatch') or {}
                if (existing.kind != 'delegation' or existing.owner_session != owner
                        or dispatch.get('channel') != channel or dispatch.get('member') != args['member']
                        or (dispatch.get('initiator') or {}).get('id') != initiator
                        or existing.prompt != args['prompt'] or existing.collaboration.get('acceptance') != args['acceptance']):
                    raise ValueError('已存在的任务与草稿确认不匹配')
                return existing
            if not audit():
                raise OSError('审计未能保存，没有派出')
            return self.assign(owner, args, initiator=initiator, channel=channel, task_id=task_id)

    def assign(self, owner, args, *, expected=None, task_id=None, routine=None, after=None, initiator=None,
               via=None, channel=None, mission=None, owner_chat=False):
        """initiator：发起这件事的成员 id（例行工作沿用登记的发起人）。执行时据此收窄权限。

        via：这件事经哪个 AI 应用发来（``{"kind": "ai_app", "grant", "name"}``，只由 web 层的 /mcp 填）。
        这样的任务从一开始就带外部内容标记：文字出自那个应用，可能夹带它读到的东西，执行时不能当作授权。"""
        if via is not None and (not isinstance(via, dict) or via.get('kind') != 'ai_app'
                                or not isinstance(via.get('name'), str)):
            raise ValueError('来源标记无效')
        # 普通频道任务绑定消息/话题；确认的草稿只绑定频道，交付给自己的确认记录。
        private_draft = isinstance(channel, dict) and channel.get('privateDraft') is True
        channel_fields = {'id', 'actorId', 'privateDraft'} if private_draft else {'id', 'thread', 'message'}
        if private_draft and initiator is None:
            channel_fields.add('ownerIdentity')
        if channel is not None and (not isinstance(channel, dict) or set(channel) != channel_fields
                                    or not all(isinstance(channel[key], str) and channel[key]
                                               for key in channel_fields - {'privateDraft', 'ownerIdentity'})):
            raise ValueError('频道来源标记无效')
        if channel is not None and routine:
            raise ValueError('例行工作不从频道发起')
        # mission：频道话题里一个 Mission 的一步（web 层填 Mission id）。一定在频道里，沿用频道的收窄。
        if mission is not None and (channel is None or not isinstance(mission, str)
                                    or not mission.startswith('mission-') or len(mission) > 80):
            raise ValueError('Mission 来源标记无效')
        if private_draft and (not task_id or mission is not None or via is not None or owner_chat):
            raise ValueError('频道草稿需要独立的确认记录')
        if private_draft and (channel['actorId'] != initiator if initiator is not None
                              else not isinstance(channel['ownerIdentity'], dict)):
            raise ValueError('频道草稿的发起人不匹配')
        if initiator is not None and not routine:
            # 成员这个月的用量到了所有者设的上限（且是「拒绝」）：不再替他派活（主人 2026-11-23 定）
            from src.workflow.usage_month import check_member
            check_member(self.root, initiator)
        with TASK_STATE_LOCK:
            member, role = self.assignment(args)
            self.executable(role)
            if private_draft:
                from src.workflow.staff.initiators import member_policy_supported
                if member['engine'] != 'vortocode' or not member_policy_supported(role, member):
                    raise ValueError('这个智能体不能处理私有频道草稿')
                if args.get('delivery') != 'review' or len(args.get('acceptance') or []) != 1:
                    raise ValueError('私有频道草稿须有一项待审核的分工')
            codex_owner = None
            if member['engine'] == 'codex':
                from src.gateway.main_conversation import main_chat_key
                from src.workflow.staff.initiators import PERSONAL_CODEX_PRIVATE, require_personal_codex_owner
                from src.workflow.staff.owner_confirmation import require_owner_route

                if (owner_chat is not True or any(value is not None for value in (routine, initiator, via, channel, mission))
                        or owner != main_chat_key(self.root) or args.get('delivery') != 'notify' or args.get('acceptance')):
                    raise ValueError(PERSONAL_CODEX_PRIVATE)
                require_owner_route(self.root)
                codex_owner = require_personal_codex_owner(self.root, role)
            started_by = None
            if initiator is not None:
                from src.workflow.staff.initiators import record
                started_by = record(initiator)
            if routine:
                from src.workflow.staff.routines import Routines
                Routines(self.root).check_dispatch(routine, initiator)
            if expected is not None and (member, role) != expected:
                raise ValueError('确认期间专员或岗位已变化，未创建任务')
            notes = None
            if member['department'] and started_by is None and codex_owner is None and not private_draft:
                # 部门笔记是内部资料，不带进成员的任务或个人 Codex 私聊。
                from src.workflow.staff.notes import StaffNotes
                notes = StaffNotes(self.root).snapshot(member['department'])
            task = self.collaboration(owner).create(args['prompt'], member['id'], args.get('acceptance'),
                task_id=task_id,
                dispatch={'source': 'staff', 'member': member['id'], 'member_revision': member['revision'],
                          'role': role['id'], 'role_revision': role['revision'], 'department': member['department'],
                          **({'notes': notes} if notes is not None else {}),
                          **({'initiator': started_by} if started_by is not None else {}),
                          **({'via': dict(via)} if via is not None else {}),
                          **({'channel': dict(channel)} if channel is not None else {}),
                          **({'mission': mission} if mission is not None else {}),
                          **({'personal_codex_owner': codex_owner} if codex_owner is not None else {}),
                          'delivery': args.get('delivery', 'review'), 'max_steps': role['max_steps'],
                          'timeout_seconds': role['timeout_seconds'], **({'routine': routine} if routine else {})})
            from src.workflow.staff.recovery import seed_queue_proof
            def seed(current):
                seed_queue_proof(current)
                if via is not None or channel is not None or (routine or {}).get('hook'):
                    current.collaboration['tainted'] = True     # webhook 叫起来的：带着外面发来的内容
            task = self.collaboration(owner).update_staff_action(task.id, 1, seed)
            try:
                self.schedule(task, member, role, after=after)
            except Exception:
                self.collaboration(owner).abort(task.id, 1, error='未能进入现有执行池')
                raise
            return task

    def schedule(self, task, member, role, *, after=None, extra_admission=None):
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass  # Unit adapters may only collect jobs; a real pool requires its running loop.
        number = task.collaboration['round']
        work = self.collaboration(task.owner_session)
        private_draft = is_private_channel_draft(task)
        # A slot is local scheduling state; durable work remains the existing task record.
        condition, running = self._slots.setdefault(member['id'], (asyncio.Condition(), [0]))
        def check_assignment():
            self.check(member, role, task=work.get(task.id) if member['engine'] == 'codex' else task)
        async def execute():
            check_assignment()
            current = work.get(task.id)
            if current.collaboration['round'] != number:
                raise ValueError('任务轮次已变化')
            work.start(task.id, number)
            legacy = ((current.collaboration['dispatch'].get('routine') or {}).get('legacy') or {})
            budget = _ActiveBudget(task.id, min(role['timeout_seconds'], legacy.get('timeout', role['timeout_seconds'])))
            token = _BUDGET.set(budget)
            try:
                budget.resume()
                await self.wait_for_budget(current, role, member)
                result = await self.execute(current, work, deepcopy(role), deepcopy(member), check_assignment)
            except asyncio.CancelledError:
                if budget.expired:
                    raise TimeoutError('专员执行超过登记时限') from None
                raise
            finally:
                budget.pause()
                _BUDGET.reset(token)
            from src.workflow.staff.notes import StaffNotes, split_staff_result
            from src.workflow.tasks.tasks import BackgroundTask
            if isinstance(result, BackgroundTask) and result.id == task.id:
                text, suggestions = result.result, []
                if member['engine'] != 'vortocode':
                    self.record_usage(task, {'total_tokens': 0, 'calls': 0}, complete=False)
            else:
                text, suggestions = (str(result or ''), []) if private_draft else split_staff_result(result)
                work.finish(task.id, number, text, tainted=True)
            if suggestions and member['department']:
                try:
                    StaffNotes(self.root).propose(member['department'], suggestions, task_id=task.id,
                        round=number, member=member['id'], sources=role['grants']['sees'])
                except (OSError, ValueError):
                    def warn(current):
                        current.collaboration['notes_error'] = '交付结果已保存，笔记建议未进入待确认列表；请核对原交付或重新提议，未写入部门笔记。'
                        if isinstance(suggestions, list) and len(suggestions) <= 8 and all(isinstance(x, str) and len(x) <= 2000 for x in suggestions):
                            current.collaboration['note_suggestions'] = suggestions
                    work.update_staff_action(task.id, number, warn)
            if after is not None:
                after(work.get(task.id))
        @asynccontextmanager
        async def staff_admission():
            async with condition:
                await condition.wait_for(lambda: running[0] < member['limits']['parallel'])
                running[0] += 1
            try:
                async with self.department_admission(member['department']):
                    yield
            finally:
                async with condition:
                    running[0] -= 1
                    condition.notify_all()
        @asynccontextmanager
        async def admission():
            async with AsyncExitStack() as stack:
                if extra_admission is not None:
                    await stack.enter_async_context(extra_admission())
                await stack.enter_async_context(staff_admission())
                yield
        def stopped(error='', cancelled=False):
            current = work.abort(task.id, number, cancelled=cancelled, error=error)
            if after is not None:
                after(current)
        queued = self.runner.enqueue_existing(task.id, execute, admission=admission,
            on_cancel=lambda: stopped(cancelled=True),
            on_failure=lambda error: stopped(error=f'专员执行失败：{type(error).__name__}: {str(error)[:240]}'))
        if not queued:
            raise ValueError('任务未能排入执行池')

    def followup(self, owner, task_id, number, message):
        with TASK_STATE_LOCK:
            work = self.collaboration(owner)
            previous = work.get(task_id)
            if previous.status == 'interrupted':
                from src.workflow.staff.owner_confirmation import require_owner_route
                require_owner_route(self.root)
            dispatch = previous.collaboration.get('dispatch', {})
            if dispatch.get('source') != 'staff':
                raise ValueError('不是专员任务')
            member, role = self.assignment({'member': dispatch['member'], 'prompt': previous.prompt})
            if member['engine'] == 'codex':
                from src.workflow.staff.initiators import PERSONAL_CODEX_PRIVATE
                raise ValueError(PERSONAL_CODEX_PRIVATE)
            self.executable(role)
            if role['revision'] != dispatch['role_revision'] or member['revision'] != dispatch['member_revision']:
                raise ValueError('原岗位或专员已变化，须重新派活，不能扩大原任务权限')
            if member['department'] != dispatch.get('department', ''):
                raise ValueError('原部门已变化，须重新派活，不能跨部门续用原任务')
            task = work.followup(task_id, number, message)
            if member['department']:
                from src.workflow.staff.notes import StaffNotes
                def snapshot(current):
                    current.collaboration['dispatch']['notes'] = StaffNotes(self.root).snapshot(member['department'])
                task = work.update_staff_action(task.id, task.collaboration['round'], snapshot)
            try:
                self.schedule(task, member, role)
            except Exception:
                work.abort(task.id, task.collaboration['round'], error='返工未能进入现有执行池')
                raise
            return task

    def directory(self):
        return staff_directory(self.root)


def staff_directory(root, *, include_prompts=False):
    from src.workflow.staff.executions import execution_projection
    tasks = TaskLedger(root).list(limit=500)
    members = StaffRegistry(root).members()
    for member in members:
        from src.workflow.staff.team_roster import is_managed
        own = [t for t in tasks if is_managed(t)
               and t.collaboration['dispatch'].get('member') == member['id']]
        member['busy'] = sum(t.status in {'queued', 'running', 'blocked'} for t in own)
        member['recent_tasks'] = [{'id': t.id, 'status': t.status, 'review': t.collaboration.get('review'),
            'delivery': t.collaboration['dispatch']['delivery'],
            'execution': execution_projection(t),
            **({'prompt': t.prompt} if include_prompts else {})} for t in own[:10]]
    from src.workflow.staff.budgets import StaffBudgets
    from src.workflow.staff.notes import StaffNotes
    departments = StaffRegistry(root).departments()
    for department in departments:
        notes = StaffNotes(root)._read(department['id'])
        department['notes_revision'] = notes['current']['revision']
        department['pending_notes'] = len(notes['pending'])
        department['notes_pending_save'] = bool(notes.get('applying'))
        department['busy'] = sum(t.status in {'queued', 'running', 'blocked'} and
            t.collaboration.get('dispatch', {}).get('department') == department['id'] for t in tasks)
    budgets = StaffBudgets(root)
    from src.workflow.staff.routines import Routines
    from src.workflow.staff.standing import standing_catalog
    routine_rows = Routines(root).list()
    for department in departments:
        department['routines'] = [item['id'] for item in routine_rows if not item['retired']
                                  and item['binding']['member']['department'] == department['id']]
    try:
        usage = budgets.usage()
    except (OSError, ValueError):
        usage = {'error': '用量暂不可核对，不能据此推断为零'}
    from src.workflow.workbench.resident_projects import load_resident_projects
    return {'roles': StaffRegistry(root).roles(), 'members': members, 'departments': departments,
            'development_projects': [{'name': row['name']} for row in load_resident_projects()],
            'standing_specialists': standing_catalog(),
            'routines': [{k: row[k] for k in ('id', 'name', 'member', 'schedule', 'timezone', 'enabled', 'revision', 'delivery', 'token_budget')}
                         for row in routine_rows if not row['retired']],
            'routine_timezone': Routines(root).read()['timezone'],
            'budget_settings': budgets.settings(), 'usage': usage}
