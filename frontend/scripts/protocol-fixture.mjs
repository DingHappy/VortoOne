// Explicitly launched, isolated UI validation service. No model, files, or live runtime are used.
import { createServer } from "node:http";
import { WebSocketServer } from "ws";

const port = Number(process.env.VORTO_FIXTURE_PORT || 8089);
const counters = { messages: 0, decisions: 0, stops: 0 };
const server = createServer((request, response) => {
  const path = new URL(request.url, "http://127.0.0.1").pathname;
  const data = {
    "/api/auth/status": { auth_required: false, authed: true },
    "/api/assistant/profile": { name: "VortoOne · 协议验证" },
    "/api/assistant/main-chat": { sid: "ui-validation" },
    "/api/agent/sessions": {
      sessions: [
        { sid: "ui-validation", title: "前端链路验证", status: "idle" },
      ],
    },
    "/api/tasks": {
      tasks: [
        {
          id: "fixture-task",
          title: "验证任务列表读取",
          status: "running",
          description: "仅存在于独立测试服务中的验证记录。",
        },
      ],
    },
    "/api/assistant/goals": [],
    "/api/assistant/memories": { memories: [] },
    "/api/assistant/schedule": { items: [] },
    "/__validation": counters,
  };
  response.writeHead(path in data ? 200 : 404, {
    "Content-Type": "application/json",
  });
  response.end(
    JSON.stringify(data[path] ?? { detail: "Unknown fixture endpoint" }),
  );
});
const wss = new WebSocketServer({ noServer: true });
server.on("upgrade", (request, socket, head) => {
  if (new URL(request.url, "http://127.0.0.1").pathname !== "/ws")
    return socket.destroy();
  wss.handleUpgrade(request, socket, head, (ws) => wss.emit("connection", ws));
});
wss.on("connection", (socket) => {
  const messages = [];
  const queued = [];
  let running = null;
  let confirmation = null;
  const timers = new Set();
  const send = (frame) => {
    if (socket.readyState === 1) socket.send(JSON.stringify(frame));
  };
  const later = (fn, ms) => {
    const timer = setTimeout(() => {
      timers.delete(timer);
      fn();
    }, ms);
    timers.add(timer);
  };
  const queue = () =>
    send({ type: "agent_queue", items: queued, running, paused: false });
  const finish = (text) => {
    messages.push({ role: "assistant", text, rid: running?.id });
    send({ type: "agent_emit", text, rid: running?.id });
    send({ type: "agent_done", rid: running?.id });
    running = null;
    queue();
  };
  send({ type: "init", v: 10, data: {} });
  send({ type: "agent_history", items: messages });
  send({ type: "agent_activity_history", items: [] });
  queue();
  socket.on("message", (bytes) => {
    let frame;
    try {
      frame = JSON.parse(bytes.toString());
    } catch {
      return;
    }
    if (frame.type === "ping") send({ type: "pong" });
    if (frame.type === "agent") {
      counters.messages++;
      const item = {
        id: frame.rid,
        text: frame.text,
        mode: frame.mode,
        version: 0,
        position: 0,
        created_at: new Date().toISOString(),
        context_count: 0,
      };
      if (running) {
        queued.push(item);
        queue();
        return;
      }
      running = item;
      messages.push({ role: "user", text: frame.text, rid: frame.rid });
      send({ type: "agent_history", items: messages });
      queue();
      send({
        type: "agent_tool",
        id: `tool-${frame.rid}`,
        rid: frame.rid,
        name: "ui_protocol_validation",
        summary: "核对本地协议回执",
        status: "succeeded",
        result: "未调用模型或执行外部操作",
      });
      if (frame.text.includes("确认")) {
        confirmation = { id: `confirm-${frame.rid}`, rid: frame.rid };
        send({
          type: "agent_confirm",
          ...confirmation,
          text: "本地协议测试：是否批准这次无副作用的验证操作？",
        });
      } else {
        const text =
          "已收到前端协议验证消息。\n\n**流式回复**、工具过程和最终回执已到达。\n\n```ts\nconst connected = true;\n```";
        later(
          () =>
            send({
              type: "agent_stream",
              rid: frame.rid,
              text: text.slice(0, 13),
            }),
          100,
        );
        later(() => send({ type: "agent_stream", rid: frame.rid, text }), 350);
        later(() => finish(text), frame.text.includes("停止") ? 15_000 : 900);
      }
    }
    if (frame.type === "agent_confirm_check")
      send({
        type: "agent_confirm_state",
        id: frame.id,
        nonce: frame.nonce,
        pending: confirmation?.id === frame.id,
      });
    if (
      frame.type === "agent_confirm_response" &&
      confirmation?.id === frame.id
    ) {
      counters.decisions++;
      send({ type: "agent_confirm_closed", id: frame.id, reason: "answered" });
      confirmation = null;
      finish(
        frame.ok
          ? "批准回执已验证；测试服务没有执行外部操作。"
          : "拒绝回执已验证；测试服务没有执行外部操作。",
      );
    }
    if (frame.type === "agent_cancel") {
      counters.stops++;
      for (const timer of timers) clearTimeout(timer);
      timers.clear();
      if (confirmation)
        send({
          type: "agent_confirm_closed",
          id: confirmation.id,
          reason: "cancelled",
        });
      confirmation = null;
      send({ type: "agent_cancelled", rid: running?.id });
      running = null;
      queue();
    }
    if (frame.type === "agent_queue_remove") {
      const index = queued.findIndex((item) => item.id === frame.id);
      if (index >= 0) queued.splice(index, 1);
      queue();
    }
  });
  socket.on("close", () => {
    for (const timer of timers) clearTimeout(timer);
  });
});
server.listen(port, "127.0.0.1", () =>
  console.log(`Isolated protocol fixture: http://127.0.0.1:${port}`),
);
