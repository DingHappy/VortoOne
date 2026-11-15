// Keep the existing runtime's transcript and activity interpretation in one place.
import type { ConversationMessage, ProtocolEvent, TurnActivity } from "./types";
import {
  hydrateActivities,
  protocolActivity,
  upsertActivity,
} from "./protocol/activities";
import {
  historyMessages,
  promptQueueFromEvent,
  withAssistantReply,
  withRunningPrompt,
} from "./protocol/transcript";

export type { ProtocolEvent };
export interface Confirmation {
  id: string;
  text: string;
  rid?: string;
  sent?: boolean;
}
export interface ChatState {
  messages: ConversationMessage[];
  activities: TurnActivity[];
  stream: string;
  streamRid?: string;
  activeRid?: string;
  busy: boolean;
  confirmations: Confirmation[];
  queue: { id: string; text: string }[];
  lastSeq: number;
}
export const emptyChat = (): ChatState => ({
  messages: [],
  activities: [],
  stream: "",
  busy: false,
  confirmations: [],
  queue: [],
  lastSeq: 0,
});

export function applyEvent(
  state: ChatState,
  event: ProtocolEvent,
  sid: string,
): ChatState {
  if (event.type === "agent_events") {
    return (event.items ?? []).reduce<ChatState>(
      (next, item) =>
        item && typeof item === "object"
          ? applyEvent(next, item as ProtocolEvent, sid)
          : next,
      state,
    );
  }
  if (typeof event.seq === "number" && event.seq <= state.lastSeq) return state;
  const next = { ...state, lastSeq: Math.max(state.lastSeq, event.seq ?? 0) };
  const belongs =
    !event.rid || !state.activeRid || event.rid === state.activeRid;
  switch (event.type) {
    case "agent_history":
      return { ...next, messages: historyMessages(event, sid) };
    case "agent_activity_history":
      return {
        ...next,
        activities: hydrateActivities(event.items ?? [], {
          missingStatus: "unknown",
        }),
      };
    case "agent_queue": {
      const { queued, running } = promptQueueFromEvent(event);
      return {
        ...next,
        queue: queued,
        busy: !!running,
        activeRid: running?.id,
        messages: running
          ? withRunningPrompt(next.messages, running)
          : next.messages,
        ...(!running ? { stream: "", streamRid: undefined } : {}),
      };
    }
    case "agent_stream":
      return belongs
        ? {
            ...next,
            stream: String(event.text ?? ""),
            streamRid: event.rid,
            activeRid: event.rid ?? state.activeRid,
            busy: true,
          }
        : next;
    case "agent_emit":
      return {
        ...next,
        messages: withAssistantReply(next.messages, event),
        ...(belongs ? { stream: "", streamRid: undefined } : {}),
      };
    case "agent_phase":
    case "agent_tool":
    case "agent_hook": {
      const activity = protocolActivity(event);
      return activity
        ? { ...next, activities: upsertActivity(next.activities, activity) }
        : next;
    }
    case "agent_confirm": {
      if (!event.id) return next;
      const item = {
        id: event.id,
        text: String(event.text ?? ""),
        rid: event.rid,
      };
      return {
        ...next,
        confirmations: [
          ...next.confirmations.filter((c) => c.id !== item.id),
          item,
        ],
      };
    }
    case "agent_confirm_closed":
      return {
        ...next,
        confirmations: next.confirmations.filter((c) => c.id !== event.id),
      };
    case "agent_error":
    case "agent_cancelled":
      return {
        ...next,
        messages: [
          ...next.messages,
          {
            id: `${event.type}-${event.rid ?? ""}-${event.seq ?? next.messages.length}`,
            role: "system",
            text:
              event.type === "agent_error"
                ? String(event.text ?? "执行失败")
                : "本回合已停止",
            rid: event.rid,
          },
        ],
        ...(belongs
          ? {
              busy: false,
              stream: "",
              streamRid: undefined,
              activeRid: undefined,
            }
          : {}),
      };
    case "agent_done":
      return belongs
        ? {
            ...next,
            busy: false,
            stream: "",
            streamRid: undefined,
            activeRid: undefined,
          }
        : next;
    default:
      return next;
  }
}
