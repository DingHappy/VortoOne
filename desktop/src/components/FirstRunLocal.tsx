// 首次引导 ·「在这台 Mac 上运行」：先核对本机状态和模型，再由主人点一次开始；之后逐步显示安装、启动、连接。
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Check, CircleAlert, LoaderCircle, RefreshCw, X } from "lucide-react";
import type { DesktopProjectProfile } from "../types";
import { checked, localResidentClient, type LocalResidentClient, type LocalResidentStatus } from "../lib/localResident";
import { LOCAL_SETUP_STEPS, localSetupRun, type LocalSetupProgress, type LocalSetupStep } from "../lib/localSetup";
import { RoleAvatar } from "./RoleAvatar";
import { Button } from "./ui";

const STEP_NAME: Record<LocalSetupStep, string> = { install: "安装常驻助理", start: "启动", connect: "连接到这台 Mac 的助理" };
const STEP_NOTE: Partial<Record<LocalSetupStep, string>> = { install: "程序和模型配置保存在这台 Mac 上" };
const STATE_WORD = { todo: "", active: "进行中", done: "已完成", failed: "没有完成" } as const;

export function elapsedLabel(ms: number) {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

/** 进度页：步骤、进度条和一组按钮。失败停在那一步并写明原因；取消只在两步之间生效。 */
export function FirstRunProgress({ progress, now, onCancel, onBack, onRetry }: {
  progress: LocalSetupProgress; now: number; onCancel: () => void; onBack: () => void; onRetry: () => void;
}) {
  const title = useRef<HTMLHeadingElement>(null);
  useEffect(() => { title.current?.focus(); }, []);
  const done = LOCAL_SETUP_STEPS.filter(step => progress.steps[step] === "done").length;
  const waited = now - progress.activeAt;
  return <div className="fr-setup">
    <div className="fr-setup-art" aria-hidden="true"><RoleAvatar character="general" size="hero" /></div>
    <h1 ref={title} tabIndex={-1} className="fr-h1-sm" id="runtime-onboarding-title">{progress.end === "done" ? "准备好了"
      : progress.end === "failed" ? "没有准备完" : progress.end === "stopped" ? "已停在这一步" : "正在准备这台 Mac"}</h1>
    <ol className="fr-steps" aria-live="polite">{LOCAL_SETUP_STEPS.map(step => {
      const state = progress.steps[step];
      return <li key={step} data-state={state}>
        <span className="fr-step-mark" aria-hidden="true">{state === "active" ? <LoaderCircle className="activity-spinner" />
          : state === "done" ? <Check /> : state === "failed" ? <X /> : <i />}</span>
        <span className="fr-step-text"><span className="fr-step-name"><b>{STEP_NAME[step]}</b>
          {STEP_NOTE[step] && <small>{STEP_NOTE[step]}</small>}
          {state !== "todo" && <span className="sr-only">{STATE_WORD[state]}</span>}
          {state === "active" && progress.end !== "stopped" && waited >= 10000 && <time aria-hidden="true">{elapsedLabel(waited)}</time>}</span>
          {state === "active" && waited >= 30000 && <span className="fr-step-line">比预期久一些，仍在进行。</span>}
          {state === "failed" && <span className="fr-step-line fr-step-failed" role="alert">{progress.error}</span>}
        </span>
      </li>;
    })}</ol>
    <div className="fr-bar" role="progressbar" aria-label="准备进度" aria-valuemin={0} aria-valuemax={LOCAL_SETUP_STEPS.length} aria-valuenow={done}>
      <span style={{ width: `${done / LOCAL_SETUP_STEPS.length * 100}%` }} /></div>
    {progress.end === "stopped" && <p className="fr-help">已经完成的步骤会保留，可以回去重新开始。</p>}
    {progress.end === "stopping" && <p className="fr-help">当前这一步做完后停下。</p>}
    <div className="fr-actions">
      {progress.end === "running" || progress.end === "stopping"
        ? <Button disabled={progress.end === "stopping"} onClick={onCancel}>{progress.end === "stopping" ? "正在停下…" : "取消"}</Button>
        : progress.end !== "done" && <><Button onClick={onBack}>返回</Button>
          <Button variant="primary" icon={<RefreshCw aria-hidden="true" />} autoFocus onClick={onRetry}>重试</Button></>}
    </div>
  </div>;
}

export function FirstRunLocal({ client = localResidentClient, modelReady, modelBusy = false, modelSetup, back, onUse, onStatus, onBusyChange, onRemote }: {
  client?: LocalResidentClient; modelReady: boolean; modelBusy?: boolean; modelSetup: ReactNode; back: ReactNode;
  onUse: (profile: DesktopProjectProfile) => Promise<void>; onStatus: (status: LocalResidentStatus) => void;
  onBusyChange: (busy: boolean) => void; onRemote: () => void;
}) {
  const [status, setStatus] = useState<LocalResidentStatus | null>(null);
  const [readError, setReadError] = useState("");
  const [reading, setReading] = useState(true);
  const [copy, setCopy] = useState(false);
  const [editingModel, setEditingModel] = useState(false);
  const [progress, setProgress] = useState<LocalSetupProgress | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const run = useRef<ReturnType<typeof localSetupRun> | null>(null);
  const callbacks = useRef({ onUse, onStatus, onBusyChange }); callbacks.current = { onUse, onStatus, onBusyChange };
  const serial = useRef(0), alive = useRef(true);
  const inspect = async () => {
    const request = ++serial.current; setReading(true); setReadError("");
    try {
      const value = checked(await client.read());
      if (alive.current && request === serial.current) { setStatus(value); callbacks.current.onStatus(value); }
    } catch (error) {
      if (alive.current && request === serial.current) setReadError(error instanceof Error ? error.message : typeof error === "string" && error ? error : "无法读取本机常驻状态");
    } finally { if (alive.current && request === serial.current) setReading(false); }
  };
  useEffect(() => {
    alive.current = true; void inspect();
    return () => { alive.current = false; serial.current++; run.current?.dispose(); callbacks.current.onBusyChange(false); };
    // Read once on entry; retries are explicit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client]);
  useEffect(() => { if (modelReady) setEditingModel(false); }, [modelReady]);
  const working = progress?.end === "running" || progress?.end === "stopping";
  useEffect(() => { callbacks.current.onBusyChange(Boolean(working)); }, [working]);
  useEffect(() => {
    if (!working) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [working]);

  const begin = () => {
    run.current?.dispose();
    const next = localSetupRun({ client, copyModelProfile: copy, publish: setProgress,
      onUse: profile => callbacks.current.onUse(profile), onStatus: value => { setStatus(value); callbacks.current.onStatus(value); } });
    run.current = next; setNow(Date.now()); void next.start();
  };
  if (progress) return <FirstRunProgress progress={progress} now={now} onCancel={() => run.current?.cancel()} onRetry={begin}
    onBack={() => { run.current?.dispose(); run.current = null; setProgress(null); void inspect(); }} />;

  return <FirstRunLocalView status={status} reading={reading} readError={readError} copy={copy} modelReady={modelReady && !editingModel}
    modelBusy={modelBusy} modelSetup={modelSetup} back={back} onCopy={setCopy} onEditModel={() => setEditingModel(true)}
    onInspect={() => void inspect()} onBegin={begin} onRemote={onRemote} />;
}

/** 开始前的那一页，只负责显示：本机状态、模型是否就绪、是否同意复制配置。 */
export function FirstRunLocalView({ status, reading, readError, copy, modelReady, modelBusy = false, modelSetup, back, onCopy, onEditModel, onInspect, onBegin, onRemote }: {
  status: LocalResidentStatus | null; reading: boolean; readError: string; copy: boolean; modelReady: boolean; modelBusy?: boolean;
  modelSetup: ReactNode; back: ReactNode; onCopy: (value: boolean) => void; onEditModel: () => void; onInspect: () => void;
  onBegin: () => void; onRemote: () => void;
}) {
  const installed = Boolean(status?.installed);
  const modelOk = installed || modelReady;
  const blocked = reading || modelBusy || Boolean(readError) || !status?.supported || !status.revision
    || ["unavailable", "cleanup_required"].includes(status.state);
  return <>
    {back}
    <h1 className="fr-h1-sm" id="runtime-onboarding-title" tabIndex={-1}>在这台 Mac 上运行</h1>
    <p className="fr-lead">{installed ? `这台 Mac 已经装有常驻助理${status?.version ? `（${status.version}）` : ""}，启动后直接连接。`
      : "资料保存在本机。退出 VortoOne 后助理仍可运行；Mac 需开机并登录，睡眠时暂停。"}</p>
    {readError && <div role="alert" className="fr-problem"><CircleAlert aria-hidden="true" /><span>{readError}</span><Button onClick={onInspect}>重新核对</Button></div>}
    {status && !status.supported && <p className="fr-help">这台设备暂不支持本机常驻助理。<button type="button" className="fr-link" onClick={onRemote}>连接另一台电脑</button></p>}
    {status?.state === "cleanup_required" && <p className="fr-help" role="status">上次的登录项还没有清理干净，请先到设置里处理后再回来。</p>}
    {status?.supported && <div className="fr-form">
      {!installed && (modelOk
        ? <div className="fr-card fr-row"><Check aria-hidden="true" /><span className="fr-row-text"><b>模型配置已保存</b><small>助理用它思考；已保存不代表推理已验证。</small></span>
          <Button disabled={modelBusy} onClick={onEditModel}>修改</Button></div>
        : <section className="fr-card" aria-label="准备模型"><h2>先接上一个模型</h2><p className="fr-help">助理用它思考。可以填本机模型或兼容服务的地址。</p>{modelSetup}</section>)}
      {!installed && <label className="fr-card fr-row fr-consent"><input type="checkbox" checked={copy} disabled={!modelOk}
        onChange={event => onCopy(event.target.checked)} /><span className="fr-row-text"><b>把这份模型配置复制给常驻助理</b>
          <small>常驻助理独立保存；之后在这里退出模型账号不会撤销它。</small></span></label>}
      <p className="fr-rest"><RoleAvatar character="general" /><span>定时工作和即时通讯先不开启，进去以后可以在设置里打开。</span></p>
      <div className="fr-actions"><Button variant="primary" size="lg" disabled={blocked || !modelOk || !installed && !copy} onClick={onBegin}>
        {reading ? "正在核对这台 Mac…" : installed ? "启动并连接" : "开始"}</Button></div>
      {!reading && !blocked && !installed && modelOk && !copy && <p className="fr-help">勾选上面一项后就可以开始。</p>}
    </div>}
  </>;
}
