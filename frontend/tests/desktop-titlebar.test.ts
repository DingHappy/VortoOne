import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { desktopMetadata } from "../apps/web/src/lib/desktop-metadata";

for (const platform of ["mac", "windows", "linux"]) {
  test(`the native ${platform} window identifies itself to both old and new pages`, () => {
    type Appearance = {
      platform: string;
      renderer: string;
      desktopSetupModelSettings: boolean;
      desktopHomeModelSettings: boolean;
    };
    const window: { __vortooneDesktop?: Appearance; __agentesDesktop?: Appearance } = {};
    const script = readFileSync(new URL("../apps/desktop-dev/desktop-titlebar.js", import.meta.url), "utf8");
    runInNewContext(script, { window, platform });
    assert.equal(window.__vortooneDesktop?.platform, platform);
    assert.equal(window.__vortooneDesktop?.renderer, "gpu");
    assert.equal(window.__vortooneDesktop?.desktopSetupModelSettings, true);
    assert.equal(window.__vortooneDesktop?.desktopHomeModelSettings, true);
    assert.equal(Object.isFrozen(window.__vortooneDesktop), true);
    assert.deepEqual(Object.keys(window.__vortooneDesktop!).sort(), [
      "desktopHomeModelSettings", "desktopSetupModelSettings", "platform", "renderer",
    ]);
    assert.equal(window.__agentesDesktop, window.__vortooneDesktop);
  });
}

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
