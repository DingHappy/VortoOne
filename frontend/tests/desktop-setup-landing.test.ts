import assert from "node:assert/strict";
import test from "node:test";
import { landingFor } from "../packages/client/src/lib/guards";
import type { Me } from "../packages/client/src/api/types";

const me = {
  user: { id: "test-owner", canCreateWorkspace: false },
  organizations: [{ id: "local", role: "owner" }],
} as Me;

test("a bundled workspace with saved setup resumes before the normal landing", () => {
  assert.deepEqual(landingFor({ ...me, desktopSetup: { step: "model", modelConfigured: false } }, "local"), {
    to: "/onboarding",
  });
  assert.deepEqual(landingFor(me, "local"), { orgId: "local" });
});
