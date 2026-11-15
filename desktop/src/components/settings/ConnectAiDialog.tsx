// 独立实现：连接配置始终由现有原生命令校验、确认并保存。
import { invoke } from "@tauri-apps/api/core";
import { ArrowLeft, Check, Cloud, Eye, EyeOff, Monitor, Plus, X } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useDialogSurface } from "../../hooks/useDialogSurface";
import { connectionModelIds, connectionProbeDraft, connectionSaveRequest, verifyConnectionReceipt } from "../../lib/connectAi";
import { confirmAction } from "../../lib/confirm";
import { errorText } from "../../lib/errorText";
import { createConnectionCheck, type ConnectionCheckState } from "../../lib/llmConnectionCheck";
import { isModelProfileConflict } from "../../lib/modelProfileConflict";
import { providerDraftState, type ProviderDraft } from "../../lib/providerDraft";
import { isLocalModelBase, PROVIDER_PRESETS } from "../../lib/providers";
import type { DesktopLlmProfileStatus, DesktopLlmProviderStatus, LlmConnectionTest } from "../../types";
import { Button, IconButton } from "../ui";
import "./ConnectAiDialog.css";

type Props = {
  profile: DesktopLlmProfileStatus | null;
  provider?: DesktopLlmProviderStatus;
  onSaved: (profile: DesktopLlmProfileStatus, message: string) => Promise<void>;
  onClose: () => void;
  onConflict: () => void;
  onStateChange: (state: { dirty: boolean; busy: boolean }) => void;
};

