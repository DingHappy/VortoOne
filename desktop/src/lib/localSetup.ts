// 首次引导里的「在这台 Mac 上运行」：主人点一次开始，按顺序安装、启动、连接，并逐步报告进度。
// 每一步前都重新读取状态，只做当前状态需要的那一步；失败就停在那一步，不自动重试任何写操作。
import type { DesktopProjectProfile } from '../types';
import { checked, type LocalResidentClient, type LocalResidentStatus } from './localResident';

export const LOCAL_SETUP_STEPS = ['install', 'start', 'connect'] as const;
export type LocalSetupStep = (typeof LOCAL_SETUP_STEPS)[number];
export type LocalSetupStepState = 'todo' | 'active' | 'done' | 'failed';
export type LocalSetupEnd = 'running' | 'stopping' | 'stopped' | 'failed' | 'done';
export interface LocalSetupProgress {
  steps: Record<LocalSetupStep, LocalSetupStepState>;
  end: LocalSetupEnd;
  error: string;
  /** 当前这一步开始的时间，用来显示已用时长。 */
  activeAt: number;
}
export const LOCAL_SETUP_IDLE: LocalSetupProgress = {
  steps: { install: 'todo', start: 'todo', connect: 'todo' }, end: 'running', error: '', activeAt: 0,
};

const failure = (error: unknown) => error instanceof Error ? error.message
  : typeof error === 'string' && error.trim() ? error : '操作结果无法确认，请重新核对后再试';

export function localSetupRun({ client, copyModelProfile, onUse, publish, onStatus, now = Date.now,
  wait = ms => new Promise<void>(resolve => setTimeout(resolve, ms)), startChecks = 60 }: {
  client: LocalResidentClient;
  /** 主人在开始前是否同意复制已保存的模型配置；尚未安装时必须同意。 */
  copyModelProfile: boolean;
  onUse: (profile: DesktopProjectProfile) => Promise<void>;
  publish: (progress: LocalSetupProgress) => void;
  onStatus?: (status: LocalResidentStatus) => void;
  now?: () => number;
  wait?: (ms: number) => Promise<void>;
  startChecks?: number;
}) {
  let active = true, cancelled = false, started = false;
  let progress: LocalSetupProgress = { ...LOCAL_SETUP_IDLE, steps: { ...LOCAL_SETUP_IDLE.steps } };
  const emit = (patch: Partial<LocalSetupProgress>) => { if (active) { progress = { ...progress, ...patch }; publish(progress); } };
  const mark = (step: LocalSetupStep, state: LocalSetupStepState) =>
    emit({ steps: { ...progress.steps, [step]: state }, ...(state === 'active' ? { activeAt: now() } : {}) });
  const read = async () => { const value = checked(await client.read()); if (active) onStatus?.(value); return value; };
  const usable = (value: LocalResidentStatus) => {
    if (!value.supported) throw new Error('这台设备暂不支持本机常驻助理，可以连接另一台电脑');
    if (value.state === 'cleanup_required') throw new Error('上次的登录项还没有清理干净，请先到设置里处理');
    if (!value.revision || value.state === 'unavailable') throw new Error('本机常驻状态无法核对，请重新核对后再试');
    return value;
  };
  /** 取消只在两步之间生效：已经发出的那一步会做完，做完的保留。 */
  const stopped = () => { if (!cancelled) return false; emit({ end: 'stopped' }); return true; };

  async function start() {
    if (started || !active) return;
    started = true;
    let step: LocalSetupStep = 'install';
    try {
      mark('install', 'active');
      let value = usable(await read());
      if (!value.installed) {
        if (!copyModelProfile) throw new Error('安装前请先同意复制已保存的模型配置');
        value = checked(await client.install({ expectedRevision: value.revision!, cronEnabled: false, copyModelProfile: true }));
        if (active) onStatus?.(value);
      }
      if (!active) return;
      mark('install', 'done');
      if (stopped()) return;

      step = 'start'; mark('start', 'active');
      value = usable(value);
      if (value.state === 'stopped') {
        value = checked(await client.start({ expectedRevision: value.revision! }));
        if (active) onStatus?.(value);
      }
      for (let tries = 0; active && value.state === 'starting'; tries++) {
        if (tries >= startChecks) throw new Error('启动时间超过预期，请重新核对运行状态');
        await wait(1000); value = await read();
      }
      if (!active) return;
      if (value.state !== 'running' || !value.launchId || !value.revision) throw new Error('本机常驻助理没有进入运行状态，请重新核对');
      mark('start', 'done');
      if (stopped()) return;

      step = 'connect'; mark('connect', 'active');
      const profile = await client.connect({ expectedRevision: value.revision });
      if (!active) return;
      if (profile.kind !== 'remote' || profile.repoRoot !== '@assistant' || !profile.id
        || value.projectId && profile.id !== value.projectId) throw new Error('连接登记与本机常驻身份不一致，请重新核对');
      const latest = await read();
      if (!active) return;
      if (!latest.supported || latest.state !== 'running' || latest.revision !== value.revision
        || latest.launchId !== value.launchId || latest.projectId && latest.projectId !== profile.id) {
        throw new Error('本机常驻运行实例已变化，请重新核对后再连接');
      }
      await onUse(profile);
      if (!active) return;
      mark('connect', 'done'); emit({ end: 'done' });
    } catch (error) {
      if (active) { mark(step, 'failed'); emit({ end: 'failed', error: failure(error) }); }
    }
  }
  return {
    start,
    cancel: () => { if (active && progress.end === 'running') { cancelled = true; emit({ end: 'stopping' }); } },
    dispose: () => { active = false; },
  };
}
