import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";

test("the native window identifies as desktop before the production page starts", () => {
  const window: { __vortooneDesktop?: { platform: string; renderer: string } } = {};
  const script = readFileSync(new URL("../apps/desktop-dev/desktop-titlebar.js", import.meta.url), "utf8");
  runInNewContext(script, { window });
  assert.equal(window.__vortooneDesktop?.platform, "mac");
  assert.equal(window.__vortooneDesktop?.renderer, "gpu");
  assert.equal(Object.isFrozen(window.__vortooneDesktop), true);
  assert.deepEqual(Object.keys(window.__vortooneDesktop!).sort(), ["platform", "renderer"]);
});
