import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { ArrowUp, Info, RefreshCw, SlidersHorizontal } from "lucide-react";
import type { GatewayClient } from "../../gateway";
import type { SettingsTargetLease } from "../../lib/settingsTarget";
import type { StaffMember, StaffRole } from "../../lib/staff";
import { startPolling } from "../../lib/polling";
import { freshAt } from "../../lib/readFreshness";
import { RoleAvatar, staffCharacter } from "../../components/RoleAvatar";
import { Button, IconButton } from "../../components/ui";
import { roleConversationController, roleDrafts, type RoleConversationState } from "./conversationController";
import { RoleTaskMessage } from "./RoleTaskMessage";
import { RoleTaskDecision } from "./RoleTaskDecision";
import type { RoleTask, RoleVerdict } from "./types";
import "./RoleConversation.css";
import { roleSummary } from "./roleSummary";

export function RoleConversation({ memberId, name, lease, drafts, onProfile, onMain }: {
  memberId: string; name: string; lease: SettingsTargetLease<GatewayClient>;
  drafts: ReturnType<typeof roleDrafts>; onProfile: (member?: StaffMember, role?: StaffRole) => void; onMain: () => void;
}) {
  const key = JSON.stringify([lease.target.targetKey, memberId]);
  useSyncExternalStore(drafts.subscribe, drafts.getSnapshot, drafts.getSnapshot);
  const draft = drafts.get(key);
  const [state, setState] = useState<RoleConversationState>({ value: null, reading: false, writing: false, error: "", notice: "", changed: false, checkedAt: 0 });
  const [intent, setIntent] = useState<{ task: RoleTask; verdict: RoleVerdict } | null>(null);
  const [options, setOptions] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null), log = useRef<HTMLDivElement>(null), follows = useRef(true);
  const controller = useMemo(() => lease.client ? roleConversationController({ client: lease.client, member: memberId,
    current: lease.current, publish: setState, pending: () => drafts.get(key).pending ? drafts.get(key).requestId : "",
    accepted: requestId => drafts.accepted(key, requestId), sending: () => drafts.sending(key) }) : null, [lease, memberId, drafts, key]);
  useEffect(() => {
    if (!controller) return;
    const stop = startPolling(() => controller.poll(), { intervalMs: 5000, hiddenIntervalMs: 60000, immediate: true, visibility: document });
    return () => { stop(); controller.dispose(); };
  }, [controller]);
  const value = state.value, current = lease.current();
  const writable = current && !!value && !state.changed && !state.error && freshAt(state.checkedAt, Date.now());
  const canSend = writable && value!.can_dispatch && !state.writing && draft.prompt.trim().length > 0 && draft.prompt.length <= 4000;
  const character = staffCharacter(value?.role), displayName = value?.member.name || name;
  const tasks = useMemo(() => [...(value?.tasks || [])].sort((a, b) => a.created.localeCompare(b.created) || a.id.localeCompare(b.id)), [value?.tasks]);
  useEffect(() => {
    if (follows.current && log.current) log.current.scrollTop = log.current.scrollHeight;
  }, [value?.tasks]);
  const send = async () => { if (canSend) { const accepted = await controller?.send(draft); if (accepted && current) input.current?.focus(); } };
  return <section className="role-conversation" aria-label={`与${displayName}的工作对话`}>
    <header className="role-conversation-header" data-tauri-drag-region="deep">
      <button type="button" className="role-conversation-identity" disabled={!current || !value} onClick={() => { if (value && current) onProfile(value.member, value.role); }} aria-label={`角色资料：${displayName}`}>
        <RoleAvatar character={character} /><span><strong>{displayName}</strong><small>{value?.role.title || "读取角色资料…"}</small></span>
      </button>
      <div><IconButton label="刷新角色对话" icon={<RefreshCw aria-hidden="true" />} disabled={state.reading || state.writing} onClick={() => void controller?.refresh()} />
        <IconButton label="查看角色资料" icon={<Info aria-hidden="true" />} disabled={!current || !value} onClick={() => { if (value && current) onProfile(value.member, value.role); }} /></div>
    </header>
    {!current && <div className="role-conversation-warning" role="status">个人空间或连接已变化，旧记录仅供回查。<Button onClick={onMain}>返回主要聊天</Button></div>}
    {state.changed && <div className="role-conversation-warning" role="status">运行端或岗位配置已变化，请核对当前角色资料后继续。<Button onClick={() => void controller?.refresh(true)}>刷新并重新核对</Button></div>}
    {state.error && <div className="role-conversation-warning" role="alert">{state.error}<Button disabled={state.reading || state.writing} onClick={() => void controller?.refresh()}>刷新核对</Button></div>}
    <div className="role-conversation-log" ref={log} onScroll={() => { const n = log.current; if (n) follows.current = n.scrollHeight - n.scrollTop - n.clientHeight < 80; }}>
      <div className="role-conversation-reading">
        {value?.next_cursor && <Button variant="text" disabled={state.reading || state.writing} onClick={() => { follows.current = false; void controller?.refresh(false, value.next_cursor!); }}>查看更早的任务</Button>}
        {value && !value.history_complete && <p className="role-conversation-note">这里只显示已读取的部分历史，不能确认所有旧记录都已找到。</p>}
        {state.browsingHistory && <p className="role-conversation-note">正在回查历史，自动刷新已暂停。点击页首刷新可返回最新记录。</p>}
        {!tasks.length && <div className="role-conversation-empty"><RoleAvatar character={character} size="portrait" />
          <h1>{displayName}</h1><p>{value ? roleSummary(value.role) : state.reading ? "正在读取已登记的分工与任务…" : "角色记录暂时不可用，请刷新核对。"}</p>
          {value && <small>交代一件事，处理过程和结果都会回到这里。默认由你验收。</small>}
          {value && !value.can_dispatch && <p className="role-conversation-note">{value.unavailable_reason}</p>}
        </div>}
        {tasks.map(task => <RoleTaskMessage key={task.id} task={task} name={displayName} character={character}
          current={writable} busy={state.writing} getClient={() => current ? lease.client as GatewayClient : null}
          onRefresh={() => void controller?.refresh()} onAction={(task, verdict) => setIntent({ task: structuredClone(task), verdict })} />)}
      </div>
    </div>
    <div className="role-conversation-compose"><div className="role-conversation-compose-inner">
      {state.notice && <p className="role-conversation-notice" role="status">{state.notice}</p>}
      {draft.pending && !state.writing && <p className="role-conversation-note">此前发送的原文和请求标识已保留。请先刷新核对，未找到时可用同一标识重试。</p>}
      <form className="role-composer" onSubmit={event => { event.preventDefault(); void send(); }}>
        <textarea ref={input} rows={2} aria-label={`发给${displayName}的消息`} placeholder={`交给${displayName}一件事…`}
          maxLength={4000} value={draft.prompt} readOnly={state.writing || draft.pending || !current}
          onChange={event => { drafts.edit(key, { prompt: event.target.value }); controller?.clearInputError(); }}
          onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.altKey && !event.ctrlKey && !event.metaKey && !event.nativeEvent.isComposing && event.keyCode !== 229) { event.preventDefault(); void send(); } }} />
        {options && <div className="role-composer-options"><label>交付要求（每行一项）<textarea rows={2} value={draft.criteria} readOnly={state.writing || draft.pending}
          onChange={event => { drafts.edit(key, { criteria: event.target.value }); controller?.clearInputError(); }} /></label><label>交付方式<select value={draft.delivery} disabled={state.writing || draft.pending}
            onChange={event => drafts.edit(key, { delivery: event.target.value as "review" | "notify" })}><option value="review">由我验收</option><option value="notify">只告知结果</option></select></label></div>}
        <footer><IconButton label={options ? "收起交付要求" : "设置交付要求"} aria-expanded={options} icon={<SlidersHorizontal aria-hidden="true" />} onClick={() => setOptions(v => !v)} />
          <small>{draft.delivery === "review" ? "由你验收" : "只告知结果"} · {value?.member.model || "沿用登记模型"}</small>
          <button type="submit" className="role-composer-send" aria-label={draft.pending ? "重试发送原消息" : "发送给角色"} disabled={!canSend}><ArrowUp aria-hidden="true" /></button>
        </footer>
      </form>
      <p className="role-composer-policy">按已登记岗位处理；需要确认的动作仍会等你批准。</p>
    </div></div>
    {intent && <RoleTaskDecision key={`${intent.task.id}:${intent.task.confirmation.digest}:${intent.verdict}`} task={intent.task} verdict={intent.verdict}
      current={writable} busy={state.writing} error={state.error} onClose={() => setIntent(null)} onConfirm={async note => {
        if (await controller?.act(intent.task, intent.verdict, note)) setIntent(null);
      }} />}
  </section>;
}
