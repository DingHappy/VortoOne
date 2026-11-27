import assert from "node:assert/strict";
import test from "node:test";
import { railState } from "../packages/client/src/lib/onboarding";

const newWorkspace: Parameters<typeof railState>[0] = {
  orgId: null,
  standing: undefined,
  billingUrl: null,
  context: "",
  team: 0,
  picked: null,
  funnel: { thinks: false, passed: undefined },
};

test("team onboarding counts purpose and name, then goes to AI without requiring context", () => {
  const purpose = railState(newWorkspace);
  assert.deepEqual(purpose.steps, ["purpose", "name", "ai", "presets"]);
  assert.equal(purpose.step, "purpose");
  assert.equal(purpose.reached, 0);

  const naming = railState({ ...newWorkspace, namingTeam: true });
  assert.equal(naming.step, "name");
  assert.equal(naming.reached, 1);

  const created = railState({ ...newWorkspace, orgId: "workspace", picked: "name" });
  assert.equal(created.step, "name");
  assert.equal(created.at, "ai");
  assert.equal(created.reached, 2);
});

test("personal onboarding goes straight from purpose to AI unless billing is due", () => {
  const purpose = railState({ ...newWorkspace, personal: true, billingUrl: "https://billing.example.test" });
  assert.deepEqual(purpose.steps, ["purpose", "plan", "ai", "presets"]);
  assert.equal(purpose.step, "purpose");

  const resumed = railState({ ...newWorkspace, orgId: "workspace", personal: true });
  assert.deepEqual(resumed.steps, ["purpose", "ai", "presets"]);
  assert.equal(resumed.step, "ai");
  assert.equal(resumed.reached, 1);
});

test("presets only unlock when the right hand is ready", () => {
  const ours = { ...newWorkspace, orgId: "workspace", team: 2 };
  assert.equal(railState(ours).at, "ai");
  assert.equal(railState({ ...ours, funnel: { thinks: undefined, passed: "agents" } }).at, "ai");
  const ready = railState({ ...ours, funnel: { thinks: true, passed: "agents" } });
  assert.equal(ready.at, "presets");
  assert.equal(ready.step, "presets");
});

test("legacy AI deferral and invitation progress remains respected", () => {
  for (const passed of ["ai", "team"] as const) {
    for (const thinks of [false, true]) {
      assert.equal(railState({ ...newWorkspace, orgId: "workspace", funnel: { thinks, passed } }).at, "open");
    }
  }
});

test("an active billing hold precedes AI and presets", () => {
  const resumed = {
    ...newWorkspace,
    orgId: "workspace",
    standing: { state: "hold", reason: "payment" } as const,
    funnel: { thinks: true, passed: "computer" } as const,
  };
  assert.equal(railState(resumed).at, "plan");
  const paid = railState({ ...resumed, standing: { state: "active", reason: null } });
  assert.deepEqual(paid.steps, ["purpose", "name", "plan", "ai", "presets"]);
  assert.equal(paid.step, "presets");
});

test("the existing phone rail remains unchanged", () => {
  const phone = railState({ ...newWorkspace, funnel: null });
  assert.deepEqual(phone.steps, ["name", "business", "team"]);
  assert.equal(phone.step, "name");
});
