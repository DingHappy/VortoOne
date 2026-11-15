import type { DesktopLlmProfileStatus } from "../types";
import { providerDraftState, type ProviderDraft } from "./providerDraft";
import { sameLlmBase } from "./providers";

export const MAX_CONNECTION_MODELS = 200;

/** Always identify the edited/new connection; a new connection must never fall back to the default key. */
export function connectionProbeDraft(draft: ProviderDraft) {
  return { baseUrl: draft.baseUrl, apiKey: draft.apiKey, model: "", providerId: draft.id };
}

/** Model IDs are configuration, never evidence of inference or tool support. */
export function connectionModelIds(values: string[]): string[] {
  if (!values.length || values.length > MAX_CONNECTION_MODELS) throw new Error(`请选择 1 到 ${MAX_CONNECTION_MODELS} 个模型`);
  const result: string[] = [];
  for (const value of values) {
    const id = value.trim();
    if (!id || new TextEncoder().encode(id).length > 160 || /[\s,\u0000-\u001f\u007f-\u009f]/u.test(id)) {
      throw new Error("模型编号最长 160 字节，不能包含空白、逗号或控制字符");
    }
    if (!result.includes(id)) result.push(id);
  }
  return result;
}

export function connectionSaveRequest(draft: ProviderDraft, values: string[], firstDefault: boolean, revision: string | undefined, profile: DesktopLlmProfileStatus | null) {
  const state = providerDraftState(draft, profile?.providers ?? []);
  if (state.missing) throw new Error(state.missing);
  if (!firstDefault && !draft.name.trim()) throw new Error("请填写连接名称");
  const models = connectionModelIds(values);
  if (firstDefault && models.length !== 1) throw new Error("首次连接请选择一个默认模型");
  return firstDefault
    ? { command: "set_llm_profile", args: { expectedRevision: revision ?? null, baseUrl: draft.baseUrl.trim(), apiKey: draft.apiKey.trim(), model: models[0], fastModel: null, strongModel: null, roleModels: {} } }
    : { command: "save_llm_provider", args: { expectedRevision: revision ?? null, id: draft.id, name: draft.name.trim(), baseUrl: draft.baseUrl.trim(), apiKey: draft.apiKey.trim(), selectedModels: models } };
}

/** Do not declare completion when the native host returned a different service or incomplete model list. */
export function verifyConnectionReceipt(next: DesktopLlmProfileStatus, draft: ProviderDraft, values: string[], firstDefault: boolean) {
  const models = connectionModelIds(values);
  const provider = next.providers?.find(item => item.id === draft.id);
  const matches = firstDefault
    ? next.configured && sameLlmBase(next.baseUrl, draft.baseUrl) && next.model === models[0]
    : provider && sameLlmBase(provider.baseUrl, draft.baseUrl) && models.every(id => provider.models.includes(id));
  if (!matches) throw new Error("保存回执与本次连接不一致，请重新读取本机配置后核对");
}
