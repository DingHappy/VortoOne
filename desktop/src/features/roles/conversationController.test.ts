import { describe, expect, it, vi } from "vitest";
import { roleConversationController, roleDrafts, type RoleDraft, type RoleConversationState } from "./conversationController";
import type { RoleConversationClient, RoleConversationSnapshot, RoleTask } from "./types";
import { SOURCE_FRESH_MS } from "../../lib/readFreshness";

function fixture() {
  const task = { id: "task-one", prompt: "Read this", status: "queued", confirmation: { digest: "task", round: 1, instance: "instance" } } as RoleTask;
  let value = { version: 1, instance: "instance", identity_digest: "binding", role_digest: "role-binding",
    member: { id: "reader", revision: 1, status: "active" }, role: { id: "read", revision: 2 }, tasks: [],
    history_complete: true, next_cursor: null, submitted_task: null, can_dispatch: true, unavailable_reason: "" } as unknown as RoleConversationSnapshot;
  const client: RoleConversationClient = { readRoleConversation: vi.fn(async () => structuredClone(value)),
    sendRoleMessage: vi.fn(async () => ({ task, replayed: false })), decideRoleTask: vi.fn(async () => ({ task })) };
  let current = true, time = 1000, request = "";
  const states: RoleConversationState[] = [], accepted = vi.fn();
  const controller = roleConversationController({ client, member: "reader", current: () => current,
    publish: state => states.push(state), now: () => time, pending: () => request, accepted });
  const draft: RoleDraft = { requestId: "a".repeat(32), prompt: "Read this", criteria: "Cite the source", delivery: "review", pending: false };
  return { client, controller, states, task, draft, accepted, value: () => value,
    setValue: (next: RoleConversationSnapshot) => { value = next; }, changeSource: () => { current = false; },
    advance: () => { time += SOURCE_FRESH_MS + 1; }, pending: () => { request = draft.requestId; } };
}

