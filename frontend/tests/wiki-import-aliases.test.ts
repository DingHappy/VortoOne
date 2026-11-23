import assert from "node:assert/strict";
import test from "node:test";
import { readVault, type VaultFile } from "../apps/web/src/lib/obsidian";
import { resolveWikiLink } from "../packages/client/src/lib/wiki";

test("a Wiki link opens a unique visible alias without changing the canonical link rules", () => {
  const first = { title: "Operations", folder: "Team", slug: "team-operations", aliases: ["Café", "staff on call"] };
  const second = { title: "Support", folder: "", slug: "support", aliases: ["CAFE"] };
  const canonical = { title: "Cafe", folder: "", slug: "cafe", aliases: [] };
  assert.equal(resolveWikiLink([first], "CAFE#Schedule"), first);
  assert.equal(resolveWikiLink([first, second], "staff on call"), first);
  assert.equal(resolveWikiLink([first, second], "Café"), undefined);
  assert.equal(resolveWikiLink([first, second, canonical], "Café"), canonical);
  assert.equal(resolveWikiLink([first, second], "Team/Operations"), first);
  assert.equal(resolveWikiLink([first, second], "team-operations"), first);
  assert.equal(resolveWikiLink([second], "staff on call"), undefined);
});

function note(path: string, text: string): VaultFile {
  const blob = new Blob([text]);
  return { path, size: blob.size, text: () => blob.text(), blob: async () => blob };
}

test("the import preview recognizes a unique alias on an already visible page", async () => {
  const files = [note("Index.md", "Read [[Cafe#Hours]] and [[Missing]].")];
  const existing = [{ title: "Operations", aliases: ["Café", "cafe"] }];
  const report = await readVault(files, { existing });
  assert.deepEqual(report.broken, ["Missing"]);
});

test("the import preview flags ambiguous aliases unless a canonical title matches", async () => {
  const files = [
    note("Index.md", "Read [[CAFE]] and [[Support]]."),
    note("Notes.md", "---\naliases: [Café, Support]\n---\nNotes"),
  ];
  const existing = [{ title: "Support", aliases: ["Cafe", "SUPPORT"] }];
  const report = await readVault(files, { existing });
  assert.deepEqual(report.broken, ["CAFE"]);
});
