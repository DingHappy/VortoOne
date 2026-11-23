"""Owner-facing role conversations, projected from existing staff collaboration tasks.

There is no second conversation log, execution pool, grant, or automatic retry.
The Web adapter supplies the authenticated owner-route scope for every write.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import re

from src.utils.ids import typed_id

from src.gateway.audit import append_audit
from src.gateway.main_conversation import main_chat_key
from src.workflow.staff.approvals import StaffApprovals, digest
from src.workflow.staff.owner_confirmation import owner_instance, require_owner_route
from src.workflow.staff.recovery import task_digest
from src.workflow.staff.registry import StaffRegistry
from src.workflow.staff.executions import execution_projection
from src.workflow.tasks.tasks import TASK_STATE_LOCK, TaskLedger

_REQUEST = re.compile(r'[a-f0-9]{32}')
ANYONE = object()      # read() 不按发起人筛（原有调用方的默认）


def _initiator(task):
    value = task.collaboration.get('dispatch', {}).get('initiator')
    return value.get('id') if isinstance(value, dict) else (None if value is None else '\0broken')


class StaffConversations:
    def __init__(self, root):
        self.root = str(root)
        self.registry = StaffRegistry(root)
        self.ledger = TaskLedger(root)

    @property
    def instance(self):
        return owner_instance(self.root)

    def _runtime(self):
        from src.scheduling.staff_runner import get_staff_service
        return get_staff_service(self.root)

    def _owner(self):
        owner = main_chat_key(self.root)
        if not owner:
            raise ValueError('请先打开主要聊天，完成个人空间连接')
        return owner

    def _identity(self, member_id):
        member = self.registry.member(member_id)
        return member, self.registry.role(member['role'])

    def _instance(self, expected):
        if expected != self.instance:
            raise ValueError('运行端实例已变化，请刷新后重新核对')

    def _request(self, value):
        if not isinstance(value, str) or not _REQUEST.fullmatch(value):
            raise ValueError('请求标识无效')

    @staticmethod
    def _matches(task, member_id):
        return (task.kind == 'delegation'
                and task.collaboration.get('dispatch', {}).get('source') == 'staff'
                and task.collaboration['dispatch'].get('member') == member_id)

    def _task(self, member_id, task_id):
        if not typed_id(task_id, 'task'):
            raise ValueError('任务标识无效')
        task = self.ledger.load(task_id)
        if task is None or not self._matches(task, member_id):
            raise ValueError('这件任务不属于当前角色')
        return task

    def _view(self, task):
        return {**task.to_dict(), 'execution': execution_projection(task), 'confirmation': {'digest': task_digest(task),
                'round': task.collaboration['round'], 'instance': self.instance}}

    def read(self, member_id, *, before='', limit=30, request_id='', initiator=ANYONE):
        """initiator：None 只看所有者自己交代的事；一个成员 id 只看那个成员发起的事；不传则全部。
        筛选在分页之前做，摘要（busy、recent_tasks）也只按筛过的算——成员看不到别人交代了什么。"""
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('每页最多读取 50 件任务')
        with TASK_STATE_LOCK:
            member, role = self._identity(member_id)
            inventory = self.ledger.scan()
            tasks = sorted((t for t in inventory.tasks if self._matches(t, member_id)
                            and not t.collaboration.get('dispatch', {}).get('channel')    # 频道里的事不进私聊
                            and (initiator is ANYONE or _initiator(t) == initiator)),
                           key=lambda t: (t.created, t.id), reverse=True)
            all_tasks = tasks
            if before:
                cursor = self._task(member_id, before)
                if initiator is not ANYONE and _initiator(cursor) != initiator:
                    raise ValueError('这件任务不属于当前对话')
                tasks = [t for t in tasks if (t.created, t.id) < (cursor.created, cursor.id)]
            page = tasks[:limit]
            shown_member = {**member, 'busy': sum(t.status in {'queued', 'running', 'blocked'} for t in all_tasks),
                'recent_tasks': [{'id': t.id, 'status': t.status, 'review': t.collaboration['review'],
                    'delivery': t.collaboration['dispatch'].get('delivery', 'review'), 'prompt': t.prompt,
                    'execution': execution_projection(t)} for t in all_tasks[:20]]}
            submitted = None
            if request_id:
                self._request(request_id)
                task_id = self.request_task_id(member_id, request_id)
                existing = self.ledger.load(task_id)
                if existing is not None and (initiator is ANYONE or _initiator(existing) == initiator):
                    submitted = self._view(self._task(member_id, task_id))
            available, unavailable = True, ''
            try:
                service = self._runtime()
                current = service.assignment({'member': member_id, 'prompt': 'Owner task preview'})
                service.executable(current[1])
                self._owner()
            except ValueError as error:
                available, unavailable = False, str(error)
            return {'version': 1, 'instance': self.instance, 'member': shown_member, 'role': role,
                    'identity_digest': digest({'member': member, 'role': role}), 'role_digest': digest(role),
                    'tasks': [self._view(t) for t in page], 'history_complete': inventory.complete,
                    'next_cursor': page[-1].id if len(tasks) > limit else None,
                    'submitted_task': submitted, 'can_dispatch': available, 'unavailable_reason': unavailable}

    @staticmethod
    def request_task_id(member_id, request_id):
        return 'task-' + hashlib.sha256(f'{member_id}\0{request_id}'.encode()).hexdigest()[:24]

    def _audit(self, operation, task, action_digest, *, kind='execute'):
        if not append_audit(self.root, {'category': 'decision', 'decision': 'allowed',
                'decision_source': 'user', 'operation': operation, 'operation_kind': kind,
                'approval_scope': 'once', 'staff_task': task, 'action_digest': action_digest,
                'instance': self.instance, 'approval_source': 'owner_role_conversation'}, durable=True):
            raise OSError('主人操作审计未能保存，未放行')

    def submit(self, member_id, body, *, via=None):
        require_owner_route(self.root)
        self._instance(body['instance'])
        self._request(body['request_id'])
        with TASK_STATE_LOCK:
            service = self._runtime()
            args = {key: deepcopy(body[key]) for key in ('prompt', 'acceptance', 'delivery')}
            args['member'] = member_id
            member, role = service.assignment(args)
            if (member['revision'] != body['member_revision']
                    or role['revision'] != body['role_revision']
                    or body['identity_digest'] != digest({'member': member, 'role': role})):
                raise ValueError('角色或岗位已变化，请刷新后重新核对')
            service.executable(role)
            owner = self._owner()
            task_id = self.request_task_id(member_id, body['request_id'])
            existing = self.ledger.load(task_id)
            if existing is not None:
                dispatch = existing.collaboration.get('dispatch', {})
                if (not self._matches(existing, member_id) or existing.owner_session != owner
                        or existing.prompt != args['prompt']
                        or existing.collaboration['acceptance'] != args['acceptance']
                        or dispatch.get('delivery') != args['delivery']
                        or dispatch.get('member_revision') != member['revision']
                        or dispatch.get('role_revision') != role['revision']):
                    raise ValueError('该请求标识已有其它内容，未重复派发')
                return {'task': self._view(existing), 'replayed': True}
            self._audit('主人交给角色处理任务', task_id, digest(args))
            task = service.assign(owner, args, expected=(member, role), task_id=task_id, via=via)
            return {'task': self._view(task), 'replayed': False}

    def act(self, member_id, task_id, body):
        require_owner_route(self.root)
        self._instance(body['instance'])
        with TASK_STATE_LOCK:
            self._identity(member_id)
            task = self._task(member_id, task_id)
            if body['round'] != task.collaboration['round'] or body['digest'] != task_digest(task):
                raise ValueError('任务轮次或内容已变化，请刷新核对后再操作')
            service = self._runtime()
            verdict = body['verdict']
            if verdict == 'accept':
                if task.status != 'done' or task.collaboration['review'] != 'pending':
                    raise ValueError('当前没有待验收的完整结果')
                self._audit('主人验收角色交付', task.id, body['digest'], kind='irreversible')
                task = service.collaboration(task.owner_session).review(task.id, body['round'],
                    'accept', body['note'], owner_approved=True)
            elif verdict == 'rework':
                if task.collaboration['dispatch'].get('routine'):
                    raise ValueError('例行工作请在原任务中核对，不从角色对话重新派发')
                # The existing service rechecks original grants, department and round limits.
                self._audit('主人补充角色任务并继续', task.id, body['digest'])
                task = service.followup(task.owner_session, task.id, body['round'], body['note'])
            elif verdict == 'stop':
                if task.status not in {'queued', 'running', 'blocked'}:
                    raise ValueError('这件任务当前不在运行')
                self._audit('主人请求停止角色任务', task.id, body['digest'], kind='write')
                if not service.runner.cancel(task.id):
                    raise ValueError('当前执行池没有接到停止请求，请刷新核对，不自动重跑')
                return {'task': self._view(self._task(member_id, task_id)), 'stop_requested': True}
            else:
                raise ValueError('不支持的角色任务操作')
            return {'task': self._view(task)}

    def stop_channel_task(self, task_id: str) -> bool:
        """所有者停止频道里交给 agent 的一件事。走和私聊停止同一条有审计的路径。"""
        require_owner_route(self.root)
        with TASK_STATE_LOCK:
            task = self.ledger.load(task_id)
            dispatch = (task.collaboration or {}).get('dispatch', {}) if task is not None else {}
            if task is None or not dispatch.get('channel') or not dispatch.get('member'):
                raise LookupError('没有这件事')
            if task.status not in {'queued', 'running', 'blocked'}:
                return False
            decision = self.act(dispatch['member'], task_id, {
                'verdict': 'stop', 'note': '主人停止频道里交给 agent 的事', **self._view(task)['confirmation']})
            return decision['stop_requested'] is True

    def stop_routine(self, routine_id: str) -> bool:
        """Request a stop for the occurrence held by the routine's current claim."""
        from src.workflow.staff.routines import Routines

        require_owner_route(self.root)
        with TASK_STATE_LOCK:
            routine = next((item for item in Routines(self.root).list()
                            if item['id'] == routine_id and not item['retired']), None)
            if routine is None:
                raise LookupError('没有这条例行工作')
            task_id = routine['state'].get('task')
            if not task_id:
                return False
            task = self._task(routine['member'], task_id)
            reference = task.collaboration['dispatch'].get('routine')
            if not isinstance(reference, dict) or reference.get('id') != routine_id:
                raise ValueError('当前例行工作与任务的认领记录不一致')
            if task.status not in {'queued', 'running', 'blocked'}:
                return False
            decision = self.act(routine['member'], task_id, {
                'verdict': 'stop', 'note': '主人停止这条例行工作的当前执行',
                **self._view(task)['confirmation'],
            })
            return decision['stop_requested'] is True

    def propose_member(self, body):
        require_owner_route(self.root)
        self._instance(body['instance'])
        self._request(body['request_id'])
        with TASK_STATE_LOCK:
            role = self.registry.role(body['role'])
            if (not role['enabled'] or role['revision'] != body['role_revision']
                    or body['role_digest'] != digest(role)):
                raise ValueError('岗位已变化或停用，请重新核对')
            data = {key: body[key] for key in ('name', 'role', 'engine', 'model')}
            data['id'] = 'staff-' + body['request_id']
            approvals = StaffApprovals(self.root)
            owner = self._owner()
            for item in approvals.list():
                if item['snapshot']['kind'] == 'member' and item['snapshot']['data']['id'] == data['id']:
                    if item['owner'] != owner or any(item['snapshot']['data'].get(key) != value for key, value in data.items()):
                        raise ValueError('该提议标识已有其它内容，请重新核对')
                    return item
            snapshot = approvals.snapshot('member', data)
            return approvals.propose(owner, 'member', data, '主人从角色创建表单选择已登记岗位', expected=snapshot)
