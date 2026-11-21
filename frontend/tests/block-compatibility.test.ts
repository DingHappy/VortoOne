import assert from "node:assert/strict";
import test from "node:test";
import { createContext, runInContext } from "node:vm";
import { BLOCK_KIT_JS } from "../packages/client/src/lib/block-kit";
import { blockEvent } from "../packages/client/src/lib/blocks";

test("saved blocks can still use the original script API and message marker", () => {
  const messages: unknown[] = [];
  const runtime = createContext({
    window: {},
    document: { documentElement: { dataset: {}, style: { setProperty() {} } } },
    parent: { postMessage(message: unknown) { messages.push(message); } },
    HTMLElement: class {},
    customElements: { define() {} },
    addEventListener() {},
    queueMicrotask() {},
  });
  runInContext(BLOCK_KIT_JS, runtime);
  assert.equal(runInContext("window.agentes === window.vortoone", runtime), true);
  assert.equal(runInContext("window.__agentesHost === window.__vortooneHost", runtime), true);
  runInContext('window.agentes.set("answer", 42); window.agentes.send("Reply", "Send")', runtime);
  assert.equal(runInContext("window.vortoone.state.answer", runtime), 42);
  const [message] = messages;
  assert.equal((message as Record<string, unknown>).agentes, 1);
  assert.equal((message as Record<string, unknown>).vortoone, 1);
  assert.deepEqual(blockEvent(message), { type: "draft", text: "Reply", label: "Send", ready: true, hint: "" });
  assert.deepEqual(blockEvent({ agentes: 1, type: "ready" }), { type: "ready" });
  assert.equal(blockEvent({ type: "ready" }), null);
});
