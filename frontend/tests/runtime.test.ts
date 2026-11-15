import assert from "node:assert/strict";
import { test } from "node:test";
import { applyEvent, emptyChat } from "../apps/web/src/vortoone/protocol";
import { VortoRuntime } from "../apps/web/src/vortoone/runtime";

class Socket {
  readyState = 1;
  sent: Record<string, unknown>[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  send(data: string) {
    this.sent.push(JSON.parse(data));
  }
  close() {
    this.readyState = 3;
    this.onclose?.();
  }
  frame(frame: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
}
function harness(
  options: { deliveryTimeout?: number; reconnectMs?: number } = {},
) {
  const sockets: Socket[] = [];
  const urls: string[] = [];
  const runtime = new VortoRuntime({
    ...options,
    location: { host: "127.0.0.1:5174", protocol: "http:" },
    socket: (url) => {
      const socket = new Socket();
      sockets.push(socket);
      urls.push(url);
      return socket as unknown as WebSocket;
    },
  });
  runtime.connect("session-a");
  const socket = sockets[0]!;
  socket.frame({ type: "init", v: 10 });
  return { runtime, socket, sockets, urls };
}
const tick = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms));

test("cumulative streaming replaces text and sequence replay does not duplicate output", () => {
  let state = applyEvent(
    emptyChat(),
    { type: "agent_stream", rid: "r", text: "你", seq: 1 },
    "s",
  );
  state = applyEvent(
    state,
    { type: "agent_stream", rid: "r", text: "你好", seq: 2 },
    "s",
  );
  assert.equal(state.stream, "你好");
  state = applyEvent(
    state,
    { type: "agent_emit", rid: "r", text: "你好", seq: 3 },
    "s",
  );
  const replay = applyEvent(
    state,
    { type: "agent_emit", rid: "r", text: "你好", seq: 3 },
    "s",
  );
  assert.equal(replay.messages.length, 1);
  assert.equal(replay.stream, "");
});

test("old terminal frames cannot stop a different active turn", () => {
  let state = applyEvent(
    emptyChat(),
    { type: "agent_stream", rid: "new", text: "正在处理" },
    "s",
  );
  state = applyEvent(state, { type: "agent_done", rid: "old" }, "s");
  assert.equal(state.busy, true);
  assert.equal(state.stream, "正在处理");
});

test("history and queue hydration use server messages and preserve tool details", () => {
  let state = applyEvent(
    emptyChat(),
    {
      type: "agent_history",
      items: [{ role: "user", text: "查资料", rid: "r" }],
    },
    "s",
  );
  state = applyEvent(
    state,
    {
      type: "agent_queue",
      items: [],
      running: {
        id: "r",
        text: "查资料",
        mode: "plan",
        version: 0,
        position: 0,
        created_at: "",
        context_count: 0,
      },
    },
    "s",
  );
  assert.equal(state.messages.length, 1);
  state = applyEvent(
    state,
    {
      type: "agent_tool",
      id: "t",
      rid: "r",
      name: "read",
      args: { path: "a" },
      status: "running",
    },
    "s",
  );
  state = applyEvent(
    state,
    {
      type: "agent_tool",
      id: "t",
      rid: "r",
      name: "read",
      result: "ok",
      status: "succeeded",
    },
    "s",
  );
  assert.deepEqual(state.activities[0]?.args, { path: "a" });
  assert.equal(state.activities[0]?.result, "ok");
});

test("closed approvals disappear and are not left actionable", () => {
  const state = applyEvent(
    emptyChat(),
    { type: "agent_confirm", id: "c", text: "发布？" },
    "s",
  );
  assert.equal(state.confirmations.length, 1);
  assert.equal(
    applyEvent(
      state,
      { type: "agent_confirm_closed", id: "c", reason: "timeout" },
      "s",
    ).confirmations.length,
    0,
  );
});

test("connection generations reject stale frames from a previous session", () => {
  const { runtime, socket, sockets, urls } = harness();
  try {
    runtime.connect("session-b");
    socket.frame({ type: "agent_emit", text: "old", rid: "old" });
    const current = sockets[1]!;
    current.frame({ type: "init", v: 10 });
    current.frame({ type: "agent_emit", text: "new", rid: "new" });
    assert.deepEqual(
      runtime.getSnapshot().chat.messages.map((item) => item.text),
      ["new"],
    );
    assert.match(urls[1]!, /\/ws\?sid=session-b$/);
  } finally {
    runtime.disconnect();
  }
});

