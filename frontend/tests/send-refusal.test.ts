import assert from "node:assert/strict";
import test from "node:test";
import { MutationObserver, QueryClient } from "@tanstack/react-query";
import { createInstance } from "i18next";
import type { ApiClient } from "../packages/client/src/api/client";
import { ApiError, NetworkError } from "../packages/client/src/api/http";
import { keys } from "../packages/client/src/api/keys";
import { outbox } from "../packages/client/src/api/outbox";
import { flushOutbox, postMessageMutation } from "../packages/client/src/api/queries";
import type { Message } from "../packages/client/src/api/types";
import { sendFailureText, sendNeedsEdit } from "../packages/client/src/lib/errors";
import {
  agentWorkingIn,
  applyChannelEvent,
  dropProvisional,
  emptyStreams,
  expectReply,
} from "../packages/client/src/realtime/streams";
import { en } from "../packages/i18n/src/en";
import zhHans from "../packages/i18n/src/locales/zh-Hans.json";

const noModel = new ApiError(409, "no_ai", "Backend detail");

test("a no-model refusal keeps the retryable draft and explains how to recover", async () => {
  const qc = new QueryClient();
  const channelId = "assistant";
  const api = {
    chat: { post: async () => { throw noModel; } },
  } as unknown as ApiClient;
  const rejected: string[] = [];
  let streams = emptyStreams;
  const observer = new MutationObserver(
    qc,
    postMessageMutation(api, qc, "home", channelId, {
      me: { id: "owner" },
      onOptimistic: (local) => {
        streams = expectReply(streams, { channelId, threadId: local.id, agentId: "assistant", messageId: local.id });
        assert.equal(agentWorkingIn(streams, "assistant"), channelId);
      },
      onFailed: (error, { waits, local }) => {
        assert.equal(error, noModel);
        assert.equal(waits, false);
        assert.ok(local);
        rejected.push(local.id);
        streams = dropProvisional(streams, new Set([local.id]), local.channelId);
      },
    }),
  );
  try {
    await assert.rejects(observer.mutate({ clientId: "first", body: "Keep this draft" }), noModel);
    const [message] = qc.getQueryData<Message[]>(keys.messages("home", channelId)) ?? [];
    assert.deepEqual(rejected, ["local-first"]);
    assert.equal(agentWorkingIn(streams, "assistant"), null);
    assert.equal(message?.id, "local-first");
    assert.equal(message?.local, "failed");
    assert.equal(message?.body, "Keep this draft");
    assert.equal(sendNeedsEdit(message?.sendFailure), false);

    const i18n = createInstance();
    await i18n.init({ lng: "en", resources: { en: { translation: en }, "zh-Hans": { translation: zhHans } } });
    assert.equal(
      sendFailureText(i18n.getFixedT("en"), message?.sendFailure),
      "Not sent. Connect a model, then retry the message.",
    );
    assert.equal(sendFailureText(i18n.getFixedT("zh-Hans"), message?.sendFailure), "未发送。请先连接模型，再重试发送。");
  } finally {
    observer.reset();
    qc.clear();
  }
});

test("rejection leaves concurrent guesses and confirmed runs in place", () => {
  let streams = applyChannelEvent(emptyStreams, {
    type: "agent.run",
    status: "running",
    channelId: "assistant",
    threadId: "thread",
    triggerMessageId: "local-first",
    agentId: "confirmed-agent",
    runId: "confirmed-run",
  });
  streams = expectReply(streams, {
    channelId: "assistant", threadId: "thread", agentId: "first-agent", messageId: "local-first",
  });
  streams = expectReply(streams, {
    channelId: "assistant", threadId: "thread", agentId: "second-agent", messageId: "local-second",
  });
  streams = expectReply(streams, {
    channelId: "other", threadId: "other-thread", agentId: "other-agent", messageId: "local-first",
  });

  const next = dropProvisional(streams, new Set(["local-first"]), "assistant");
  assert.equal(agentWorkingIn(next, "first-agent"), null);
  assert.equal(agentWorkingIn(next, "second-agent"), "assistant");
  assert.equal(agentWorkingIn(next, "other-agent"), "other");
  assert.ok(next.runs["confirmed-run"]);
  assert.equal(next.runs["confirmed-run"], streams.runs["confirmed-run"]);
});

test("an unanswered send stays queued until a definitive refusal identifies its exact message", async () => {
  const qc = new QueryClient();
  const channelId = "delayed-assistant";
  const orgId = "delayed-home";
  const key = keys.messages(orgId, channelId);
  const offline = new NetworkError(new Error("network unavailable"));
  let failure: Error = offline;
  const api = {
    chat: { post: async () => { throw failure; } },
  } as unknown as ApiClient;
  qc.setQueryData(keys.me, { user: { id: "owner" } });
  const observer = new MutationObserver(
    qc,
    postMessageMutation(api, qc, orgId, channelId, {
      me: { id: "owner" },
      onFailed: (error, { waits, local }) => {
        assert.equal(error, offline);
        assert.equal(waits, true);
        assert.equal(local?.id, "local-delayed");
      },
    }),
  );
  const refused: string[] = [];
  const onRefused = (error: unknown, local: Message) => {
    assert.equal(error, noModel);
    refused.push(local.id);
  };
  try {
    await assert.rejects(observer.mutate({ clientId: "delayed", body: "Wait for connection" }), offline);
    assert.equal(await flushOutbox(api, qc, orgId, onRefused), true);
    assert.equal(outbox.has("delayed"), true);
    assert.deepEqual(refused, []);

    failure = noModel;
    assert.equal(await flushOutbox(api, qc, orgId, onRefused), false);
    assert.deepEqual(refused, ["local-delayed"]);
    assert.equal(outbox.has("delayed"), false);
    assert.equal(qc.getQueryData<Message[]>(key)?.[0]?.sendFailure?.code, "no_ai");
  } finally {
    outbox.remove("delayed");
    observer.reset();
    qc.clear();
  }
});
