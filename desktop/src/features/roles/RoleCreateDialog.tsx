import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Check, X } from "lucide-react";
import type { GatewayClient } from "../../gateway";
import type { StaffSnapshot } from "../../lib/staff";
import type { SettingsTargetLease } from "../../lib/settingsTarget";
import { useDialogSurface } from "../../hooks/useDialogSurface";
import { RoleAvatar, staffCharacter } from "../../components/RoleAvatar";
import { RoleGrants } from "../../components/settings/StaffPane";
import { Button, IconButton } from "../../components/ui";
import { roleCreationController, roleEngines, type RoleCreationDraft, type RoleCreationState } from "./createController";
import { roleRequestId } from "./conversationController";
import "./RoleCreateDialog.css";
import { roleSummary } from "./roleSummary";

export function RoleCreateDialog({ registry, lease, onRegistered, onClose }: {
  registry: StaffSnapshot; lease: SettingsTargetLease<GatewayClient>; onRegistered: (id: string) => void; onClose: () => void;
}) {
  const dialog = useDialogSurface(), requestId = useRef(roleRequestId()), callback = useRef(onRegistered);
  callback.current = onRegistered;
  const [draft, setDraft] = useState<RoleCreationDraft>({ name: "", role: "", engine: "", model: "" });
  const [reviewing, setReviewing] = useState(false);
  const [state, setState] = useState<RoleCreationState>({ busy: false, error: "", proposal: null });
  const role = registry.roles.find(item => item.id === draft.role), current = lease.current();
  const controller = useRef<ReturnType<typeof roleCreationController> | null>(null);
  useEffect(() => {
    if (!lease.client) return;
    const session = roleCreationController({ client: lease.client, registry, requestId: requestId.current,
      current: lease.current, publish: setState, registered: id => callback.current(id) });
    controller.current = session;
    return () => { session.dispose(); if (controller.current === session) controller.current = null; };
  }, [lease, registry]);
  const available = registry.conversation_version === 1 && !!registry.owner_instance;
  const change = (patch: Partial<RoleCreationDraft>) => { if (!state.proposal && !state.busy) setDraft(previous => ({ ...previous, ...patch })); };
  const canProceed = current && available && !!draft.name.trim() && !!role && role.enabled && roleEngines(role).includes(draft.engine);
  return <dialog ref={dialog} className="role-create-dialog" aria-labelledby="role-create-heading"
    onCancel={event => { event.preventDefault(); if (!state.busy) onClose(); }}>
    <header><div><h2 id="role-create-heading">{state.proposal ? "核对角色提议" : reviewing ? "核对这位角色" : "添加角色"}</h2>
      <p>{state.proposal ? "批准前不会登记或派活。" : "选择一份已登记的分工，让它帮你处理事情。"}</p></div>
      <IconButton label="关闭添加角色" icon={<X aria-hidden="true" />} disabled={state.busy} onClick={onClose} /></header>
    <form onSubmit={event => { event.preventDefault(); if (!canProceed || state.busy || state.proposal) return;
      if (!reviewing) setReviewing(true); else void controller.current?.propose(draft); }}>
      <div className="role-create-body"><div className="role-create-identity"><RoleAvatar character={staffCharacter(role)} size="portrait" />
        <h3>{draft.name.trim() || "你的新角色"}</h3><p>{roleSummary(role)}</p>
        {role && <details><summary>查看岗位的工具与权限</summary><RoleGrants role={role} /></details>}
      </div><div className="role-create-fields">
        {!reviewing && !state.proposal ? <>
          <label>名字<input autoFocus required maxLength={80} placeholder="例如：研究助手" value={draft.name} disabled={!current || state.busy} onChange={event => change({ name: event.target.value })} /></label>
          <fieldset disabled={!current || state.busy}><legend>它负责什么</legend><div className="role-create-roles">
            {registry.roles.filter(item => item.enabled).map(item => <label key={item.id} className="role-create-choice">
              <input type="radio" name="registered-role" value={item.id} checked={item.id === draft.role} onChange={() => change({ role: item.id, engine: roleEngines(item)[0] || "" })} />
              <span><strong>{item.title}</strong><small>{roleSummary(item)}</small></span></label>)}
          </div></fieldset>
          {role && <><label>执行方式<select value={draft.engine} disabled={!current || state.busy} onChange={event => change({ engine: event.target.value })}>
            {roleEngines(role).map(engine => <option key={engine} value={engine}>{engine === "vortocode" ? "内置助理" : engine}</option>)}</select></label>
            <label>模型（可选）<input maxLength={160} placeholder="留空沿用登记配置" value={draft.model} disabled={!current || state.busy} onChange={event => change({ model: event.target.value })} /></label></>}
        </> : <div className="role-create-review"><dl><dt>名字</dt><dd>{draft.name.trim()}</dd><dt>岗位</dt><dd>{role?.title} · 修订 {role?.revision}</dd>
          <dt>执行方式</dt><dd>{draft.engine === "vortocode" ? "内置助理" : draft.engine}</dd><dt>模型</dt><dd>{draft.model.trim() || "沿用登记配置"}</dd>
          <dt>所在空间</dt><dd>{lease.target.runtimeLabel}</dd></dl><p>权限完全来自所选岗位，任务消息不能附带权限。</p>
          {role?.grants.project && !role.project_binding && <p>这份岗位尚未绑定登记项目。可以登记角色，绑定前不能接活。</p>}
        </div>}
      </div></div>
      {!current && <p className="role-create-warning" role="alert">个人空间或连接已变化，请重新打开；已保存的提议仍属于原空间。</p>}
      {!available && <p className="role-create-warning" role="status">当前运行端尚未提供直接创建角色，请先更新服务。</p>}
      {state.error && <p className="role-create-warning" role="alert">{state.error}</p>}
      {state.proposal && <p className="role-create-receipt" role="status">{state.proposal.status === "approved" ? "这项提议已批准，角色已经登记。" : state.proposal.status === "denied" ? "这项提议已拒绝。" : "提议已保存。请核对分工、权限和模型，再决定是否批准。关闭窗口后可在角色与权限继续核对。"}</p>}
      <footer>{reviewing && !state.proposal ? <Button icon={<ArrowLeft aria-hidden="true" />} disabled={state.busy} onClick={() => setReviewing(false)}>返回修改</Button> : <Button disabled={state.busy} onClick={onClose}>关闭</Button>}
        {state.proposal?.status === "pending" ? <div><Button disabled={!current || state.busy} onClick={() => void controller.current?.decide(false)}>拒绝提议</Button>
          <Button variant="primary" icon={<Check aria-hidden="true" />} disabled={!current || state.busy} onClick={() => void controller.current?.decide(true)}>{state.busy ? "正在保存…" : "批准并添加角色"}</Button></div>
          : state.proposal?.status === "approved" ? <Button variant="primary" disabled={!current || state.busy} onClick={() => void controller.current?.decide(true)}>打开已登记角色</Button>
          : !state.proposal && <Button type="submit" variant="primary" disabled={!canProceed || state.busy}>{state.busy ? "正在保存提议…" : reviewing ? "保存提议，继续核对" : "下一步"}</Button>}
      </footer>
    </form>
  </dialog>;
}
