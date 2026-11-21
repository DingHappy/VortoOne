import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
import { QueryClient } from "@tanstack/react-query";
import { readDraftBook } from "@vortoone/client/lib/composer-drafts";
import { STORAGE_KEYS } from "../apps/web/src/lib/storage";
import { PUSH_OPEN_MESSAGE } from "../apps/web/src/lib/web-push";
import { outbox } from "../packages/client/src/api/outbox";

test("existing tabs retain unsent drafts, queued messages, and explicit logout", () => {
  const now = Date.now();
  const local = new Map([
    ["agentes.signedOut", "1"],
    ["agentes.drafts.person", JSON.stringify({ "channel:work": { text: "Unsent draft", at: now } })],
  ]);
  const message = { clientId: "local-message", channelId: "work", body: "Waiting to send", author: { id: "person" } };
  const session = new Map([["agentes.outbox", JSON.stringify([{ orgId: "home", message }])]]);

  assert.equal(local.get(STORAGE_KEYS.signedOut), "1");
  const raw = local.get(`${STORAGE_KEYS.composerDrafts}.person`) ?? null;
  assert.equal(readDraftBook(raw, now)["channel:work"]?.text, "Unsent draft");
  const stop = outbox.keep(new QueryClient(), {
    read: () => session.get(STORAGE_KEYS.outbox) ?? null,
    write: (value) => session.set(STORAGE_KEYS.outbox, value),
  });
  try {
    assert.deepEqual(outbox.of("home"), [message]);
  } finally {
    stop();
    outbox.remove(message.clientId);
  }
});

test("an updated notification worker can still ask an existing tab to open a notification", async () => {
  const origin = "https://vortoone.example";
  const messages: { type: string; url: string }[] = [];
  const click = {
    notification: { data: { url: "/inbox" }, close() {} },
    pending: Promise.resolve(),
    waitUntil(pending: Promise<void>) { this.pending = pending; },
  };
  const self = {
    location: { origin },
    clients: {
      matchAll: async () => [{
        url: `${origin}/`,
        focused: true,
        focus: async () => {},
        postMessage: (message: (typeof messages)[number]) => messages.push(message),
      }],
    },
    addEventListener(name: string, handler: (event: typeof click) => void) {
      if (name === "notificationclick") handler(click);
    },
  };
  const script = readFileSync(new URL("../apps/web/public/sw.js", import.meta.url), "utf8");
  runInNewContext(script, { self, URL });
  await click.pending;

  assert.equal(messages.length, 1);
  assert.equal(messages[0]?.url, "/inbox");
  assert.equal(messages[0]?.type, "agentes.push.open");
  assert.equal(messages[0]?.type, PUSH_OPEN_MESSAGE);
});
