import {
  applyEvent,
  emptyChat,
  type ChatState,
  type ProtocolEvent,
} from "./protocol";

export const PROTOCOL_VERSION = 10;
export type Connection =
  | "disconnected"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "incompatible";
export interface RuntimeState {
  sid: string;
  connection: Connection;
  chat: ChatState;
  error: string;
}
export interface Session {
  sid: string;
  title?: string;
  status?: string;
  activity?: string;
  updated_at?: string;
  message_count?: number;
}
export type RecordItem = Record<string, unknown>;

export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(path, {
    ...options,
    credentials: "same-origin",
    signal: options.signal ?? AbortSignal.timeout(15_000),
    headers: {
      ...options.headers,
      ...(options.body ? { "Content-Type": "application/json" } : {}),
    },
  });
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail =
      data && typeof data === "object" && "detail" in data
        ? String(data.detail)
        : `请求失败（${response.status}）`;
    throw new Error(response.status === 401 ? "请先登录运行端" : detail);
  }
  return data as T;
}

interface RuntimeOptions {
  location?: { protocol: string; host: string };
  socket?: (url: string) => WebSocket;
  deliveryTimeout?: number;
  heartbeatMs?: number;
  reconnectMs?: number;
}

/** Browser-only transport for the existing /ws protocol. It never owns execution or replays writes. */
export class VortoRuntime {
  private state: RuntimeState = {
    sid: "",
    connection: "disconnected",
    chat: emptyChat(),
    error: "",
  };
  private listeners = new Set<() => void>();
  private socket: WebSocket | null = null;
  private generation = 0;
  private retry = 0;
  private retryTimer?: ReturnType<typeof setTimeout>;
  private heartbeat?: ReturnType<typeof setInterval>;
  private handshake?: ReturnType<typeof setTimeout>;
  private lastReceived = 0;
  private decisions = new Set<string>();
  private deliveries = new Set<{
    resolve: () => void;
    reject: (e: Error) => void;
    timer: ReturnType<typeof setTimeout>;
  }>();
  private checks = new Map<
    string,
    {
      id: string;
      resolve: (pending: boolean) => void;
      reject: (e: Error) => void;
      timer: ReturnType<typeof setTimeout>;
    }
  >();
  constructor(private options: RuntimeOptions = {}) {}
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  getSnapshot = () => this.state;
  private update(patch: Partial<RuntimeState>) {
    this.state = { ...this.state, ...patch };
    for (const listener of this.listeners) listener();
  }
  clearError = () => this.update({ error: "" });
  private failPending() {
    for (const item of this.deliveries) {
      clearTimeout(item.timer);
      item.reject(
        new Error("未确认送达。请重连后先核对聊天记录，再决定是否重发。"),
      );
    }
    this.deliveries.clear();
    for (const item of this.checks.values()) {
      clearTimeout(item.timer);
      item.reject(new Error("连接已断开，尚未提交决定。"));
    }
    this.checks.clear();
  }
  disconnect = () => {
    ++this.generation;
    clearTimeout(this.retryTimer);
    clearTimeout(this.handshake);
    clearInterval(this.heartbeat);
    this.failPending();
    this.socket?.close();
    this.socket = null;
    this.update({ connection: "disconnected" });
  };
  connect = (sid: string) => {
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(sid)) throw new Error("会话标识无效");
    this.disconnect();
    this.retry = 0;
    this.update({
      sid,
      chat: emptyChat(),
      error: "",
      connection: "connecting",
    });
    this.open(this.generation);
  };
  private open(generation: number) {
    // Reconnect rehydrates from the server. A restarted process can start its event sequence over.
    this.update({ chat: { ...this.state.chat, lastSeq: 0 } });
    const location = this.options.location ?? window.location;
    const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws?sid=${encodeURIComponent(this.state.sid)}`;
    const socket = (this.options.socket ?? ((url) => new WebSocket(url)))(url);
    this.socket = socket;
    const current = () =>
      generation === this.generation && socket === this.socket;
    this.lastReceived = Date.now();
    this.handshake = setTimeout(() => {
      if (current() && this.state.connection !== "connected") socket.close();
    }, 12_000);
    socket.onmessage = (event) => {
      if (!current()) return;
      let frame: ProtocolEvent;
      try {
        frame = JSON.parse(String(event.data)) as ProtocolEvent;
      } catch {
        return;
      }
      if (!frame || typeof frame.type !== "string") return;
      this.lastReceived = Date.now();
      if (
        frame.type === "init" ||
        (frame.type === "status" && frame.v !== undefined)
      ) {
        if (frame.v !== PROTOCOL_VERSION) {
          this.update({
            connection: "incompatible",
            error: `运行端协议 ${frame.v ?? "未知"} 与前端协议 ${PROTOCOL_VERSION} 不兼容。请更新后重新连接。`,
          });
          socket.close();
          return;
        }
        clearTimeout(this.handshake);
        this.retry = 0;
        this.update({ connection: "connected", error: "" });
      }
      if (frame.type === "pong") {
        for (const item of this.deliveries) {
          clearTimeout(item.timer);
          item.resolve();
        }
        this.deliveries.clear();
      }
      if (frame.type === "agent_confirm_state") {
        const nonce = typeof frame.nonce === "string" ? frame.nonce : "";
        const check = this.checks.get(nonce);
        if (
          check &&
          check.id === frame.id &&
          typeof frame.pending === "boolean"
        ) {
          clearTimeout(check.timer);
          this.checks.delete(nonce);
          check.resolve(frame.pending);
        }
      }
      this.update({ chat: applyEvent(this.state.chat, frame, this.state.sid) });
    };
    socket.onopen = () => {
      if (!current()) return;
      clearInterval(this.heartbeat);
      this.heartbeat = setInterval(() => {
        if (!current()) return;
        if (
          Date.now() - this.lastReceived >
          (this.options.heartbeatMs ?? 10_000) * 3
        ) {
          socket.close();
          return;
        }
        if (socket.readyState === 1)
          socket.send(JSON.stringify({ type: "ping" }));
      }, this.options.heartbeatMs ?? 10_000);
    };
    socket.onerror = () => {
      /* onclose owns reconnect, including failed handshakes. */
    };
    socket.onclose = () => {
      if (!current()) return;
      clearTimeout(this.handshake);
      clearInterval(this.heartbeat);
      this.failPending();
      if (this.state.connection === "incompatible") return;
      this.update({
        connection: "reconnecting",
        error: "运行端连接已断开，正在重连。",
      });
      const delay = Math.min(
        30_000,
        (this.options.reconnectMs ?? 1000) * 2 ** Math.min(this.retry++, 5),
      );
      this.retryTimer = setTimeout(() => {
        if (current()) this.open(generation);
      }, delay);
    };
  }
  private send(frame: Record<string, unknown>) {
    if (this.state.connection !== "connected" || this.socket?.readyState !== 1)
      throw new Error("请先连接运行端");
    this.socket.send(JSON.stringify(frame));
  }
  sendMessage(text: string, mode: "plan" | "build" = "plan") {
    const rid = crypto.randomUUID();
    // The server's queue/history establishes the authoritative transcript.
    this.send({ type: "agent", text, mode, rid });
    return new Promise<void>((resolve, reject) => {
      const item = {
        resolve,
        reject,
        timer: setTimeout(() => {
          this.deliveries.delete(item);
          reject(new Error("未确认送达。请先核对聊天记录，再决定是否重发。"));
        }, this.options.deliveryTimeout ?? 10_000),
      };
      this.deliveries.add(item);
      try {
        this.send({ type: "ping" });
      } catch (error) {
        clearTimeout(item.timer);
        this.deliveries.delete(item);
        reject(error);
      }
    });
  }
  stop = () => this.send({ type: "agent_cancel" });
  removeQueued = (id: string) => this.send({ type: "agent_queue_remove", id });
  async decide(id: string, ok: boolean) {
    if (
      this.decisions.has(id) ||
      this.state.chat.confirmations.find((c) => c.id === id)?.sent
    )
      throw new Error("这项决定已经提交或正在提交，请等待回执。");
    if (!this.state.chat.confirmations.some((c) => c.id === id))
      throw new Error("这项确认已结束。");
    this.decisions.add(id);
    try {
      const generation = this.generation;
      const nonce = crypto.randomUUID();
      const pending = await new Promise<boolean>((resolve, reject) => {
        const timer = setTimeout(() => {
          this.checks.delete(nonce);
          reject(new Error("未收到确认状态，尚未提交决定，请重连后再核对。"));
        }, 8000);
        this.checks.set(nonce, { id, resolve, reject, timer });
        try {
          this.send({ type: "agent_confirm_check", id, nonce });
        } catch (error) {
          clearTimeout(timer);
          this.checks.delete(nonce);
          reject(error);
        }
      });
      if (generation !== this.generation)
        throw new Error("会话已切换，尚未提交决定");
      if (!pending) {
        this.update({
          chat: {
            ...this.state.chat,
            confirmations: this.state.chat.confirmations.filter(
              (c) => c.id !== id,
            ),
          },
        });
        throw new Error("这项确认已结束，请核对当前状态。");
      }
      this.send({ type: "agent_confirm_response", id, ok });
      this.update({
        chat: {
          ...this.state.chat,
          confirmations: this.state.chat.confirmations.map((c) =>
            c.id === id ? { ...c, sent: true } : c,
          ),
        },
      });
    } finally {
      this.decisions.delete(id);
    }
  }
}
