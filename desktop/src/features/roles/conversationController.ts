import { freshAt } from "../../lib/readFreshness";
import type { RoleConversationClient, RoleConversationSnapshot, RoleMessage, RoleTask, RoleVerdict } from "./types";

export interface RoleConversationState {
  value: RoleConversationSnapshot | null; reading: boolean; writing: boolean;
  error: string; notice: string; changed: boolean; checkedAt: number; browsingHistory?: boolean;
}
export interface RoleDraft { requestId: string; prompt: string; criteria: string; delivery: "review" | "notify"; pending: boolean }
export const roleRequestId = () => crypto.randomUUID().replace(/-/g, "");

/** Every write is one explicit owner action on the displayed, source-bound record. */
export function roleConversationController(options: {
  client: RoleConversationClient; member: string; current: () => boolean;
  publish: (state: RoleConversationState) => void; pending: () => string;
  accepted: (requestId: string, task: RoleTask) => void; now?: () => number;
  sending?: () => void;
}) {
  const { client, member, current, publish, pending, accepted } = options;
  const now = options.now ?? Date.now;
  let active = true, readSerial = 0, identity = "", browsingHistory = false;
  let state: RoleConversationState = { value: null, reading: false, writing: false, error: "", notice: "", changed: false, checkedAt: 0 };
  const valid = () => active && current();
  const emit = (patch: Partial<RoleConversationState>) => { if (valid()) { state = { ...state, ...patch }; publish(state); } };
  const binding = (value: RoleConversationSnapshot) => `${value.instance}:${value.identity_digest}`;
  function check() {
    if (!valid()) throw new Error("个人空间或连接已变化，请重新打开角色对话");
    if (!state.value || state.changed || !freshAt(state.checkedAt, now())) throw new Error("请先刷新并核对角色的当前岗位");
    if (state.error) throw new Error("前次操作结果待核对，请先刷新");
    return state.value;
  }
  function merge(task: RoleTask) {
    if (!state.value) return;
    emit({ value: { ...state.value, tasks: [task, ...state.value.tasks.filter(item => item.id !== task.id)] } });
  }
  async function refresh(acknowledge = false, before = "") {
    if (!valid() || state.writing || state.reading) return;
    const serial = ++readSerial, checkedAt = now(), requestId = pending();
    emit({ reading: true });
    try {
      const value = await client.readRoleConversation(member, before, requestId);
      if (!valid() || serial !== readSerial) return;
      if (value.version !== 1 || value.member.id !== member || !value.instance || !value.identity_digest)
        throw new Error("运行端返回的角色资料不匹配，请刷新核对");
      const nextIdentity = binding(value);
      const changed = identity !== "" && identity !== nextIdentity;
      if (acknowledge || !identity) identity = nextIdentity;
      if (before && state.value && !changed) {
        const newer = state.value.tasks;
        value.tasks = [...newer, ...value.tasks.filter(item => !newer.some(previous => previous.id === item.id))];
      }
      browsingHistory = !!before;
      emit({ value, checkedAt, changed: changed && !acknowledge, error: "", browsingHistory });
      if (requestId && value.submitted_task) {
        merge(value.submitted_task); accepted(requestId, value.submitted_task);
        emit({ notice: "已核对到此前发送的任务，没有再次执行。" });
      }
    } catch (error) {
      if (valid() && serial === readSerial) emit({ error: error instanceof Error ? error.message : "角色记录暂时读不到，请刷新" });
    } finally {
      if (valid() && serial === readSerial) emit({ reading: false });
    }
  }
  async function send(draft: RoleDraft) {
    if (state.writing) return false;
    try {
      const displayed = check();
      if (!displayed.can_dispatch) throw new Error(displayed.unavailable_reason || "当前角色暂时不能接活");
      if (!draft.prompt.trim() || draft.prompt.length > 4000) throw new Error("任务原文须为 1～4000 字符");
      const acceptance = draft.criteria.split("\n").map(line => line.trim()).filter(Boolean);
      if (acceptance.length > 20 || acceptance.some(item => item.length > 500)) throw new Error("交付要求最多 20 项，每项最多 500 字符");
      const input: RoleMessage = { request_id: draft.requestId, prompt: draft.prompt,
        acceptance, delivery: draft.delivery,
        member_revision: displayed.member.revision, role_revision: displayed.role.revision,
        identity_digest: displayed.identity_digest, instance: displayed.instance };
      ++readSerial; emit({ reading: false, writing: true, notice: "", error: "" });
      options.sending?.();
      const receipt = await client.sendRoleMessage(member, input);
      if (!valid()) return false;
      merge(receipt.task); accepted(draft.requestId, receipt.task);
      emit({ notice: receipt.replayed ? "已核对到原来的任务，没有再次执行。" : "消息已受理，处理结果会回到这里。" });
      return true;
    } catch (error) {
      emit({ error: error instanceof Error ? error.message : "发送结果待核对，原文已保留；请先刷新" });
      return false;
    } finally { emit({ writing: false }); }
  }
  async function act(shown: RoleTask, verdict: RoleVerdict, note: string) {
    if (state.writing) return false;
    try {
      const value = check();
      const latest = value.tasks.find(item => item.id === shown.id);
      if (!latest || latest.confirmation.digest !== shown.confirmation.digest
          || shown.confirmation.instance !== value.instance) throw new Error("这张任务卡已变化，请重新核对");
      ++readSerial; emit({ reading: false, writing: true, notice: "", error: "" });
      const receipt = await client.decideRoleTask(member, shown.id, { ...shown.confirmation, verdict, note });
      if (!valid()) return false;
      merge(receipt.task);
      emit({ notice: receipt.stop_requested ? "停止请求已受理，等待任务回执。" : verdict === "accept" ? "你的验收决定已保存。" : "补充要求已受理，正在等待新一轮处理。" });
      return true;
    } catch (error) { emit({ error: error instanceof Error ? error.message : "操作结果待核对，请刷新" }); return false; }
    finally { emit({ writing: false }); }
  }
  return { refresh, send, act, state: () => state,
    poll: () => browsingHistory ? Promise.resolve() : refresh(),
    clearInputError() { if (!pending() && !state.writing) emit({ error: "" }); },
    dispose() { active = false; ++readSerial; } };
}

/** Window-local unsent text only. A lost receipt keeps its original submission id. */
export function roleDrafts(makeId = roleRequestId) {
  const entries = new Map<string, RoleDraft>(), listeners = new Set<() => void>();
  let revision = 0;
  const notify = () => { revision++; listeners.forEach(listener => listener()); };
  return {
    subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    getSnapshot: () => revision,
    get(key: string) {
      if (!entries.has(key)) entries.set(key, { requestId: makeId(), prompt: "", criteria: "", delivery: "review", pending: false });
      return entries.get(key)!;
    },
    edit(key: string, patch: Partial<Pick<RoleDraft, "prompt" | "criteria" | "delivery">>) {
      const entry = this.get(key);
      if (entry.pending) return;
      entries.set(key, { ...entry, ...patch }); notify();
    },
    sending(key: string) { entries.set(key, { ...this.get(key), pending: true }); notify(); },
    accepted(key: string, requestId: string) {
      if (this.get(key).requestId !== requestId) return;
      entries.set(key, { requestId: makeId(), prompt: "", criteria: "", delivery: "review", pending: false }); notify();
    },
  };
}
