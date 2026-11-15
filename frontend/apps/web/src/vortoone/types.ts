// Portable subset of desktop/src/types.ts for the existing runtime protocol.
export interface ProtocolEvent {
  type: string;
  recorded_at?: string;
  rid?: string;
  seq?: number;
  v?: number;
  cursor?: number;
  earliest_seq?: number;
  latest_seq?: number;
  truncated?: boolean;
  data?: unknown;
  text?: string;
  title?: string;
  diff?: string;
  id?: string;
  name?: string;
  event?: string;
  tool?: string;
  phase?: string;
  status?: string;
  label?: string;
  summary?: string;
  args?: Record<string, unknown>;
  result?: unknown;
  duration_ms?: number;
  ok?: boolean;
  path?: string;
  message?: string;
  error?: string;
  stop_execution?: boolean;
  sha256?: string;
  tainted?: boolean;
  website?: unknown;
  resources?: unknown;
  scope?: "general" | "scratch" | "project";
  reason?: string;
  nonce?: string;
  pending?: boolean;
  task?: string;
  items?: unknown[];
  running?: PromptQueueItem;
  paused?: boolean;
  baseline?: string;
  source_revision?: string;
  head?: string;
  files?: number;
  paths?: string[];
}

export interface PromptQueueItem {
  id: string;
  version: number;
  text: string;
  mode: "plan" | "build";
  position: number;
  created_at: string;
  context_count: number;
}

export interface ConversationMessage {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  rid?: string;
  /** 从哪个入口来的（如 telegram）；电脑上的不带 */
  source?: string;
  /** 本次会话里随消息发出的图片（data URL，只在内存里，不落盘；重开会话后只剩「🖼×N」标记） */
  images?: string[];
}

export type TurnActivityStatus =
  | "running"
  | "completed"
  | "succeeded"
  | "failed"
  | "blocked"
  | "cancelled"
  | string;

export interface TurnActivity {
  id: string;
  rid?: string;
  kind: "phase" | "tool" | "hook" | "event";
  status: TurnActivityStatus;
  label: string;
  phase?: string;
  name?: string;
  event?: string;
  tool?: string;
  error?: string;
  args?: Record<string, unknown>;
  result?: string;
  durationMs?: number;
}
