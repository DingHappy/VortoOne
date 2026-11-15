import { describe, expect, it } from 'vitest';
import type { DesktopProjectProfile } from '../types';
import type { LocalResidentClient, LocalResidentStatus } from './localResident';
import { localSetupRun, type LocalSetupProgress } from './localSetup';

const host: DesktopProjectProfile = { id: 'local-host', name: '这台 Mac', kind: 'remote', repoRoot: '@assistant', baseUrl: 'http://127.0.0.1:1', lastOpenedAt: 0 };
const base: LocalResidentStatus = { supported: true, installed: false, state: 'not_installed', availableVersion: '1', cronEnabled: false, revision: 'r1', message: '' };
function harness(initial: Partial<LocalResidentStatus> = {}, overrides: Partial<LocalResidentClient> = {}, copy = true) {
  let status: LocalResidentStatus = { ...base, ...initial };
  const calls: string[] = [], seen: LocalSetupProgress[] = [], used: string[] = [];
  const client: LocalResidentClient = {
    read: async () => { calls.push('read'); return { ...status }; },
    install: async options => { calls.push(`install:${options.cronEnabled}:${options.copyModelProfile}`); status = { ...status, installed: true, state: 'stopped', revision: 'r2' }; return { ...status }; },
    start: async () => { calls.push('start'); status = { ...status, state: 'running', launchId: 'l1', projectId: host.id, revision: 'r3' }; return { ...status }; },
    connect: async expected => { calls.push(`connect:${expected.expectedRevision}`); return host; },
    stop: async () => { throw new Error('unexpected'); }, update: async () => { throw new Error('unexpected'); }, applyModel: async () => { throw new Error('unexpected'); },
    ...overrides,
  };
  const run = localSetupRun({ client, copyModelProfile: copy, publish: value => seen.push(value), wait: async () => {}, now: () => 1,
    onUse: async profile => { used.push(profile.id); } });
  return { run, calls, seen, used, last: () => seen[seen.length - 1], set: (patch: Partial<LocalResidentStatus>) => { status = { ...status, ...patch }; } };
}

describe('local first-run setup', () => {
  it('one start installs, starts and connects in order, with scheduled jobs off', async () => {
    const h = harness();
    await h.run.start();
    expect(h.calls.filter(call => call !== 'read')).toEqual(['install:false:true', 'start', 'connect:r3']);
    expect(h.used).toEqual(['local-host']);
    expect(h.last()).toMatchObject({ end: 'done', steps: { install: 'done', start: 'done', connect: 'done' } });
  });
  it('never installs without the owner agreeing to copy the model configuration', async () => {
    const h = harness({}, {}, false);
    await h.run.start();
    expect(h.calls).toEqual(['read']); expect(h.used).toEqual([]);
    expect(h.last()).toMatchObject({ end: 'failed', steps: { install: 'failed', start: 'todo' } });
    expect(h.last().error).toContain('同意');
  });
  it('skips what is already done: an installed, running assistant is only connected', async () => {
    const h = harness({ installed: true, state: 'running', launchId: 'l1', projectId: host.id, revision: 'r9' }, {}, false);
    await h.run.start();
    expect(h.calls.filter(call => call !== 'read')).toEqual(['connect:r9']);
    expect(h.last().end).toBe('done');
  });
  it('a failed step stops the run there and nothing is retried', async () => {
    const h = harness({}, { start: async () => { throw new Error('合成启动失败'); } });
    await h.run.start(); await h.run.start();
    expect(h.last()).toMatchObject({ end: 'failed', error: '合成启动失败', steps: { install: 'done', start: 'failed', connect: 'todo' } });
    expect(h.used).toEqual([]);
  });
  it('a failed app connection is not reported as done', async () => {
    const h = harness();
    const run = localSetupRun({ client: { read: async () => ({ ...base, installed: true, state: 'running', launchId: 'l1', revision: 'r3' }),
      connect: async () => host } as unknown as LocalResidentClient, copyModelProfile: false, publish: value => h.seen.push(value),
      onUse: async () => { throw new Error('连接没有成功'); } });
    await run.start();
    expect(h.last()).toMatchObject({ end: 'failed', error: '连接没有成功', steps: { connect: 'failed' } });
  });
  it('does not connect an instance that changed, or one registered under another identity', async () => {
    const other = harness({ installed: true, state: 'running', launchId: 'l1', projectId: 'someone-else', revision: 'r3' });
    await other.run.start();
    expect(other.used).toEqual([]); expect(other.last().error).toContain('不一致');
    const moved = harness({ installed: true, state: 'running', launchId: 'l1', revision: 'r3' },
      { connect: async () => { moved.set({ launchId: 'l2' }); return host; } });
    await moved.run.start();
    expect(moved.used).toEqual([]); expect(moved.last().error).toContain('已变化');
  });
  it('cancel takes effect between steps and keeps what was done', async () => {
    let h!: ReturnType<typeof harness>;
    h = harness({}, { install: async () => { h.run.cancel(); h.set({ installed: true, state: 'stopped', revision: 'r2' }); return { ...base, installed: true, state: 'stopped', revision: 'r2' }; } });
    await h.run.start();
    expect(h.calls).not.toContain('start');
    expect(h.last()).toMatchObject({ end: 'stopped', steps: { install: 'done', start: 'todo' } });
  });
  it('waits for a starting service a bounded number of times, then says so', async () => {
    const h = harness({ installed: true, state: 'starting', revision: 'r2' });
    await h.run.start();
    expect(h.last().error).toContain('超过预期'); expect(h.used).toEqual([]);
  });
  it('a disposed run publishes nothing more and does not switch the app', async () => {
    let h!: ReturnType<typeof harness>;
    h = harness({}, { install: async () => { h.run.dispose(); return { ...base, installed: true, state: 'stopped', revision: 'r2' }; } });
    await h.run.start();
    expect(h.used).toEqual([]); expect(h.calls).not.toContain('start');
  });
});
