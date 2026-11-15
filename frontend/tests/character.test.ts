import assert from "node:assert/strict";
import test from "node:test";
import {
  ACCESSORY_NAMES,
  bodyPath,
  drawAccessory,
  drawAgent,
  eyeShapes,
  inBody,
  SLIME_ROWS,
  spriteGrid,
  bodyColorAt,
} from "../packages/ui/src/agent/engine.js";
import {
  AGENT_ACCESSORIES,
  normalizeAppearance,
} from "../packages/tokens/src/look";

function drawing(run: (ctx: any) => void) {
  const commands: unknown[] = [];
  const ctx = new Proxy({} as any, {
    get: (o, key) =>
      key in o ? o[key] : (...args: unknown[]) => commands.push([key, ...args]),
    set: (o, key, value) => {
      o[key] = value;
      commands.push([key, value]);
      return true;
    },
  });
  run(ctx);
  return commands;
}
test("slime has a continuous crown, open corners and broad lower body", () => {
  assert.equal(inBody(0.5, 0.15), true);
  assert.equal(inBody(0.05, 0.15), false);
  assert.equal(inBody(0.05, 0.65), true);
  for (const row of SLIME_ROWS.filter(Boolean)) assert.ok(row![0] < row![1]);
  assert.ok(bodyPath().length > 0);
  assert.ok(
    spriteGrid().join("").split("o").length > 3,
    "small sprites retain both eyes and mouth",
  );
});
test("custom hex changes rendered body without changing the outline", () => {
  const a = drawing((ctx) => drawAgent(ctx, 0, 0, 120, { color: "#4ac0b2" }));
  const b = drawing((ctx) => drawAgent(ctx, 0, 0, 120, { color: "#ac76e2" }));
  assert.notDeepEqual(a, b);
  assert.deepEqual(
    a.filter((c: any) => c[0] === "fillRect"),
    b.filter((c: any) => c[0] === "fillRect"),
  );
});
test("body keeps continuous material shading instead of five flat bands", () => {
  const colors = new Set(
    Array.from({ length: 80 }, (_, i) =>
      bodyColorAt("sky", 0.2 + i * 0.007, 0.55),
    ),
  );
  assert.ok(colors.size > 30, `only ${colors.size} distinct material samples`);
});
test("expressions change the face independently of identity color", () => {
  assert.notDeepEqual(
    eyeShapes("round"),
    eyeShapes("round", { expression: "heart" }),
  );
  const a = drawing((ctx) =>
    drawAgent(ctx, 0, 0, 120, { color: "teal", pose: { expression: "happy" } }),
  );
  const b = drawing((ctx) =>
    drawAgent(ctx, 0, 0, 120, {
      color: "teal",
      pose: { expression: "surprised" },
    }),
  );
  assert.notDeepEqual(a, b);
});
test("every advertised accessory has a drawable layer, including glasses", () => {
  assert.deepEqual(new Set(ACCESSORY_NAMES), new Set(AGENT_ACCESSORIES));
  for (const name of AGENT_ACCESSORIES.filter((a) => a !== "none"))
    assert.ok(drawing((ctx) => drawAccessory(ctx, 120, name)).length > 0, name);
});
test("saved appearances accept hex and glasses and reject unknown options", () => {
  assert.deepEqual(
    normalizeAppearance({
      color: "#AbCDef",
      eyes: "happy",
      accessory: "glasses",
    }),
    { color: "#abcdef", eyes: "happy", accessory: "glasses" },
  );
  assert.deepEqual(
    normalizeAppearance({
      color: "javascript:bad",
      eyes: "missing",
      accessory: "missing",
    }),
    { color: undefined, eyes: undefined, accessory: undefined },
  );
});
test("forming slime draws no face before its body is ready", () => {
  const partial = drawing((ctx) =>
    drawAgent(ctx, 0, 0, 120, { progress: 0.5 }),
  );
  const full = drawing((ctx) => drawAgent(ctx, 0, 0, 120));
  assert.notDeepEqual(partial, full);
  assert.equal(
    partial.some((c: any) => c[0] === "roundRect"),
    false,
  );
  assert.equal(
    full.some((c: any) => c[0] === "roundRect"),
    true,
  );
});
