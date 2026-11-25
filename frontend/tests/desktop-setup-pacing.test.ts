import assert from "node:assert/strict";
import test from "node:test";
import { type StepChange, STEP_SHOW_MS, stepPacer } from "../apps/desktop/src/control/local-server";

/** A clock that only moves when the pacer waits. */
function fakeTime() {
  let t = 1_000;
  const waits: number[] = [];
  return {
    now: () => t,
    wait: async (ms: number) => {
      waits.push(ms);
      t += ms;
    },
    waits,
    advance: (ms: number) => {
      t += ms;
    },
  };
}

const change = (step: StepChange["step"], state: StepChange["state"]): StepChange => ({ step, state });

test("a setup that ends in a blink still shows each step for a moment, in order", async () => {
  const time = fakeTime();
  const shown: { change: StepChange; at: number }[] = [];
  const pacer = stepPacer((c) => shown.push({ change: c, at: time.now() }), time);
  const all = [
    change("database", "active"),
    change("database", "done"),
    change("start", "active"),
    change("start", "done"),
    change("account", "done"),
    change("computer", "done"),
  ];
  for (const c of all) pacer.push(c);
  await pacer.settled();
  assert.deepEqual(
    shown.map((s) => s.change),
    all,
  );
  for (let i = 1; i < shown.length; i++) assert.equal(shown[i].at - shown[i - 1].at, STEP_SHOW_MS);
  assert.equal(time.waits.length, all.length - 1, "the first change shows at once");
});

test("a step that really takes long is shown as soon as it ends, without an extra wait", async () => {
  const time = fakeTime();
  const shown: StepChange[] = [];
  const pacer = stepPacer((c) => shown.push(c), time);
  pacer.push(change("start", "active"));
  await pacer.settled();
  time.advance(5_000);
  pacer.push(change("start", "done"));
  await pacer.settled();
  assert.equal(shown.length, 2);
  assert.deepEqual(time.waits, []);
});

test("after a stop (cancelled, or a new run) nothing more shows", async () => {
  const time = fakeTime();
  const shown: StepChange[] = [];
  const pacer = stepPacer((c) => shown.push(c), time);
  pacer.push(change("database", "active"));
  await pacer.settled();
  pacer.push(change("database", "done"));
  pacer.stop();
  await pacer.settled();
  assert.deepEqual(shown, [change("database", "active")]);
});
