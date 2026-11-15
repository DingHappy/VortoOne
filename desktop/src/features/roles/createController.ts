import type { StaffClient, StaffProposal, StaffSnapshot, StaffRole } from "../../lib/staff";
import type { MemberProposalInput } from "./types";

export interface RoleCreationDraft { name: string; role: string; engine: string; model: string }
export interface RoleCreationState { busy: boolean; error: string; proposal: StaffProposal | null }
export const roleEngines = (role: StaffRole) => role.id === "developer" || role.project_binding
  ? role.engine : role.engine.filter(engine => engine === "vortocode");

export function memberProposalInput(draft: RoleCreationDraft, registry: StaffSnapshot, requestId: string): MemberProposalInput {
  const role = registry.roles.find(item => item.id === draft.role && item.enabled);
  const digest = role && registry.role_digests?.[role.id];
  if (!role || !digest || registry.conversation_version !== 1 || !registry.owner_instance)
    throw new Error("当前运行端尚不支持直接创建角色，请更新服务并重新核对");
  if (!draft.name.trim() || !roleEngines(role).includes(draft.engine)) throw new Error("请填写名字并选择这个岗位支持的执行方式");
  return { request_id: requestId, name: draft.name.trim(), role: role.id, role_revision: role.revision,
    role_digest: digest, engine: draft.engine, model: draft.model.trim(), instance: registry.owner_instance };
}

export function roleCreationController(options: {
  client: StaffClient; registry: StaffSnapshot; requestId: string; current: () => boolean;
  publish: (state: RoleCreationState) => void; registered: (id: string) => void;
}) {
  const { client, registry, requestId, current, publish, registered } = options;
  let active = true, state: RoleCreationState = { busy: false, error: "", proposal: null };
  const valid = () => active && current();
  const emit = (patch: Partial<RoleCreationState>) => { if (valid()) { state = { ...state, ...patch }; publish(state); } };
  const requireCurrent = () => { if (!valid()) throw new Error("个人空间或连接已变化，请重新打开添加角色"); };
  async function propose(draft: RoleCreationDraft) {
    if (state.busy || state.proposal) return;
    emit({ busy: true, error: "" });
    try {
      requireCurrent();
      const input = memberProposalInput(draft, registry, requestId);
      const fresh = await client.readStaff(); requireCurrent();
      if (fresh.owner_instance !== input.instance || fresh.role_digests?.[input.role] !== input.role_digest)
        throw new Error("岗位或运行端已变化，请关闭后重新核对");
      if (!client.proposeStaffMember) throw new Error("当前服务没有直接创建角色的接口");
      const proposal = await client.proposeStaffMember(input); requireCurrent();
      if (proposal.snapshot.kind !== "member" || proposal.snapshot.data.id !== `staff-${requestId}`
          || proposal.snapshot.data.role !== input.role || proposal.snapshot.data.name !== input.name)
        throw new Error("提议回执与当前表单不一致，请到角色与权限核对");
      emit({ proposal });
    } catch (error) { emit({ error: error instanceof Error ? error.message : "提议保存结果待核对，原文已保留" }); }
    finally { emit({ busy: false }); }
  }
  async function decide(approve: boolean) {
    const shown = state.proposal;
    if (state.busy || !shown) return;
    emit({ busy: true, error: "" });
    try {
      requireCurrent(); const fresh = await client.readStaff(); requireCurrent();
      const latest = fresh.proposals.find(item => item.id === shown.id);
      if (!latest || ["digest", "nonce", "round", "instance"].some(key => Reflect.get(latest, key) !== Reflect.get(shown, key)))
        throw new Error("这张提议已变化，请关闭后重新核对");
      if (latest.status !== "pending") {
        if (approve && latest.status === "approved" && fresh.members.some(member => member.id === shown.snapshot.data.id)) {
          registered(String(shown.snapshot.data.id)); return;
        }
        throw new Error("提议已有决定，请到角色与权限核对");
      }
      await client.decideStaff(shown.id, approve, { digest: shown.digest, nonce: shown.nonce, round: shown.round, instance: shown.instance });
      requireCurrent();
      const after = await client.readStaff(); requireCurrent();
      const saved = after.proposals.find(item => item.id === shown.id);
      if (saved?.status !== (approve ? "approved" : "rejected")) throw new Error("决定结果待核对，请到角色与权限刷新");
      emit({ proposal: saved });
      if (approve) {
        if (!after.members.some(member => member.id === shown.snapshot.data.id)) throw new Error("批准已保存，登记结果待核对");
        registered(String(shown.snapshot.data.id));
      }
    } catch (error) { emit({ error: error instanceof Error ? error.message : "决定结果待核对，请刷新原提议" }); }
    finally { emit({ busy: false }); }
  }
  return { propose, decide, state: () => state, dispose() { active = false; } };
}
