import assert from "node:assert/strict";
import test from "node:test";
import { contrastOf, gradientOf } from "../packages/ui/src/agent/engine.js";
import {
  characterBackdrop,
  characterGlow,
} from "../packages/ui/src/sky/contrast";

test("very dark and pale custom bodies remain distinct from their avatar backdrop", () => {
  for (const [color, dark] of [
    ["#000000", true],
    ["#ffffff", false],
  ] as const) {
    const [body] = gradientOf(color);
    for (const backdrop of characterBackdrop(color, dark))
      assert.ok(contrastOf(body, backdrop) >= 3);
  }
});
test("character halos start from the actual surface and stay neutral across identity colors", () => {
  for (const surface of ["#181817", "rgb(250, 250, 250)"]) {
    assert.deepEqual(
      characterGlow("sky", surface),
      characterGlow("indigo", surface),
    );
    assert.equal(characterGlow("sky", surface).length, 8);
    assert.equal(
      characterGlow("sky", surface)[0],
      surface.startsWith("#") ? surface : "#fafafa",
    );
  }
});
