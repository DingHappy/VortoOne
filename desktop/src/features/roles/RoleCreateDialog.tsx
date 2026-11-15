import { useEffect, useRef, useState } from "react";
import { Check, X } from "lucide-react";
import type { GatewayClient } from "../../gateway";
import type { StaffRole, StaffSnapshot } from "../../lib/staff";
import type { SettingsTargetLease } from "../../lib/settingsTarget";
import { useDialogSurface } from "../../hooks/useDialogSurface";
import { RoleAvatar, staffCharacter } from "../../components/RoleAvatar";
import { RoleGrants } from "../../components/settings/StaffPane";
import { Button, IconButton } from "../../components/ui";
import { roleCreationController, roleEngines, type RoleCreationDraft, type RoleCreationState } from "./createController";
import { roleRequestId } from "./conversationController";
import "./RoleCreateDialog.css";
import { roleSummary } from "./roleSummary";
import { commandKeyTarget, tabKeyTarget } from "../../lib/iconNavigation";

/** 名片上的能力标签：只说主人关心的范围，完整工具与权限在下面展开。 */
export function roleCapabilities(role: StaffRole): string[] {
  const { grants } = role;
  return [grants.web ? "可以查公开资料" : "不联网",
    ...(grants.project ? [role.project_binding ? `仅在项目「${role.project}」的一次性副本中工作` : "项目副本（尚未绑定项目）"] : []),
    ...(grants.sees.length ? [`可读取 ${grants.sees.length} 类私人来源`] : ["不读私人来源"]),
    grants.tools.length ? `${grants.tools.length} 个登记工具` : "没有登记工具"];
}

const engineName = (engine: string) => engine === "vortocode" ? "内置助理" : engine;

/**
 * 添加角色：左边从一份已登记的分工开始，右边是这位角色自己的名片，在名片上直接起名、选执行方式。
 * 权限完全来自所选分工；保存的是一份提议，主人批准后才登记，批准前不派活。
 */
