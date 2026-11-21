import assert from "node:assert/strict";
import test from "node:test";
import {
  buildTimeline,
  upsertMessage,
  withLocalCopies,
} from "../packages/client/src/lib/timeline";
import type { Message } from "../packages/client/src/api/types";

const question: Message = {
  id: "ffffffff-ffff-5fff-8fff-ffffffffffff",
  channelId: "main",
  threadId: null,
  author: { type: "person", id: "owner" },
  body: "question",
  mentionedAgentIds: [],
  agentRunId: null,
  createdAt: "2026-11-16T14:00:00.000Z",
  sequence: 1,
};
const answer: Message = {
  ...question,
  id: "00000000-0000-5000-8000-000000000000",
  body: "answer",
  author: { type: "agent", id: "assistant" },
  agentRunId: "run",
  sequence: 2,
};
const bodies = (rows: Message[]) =>
  buildTimeline(rows, [], []).flatMap((item) =>
    item.kind === "message" ? [item.message.body] : [],
  );

test("a same-millisecond reply stays after its question live and after a reload", () => {
  let live = upsertMessage([], question);
  live = upsertMessage(live, answer);
  const { sequence: _sequence, ...echo } = question;
  live = upsertMessage(live, { ...echo, clientId: "sent", createdAt: "2026-11-16T14:00:00.123Z" });
  assert.deepEqual(bodies(live), ["question", "answer"]);
  assert.equal(live[0]?.createdAt, question.createdAt);
  assert.deepEqual(
    bodies(withLocalCopies([question, answer], live, () => false)),
    ["question", "answer"],
  );
});

test("an unsequenced card at the same time sorts consistently after the transcript records", () => {
  const { sequence: _sequence, ...card } = answer;
  assert.deepEqual(bodies([answer, { ...card, id: "middle", body: "card" }, question]), ["question", "answer", "card"]);
});

test("servers without transcript sequences keep their time and id ordering", () => {
  const { sequence: _q, ...q } = question;
  const { sequence: _a, ...a } = answer;
  assert.deepEqual(bodies([q, a]), ["answer", "question"]);
  assert.deepEqual(
    bodies([a, { ...q, createdAt: "2026-11-16T13:59:59.000Z" }]),
    ["question", "answer"],
  );
});
