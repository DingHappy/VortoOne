"""Owner-approved recurring definitions, dispatching the existing collaboration service."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib

from src.scheduling.routine_schedule import RoutineSchedule, instant, zone
from src.workflow.staff.registry import StaffRegistry, _ID
from src.workflow.tasks.tasks import TASK_STATE_LOCK, TaskLedger

PUBLIC_FIELDS = {'id', 'name', 'member', 'prompt', 'acceptance', 'delivery', 'schedule', 'timezone', 'enabled', 'revision', 'token_budget'}


class RoutineCreatorUnavailable(ValueError):
    def __init__(self, reason, message, *, verified=True):
        super().__init__(message)
        self.reason = reason
        self.verified = verified


class RoutineRunRejected(ValueError):
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


class Routines:
    def __init__(self, root, *, clock=None, registry=None):
        self.root = str(root)
        self.registry = registry or StaffRegistry(self.root)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def read(self):
        value = self.registry._read('routines.json', {'version': 1, 'revision': 1, 'timezone': '', 'routines': [], 'migrations': []})
        value.setdefault('migrations', [])
        if (set(value) != {'version', 'revision', 'timezone', 'routines', 'migrations'} or type(value['revision']) is not int
                or value['revision'] < 1 or not isinstance(value['routines'], list)):
            raise ValueError('例行工作登记损坏，未恢复为空表')
        if value['timezone']:
            zone(value['timezone'])
        ids = [x.get('id') for x in value['routines'] if isinstance(x, dict)]
        if len(ids) != len(value['routines']) or len(ids) != len(set(ids)):
            raise ValueError('例行工作记录重复或无效')
        for item in value['routines']:
            required = PUBLIC_FIELDS | {'created_at', 'owner', 'binding', 'retired', 'legacy', 'state'}
            if (not required <= set(item) <= required | {'initiator'}
                    or 'initiator' in item and (not isinstance(item['initiator'], str) or not item['initiator'])
                    or not isinstance(item['id'], str) or not _ID.fullmatch(item['id'])
                    or type(item['revision']) is not int or item['revision'] < 1
                    or type(item['enabled']) is not bool or type(item['retired']) is not bool
                    or type(item['token_budget']) is not int or item['token_budget'] < 0
                    or not isinstance(item['state'], dict) or not isinstance(item['binding'], dict)):
                raise ValueError('例行工作记录损坏，未自动恢复或扩大权限')
            try:
                RoutineSchedule(item['schedule'], item['timezone'])
                instant(item['state']['cursor']); instant(item['created_at'])
                if not isinstance(item['binding']['member']['name'], str) or not isinstance(item['binding']['role']['title'], str):
                    raise ValueError('成员快照无效')
            except (KeyError, TypeError) as error:
                raise ValueError('例行工作的快照或排期状态无效') from error
        if not isinstance(value['migrations'], list):
            raise ValueError('迁移接管记录损坏')
        for marker in value['migrations']:
            if (not isinstance(marker, dict) or set(marker) != {'id', 'status', 'names', 'routines', 'cursors'}
                    or marker['status'] not in {'applying', 'complete'} or not isinstance(marker['names'], dict)
                    or not isinstance(marker['routines'], list) or not isinstance(marker['cursors'], dict)
                    or marker['status'] == 'complete' and not set(marker['routines']) <= set(ids)):
                raise ValueError('迁移接管记录无效，不能退回旧配置执行')
        return value

    def list(self):
        return deepcopy(self.read()['routines'])

    def definitions(self):
        return [{k: v for k, v in item.items() if k != 'state'} for item in self.list()]

    def get(self, routine_id):
        return next((x for x in self.list() if x['id'] == routine_id), None) or self.registry._missing('例行工作不存在')

    def configure_timezone(self, name, revision):
        zone(name)
        with TASK_STATE_LOCK:
            value = self.read()
            if type(revision) is not int or revision != value['revision']:
                raise ValueError('默认时区已变化，请刷新')
            value.update(timezone=name, revision=revision + 1)
            self.registry._write('routines.json', value)
            return value

    def normalize(self, data):
        if not isinstance(data, dict) or set(data) - PUBLIC_FIELDS:
            raise ValueError('例行工作不能附带权限、模型或未知字段')
        item = deepcopy(data)
        if type(item.get('revision', 1)) is not int or item.get('revision', 1) < 1:
            raise ValueError('例行工作版本必须为正整数')
        if not isinstance(item.get('id'), str) or not _ID.fullmatch(item['id']) or not item['id'].startswith('routine-'):
            raise ValueError('例行工作 id 必须以 routine- 开头')
        for field, maximum in [('name', 80), ('prompt', 4000)]:
            if not isinstance(item.get(field), str) or not item[field].strip() or len(item[field]) > maximum:
                raise ValueError(f'{field} 须为 1～{maximum} 字符，不能静默截断')
        member = self.registry.validate_member(self.registry.member(item.get('member')))
        role = self.registry.role(member['role'])
        if member['status'] != 'active' or not role['enabled']:
            raise ValueError('专员或岗位已暂停')
        from src.workflow.staff.service import StaffService
        StaffService.executable(role)
        acceptance = item.get('acceptance', [])
        if (not isinstance(acceptance, list) or len(acceptance) > 20
                or any(not isinstance(x, str) or not x.strip() or len(x) > 500 for x in acceptance)):
            raise ValueError('验收标准无效')
        delivery = item.get('delivery', 'review')
        if type(item.get('token_budget', 0)) is not int or item.get('token_budget', 0) < 0:
            raise ValueError('token_budget 必须为非负整数，0 表示不限')
        if delivery not in {'review', 'notify'} or type(item.get('enabled', True)) is not bool:
            raise ValueError('交付方式或启用状态无效')
        name = item.get('timezone') or self.read()['timezone']
        parsed = RoutineSchedule(item.get('schedule'), name)
        if parsed.once is not None and parsed.once <= instant(self.clock()):
            raise ValueError('一次性排期已过去，请明确选择未来时间')
        return {'id': item['id'], 'name': item['name'], 'member': member['id'], 'prompt': item['prompt'],
                'acceptance': acceptance, 'delivery': delivery, 'schedule': item['schedule'], 'timezone': name,
                'enabled': item.get('enabled', True), 'revision': item.get('revision', 1),
                'token_budget': item.get('token_budget', 0)}, member, role

    @staticmethod
    def check_unattended(role):
        from src.workflow.staff.roles import is_full
        if is_full(role):
            raise ValueError('这个 agent 是完整能力，例行工作无人值守、不用完整能力：登记了到点也不会执行。'
                             '请交给只读或公开资料的 agent（ADR-021）')

    def snapshot(self, data, *, initiator=None):
        item, member, role = self.normalize(data)
        previous = next((x for x in self.definitions() if x['id'] == item['id']), None)
        if initiator is not None:
            if previous is not None:
                raise ValueError('成员只能提议新建例行工作')
            from src.workflow.staff.initiators import record, role_for_member
            record(initiator)
            level = self.check_creator({**item, 'initiator': initiator}, member)
            role_for_member(self.registry, role, level)
        if previous and (previous['retired'] or type(data.get('revision')) is not int or data['revision'] != previous['revision']):
            raise ValueError('例行工作已删除或版本变化')
        return {'kind': 'routine', 'data': item, 'previous': previous, 'member_snapshot': member, 'role_snapshot': role,
                **({'initiator': initiator} if initiator is not None else {})}

    def target(self, snapshot, at, owner):
        previous = snapshot['previous']
        initiator = previous.get('initiator') if previous else snapshot.get('initiator')
        return {**snapshot['data'], 'revision': previous['revision'] + 1 if previous else 1,
            'created_at': previous['created_at'] if previous else at, 'owner': previous['owner'] if previous else owner,
            'binding': {'member': snapshot['member_snapshot'], 'role': snapshot['role_snapshot']},
            'retired': False, 'legacy': previous.get('legacy') if previous else None,
            **({'initiator': initiator} if initiator is not None else {})}

    def apply(self, target, previous):
        with TASK_STATE_LOCK:
            value = self.read()
            current = next((x for x in value['routines'] if x['id'] == target['id']), None)
            definition = {k: v for k, v in current.items() if k != 'state'} if current else None
            if definition == target:
                return current
            if definition != previous:
                raise ValueError('例行工作已变化，不能恢复旧决定')
            if current is None and len(value['routines']) >= 256:
                raise ValueError('例行工作记录达到上限，请先核对保留的记录')
            state = deepcopy(current['state']) if current else {}
            # Editing/resuming is an explicit new start, not a replay of paused slots.
            awaiting_takeover = current is not None and self.awaiting_takeover_resume(current)
            previous_task = state.get('task') if awaiting_takeover else None
            state.update(cursor=instant(self.clock()).isoformat(), task=previous_task, error='')
            if target['enabled'] and awaiting_takeover:
                state['takeover']['awaiting_enable'] = False
            result = {**deepcopy(target), 'state': state}
            value['routines'] = [result if x['id'] == target['id'] else x for x in value['routines']] if current else [*value['routines'], result]
            self.registry._write('routines.json', value)
            return deepcopy(result)

    def register(self, data, *, owner='owner'):
        """Trusted owner-approved helper; model tools only prepare proposals."""
        with TASK_STATE_LOCK:
            snapshot = self.snapshot(data)
            self.check_unattended(snapshot['role_snapshot'])
            return self.apply(self.target(snapshot, instant(self.clock()).isoformat(), owner), snapshot['previous'])

    def control(self, routine_id, revision, *, enabled=None, retire=False):
        with TASK_STATE_LOCK:
            value = self.read()
            item = next((x for x in value['routines'] if x['id'] == routine_id), None)
            if item is None or item['retired'] or type(revision) is not int or revision != item['revision']:
                raise ValueError('例行工作已变化，请刷新')
            if type(retire) is not bool or enabled is not None and type(enabled) is not bool:
                raise ValueError('启停参数无效')
            if enabled is True:
                member, role = self.check_binding(item)
                self.check_unattended(role)
                self.check_creator(item, member)
                if self.awaiting_takeover_resume(item):
                    once = RoutineSchedule(item['schedule'], item['timezone']).once
                    if once is not None and once <= instant(self.clock()):
                        raise ValueError('一次性排期已过去，请先重新排期')
                    item['state']['takeover']['awaiting_enable'] = False
            item.update(enabled=False if retire else enabled if enabled is not None else item['enabled'],
                        retired=retire, revision=revision + 1)
            item['state'].update(cursor=instant(self.clock()).isoformat(), error='')
            self.registry._write('routines.json', value)
            return deepcopy(item)

    def check_binding(self, item):
        member = self.registry.member(item['member'])
        role = self.registry.role(member['role'])
        if member != item['binding']['member'] or role != item['binding']['role'] or member['status'] != 'active' or not role['enabled']:
            raise ValueError('专员或岗位版本已变化，请重新提议例行工作')
        if member['department'] and self.registry.department(member['department'])['status'] != 'active':
            raise ValueError('部门已暂停')
        return member, role

    def check_creator(self, item, member=None):
        member_id = item.get('initiator')
        if member_id is None:
            return
        from src.workflow.members.registry import MemberRegistry, Missing
        from src.workflow.staff.roles import CLI_ENGINES
        member = member if member is not None else self.registry.member(item['member'])
        try:
            level = MemberRegistry(self.root).member_act_level_on(
                member['id'], member_id, required_permission='roles.manage_routines',
                requires_shared_plan=member.get('engine') in CLI_ENGINES)
        except Missing as error:
            raise RoutineCreatorUnavailable('creator_left', '例行工作的创建者已离开，等待接手') from error
        except (OSError, ValueError) as error:
            raise RoutineCreatorUnavailable('creator_no_access', '无法核对例行工作创建者的权限', verified=False) from error
        if level == 'none':
            raise RoutineCreatorUnavailable('creator_no_access', '创建者当前无权运行这条例行工作')
        return level

    def creator_issue(self, item, member=None):
        try:
            self.check_creator(item, member)
        except RoutineCreatorUnavailable as error:
            return error
        except (OSError, ValueError):
            return RoutineCreatorUnavailable('creator_no_access', '无法核对例行工作创建者的权限', verified=False)
        return None

    def _takeover_preview(self, item, owner, owner_session):
        from src.workflow.staff.approvals import digest
        from src.workflow.staff.owner_confirmation import owner_instance
        if not item.get('initiator'):
            raise ValueError('这条例行工作已经由所有者负责')
        member, role = self.check_binding(item)
        issue = self.creator_issue(item, member)
        if issue is None:
            raise ValueError('创建者仍能运行这条例行工作')
        if not issue.verified:
            raise ValueError(str(issue))
        definition = {key: value for key, value in item.items() if key != 'state'}
        approval = digest({'definition': definition, 'owner': owner, 'session': owner_session,
                           'reason': issue.reason, 'instance': owner_instance(self.root)})
        return {'digest': approval, 'name': item['name'], 'prompt': item['prompt'],
                'acceptance': item['acceptance'], 'schedule': item['schedule'], 'timezone': item['timezone'],
                'agentName': member['name'], 'operations': role['grants']['operations'],
                'tools': role['grants']['tools'], 'sources': role['grants']['sees']}

    def takeover_preview(self, routine_id, owner, owner_session):
        item = next((row for row in self.list() if row['id'] == routine_id and not row['retired']), None)
        if item is None:
            raise LookupError('没有这条例行工作')
        return self._takeover_preview(item, owner, owner_session)

    @staticmethod
    def awaiting_takeover_resume(item):
        receipt = item['state'].get('takeover')
        return isinstance(receipt, dict) and receipt.get('awaiting_enable') is True

    def take_over(self, routine_id, owner, owner_session, expected_digest):
        """Transfer future runs after an explicit owner decision on the stored definition."""
        from src.gateway.audit import append_audit
        from src.workflow.staff.owner_confirmation import require_owner_route
        require_owner_route(self.root)
        with TASK_STATE_LOCK:
            value = self.read()
            item = next((row for row in value['routines'] if row['id'] == routine_id and not row['retired']), None)
            if item is None:
                raise LookupError('没有这条例行工作')
            receipt = item['state'].get('takeover')
            if isinstance(receipt, dict) and receipt.get('digest') == expected_digest and receipt.get('owner') == owner:
                return
            if self._takeover_preview(item, owner, owner_session)['digest'] != expected_digest:
                raise ValueError('接手内容已变化，请重新核对')
            creator = item['initiator']
            at = instant(self.clock()).isoformat()
            if not append_audit(self.root, {
                'category': 'decision', 'operation': '接手例行工作', 'operation_kind': 'execute',
                'decision': 'allowed', 'decision_source': 'user', 'approval_scope': 'once',
                'approval_source': 'routine_takeover', 'action_digest': expected_digest,
                'routine_id': routine_id, 'initiator': creator, 'actor': owner,
            }, durable=True):
                raise OSError('接手审批未能写入审计')
            item.pop('initiator')
            item.update(owner=owner_session, enabled=False, revision=item['revision'] + 1)
            # Keep the previous task for reconciliation. The new revision blocks its tools and rework.
            item['state'].update(cursor=at, error='',
                                 takeover={'digest': expected_digest, 'owner': owner, 'from': creator,
                                           'at': at, 'awaiting_enable': True})
            self.registry._write('routines.json', value)

    def check_dispatch(self, reference, initiator):
        item = self.get(reference['id'])
        if item['state'].get('error'):
            raise ValueError('例行执行等待主人核对，不能继续旧步骤')
        if (self.awaiting_takeover_resume(item) or (not item['enabled'] and reference.get('manual') is not True)
                or item['retired'] or item['revision'] != reference['revision']):
            raise ValueError('例行工作已暂停、删除或改版，旧执行不能继续')
        if reference.get('legacy') != item['legacy'] or reference.get('token_budget', 0) != item['token_budget']:
            raise ValueError('例行工作的原约束已变化')
        if item.get('initiator') != initiator:
            raise ValueError('例行工作的发起人记录不一致，未执行')
        self.check_creator(item)
        self.check_binding(item)

    def check_task(self, task):
        dispatch = task.collaboration['dispatch']
        if dispatch.get('routine'):
            from src.workflow.staff.initiators import initiator_of
            actor = initiator_of(task)
            self.check_dispatch(dispatch['routine'], actor.id if actor is not None else None)

    def settle(self, task):
        if task.status not in {'done', 'failed', 'cancelled', 'interrupted'}:
            return
        reference = task.collaboration['dispatch']['routine']
        with TASK_STATE_LOCK:
            value = self.read()
            item = next(x for x in value['routines'] if x['id'] == reference['id'])
            receipt = task.id + ':round:' + str(task.collaboration['round'])
            if item['legacy'] and receipt not in item['state'].get('recorded_tasks', []):
                from src.scheduling.cron import CronState, record_outcome
                ok = task.status == 'done'
                run_id = record_outcome(self.root, item['legacy']['name'], ok=ok, summary=task.result or task.error,
                                       error=task.error, run_id='run-routine-' + task.id + '-r' + str(task.collaboration['round']))
                if not run_id:
                    raise OSError('旧作业的结果台账未保存，请核对原任务；没有重跑执行')
                CronState(self.root).record_task_result(item['legacy']['name'], ok, receipt)
                item['state']['recorded_tasks'] = [*item['state'].get('recorded_tasks', []), receipt][-100:]
            if item['state'].get('task') != task.id:
                self.registry._write('routines.json', value)
                return
            item['state'].update(last_task=task.id, last_status=task.status)
            # Review deliveries hold the routine until the owner accepts them.
            if task.status != 'done' or item['delivery'] == 'notify' or task.collaboration.get('review') == 'accepted':
                item['state']['task'] = None
            self.registry._write('routines.json', value)

    def run_now(self, routine_id, *, owner):
        """Dispatch a saved routine after the caller proves an explicit owner action."""
        from src.gateway.audit import append_audit
        from src.scheduling.staff_runner import get_staff_service
        from src.workflow.staff.approvals import digest

        with TASK_STATE_LOCK:
            item = next((row for row in self.list() if row['id'] == routine_id and not row['retired']), None)
            if item is None:
                raise RoutineRunRejected('not_found', '没有这条例行工作')
            if item['state'].get('error'):
                raise RoutineRunRejected('needs_review', f"它停着等你核对：{item['state']['error']}")
            if self.awaiting_takeover_resume(item):
                raise RoutineRunRejected('needs_review', '接手以后要先恢复它，才能运行')
            claim = item['state'].get('task')
            current = TaskLedger(self.root).load(claim) if claim else None
            if current is not None and current.status in {'queued', 'running', 'blocked'}:
                raise RoutineRunRejected('running', '它正在运行，没有再派一次')
            if (current is not None and current.status == 'done' and item['delivery'] == 'review'
                    and current.collaboration.get('review') != 'accepted'):
                raise RoutineRunRejected('awaiting_review', '上一次的结果还等你验收，验收以后才能再运行')
            self.check_creator(item)
            _, role = self.check_binding(item)
            self.check_unattended(role)
            staff = get_staff_service(self.root)
            when = instant(self.clock())
            slot = when.isoformat()
            definition = {key: value for key, value in item.items() if key != 'state'}
            if not append_audit(self.root, {
                'category': 'decision', 'operation': '主人手动运行例行工作', 'operation_kind': 'execute',
                'decision': 'allowed', 'decision_source': 'user', 'approval_scope': 'once',
                'approval_source': 'routine_manual', 'routine_id': routine_id, 'actor': owner,
                'routine_revision': item['revision'], 'slot': slot,
                'action_digest': digest({'definition': definition, 'slot': slot}),
            }, durable=True):
                raise RoutineRunRejected('audit_unavailable', '暂时无法记录这次手动运行，未派发')
            started = self.dispatch(staff, when, only=routine_id, manual=True)
            if not started:
                reason = self.get(routine_id)['state'].get('error')
                raise RoutineRunRejected('not_started', f"没能运行：{reason or '它的状态刚刚变了，请刷新'}")
            return started[0]

    def dispatch(self, staff, now, *, only=None, manual=False):
        started = []
        with TASK_STATE_LOCK:
            for original in self.list():
                if only is not None and original['id'] != only:
                    continue
                if any(m['status'] == 'applying' and original['id'] in m['routines'] for m in self.read()['migrations']):
                    continue
                if (not original['enabled'] and not manual) or original['retired'] or original['state'].get('error'):
                    continue
                if self.awaiting_takeover_resume(original):
                    continue
                value = self.read()
                item = next(x for x in value['routines'] if x['id'] == original['id'])
                claimed = None
                try:
                    self.check_creator(item)
                    if item['state'].get('task'):
                        task = TaskLedger(self.root).load(item['state']['task'])
                        if task is None:
                            raise ValueError('认领记录缺少原任务，请核对后显式恢复')
                        if task.status in {'queued', 'running', 'blocked'} or task.status == 'done' and item['delivery'] == 'review' and task.collaboration['review'] != 'accepted':
                            continue
                        self.settle(task)
                        value = self.read(); item = next(x for x in value['routines'] if x['id'] == original['id'])
                    member, role = self.check_binding(item)
                    due = instant(now) if manual else RoutineSchedule(item['schedule'], item['timezone']).due(now, item['state']['cursor'])
                    if due is None:
                        continue
                    slot = due.isoformat()
                    task_id = 'task-' + hashlib.sha256(f'{item["id"]}:{item["revision"]}:{slot}'.encode()).hexdigest()[:20]
                    claimed = task_id
                    item['state']['task'] = task_id
                    if not manual:
                        item['state']['cursor'] = slot
                    self.registry._write('routines.json', value)
                    task = staff.assign(item['owner'], {k: item[k] for k in ('member', 'prompt', 'acceptance', 'delivery')},
                        expected=(member, role), task_id=task_id,
                        routine={'id': item['id'], 'revision': item['revision'], 'slot': slot, 'legacy': item['legacy'],
                                 'token_budget': item['token_budget'], 'manual': manual}, after=self.settle,
                        **({'initiator': item['initiator']} if 'initiator' in item else {}))
                    started.append(task.id)
                except Exception as error:  # noqa: BLE001 - park this claim, then dispatch the other routines.
                    value = self.read(); item = next(x for x in value['routines'] if x['id'] == original['id'])
                    item['state']['error'] = f'{type(error).__name__}: {error}'
                    if isinstance(error, RoutineCreatorUnavailable):
                        item['enabled'] = False
                        if claimed is not None and item['state'].get('task') == claimed and TaskLedger(self.root).load(claimed) is None:
                            item['state']['task'] = None
                    self.registry._write('routines.json', value)
        return started

    def recover(self):
        with TASK_STATE_LOCK:
            value = self.read()
            changed = False
            for item in value['routines']:
                task_id = item['state'].get('task')
                if not task_id:
                    continue
                task = TaskLedger(self.root).load(task_id)
                from src.workflow.staff.recovery import never_started
                if task is not None and never_started(task):
                    continue
                if task is None or task.status in {'queued', 'running', 'blocked', 'interrupted'}:
                    item['state']['error'] = '重启中断了原执行；请核对任务和已生效动作后显式恢复，未自动重跑。'
                    if task is not None and task.status in {'queued', 'running', 'blocked'}:
                        from src.workflow.tasks.collaboration import CollaborationService
                        def interrupt(current):
                            current.status = 'interrupted'
                            current.error = '重启中断例行工作；请核对原任务，不会自动重放旧步骤。'
                        CollaborationService(self.root, task.owner_session).update_staff_action(task.id, task.collaboration['round'], interrupt)
                    changed = True
            if changed:
                self.registry._write('routines.json', value)

    def prepare_rework(self, task):
        """Explicit owner retry of this latest original slot, never another routine run."""
        with TASK_STATE_LOCK:
            reference = task.collaboration['dispatch']['routine']
            value = self.read()
            item = next(x for x in value['routines'] if x['id'] == reference['id'])
            previous = deepcopy(item['state'])
            if item['state'].get('task') not in {None, task.id} or (item['state'].get('task') is None and item['state'].get('last_task') != task.id):
                raise ValueError('例行工作已有其它执行，不能返工旧时段')
            if not reference.get('manual') and item['state']['cursor'] != reference['slot']:
                raise ValueError('例行工作已进入其它时段')
            item['state'].update(task=task.id, error='')
            self.registry._write('routines.json', value)
            try:
                self.check_task(task)
            except Exception:
                self.restore_rework((item['id'], task.id, previous))
                raise
            return item['id'], task.id, previous

    def restore_rework(self, previous):
        with TASK_STATE_LOCK:
            routine_id, task_id, state = previous
            value = self.read()
            item = next(x for x in value['routines'] if x['id'] == routine_id)
            if item['state'].get('task') == task_id:
                item['state'] = state
                self.registry._write('routines.json', value)