export function RoleCreateDialog({ registry, lease, onRegistered, onClose }: {
  registry: StaffSnapshot; lease: SettingsTargetLease<GatewayClient>; onRegistered: (id: string) => void; onClose: () => void;
}) {
  const dialog = useDialogSurface(), requestId = useRef(roleRequestId()), callback = useRef(onRegistered);
  callback.current = onRegistered;
  const starters = registry.roles.filter(item => item.enabled);
  const [draft, setDraft] = useState<RoleCreationDraft>(() => ({ name: "", role: starters[0]?.id ?? "",
    engine: starters[0] ? roleEngines(starters[0])[0] || "" : "", model: "" }));
  const [state, setState] = useState<RoleCreationState>({ busy: false, error: "", proposal: null });
  const role = registry.roles.find(item => item.id === draft.role), current = lease.current();
  const controller = useRef<ReturnType<typeof roleCreationController> | null>(null);
  const rows = useRef<(HTMLButtonElement | null)[]>([]);
  useEffect(() => {
    if (!lease.client) return;
    const session = roleCreationController({ client: lease.client, registry, requestId: requestId.current,
      current: lease.current, publish: setState, registered: id => callback.current(id) });
    controller.current = session;
    return () => { session.dispose(); if (controller.current === session) controller.current = null; };
  }, [lease, registry]);
  const available = registry.conversation_version === 1 && !!registry.owner_instance;
  const locked = !current || state.busy || !!state.proposal;
  const change = (patch: Partial<RoleCreationDraft>) => { if (!state.proposal && !state.busy) setDraft(previous => ({ ...previous, ...patch })); };
  const pick = (index: number) => { const item = starters[index]; if (item) change({ role: item.id, engine: roleEngines(item)[0] || "" }); };
  const engines = role ? roleEngines(role) : [];
  const canProceed = current && available && !!draft.name.trim() && !!role && role.enabled && engines.includes(draft.engine);
  const pending = state.proposal?.status === "pending";
  return <dialog ref={dialog} className="role-create-dialog" aria-labelledby="role-create-heading"
    onCancel={event => { event.preventDefault(); if (!state.busy) onClose(); }}>
    <form onSubmit={event => { event.preventDefault(); if (canProceed && !state.busy && !state.proposal) void controller.current?.propose(draft); }}>
      <div className="role-create-grid">
        <aside className="role-create-list">
          <header><h2 id="role-create-heading">添加角色</h2><p>从一份已登记的分工开始，让它帮你处理事情。</p></header>
          {starters.length ? <div role="radiogroup" aria-label="从哪份分工开始" className="role-create-starters">
            {starters.map((item, index) => <button key={item.id} ref={node => { rows.current[index] = node; }} type="button" role="radio"
              className="role-create-starter" aria-checked={item.id === draft.role} tabIndex={item.id === draft.role || !role && index === 0 ? 0 : -1}
              disabled={locked} onClick={() => pick(index)} onKeyDown={event => {
                if (event.nativeEvent.isComposing || event.altKey || event.ctrlKey || event.metaKey) return;
                const next = commandKeyTarget(event.key, index, starters.length) ?? tabKeyTarget(event.key, index, starters.length);
                if (next === null) return;
                event.preventDefault();
                pick(next); rows.current[next]?.focus();
              }}>
              <RoleAvatar character={staffCharacter(item)} /><span><strong>{item.title}</strong><small>{roleSummary(item)}</small></span>
            </button>)}</div> : <p className="role-create-none">还没有启用的分工。</p>}
          <p className="role-create-hint">新的分工在「设置 › 角色与权限」里登记，批准后会出现在这里。</p>
        </aside>
        <section className="role-create-card" aria-label="角色名片">
          <IconButton className="role-create-close" label="关闭添加角色" icon={<X aria-hidden="true" />} disabled={state.busy} onClick={onClose} />
          <div className="role-create-cover"><RoleAvatar character={staffCharacter(role)} size="portrait" />
            <div className="role-create-bubble">
              <label><span>你好，我是</span><input autoFocus required maxLength={80} placeholder="给它起个名字" aria-label="角色名字"
                value={draft.name} disabled={locked} onChange={event => change({ name: event.target.value })} /></label>
              <p>{roleSummary(role)}</p>
              {role && !draft.name.trim() && !locked && <button type="button" className="role-create-idea" onClick={() => change({ name: role.title })}>就叫「{role.title}」</button>}
            </div></div>
          <div className="role-create-body">
            {role && <section><h3>能做什么</h3><ul className="role-create-chips">{roleCapabilities(role).map(text => <li key={text}>{text}</li>)}</ul>
              <details><summary>查看这份分工的工具与权限</summary><RoleGrants role={role} /></details>
              <p className="role-create-hint">权限完全来自这份分工（修订 {role.revision}），任务消息不能附带权限。</p></section>}
            {role && <section><h3>怎么工作</h3><div className="role-create-fields">
              <label>执行方式{engines.length > 1 ? <select value={draft.engine} disabled={locked} onChange={event => change({ engine: event.target.value })}>
                {engines.map(engine => <option key={engine} value={engine}>{engineName(engine)}</option>)}</select>
                : <output>{engineName(draft.engine) || "这份分工没有可用的执行方式"}</output>}</label>
              <label>模型<input maxLength={160} placeholder="留空沿用登记配置" value={draft.model} disabled={locked} onChange={event => change({ model: event.target.value })} /></label>
            </div></section>}
            <section><h3>在哪里工作</h3><p>{lease.target.runtimeLabel}</p>
              {role?.grants.project && !role.project_binding && <p className="role-create-hint">这份分工还没有绑定登记项目。可以先添加角色，绑定前不能接活。</p>}</section>
          </div>
        </section>
      </div>
      {!current && <p className="role-create-warning" role="alert">个人空间或连接已变化，请重新打开；已保存的提议仍属于原空间。</p>}
      {!available && <p className="role-create-warning" role="status">当前运行端尚未提供直接创建角色，请先更新服务。</p>}
      {state.error && <p className="role-create-warning" role="alert">{state.error}</p>}
      {state.proposal && <p className="role-create-receipt" role="status">{state.proposal.status === "approved" ? "这项提议已批准，角色已经登记。" : state.proposal.status === "rejected" ? "这项提议已拒绝。" : "提议已保存，还没有登记。核对名片上的分工、权限和模型，再决定是否批准；关闭后可在「角色与权限」继续。"}</p>}
      <footer><Button disabled={state.busy} onClick={onClose}>关闭</Button>
        {pending ? <div><Button disabled={!current || state.busy} onClick={() => void controller.current?.decide(false)}>拒绝提议</Button>
          <Button variant="primary" icon={<Check aria-hidden="true" />} disabled={!current || state.busy} onClick={() => void controller.current?.decide(true)}>{state.busy ? "正在保存…" : "批准并添加角色"}</Button></div>
          : state.proposal?.status === "approved" ? <Button variant="primary" disabled={!current || state.busy} onClick={() => void controller.current?.decide(true)}>打开已登记角色</Button>
          : !state.proposal && <Button type="submit" variant="primary" disabled={!canProceed || state.busy}>{state.busy ? "正在保存提议…" : "保存角色提议"}</Button>}
      </footer>
    </form>
  </dialog>;
}
