import assert from "node:assert/strict";
import test from "node:test";
import { MutationObserver, QueryClient } from "@tanstack/react-query";
import { sendDmMutation } from "../packages/client/src/api/queries";
import { keys } from "../packages/client/src/api/keys";
import type { ApiClient } from "../packages/client/src/api/client";
import type { Message } from "../packages/client/src/api/types";
import { buildTimeline } from "../packages/client/src/lib/timeline";

for (const readFails of [false, true]) {
  test(`opening a DM after send reads canonical history; read failure=${readFails}`, async () => {
    const qc = new QueryClient({ defaultOptions: {
      queries: { retry: false, gcTime: 0 }, mutations: { retry: false, gcTime: 0 },
    } });
    const question: Message = {
      id: "ffffffff-ffff-5fff-8fff-ffffffffffff", channelId: "main", threadId: null,
      author: { type: "person", id: "owner" }, body: "question", mentionedAgentIds: [],
      agentRunId: null, createdAt: "2026-11-16T14:00:00.000Z", sequence: 1,
    };
    const answer: Message = { ...question, id: "00000000-0000-5000-8000-000000000000",
      body: "answer", author: { type: "agent", id: "assistant" }, agentRunId: "run", sequence: 2 };
    let posts = 0;
    const api = {
      dms: { open: async () => ({ channelId: "main" }) },
      chat: {
        post: async () => {
          posts++;
          const { sequence: _sequence, ...echo } = question;
          return { ...echo, createdAt: "2026-11-16T14:00:00.123Z" };
        },
        messages: async () => {
          if (readFails) throw new Error("temporarily unavailable");
          return [question, answer];
        },
      },
    } as unknown as ApiClient;
    const observer = new MutationObserver(qc, sendDmMutation(api, qc, "org"));
    try {
      await observer.mutate({ agentId: "assistant", body: "question" });
      assert.equal(observer.getCurrentResult().status, "success");
      assert.equal(posts, 1);
      if (!readFails) {
        const list = qc.getQueryData<Message[]>(keys.messages("org", "main"))!;
        assert.deepEqual(buildTimeline(list, [], []).flatMap(item =>
          item.kind === "message" ? [item.message.body] : []), ["question", "answer"]);
      } else {
        assert.equal(qc.getQueryState(keys.messages("org", "main"))?.isInvalidated, true);
      }
    } finally {
      observer.reset();
      qc.clear();
    }
  });
}
