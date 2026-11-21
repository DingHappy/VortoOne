import { useEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { ArrowRight, ChevronLeft, Laptop, LoaderCircle, Server } from "lucide-react";
import type { DesktopProjectProfile } from "../types";
import { isAssistantHost } from "../lib/assistantHost";
import { completeRuntimeSetup, writeRuntimeSetup, type RuntimeSetupRecord } from "../lib/runtimeOnboarding";
import { type LocalResidentClient, type LocalResidentStatus } from "../lib/localResident";
import { useDialogSurface } from "../hooks/useDialogSurface";
import { FirstRunLocal } from "./FirstRunLocal";
import { RemoteProjectDialog } from "./RemoteProjectDialog";
import { RoleAvatar } from "./RoleAvatar";
import { Button } from "./ui";
import "./RuntimeOnboarding.css";

export interface RuntimeOnboardingProps {
  initial: RuntimeSetupRecord;
  initialError?: string;
  inspectionFailed?: boolean;
  restoreTarget?: DesktopProjectProfile;
  profiles: DesktopProjectProfile[];
  modelReady: boolean;
  modelBusy?: boolean;
  modelSetup: ReactNode;
  localClient?: LocalResidentClient;
  onConnect: (profile: DesktopProjectProfile) => Promise<void>;
  onComplete: (record: RuntimeSetupRecord) => void;
  onStatus: (status: LocalResidentStatus) => void;
  onCancel?: () => void;
  onInspectAgain: () => void;
}

type Place = "local" | "remote";
const PLACES: { value: Place; title: string; text: string; go: string }[] = [
  { value: "local", title: "在这台 Mac 上", text: "资料保存在本机。退出 VortoOne 后助理仍可运行；Mac 需开机并登录，睡眠时暂停。", go: "准备这台 Mac" },
  { value: "remote", title: "在一台一直开着的电脑上", text: "助理在另一台电脑上运行。关掉这里的窗口，它仍可以继续工作。", go: "连接那台电脑" },
];

/** 运行位置：左边的画面跟着右边的选择变；选好后由一个按钮继续，按下之前不保存也不启动任何东西。 */
export function FirstRunChoose({ disabled, onGo }: { disabled: boolean; onGo: (place: Place) => void }) {
  const [pick, setPick] = useState<Place>("local");
  const rows = useRef<(HTMLButtonElement | null)[]>([]);
  const move = (event: KeyboardEvent, index: number) => {
    const step = ["ArrowDown", "ArrowRight"].includes(event.key) ? 1 : ["ArrowUp", "ArrowLeft"].includes(event.key) ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    const next = (index + step + PLACES.length) % PLACES.length;
    setPick(PLACES[next].value); rows.current[next]?.focus();
  };
  const chosen = PLACES.find(place => place.value === pick)!;
  return <div className="fr-choose">
    <div className="fr-choose-art" data-place={pick} aria-hidden="true">
      <div className="fr-choose-scene"><RoleAvatar character="general" size="hero" />
        <span className="fr-choose-machine">{pick === "local" ? <Laptop /> : <Server />}</span></div>
      <span className="fr-choose-brand"><RoleAvatar character="general" size="small" />VortoOne</span>
    </div>
    <div className="fr-choose-main">
      <h1 id="runtime-onboarding-title" tabIndex={-1}>助理在哪里运行？</h1>
      <p className="fr-lead">选一台电脑保存你的资料、运行你的个人助理。每个位置保留自己的对话和资料。</p>
      <div className="fr-options" role="radiogroup" aria-label="助理在哪里运行">{PLACES.map((place, index) =>
        <button key={place.value} ref={node => { rows.current[index] = node; }} type="button" role="radio" className="fr-option"
          aria-checked={pick === place.value} tabIndex={pick === place.value ? 0 : -1} disabled={disabled}
          onClick={() => setPick(place.value)} onKeyDown={event => move(event, index)}>
          <b>{place.title}</b><span>{place.text}</span>
        </button>)}</div>
      <Button variant="primary" size="lg" className="fr-go" disabled={disabled} onClick={() => onGo(pick)}>
        {chosen.go}<ArrowRight aria-hidden="true" /></Button>
    </div>
  </div>;
}

export function RuntimeOnboarding({ initial, initialError = "", inspectionFailed = false, restoreTarget, profiles, modelReady, modelBusy = false, modelSetup, localClient, onConnect, onComplete, onStatus, onCancel, onInspectAgain }: RuntimeOnboardingProps) {
  const dialog = useDialogSurface();
  const shell = useRef<HTMLDivElement>(null);
  const [choice, setChoice] = useState<Place | null>(initial.status === "pending" ? initial.choice : null);
  const [restoring, setRestoring] = useState(Boolean(restoreTarget));
  const [error, setError] = useState(initialError);
  const [busy, setBusy] = useState(false);
  const [localBusy, setLocalBusy] = useState(false);
  const [remoteBusy, setRemoteBusy] = useState(false);
  const alive = useRef(true), flight = useRef(false), autoRestored = useRef(false);
  const callbacks = useRef({ onConnect, onComplete, onStatus }); callbacks.current = { onConnect, onComplete, onStatus };
  const blocked = busy || localBusy || remoteBusy || modelBusy;
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { shell.current?.querySelector<HTMLElement>("#runtime-onboarding-title")?.focus(); }, [choice, restoring]);

  async function finish(profile: DesktopProjectProfile) {
    if (flight.current) throw new Error("正在连接，请稍候");
    flight.current = true; setBusy(true); setError("");
    try {
      const record = await completeRuntimeSetup(profile, p => callbacks.current.onConnect(p), localStorage, () => alive.current);
      if (alive.current) callbacks.current.onComplete(record);
    } finally {
      flight.current = false;
      if (alive.current) setBusy(false);
    }
  }
  const connectKnown = (profile: DesktopProjectProfile) => { void finish(profile).catch(cause => { if (alive.current) setError(cause instanceof Error ? cause.message : String(cause)); }); };
  useEffect(() => {
    if (restoreTarget && !autoRestored.current) { autoRestored.current = true; connectKnown(restoreTarget); }
    // Restore once; callbacks are refreshed without restarting an in-flight connection.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  function choose(next: Place | null) {
    if (blocked || inspectionFailed) return;
    try {
      writeRuntimeSetup(localStorage, { version: 1, status: "pending", choice: next });
      setChoice(next); setRestoring(false); setError("");
    } catch { setError("无法保存引导进度，请检查本机存储后重试。"); }
  }
  const targetName = restoreTarget?.name || "上次选择的助手";
  const known = profiles.filter(isAssistantHost);
  const back = <button type="button" className="fr-back" disabled={blocked} onClick={() => choose(null)}><ChevronLeft aria-hidden="true" />运行位置</button>;
  const screen = restoring && restoreTarget ? "restore" : choice ?? "choose";
  return <dialog ref={dialog} className="runtime-onboarding" data-screen={screen} aria-labelledby="runtime-onboarding-title"
    onCancel={event => { event.preventDefault(); if (blocked) return; if (screen === "local" || screen === "remote") choose(null); else onCancel?.(); }}>
    {onCancel && <button type="button" className="fr-close" disabled={blocked} onClick={onCancel}>返回当前助手</button>}
    <div className="fr-body" ref={shell} key={screen}>
      {error && <div role="alert" className="fr-problem"><span>{error}</span>{inspectionFailed && <Button onClick={onInspectAgain}>重新核对已有配置</Button>}</div>}
      {screen === "restore" ? <>
        <h1 className="fr-h1-sm" id="runtime-onboarding-title" tabIndex={-1}>打开{targetName}</h1>
        <p className="fr-lead">连接后继续原来的对话。原来的对话与资料仍属于这份助手。</p>
        <div className="fr-actions"><Button variant="primary" size="lg" disabled={blocked} onClick={() => connectKnown(restoreTarget!)}>
          {busy ? <><LoaderCircle className="activity-spinner" aria-hidden="true" />正在连接…</> : "重新连接这份助手"}</Button>
          <Button disabled={blocked} onClick={() => choose(null)}>重新选择运行位置</Button></div>
      </> : screen === "choose" ? <FirstRunChoose disabled={inspectionFailed || blocked} onGo={choose} />
        : screen === "local" ? <FirstRunLocal client={localClient} modelReady={modelReady} modelBusy={modelBusy} modelSetup={modelSetup} back={back}
          onUse={finish} onStatus={status => callbacks.current.onStatus(status)} onBusyChange={setLocalBusy} onRemote={() => choose("remote")} />
        : <>
          {back}
          <h1 className="fr-h1-sm" id="runtime-onboarding-title" tabIndex={-1}>连接另一台电脑</h1>
          <p className="fr-lead">使用那台电脑上已有的模型和资料。连接前由你核对。</p>
          {known.length > 0 && <section className="fr-found" aria-label="已登记的助手"><h2>已登记的助手</h2>
            {known.map(profile => <div key={profile.id} className="fr-found-row"><Server aria-hidden="true" />
              <span className="fr-row-text"><b>{profile.name}</b><small>{profile.assistantPending ? "待连接" : profile.baseUrl}</small></span>
              <Button disabled={blocked} onClick={() => connectKnown(profile)}>使用</Button></div>)}</section>}
          <RemoteProjectDialog purpose="assistant" embedded deferSelection onRegistered={finish} onBusyChange={setRemoteBusy} onClose={() => choose(null)} />
          <p className="fr-help">还没有一直开着的电脑？<button type="button" className="fr-link" disabled={blocked} onClick={() => choose("local")}>在这台 Mac 上运行</button></p>
        </>}
    </div>
  </dialog>;
}
