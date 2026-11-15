import { describe, expect, it, vi } from "vitest";
import { memberProposalInput, roleCreationController } from "./createController";
import type { StaffClient, StaffProposal, StaffSnapshot } from "../../lib/staff";

function fixture() {
  const draft = { name: "  研究助手  ", role: "reader", engine: "vortocode", model: "" };
  const requestId = "a".repeat(32), id = `staff-${requestId}`;
  const proposal = { id: "proposal-one", digest: "digest", nonce: "nonce", round: 1, instance: "run",
    status: "pending", reason: "owner", approval_level: "desktop", snapshot: { kind: "member", previous: null,
      data: { id, name: "研究助手", role: "reader", engine: "vortocode", model: "" } } } as StaffProposal;
  let registry = { roles: [{ id: "reader", revision: 2, enabled: true, engine: ["vortocode"] }],
    members: [], proposals: [proposal], conversation_version: 1, owner_instance: "run", role_digests: { reader: "role-digest" } } as unknown as StaffSnapshot;
  let current = true;
  const client: StaffClient = { readStaff: vi.fn(async () => structuredClone(registry)),
    proposeStaffMember: vi.fn(async () => structuredClone(proposal)), setStaffStatus: vi.fn(),
    decideStaff: vi.fn(async (_id, approve) => { registry = { ...registry,
      proposals: [{ ...proposal, status: approve ? "approved" : "denied" }],
      members: approve ? [{ id } as StaffSnapshot["members"][number]] : [] }; }) };
  const registered = vi.fn(), publish = vi.fn();
  const controller = roleCreationController({ client, registry: structuredClone(registry), requestId,
    current: () => current, registered, publish });
  return { client, controller, registered, publish, draft, proposal, requestId, id,
    registry: () => registry, update: (patch: Partial<StaffSnapshot>) => { registry = { ...registry, ...patch }; },
    changeSource: () => { current = false; } };
}

describe("direct owner role creation", () => {
  it("whitelists form fields and freezes registered role proof without any task authority", () => {
    const f = fixture();
    expect(memberProposalInput({ ...f.draft, grants: { web: true } } as typeof f.draft, f.registry(), f.requestId))
      .toEqual({ request_id: f.requestId, name: "研究助手", role: "reader", engine: "vortocode", model: "",
        role_revision: 2, role_digest: "role-digest", instance: "run" });
  });
  it("saving a proposal neither approves nor registers; a second explicit decision uses the exact proof", async () => {
    const f = fixture(); await f.controller.propose(f.draft);
    expect(f.client.decideStaff).not.toHaveBeenCalled(); expect(f.registered).not.toHaveBeenCalled();
    await f.controller.decide(true);
    expect(f.client.decideStaff).toHaveBeenCalledWith("proposal-one", true, { digest: "digest", nonce: "nonce", round: 1, instance: "run" });
    expect(f.registered).toHaveBeenCalledWith(f.id);
  });
  it("rejecting a proposal leaves the roster unchanged", async () => {
    const f = fixture(); await f.controller.propose(f.draft); await f.controller.decide(false);
    expect(f.controller.state().proposal?.status).toBe("denied"); expect(f.registered).not.toHaveBeenCalled();
    expect(f.registry().members).toEqual([]);
  });
  it.each(["owner_instance", "role_digests"])("does not propose after %s changes", async key => {
    const f = fixture(); f.update(key === "owner_instance" ? { owner_instance: "new-run" } : { role_digests: { reader: "changed" } });
    await f.controller.propose(f.draft); expect(f.client.proposeStaffMember).not.toHaveBeenCalled();
    expect(f.controller.state().error).toContain("变化");
  });
  it.each(["digest", "nonce", "round", "instance"])("does not approve a changed %s", async key => {
    const f = fixture(); await f.controller.propose(f.draft);
    f.update({ proposals: [{ ...f.proposal, [key]: key === "round" ? 2 : "changed" }] });
    await f.controller.decide(true); expect(f.client.decideStaff).not.toHaveBeenCalled();
    expect(f.registered).not.toHaveBeenCalled();
  });
  it("cannot write from an old space or consume a late response", async () => {
    const f = fixture(); let resolve!: (value: StaffProposal) => void;
    vi.mocked(f.client.proposeStaffMember!).mockReturnValueOnce(new Promise(done => { resolve = done; }));
    const pending = f.controller.propose(f.draft); await vi.waitFor(() => expect(f.client.proposeStaffMember).toHaveBeenCalledOnce());
    f.changeSource(); resolve(f.proposal); await pending;
    expect(f.controller.state().proposal).toBeNull(); await f.controller.decide(true);
    expect(f.client.decideStaff).not.toHaveBeenCalled();
  });
  it("recovers an already saved approval with a read, without approving twice", async () => {
    const f = fixture(); await f.controller.propose(f.draft);
    f.update({ proposals: [{ ...f.proposal, status: "approved" }], members: [{ id: f.id } as StaffSnapshot["members"][number]] });
    await f.controller.decide(true); expect(f.registered).toHaveBeenCalledWith(f.id);
    expect(f.client.decideStaff).not.toHaveBeenCalled();
  });
  it("does not claim registration if the approval receipt lacks the registered member", async () => {
    const f = fixture(); await f.controller.propose(f.draft);
    vi.mocked(f.client.decideStaff).mockImplementationOnce(async () => { f.update({ proposals: [{ ...f.proposal, status: "approved" }] }); });
    await f.controller.decide(true); expect(f.registered).not.toHaveBeenCalled(); expect(f.controller.state().error).toContain("登记结果待核对");
  });
  it("reuses the original proposal request after a lost receipt and refuses unsupported engines", async () => {
    const f = fixture(); vi.mocked(f.client.proposeStaffMember!).mockRejectedValueOnce(new Error("lost"));
    await f.controller.propose(f.draft); await f.controller.propose(f.draft);
    expect(vi.mocked(f.client.proposeStaffMember!).mock.calls.map(call => call[0].request_id)).toEqual([f.requestId, f.requestId]);
    expect(() => memberProposalInput({ ...f.draft, engine: "other" }, f.registry(), f.requestId)).toThrow("支持");
  });
});