export function ConnectAiDialog({ profile, provider, onSaved, onClose, onConflict, onStateChange }: Props) {
  const dialog = useDialogSurface();
  const [revision] = useState(profile?.revision);
  const [firstDefault] = useState(!provider && !profile?.configured);
  const [id] = useState(() => provider?.id ?? `custom_${crypto.randomUUID().replace(/-/g, "").slice(0, 16)}`);
  const [step, setStep] = useState(provider ? 1 : 0);
  const [kind, setKind] = useState<"cloud" | "local">("cloud");
  const [preset, setPreset] = useState("custom");
  const [draft, setDraft] = useState<ProviderDraft>({ id, name: provider?.name ?? "", baseUrl: provider?.baseUrl ?? "", apiKey: "", editing: Boolean(provider) });
  const [models, setModels] = useState<string[]>(provider?.models ?? []);
  const [selected, setSelected] = useState<string[]>(provider?.models ?? []);
  const [typed, setTyped] = useState("");
  const [modelQuery, setModelQuery] = useState("");
  const [blocked, setBlocked] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const saving = useRef(false), alive = useRef(true);
  const profileRef = useRef(profile); profileRef.current = profile;
  const draftRef = useRef(draft); draftRef.current = draft;
  const stale = blocked || profile?.revision !== revision;
  const state = providerDraftState(draft, profile?.providers ?? []);
  const connectionIssue = state.missing || (!firstDefault && !draft.name.trim() ? "请填写连接名称" : "");
  const dirty = provider ? state.dirty || selected.join("\n") !== provider.models.join("\n") || Boolean(typed)
    : Boolean(draft.name || draft.baseUrl || draft.apiKey || models.length || typed);
  const [check, setCheck] = useState<ConnectionCheckState | null>(null);
  const checker = useRef<ReturnType<typeof createConnectionCheck> | null>(null);
  if (!checker.current) checker.current = createConnectionCheck(
    () => connectionProbeDraft(draftRef.current),
    value => invoke<LlmConnectionTest>("test_llm_connection", { ...value, providerId: value.providerId ?? null }),
    value => { if (alive.current) setCheck(value); },
    values => { if (alive.current) setModels(previous => [...new Set([...previous, ...values])]); },
  );
  const previousConnection = useRef({ baseUrl: draft.baseUrl, apiKey: draft.apiKey });
  useLayoutEffect(() => {
    const previous = previousConnection.current;
    if (previous.baseUrl === draft.baseUrl && previous.apiKey === draft.apiKey) return;
    previousConnection.current = { baseUrl: draft.baseUrl, apiKey: draft.apiKey };
    checker.current!.reset(); setCheck(null); setModels([]); setSelected([]); setModelQuery("");
  }, [draft.baseUrl, draft.apiKey]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; checker.current?.reset(); }; }, []);
  useEffect(() => { onStateChange({ dirty, busy }); }, [dirty, busy, onStateChange]);
  useEffect(() => () => onStateChange({ dirty: false, busy: false }), [onStateChange]);

  const close = async () => {
    if (saving.current) return;
    if (dirty && !await confirmAction("放弃尚未保存的模型连接？取消会保留全部输入。")) return;
    if (alive.current) onClose();
  };
  const choose = async (value: string, nextKind = kind) => {
    if (value === preset && nextKind === kind) return;
    if ((draft.apiKey || models.length || typed) && !await confirmAction("切换模型服务会清空密钥和当前模型清单，继续？取消会保留草稿。")) return;
    if (!alive.current) return;
    checker.current!.reset(); setCheck(null); setModels([]); setSelected([]); setModelQuery("");
    setKind(nextKind); setPreset(value); setTyped(""); setError("");
    const item = PROVIDER_PRESETS.find(item => item.id === value);
    setDraft(previous => ({ ...previous, name: item?.label ?? "", baseUrl: item?.baseUrl ?? "", apiKey: "" }));
  };
  const addModel = () => {
    try {
      const [model] = connectionModelIds([typed]);
      if (!models.includes(model) && models.length >= 200) throw new Error("最多保留 200 个模型，请先移除不用的模型");
      setModels(previous => previous.includes(model) ? previous : [...previous, model]);
      setSelected(previous => firstDefault ? [model] : [...new Set([...previous, model])]);
      setTyped(""); setError("");
    } catch (failure) { setError(errorText(failure, "模型编号无效")); }
  };
  const save = async () => {
    if (saving.current || stale) return;
    saving.current = true; setBusy(true); setError("");
    try {
      const request = connectionSaveRequest(draft, selected, firstDefault, revision, profile);
      const next = await invoke<DesktopLlmProfileStatus>(request.command, request.args);
      if (!alive.current) return;
      if (profileRef.current?.revision !== revision) throw new Error("本机配置已变化，请关闭后重新读取并核对保存结果");
      verifyConnectionReceipt(next, draft, selected, firstDefault);
      await onSaved(next, firstDefault ? `已保存默认模型 ${selected[0]}` : `已${provider ? "更新" : "添加"} ${draft.name}，加入 ${selected.length} 个模型`);
    } catch (failure) {
      if (alive.current) {
        if (isModelProfileConflict(failure)) { setBlocked(true); onConflict(); }
        setError(errorText(failure, "保存连接失败"));
      }
    } finally {
      saving.current = false;
      if (alive.current) setBusy(false);
    }
  };
  const candidates = PROVIDER_PRESETS.filter(item => kind === "local" ? !item.requiresKey : item.requiresKey);
  return <dialog ref={dialog} className="connect-ai-dialog" aria-labelledby="connect-ai-title"
    onCancel={event => { event.preventDefault(); void close(); }}>
    <header><div><span className="connect-ai-location">这台 Mac · 独立模型配置</span><h2 id="connect-ai-title">{provider ? "编辑模型连接" : "连接 AI"}</h2></div>
      <IconButton label="关闭连接 AI" icon={<X />} disabled={busy} onClick={() => void close()} /></header>
    <ol className="connect-ai-steps" aria-label="连接步骤">{["连接方式", "连接与模型", "核对保存"].map((title, index) =>
      <li key={title} aria-current={step === index ? "step" : undefined}>{step > index ? <Check size={14} aria-hidden="true" /> : index + 1}<span>{title}</span></li>)}</ol>
    {stale && <p className="connect-ai-error" role="alert">本机配置已变化，请关闭向导后核对最新配置；本次草稿没有自动覆盖新配置。</p>}
    <fieldset disabled={busy || stale}>
      {step === 0 && <div className="connect-ai-body">
        <p>选择模型在哪里运行。这里的连接保存在 Desktop，不会自动复制到常驻助手。</p>
        <div className="connect-ai-methods" role="group" aria-label="连接方式">
          <Button aria-pressed={kind === "cloud"} icon={<Cloud size={20} />} onClick={() => void choose("custom", "cloud")}>使用 API 密钥<span>云端服务或兼容网关</span></Button>
          <Button aria-pressed={kind === "local"} icon={<Monitor size={20} />} onClick={() => void choose("local", "local")}>本机模型<span>连接此 Mac 上的模型服务</span></Button>
        </div>
        <label>模型服务<select autoFocus value={preset} onChange={event => void choose(event.target.value)}>
          <option value="custom">自定义 OpenAI 兼容服务</option>{candidates.map(item => <option value={item.id} key={item.id}>{item.label}</option>)}
        </select></label>
        <p className="connect-ai-help">支持 OpenAI 兼容接口。订阅账号请在对应执行器中登录。</p>
      </div>}
      {step === 1 && <div className="connect-ai-body">
        {!firstDefault && <label>连接名称<input autoFocus value={draft.name} maxLength={40} placeholder="例如：研究模型" onChange={event => setDraft({ ...draft, name: event.target.value })} /></label>}
        <label>接口地址<input autoFocus={firstDefault} value={draft.baseUrl} maxLength={512} inputMode="url" autoComplete="off" spellCheck={false} placeholder="https://api.example.com/v1" onChange={event => setDraft({ ...draft, baseUrl: event.target.value })} /></label>
        <label>API 密钥<span className="connect-ai-key"><input type={showKey ? "text" : "password"} value={draft.apiKey} maxLength={2048} autoComplete="new-password" spellCheck={false}
          placeholder={state.canKeepKey ? "留空保留此连接已保存的密钥" : isLocalModelBase(draft.baseUrl) ? "此本机服务无需鉴权时可留空" : "填写对应服务的密钥"} onChange={event => setDraft({ ...draft, apiKey: event.target.value })} />
          <IconButton label={showKey ? "隐藏密钥" : "显示密钥"} icon={showKey ? <EyeOff /> : <Eye />} onClick={() => setShowKey(!showKey)} /></span></label>
        <div className="connect-ai-model-heading"><h3>{firstDefault ? "选择默认模型" : "加入模型菜单"}</h3><Button disabled={Boolean(state.missing) || check?.testing} onClick={() => void checker.current!.test()}>{check?.testing ? "获取中…" : "获取模型列表"}</Button></div>
        <p className="connect-ai-help">只查询模型列表，不保存配置。推理与工具调用尚未验证；也可以手动填写模型编号。</p>
        {(check?.error || check?.result) && <p role="status" className={check.error || !check.result?.ok ? "connect-ai-error" : ""}>{check.error || check.result?.message}</p>}
        <div className="connect-ai-manual"><input aria-label="手动模型编号" value={typed} maxLength={160} spellCheck={false} placeholder="例如：my-chat-model" onChange={event => setTyped(event.target.value)} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); addModel(); } }} />
          <IconButton label="添加模型编号" icon={<Plus />} disabled={!typed.trim()} onClick={addModel} /></div>
        {models.length > 8 && <input aria-label="搜索模型" type="search" value={modelQuery} placeholder="搜索模型" onChange={event => setModelQuery(event.target.value)} />}
        {models.length > 0 && <div className="connect-ai-models" role="group" aria-label={firstDefault ? "默认模型候选" : "模型候选"}>{models.filter(model => model.toLowerCase().includes(modelQuery.trim().toLowerCase())).map(model => <div className="connect-ai-model" key={model}>
          <label><input aria-label={`选择模型 ${model}`} type={firstDefault ? "radio" : "checkbox"} name="connect-ai-model" checked={selected.includes(model)} onChange={event => setSelected(previous => firstDefault ? [model] : event.target.checked ? [...previous, model] : previous.filter(id => id !== model))} /><span>{model}</span></label>
          <IconButton label={`移除模型 ${model}`} icon={<X size={14} />} onClick={() => { setModels(values => values.filter(id => id !== model)); setSelected(values => values.filter(id => id !== model)); }} />
        </div>)}</div>}
        {modelQuery && !models.some(model => model.toLowerCase().includes(modelQuery.trim().toLowerCase())) && <p className="connect-ai-help">没有匹配的模型，已选模型仍保留。</p>}
        {(connectionIssue || typed.trim()) && <p className="connect-ai-help">{connectionIssue || "请先添加输入的模型编号，或清空输入。"}</p>}
      </div>}
      {step === 2 && <div className="connect-ai-body"><h3>核对这次连接</h3><dl><dt>保存位置</dt><dd>这台 Mac 的 Desktop</dd>{!firstDefault && <><dt>名称</dt><dd>{draft.name}</dd></>}<dt>接口</dt><dd>{draft.baseUrl}</dd><dt>{firstDefault ? "默认模型" : "加入菜单"}</dt><dd>{selected.join("、")}</dd><dt>密钥</dt><dd>{draft.apiKey ? "已填写，不在回执中显示" : state.canKeepKey ? "保留此连接已保存的密钥" : "未填写，本机服务无需鉴权"}</dd></dl>
        <p>接下来会弹出系统确认。{firstDefault ? "保存后用于本机引擎的新对话。" : "保留当前默认模型，可在输入框切换。"}常驻助手的配置独立管理。</p><p className="connect-ai-help">模型编号来自接口列表或手动填写，不代表已验证推理与工具调用。</p></div>}
    </fieldset>
    {error && <p className="connect-ai-error" role="alert">{error}</p>}
    <footer><Button disabled={busy} onClick={() => step > 0 ? (setStep(step - 1), setError("")) : void close()} icon={step > 0 ? <ArrowLeft size={14} /> : undefined}>{step > 0 ? "上一步" : "取消"}</Button>
      {step < 2 ? <Button variant="primary" disabled={busy || stale || (step === 1 && (Boolean(connectionIssue) || !selected.length || Boolean(typed.trim())))} onClick={() => { setStep(step + 1); setError(""); }}>{step === 0 ? "设置连接" : "核对连接"}</Button>
        : <Button variant="primary" disabled={busy || stale} onClick={() => void save()}>{busy ? "等待确认并保存…" : "确认并保存"}</Button>}</footer>
  </dialog>;
}
