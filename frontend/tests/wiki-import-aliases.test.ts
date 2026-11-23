import assert from "node:assert/strict";
import test from "node:test";
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
