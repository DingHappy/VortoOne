import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { desktopMetadata } from "../apps/web/src/lib/desktop-metadata";

test("the native window identifies as desktop for both old and new pages", () => {
  type Appearance = { platform: string; renderer: string };
  const window: { __vortooneDesktop?: Appearance; __agentesDesktop?: Appearance } = {};
  const script = readFileSync(new URL("../apps/desktop-dev/desktop-titlebar.js", import.meta.url), "utf8");
  runInNewContext(script, { window });
  assert.equal(window.__vortooneDesktop?.platform, "mac");
  assert.equal(window.__vortooneDesktop?.renderer, "gpu");
  assert.equal(Object.isFrozen(window.__vortooneDesktop), true);
  assert.deepEqual(Object.keys(window.__vortooneDesktop!).sort(), ["platform", "renderer"]);
  assert.equal(window.__agentesDesktop, window.__vortooneDesktop);
});

test("a new page reads an older shell's appearance and capability flags", () => {
  const legacy = { platform: "linux", renderer: "cpu", files: true, connectorReturn: true };
  assert.equal(desktopMetadata({ __agentesDesktop: legacy }), legacy);
  assert.equal(desktopMetadata({}), undefined);
});

test("the current marker wins when both names are present", () => {
  const legacy = { files: true };
  const current = { platform: "mac" };
  assert.equal(desktopMetadata({ __vortooneDesktop: current, __agentesDesktop: legacy }), current);
});
