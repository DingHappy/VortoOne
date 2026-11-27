import assert from "node:assert/strict";
import test from "node:test";
import type { Agent, AgentInput } from "../packages/client/src/api/types";
import { createSelectedPresets } from "../apps/web/src/pages/onboarding/preset-creation";

function agent(handle: string, previousHandles: string[] = []): Agent {
  return {
    id: `test-${handle}`, name: handle, handle, previousHandles, role: "", instructions: "", model: "",
    ownerUserId: null, hasCredential: false, createdAt: "2026-01-01T00:00:00Z",
  };
}

const localized = (): AgentInput => ({ name: "示例智能体", handle: "localized-name", role: "研究" });

test("multiple presets create under stable handles and keep the workspace's existing agents", async () => {
  const roster = [agent("main"), agent("renamed", ["lia"])];
  const submitted: AgentInput[] = [];
  const api = {
    list: async () => roster,
    create: async (_orgId: string, input: AgentInput) => {
      submitted.push(input);
      return agent(input.handle);
    },
  };
  const result = await createSelectedPresets(api, "test", ["lia", "theo", "ada"], localized);
  assert.deepEqual(submitted.map((input) => input.handle), ["theo", "ada"]);
  assert.ok(submitted.every((input) => input.name === "示例智能体"));
  assert.deepEqual(result.completed, ["lia", "theo", "ada"]);
  assert.equal(result.failures.size, 0);
});

test("retry only creates selections that are still missing after a partial failure", async () => {
  const roster: Agent[] = [];
  const attempted: string[] = [];
  const failure = new Error("test refusal");
  let refuseAda = true;
  const api = {
    list: async () => [...roster],
    create: async (_orgId: string, input: AgentInput) => {
      attempted.push(input.handle);
      if (refuseAda && input.handle === "ada") throw failure;
      const row = agent(input.handle);
      roster.push(row);
      return row;
    },
  };
  const first = await createSelectedPresets(api, "test", ["theo", "ada", "lia"], localized);
  assert.deepEqual(first.completed, ["theo", "lia"]);
  assert.deepEqual([...first.failures], [["ada", failure]]);
  refuseAda = false;
  const retry = await createSelectedPresets(api, "test", ["theo", "ada", "lia"], localized);
  assert.deepEqual(attempted, ["theo", "ada", "lia", "ada"]);
  assert.deepEqual(retry.completed, ["theo", "ada", "lia"]);
  assert.equal(retry.failures.size, 0);
});

test("a lost create reply is reconciled against the roster and does not ask for another create", async () => {
  const roster: Agent[] = [];
  const api = {
    list: async () => [...roster],
    create: async (_orgId: string, input: AgentInput) => {
      roster.push(agent(input.handle));
      throw new Error("test lost response");
    },
  };
  const result = await createSelectedPresets(api, "test", ["atlas"], localized);
  assert.deepEqual(result.completed, ["atlas"]);
  assert.deepEqual(result.agents, roster);
  assert.equal(result.failures.size, 0);
});
