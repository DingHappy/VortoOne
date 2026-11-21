import assert from "node:assert/strict";
import { test } from "node:test";
import { createHttpApi } from "../packages/client/src/api/http-client";

test("member routine actions use the approval path for submission and status", async () => {
  const calls: Array<{ url: string; method: string | undefined; body: unknown }> = [];
  const proposal = {
    id: "staff-proposal-1",
    name: "Weekly report",
    agentName: "Reader",
    schedule: "30 9 * * 1",
    timezone: "Asia/Shanghai",
    createdAt: "2026-11-18T00:00:00Z",
    status: "pending",
  };
  const api = createHttpApi({
    baseUrl: "https://example.test",
    fetch: async (url, init) => {
      calls.push({ url, method: init.method, body: init.body ? JSON.parse(String(init.body)) : undefined });
      return init.method === "POST"
        ? Response.json({ id: proposal.id, status: proposal.status }, { status: 201 })
        : Response.json({ proposals: [proposal] });
    },
  });
  const input = {
    agentId: "a-reader",
    definition: {
      id: "routine-weekly-report",
      name: proposal.name,
      prompt: "Review public project updates.",
      schedule: proposal.schedule,
      timezone: proposal.timezone,
    },
    reason: "Keep the weekly review up to date.",
  };
  assert.deepEqual(await api.routines.proposeForMember("home", input), { id: proposal.id, status: "pending" });
  assert.deepEqual(await api.routines.memberProposals("home"), [proposal]);
  const url = "https://example.test/api/app/organizations/home/routines/proposals";
  assert.deepEqual(calls, [{ url, method: "POST", body: input }, { url, method: "GET", body: undefined }]);
});

test("routine takeover confirms the review digest in one write", async () => {
  const calls: Array<{ url: string; method: string | undefined; body: unknown }> = [];
  const digest = "a".repeat(64);
  const api = createHttpApi({
    baseUrl: "https://example.test",
    fetch: async (url, init) => {
      calls.push({ url, method: init.method, body: init.body ? JSON.parse(String(init.body)) : undefined });
      return init.method === "POST"
        ? Response.json({ id: "routine-1", active: false })
        : Response.json({ digest, prompt: "Review public notes." });
    },
  });
  const preview = await api.routines.takeoverPreview("home", "routine-1");
  const saved = await api.routines.takeOver("home", "routine-1", preview.digest);
  assert.equal(saved.active, false);
  const url = "https://example.test/api/app/organizations/home/routines/routine-1/take-over";
  assert.deepEqual(calls, [{ url, method: "GET", body: undefined }, { url, method: "POST", body: { digest } }]);
});

test("owner routine decisions carry the proof from the reviewed proposal", async () => {
  const calls: Array<{ url: string; method: string | undefined; body: unknown }> = [];
  const proof = { digest: "c".repeat(64), nonce: "preview", round: 1, instance: "current-service" };
  const proposal = { id: "staff-proposal-1", status: "pending", proof };
  const api = createHttpApi({
    baseUrl: "https://example.test",
    fetch: async (url, init) => {
      calls.push({ url, method: init.method, body: init.body ? JSON.parse(String(init.body)) : undefined });
      return init.method === "POST"
        ? Response.json({ id: proposal.id, status: "approved" })
        : Response.json({ proposals: [proposal] });
    },
  });
  const [reviewed] = await api.routines.proposalReviews("home");
  const result = await api.routines.decideProposal("home", reviewed.id, { ...reviewed.proof, approve: true });
  assert.deepEqual(result, { id: proposal.id, status: "approved" });
  const base = "https://example.test/api/app/organizations/home/routines/proposals";
  assert.deepEqual(calls, [
    { url: `${base}/review`, method: "GET", body: undefined },
    { url: `${base}/staff-proposal-1/decision`, method: "POST", body: { ...proof, approve: true } },
  ]);
});
