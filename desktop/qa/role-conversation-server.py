"""Loopback-only, offline UI fixture using the real owner APIs and task pool.

All records live in a new temporary directory. No model or external notifier is
installed. Completing a fixture task requires an explicit /qa/finish request.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
import tempfile

SOURCE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOURCE))
ROOT = tempfile.TemporaryDirectory(prefix='vortoone-role-ui-')
os.chdir(ROOT.name)
os.environ.update(VORTOONE_API_TOKEN='offline-role-owner', VORTOONE_CRON='0',
                  VORTOONE_HEARTBEAT='0', OPENAI_API_KEY='', VORTOONE_TG_TOKEN='')

from fastapi import Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from starlette.middleware.cors import CORSMiddleware  # noqa: E402

from src.gateway.main_conversation import claim_main_chat  # noqa: E402
from src.scheduling.staff_runner import set_staff_service  # noqa: E402
from src.web.server import app  # noqa: E402
from src.workflow.staff.approvals import StaffApprovals  # noqa: E402
from src.workflow.staff.owner_confirmation import owner_route  # noqa: E402
from src.workflow.tasks.tasks import TaskRunner  # noqa: E402

claim_main_chat(ROOT.name, 'sid-owner')
approvals = StaffApprovals(ROOT.name)
proposal = approvals.propose('sid-owner', 'role', {'id': 'calendar-reader', 'install': True}, 'Offline fixture')
with owner_route(ROOT.name):
    approvals.decide(proposal['id'], True, proposal['digest'], nonce=proposal['nonce'],
                     round=proposal['round'], instance=proposal['instance'])
for data in [
    {'id': 'research', 'name': '研究助手', 'role': 'web-researcher', 'engine': 'vortocode'},
    {'id': 'calendar', 'name': '日程助手', 'role': 'calendar-reader', 'engine': 'vortocode'},
    {'id': 'developer', 'name': '开发助手', 'role': 'developer', 'engine': 'opencode'},
]:
    approvals.registry.register_member(data)

events: dict[tuple[str, int], asyncio.Event] = {}
executions: list[tuple[str, int]] = []
lose_receipt = False


async def unused_worker(task, progress):
    raise RuntimeError('This fixture only runs existing staff tasks')


async def execute(task, work, role, member, check):
    check()
    key = (task.id, task.collaboration['round'])
    executions.append(key)
    await events.setdefault(key, asyncio.Event()).wait()
    check()
    return ('这是一份离线示例交付，没有读取个人资料或调用模型。\n\n'
            '- **已整理**：按你的要求归纳了主要内容。\n'
            '- **请核对**：逐项检查内容与来源。\n'
            '- **下一步**：你可以验收，或补充要求。')


runner = TaskRunner(ROOT.name, unused_worker)
service = set_staff_service(ROOT.name, runner)
service.execute = execute


@app.middleware('http')
async def lost_receipt(request: Request, call_next):
    global lose_receipt
    response = await call_next(request)
    if (lose_receipt and request.method == 'POST'
            and request.url.path.startswith('/api/staff/members/')
            and request.url.path.endswith('/conversation') and response.status_code == 200):
        lose_receipt = False
        return JSONResponse({'detail': '示例：任务已受理，但发送回执丢失；请刷新核对。'}, status_code=503)
    return response


@app.get('/qa/state')
async def state():
    return {'tasks': [t.to_dict() for t in runner.ledger.list()], 'executions': executions,
            'source': 'offline temporary fixture; no inference'}


@app.post('/qa/finish')
async def finish():
    for task in runner.ledger.list():
        events.setdefault((task.id, task.collaboration['round']), asyncio.Event()).set()
    return {'released': True}


@app.post('/qa/lose-receipt')
async def lose_next_receipt():
    global lose_receipt
    lose_receipt = True
    return {'armed': True}


if __name__ == '__main__':
    import uvicorn
    wrapped = CORSMiddleware(app, allow_origins=['http://127.0.0.1:4350', 'http://127.0.0.1:4353'],
                             allow_methods=['*'], allow_headers=['*'])
    uvicorn.run(wrapped, host='127.0.0.1', port=4352, lifespan='off', log_level='warning')