describe("owner role conversations", () => {
  it("reads without creating a task, then submits exact displayed role bindings and no authority fields", async () => {
    const f = fixture(); await f.controller.refresh();
    expect(f.client.sendRoleMessage).not.toHaveBeenCalled();
    expect(await f.controller.send(f.draft)).toBe(true);
    expect(f.client.sendRoleMessage).toHaveBeenCalledWith("reader", { request_id: "a".repeat(32), prompt: "Read this",
      acceptance: ["Cite the source"], delivery: "review", member_revision: 1, role_revision: 2, identity_digest: "binding", instance: "instance" });
    expect(f.accepted).toHaveBeenCalledOnce();
    expect(f.client.decideRoleTask).not.toHaveBeenCalled();
  });
  it("does not dispatch from an old space or an expired read", async () => {
    for (const change of ["source", "time"]) {
      const f = fixture(); await f.controller.refresh(); change === "source" ? f.changeSource() : f.advance();
      expect(await f.controller.send(f.draft)).toBe(false); expect(f.client.sendRoleMessage).not.toHaveBeenCalled();
    }
  });
  it("late reads and receipts cannot change the new space or clear its draft", async () => {
    const f = fixture(); await f.controller.refresh();
    let finish!: (value: { task: RoleTask; replayed: boolean }) => void;
    vi.mocked(f.client.sendRoleMessage).mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
    const pending = f.controller.send(f.draft); f.changeSource(); finish({ task: f.task, replayed: false });
    expect(await pending).toBe(false); expect(f.accepted).not.toHaveBeenCalled();
  });
  it("a changed instance or role digest needs explicit refresh and review before sending", async () => {
    const f = fixture(); await f.controller.refresh();
    f.setValue({ ...f.value(), instance: "new-instance", identity_digest: "changed" });
    await f.controller.refresh(); expect(f.controller.state().changed).toBe(true);
    expect(await f.controller.send(f.draft)).toBe(false); expect(f.client.sendRoleMessage).not.toHaveBeenCalled();
    await f.controller.refresh(true); expect(await f.controller.send(f.draft)).toBe(true);
  });
  it("a lost receipt is reconciled by a read of the original id without another dispatch", async () => {
    const f = fixture(); await f.controller.refresh(); f.pending();
    vi.mocked(f.client.sendRoleMessage).mockRejectedValueOnce(new Error("Receipt unavailable"));
    expect(await f.controller.send(f.draft)).toBe(false); expect(f.accepted).not.toHaveBeenCalled();
    f.setValue({ ...f.value(), tasks: [f.task], submitted_task: f.task });
    await f.controller.refresh();
    expect(f.client.readRoleConversation).toHaveBeenLastCalledWith("reader", "", f.draft.requestId);
    expect(f.client.sendRoleMessage).toHaveBeenCalledTimes(1); expect(f.accepted).toHaveBeenCalledWith(f.draft.requestId, f.task);
  });
  it("retains the original token when the user explicitly retries after reconciliation", async () => {
    const f = fixture(); await f.controller.refresh(); f.pending();
    vi.mocked(f.client.sendRoleMessage).mockRejectedValueOnce(new Error("Network"));
    await f.controller.send(f.draft); await f.controller.refresh(); await f.controller.send(f.draft);
    expect(vi.mocked(f.client.sendRoleMessage).mock.calls.map(call => call[1].request_id)).toEqual([f.draft.requestId, f.draft.requestId]);
  });
  it("cannot decide a replaced card or accept a result automatically", async () => {
    const f = fixture(); f.setValue({ ...f.value(), tasks: [{ ...f.task, confirmation: { ...f.task.confirmation, digest: "new" } }] });
    await f.controller.refresh(); await f.controller.act(f.task, "accept", "Checked");
    expect(f.client.decideRoleTask).not.toHaveBeenCalled();
    await f.controller.refresh(); const shown = f.controller.state().value!.tasks[0];
    await f.controller.act(shown, "accept", "Checked");
    expect(f.client.decideRoleTask).toHaveBeenCalledWith("reader", shown.id, { ...shown.confirmation, verdict: "accept", note: "Checked" });
  });
  it("rejects a mismatched member from the read service", async () => {
    const f = fixture(); f.setValue({ ...f.value(), member: { ...f.value().member, id: "other" } });
    await f.controller.refresh(); expect(await f.controller.send(f.draft)).toBe(false);
    expect(f.client.sendRoleMessage).not.toHaveBeenCalled();
  });
  it("keeps older pages visible and pauses polling until an explicit latest refresh", async () => {
    const f = fixture(); f.setValue({ ...f.value(), tasks: [f.task], next_cursor: f.task.id });
    await f.controller.refresh();
    const older = { ...f.task, id: "task-older" };
    f.setValue({ ...f.value(), tasks: [older], next_cursor: null });
    await f.controller.refresh(false, f.task.id);
    expect(f.controller.state().value?.tasks.map(task => task.id)).toEqual([f.task.id, older.id]);
    const calls = vi.mocked(f.client.readRoleConversation).mock.calls.length;
    await f.controller.poll(); expect(f.client.readRoleConversation).toHaveBeenCalledTimes(calls);
    await f.controller.refresh(); expect(f.controller.state().browsingHistory).toBe(false);
  });
  it("validates delivery criteria without marking a draft as submitted", async () => {
    const f = fixture(); await f.controller.refresh();
    expect(await f.controller.send({ ...f.draft, criteria: "x".repeat(501) })).toBe(false);
    expect(f.client.sendRoleMessage).not.toHaveBeenCalled(); expect(f.accepted).not.toHaveBeenCalled();
  });
});

it("keeps drafts in their space and role, including the original id after an uncertain send", () => {
  let sequence = 0; const drafts = roleDrafts(() => String(++sequence));
  drafts.edit("space-a:reader", { prompt: "A" }); drafts.sending("space-a:reader");
  drafts.edit("space-b:reader", { prompt: "B" }); drafts.edit("space-a:reader", { prompt: "Changed" });
  expect(drafts.get("space-a:reader").prompt).toBe("A");
  expect(drafts.get("space-b:reader").prompt).toBe("B");
  drafts.accepted("space-a:reader", "old-id"); expect(drafts.get("space-a:reader").prompt).toBe("A");
  drafts.accepted("space-a:reader", drafts.get("space-a:reader").requestId);
  expect(drafts.get("space-a:reader").prompt).toBe(""); expect(drafts.get("space-b:reader").prompt).toBe("B");
});
