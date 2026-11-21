import assert from "node:assert/strict";
import test from "node:test";
import { bundleOrigin, isSameWorkspaceExport } from "../packages/client/src/lib/import/flow";
import { detectSource, parseImport } from "../packages/client/src/lib/import/index";
import { importFileCap, IMPORT_MAX_BYTES, IMPORT_TEXT_MAX_BYTES } from "../packages/client/src/lib/import/shared";

function workspaceFile(name: string, format: string, app: string, ref: string, agentName = "Support Agent") {
  return {
    name,
    text: JSON.stringify({
      format,
      version: 1,
      source: { app, ref, name: "Support workspace" },
      agents: [{ key: "agent-001", name: agentName, handle: "support", instructions: "Help the team" }],
      channels: [{ key: "help", name: "help", agents: [{ key: "agent-001", autoReply: false }] }],
    }),
  };
}

test("legacy and current workspace exports share a stable workspace ref and original item keys", () => {
  const legacy = workspaceFile("agentes-chat-export.json", "agenteschat.bundle", "agenteschat", "org-44");
  const current = workspaceFile("vortoone-chat-export.json", "vortoone.bundle", "vortoone", "org-44", "Renamed Agent");
  const renamedFormat = workspaceFile("agentes-chat-export.json", "vortoone.bundle", "agenteschat", "agenteschat:org-44");
  const alreadyScoped = workspaceFile("vortoone-export.json", "vortoone.bundle", "vortoone", "vortoone:org-44");
  for (const file of [legacy, current, renamedFormat, alreadyScoped]) {
    assert.equal(detectSource([file]), "vortoone");
    const bundle = parseImport([file]);
    assert.equal(bundle.format, "vortoone.bundle");
    assert.equal(bundle.source.app, "vortoone");
    assert.equal(bundle.source.ref, "agenteschat:org-44");
    assert.equal(bundle.agents[0]?.key, "agent-001");
    assert.equal(bundle.channels[0]?.agents[0]?.key, "agent-001");
    assert.deepEqual(bundleOrigin(bundle), { card: "vortoone", app: "VortoOne" });
    assert.equal(isSameWorkspaceExport(bundle.source.ref, "org-44"), true);
    const reparsed = parseImport([{ name: file.name, text: JSON.stringify(bundle) }]);
    assert.equal(reparsed.source.ref, bundle.source.ref);
  }
  assert.deepEqual(bundleOrigin({ source: { app: "agenteschat", ref: "agenteschat:org-44" } }), {
    card: "vortoone", app: "VortoOne",
  });
  assert.equal(isSameWorkspaceExport("vortoone:org-44", "org-44"), true);
  assert.equal(isSameWorkspaceExport("agenteschat:org-44", "org-45"), false);
});

test("model replies keep their existing source and item naming", () => {
  const legacy = workspaceFile("agentes-chat-export.json", "agenteschat.bundle", "claude", "guessed-42");
  const current = workspaceFile("vortoone-export.json", "vortoone.bundle", "claude", "different-guess");
  for (const file of [legacy, current]) {
    const bundle = parseImport([file]);
    assert.equal(bundle.source.ref, "claude");
    assert.equal(bundle.agents[0]?.key, "support-agent");
    assert.deepEqual(bundleOrigin(bundle), { card: "other", app: "Claude" });
    assert.equal(isSameWorkspaceExport(bundle.source.ref, "claude"), false);
  }
});

test("legacy and current whole-workspace filenames receive the larger import cap", () => {
  for (const filename of ["agentes-chat-export.json", "vortoone-chat-export.json", "vortoone-export.json"]) {
    assert.equal(importFileCap(`exports/${filename}`), IMPORT_TEXT_MAX_BYTES);
  }
  assert.equal(importFileCap("other-export.json"), IMPORT_MAX_BYTES);
});