test("a protocol mismatch blocks commands and does not reconnect", async () => {
  const { runtime, socket, sockets } = harness();
  try {
    socket.frame({ type: "init", v: 9 });
    assert.equal(runtime.getSnapshot().connection, "incompatible");
    assert.throws(() => runtime.sendMessage("不要发送"), /连接/);
    await tick(5);
    assert.equal(sockets.length, 1);
  } finally {
    runtime.disconnect();
  }
});

test("a message is acknowledged only after a server pong", async () => {
  const { runtime, socket } = harness();
  try {
    let confirmed = false;
    const promise = runtime.sendMessage("核对消息").then(() => {
      confirmed = true;
    });
    await tick();
    assert.equal(confirmed, false);
    assert.equal(socket.sent[0]?.type, "agent");
    assert.equal(socket.sent[1]?.type, "ping");
    socket.frame({ type: "pong" });
    await promise;
    assert.equal(confirmed, true);
  } finally {
    runtime.disconnect();
  }
});

test("uncertain delivery never resends automatically after reconnect", async () => {
  const { runtime, socket, sockets } = harness({
    deliveryTimeout: 10,
    reconnectMs: 1,
  });
  try {
    await assert.rejects(runtime.sendMessage("只发一次"), /未确认送达/);
    socket.close();
    await tick(8);
    assert.equal(sockets.length, 2);
    sockets[1]!.frame({ type: "init", v: 10 });
    assert.equal(
      sockets
        .flatMap((item) => item.sent)
        .filter((frame) => frame.type === "agent").length,
      1,
    );
  } finally {
    runtime.disconnect();
  }
});

test("approvals require a matching live status and remain awaiting a close receipt", async () => {
  const { runtime, socket } = harness();
  try {
    socket.frame({ type: "agent_confirm", id: "c", text: "执行？" });
    const decision = runtime.decide("c", true);
    const check = socket.sent.at(-1)!;
    assert.equal(check.type, "agent_confirm_check");
    assert.equal(
      socket.sent.some((frame) => frame.type === "agent_confirm_response"),
      false,
    );
    socket.frame({
      type: "agent_confirm_state",
      id: "wrong",
      nonce: check.nonce,
      pending: true,
    });
    await tick();
    assert.equal(
      socket.sent.some((frame) => frame.type === "agent_confirm_response"),
      false,
    );
    socket.frame({
      type: "agent_confirm_state",
      id: "c",
      nonce: check.nonce,
      pending: true,
    });
    await decision;
    assert.deepEqual(socket.sent.at(-1), {
      type: "agent_confirm_response",
      id: "c",
      ok: true,
    });
    assert.equal(runtime.getSnapshot().chat.confirmations[0]?.sent, true);
    socket.frame({ type: "agent_confirm_closed", id: "c", reason: "answered" });
    assert.equal(runtime.getSnapshot().chat.confirmations.length, 0);
  } finally {
    runtime.disconnect();
  }
});

test("changing session during confirmation does not send a decision to the new session", async () => {
  const { runtime, socket, sockets } = harness();
  try {
    socket.frame({ type: "agent_confirm", id: "c", text: "执行？" });
    const decision = runtime.decide("c", true);
    const rejection = assert.rejects(decision, /断开/);
    const check = socket.sent.at(-1)!;
    runtime.connect("session-b");
    socket.frame({
      type: "agent_confirm_state",
      id: "c",
      nonce: check.nonce,
      pending: true,
    });
    sockets[1]!.frame({ type: "init", v: 10 });
    await rejection;
    assert.equal(
      sockets
        .flatMap((item) => item.sent)
        .filter((frame) => frame.type === "agent_confirm_response").length,
      0,
    );
  } finally {
    runtime.disconnect();
  }
});

test("reconnecting after a runtime restart accepts the new event sequence", async () => {
  const { runtime, socket, sockets } = harness({ reconnectMs: 1 });
  try {
    socket.frame({ type: "agent_emit", text: "旧进程", rid: "old", seq: 900 });
    socket.close();
    await tick(8);
    sockets[1]!.frame({ type: "init", v: 10 });
    sockets[1]!.frame({ type: "agent_history", items: [] });
    sockets[1]!.frame({
      type: "agent_emit",
      text: "新进程",
      rid: "new",
      seq: 1,
    });
    assert.deepEqual(
      runtime.getSnapshot().chat.messages.map((item) => item.text),
      ["新进程"],
    );
  } finally {
    runtime.disconnect();
  }
});
