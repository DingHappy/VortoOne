import { describe, expect, it } from "vitest";
import { modelChoices } from "./modelChoice";
import { connectionModelIds, connectionProbeDraft, connectionSaveRequest, verifyConnectionReceipt } from "./connectAi";
import type { DesktopLlmProfileStatus } from "../types";
import type { ProviderDraft } from "./providerDraft";
const draft: ProviderDraft = { id: "custom_test", name: "我的模型", baseUrl: "https://models.example.test/v1", apiKey: "synthetic-key", editing: false };
const profile = { revision: "r1", configured: true, baseUrl: "https://old.example.test/v1", model: "old", providers: [] } as unknown as DesktopLlmProfileStatus;

describe("independent AI connection flow", () => {
  it("keeps model order while trimming and deduplicating manual IDs", () => {
    expect(connectionModelIds([" local/model ", "other:version", "local/model"])).toEqual(["local/model", "other:version"]);
  });
  it.each([[], ["bad,model"], ["bad\nmodel"], [" "], ["模型".repeat(54)], Array(201).fill("m")].map(values => ({ values })))("rejects invalid explicit model lists", ({ values }) => {
    expect(() => connectionModelIds(values)).toThrow();
  });
  it("adds a provider without changing the existing default or borrowing another key", () => {
    expect(connectionSaveRequest(draft, ["manual/model"], false, "r1", profile)).toEqual({ command: "save_llm_provider", args: {
      expectedRevision: "r1", id: "custom_test", name: "我的模型", baseUrl: draft.baseUrl, apiKey: "synthetic-key", selectedModels: ["manual/model"],
    } });
    expect(() => connectionSaveRequest({ ...draft, name: "" }, ["model"], false, "r1", profile)).toThrow(/连接名称/);
    expect(() => connectionSaveRequest({ ...draft, apiKey: "" }, ["model"], false, "r1", profile)).toThrow(/API Key/);
  });
  it("initializes one default model through the existing native confirmation command", () => {
    const request = connectionSaveRequest(draft, ["manual/model"], true, "empty-revision", null);
    expect(request.command).toBe("set_llm_profile");
    expect(request.args).toMatchObject({ expectedRevision: "empty-revision", model: "manual/model", fastModel: null, strongModel: null, roleModels: {} });
    expect(() => connectionSaveRequest(draft, ["a", "b"], true, "r1", null)).toThrow(/一个默认模型/);
  });
  it("allows loopback models without a key and rejects remote HTTP or embedded credentials", () => {
    expect(connectionSaveRequest({ ...draft, baseUrl: "http://127.0.0.1:1234/v1", apiKey: "" }, ["local"], false, "r1", profile).command).toBe("save_llm_provider");
    for (const baseUrl of ["http://remote.example.test/v1", "https://user:pass@remote.example.test/v1", "https://remote.example.test/v1?token=x"]) {
      expect(() => connectionSaveRequest({ ...draft, baseUrl }, ["m"], false, "r1", profile)).toThrow();
    }
  });
  it("requires a read-back matching the selected provider and all manual models", () => {
    const provider = { id: draft.id, name: draft.name, baseUrl: draft.baseUrl, hasKey: true, models: ["manual/model"] };
    expect(() => verifyConnectionReceipt({ ...profile, providers: [provider] }, draft, ["manual/model"], false)).not.toThrow();
    for (const changed of [{ ...provider, models: [] }, { ...provider, id: "other" }, { ...provider, baseUrl: "https://other.example.test/v1" }]) {
      expect(() => verifyConnectionReceipt({ ...profile, providers: [changed] }, draft, ["manual/model"], false)).toThrow(/回执/);
    }
    expect(() => verifyConnectionReceipt({ ...profile, baseUrl: draft.baseUrl, model: "manual/model" }, draft, ["manual/model"], true)).not.toThrow();
    expect(() => verifyConnectionReceipt(profile, draft, ["manual/model"], true)).toThrow(/回执/);
  });
});

it("offers persisted manual models in the real chat model menu", () => {
  const saved = { ...profile, providers: [{ id: "custom_test", name: "我的连接", baseUrl: draft.baseUrl, hasKey: true, models: ["manual/model"] }] };
  expect(modelChoices(saved).map(choice => choice.value)).toContain("custom_test:manual/model");
});

it("identifies a new connection during discovery so native code cannot borrow the default key", () => {
  const probe = connectionProbeDraft({ ...draft, baseUrl: "http://127.0.0.1:1234/v1", apiKey: "" });
  expect(probe).toEqual({ baseUrl: "http://127.0.0.1:1234/v1", apiKey: "", model: "", providerId: draft.id });
  expect(connectionProbeDraft({ ...draft, editing: true }).providerId).toBe(draft.id);
});
