import assert from "node:assert/strict";
import test from "node:test";
import { setTimeout as nextTurn } from "node:timers/promises";
import { isCancelledError, QueryClient, QueryObserver } from "@tanstack/react-query";
import type { ApiClient } from "../packages/client/src/api/client";
import { createHttpApi } from "../packages/client/src/api/http-client";
import { keys } from "../packages/client/src/api/keys";
import { invitationStateQuery, refreshInvitationAfterSignOut } from "../packages/client/src/api/queries";
import type { InvitationState } from "../packages/client/src/api/types";

const accepted: InvitationState = {
  state: "accepted",
  orgId: "workspace",
  orgName: "Test workspace",
  at: "2026-11-22T00:00:00Z",
};

function invitationApi(read: () => Promise<InvitationState | null>): ApiClient {
  return { orgs: { invitationState: read } } as unknown as ApiClient;
}

test("the session-scoped receipt query opts into accepted state while legacy and guest requests stay public", async () => {
  const calls: Array<{ url: string; credentials?: RequestCredentials }> = [];
  const api = createHttpApi({
    fetch: async (url, init) => {
      calls.push({ url, credentials: init.credentials });
      return url.includes("?receipt=1") ? Response.json(accepted) : Response.json({}, { status: 404 });
    },
  }) as ApiClient;
  const qc = new QueryClient();
  try {
    assert.equal(await api.orgs.invitationState("token"), null);
    assert.equal(await qc.fetchQuery(invitationStateQuery(api, "token", null)), null);
    assert.deepEqual(await qc.fetchQuery(invitationStateQuery(api, "token", "recipient")), accepted);
    const path = "/api/app/invitations/token/state";
    assert.deepEqual(calls, [
      { url: path, credentials: "same-origin" },
      { url: path, credentials: "same-origin" },
      { url: path + "?receipt=1", credentials: "same-origin" },
    ]);
  } finally {
    qc.clear();
  }
});

test("signing out from a different invitation clears receipts and discards an in-flight old response", async () => {
  const qc = new QueryClient();
  const pending = Promise.withResolvers<InvitationState>();
  const preview = Promise.withResolvers<{ you?: { email: string } }>();
  const api = invitationApi(() => pending.promise);
  const old = invitationStateQuery(api, "old-invite", "recipient");
  const active = invitationStateQuery(api, "active-invite", "recipient");
  const currentPreview = new QueryObserver(qc, {
    queryKey: keys.invitation("another-link"),
    queryFn: () => preview.promise,
    staleTime: Infinity,
  });
  qc.setQueryData(old.queryKey, accepted);
  qc.setQueryData(keys.invitation("old-invite"), { you: { email: "recipient@example.test" } });
  qc.setQueryData(keys.invitation("another-link"), { you: { email: "recipient@example.test" } });
  qc.setQueryData(keys.authConfig, { password: true });
  qc.setQueryData(keys.me, { user: { id: "recipient" } });
  const unsubscribe = currentPreview.subscribe(() => {});
  const request = assert.rejects(qc.fetchQuery(active), isCancelledError);
  try {
    const reloaded = refreshInvitationAfterSignOut(qc, "another-link");
    assert.equal(currentPreview.getCurrentResult().data, undefined);
    assert.equal(currentPreview.getCurrentResult().isPending, true);
    preview.resolve({});
    await reloaded;
    pending.resolve(accepted);
    await request;

    assert.equal(qc.getQueryData(old.queryKey), undefined);
    assert.equal(qc.getQueryData(active.queryKey), undefined);
    assert.equal(qc.getQueryData(keys.me), null);
    assert.equal(qc.getQueryData(keys.invitation("old-invite")), undefined);
    assert.deepEqual(qc.getQueryData(keys.invitation("another-link")), {});
    assert.deepEqual(qc.getQueryData(keys.authConfig), { password: true });
    const newSession = invitationStateQuery(invitationApi(async () => null), "old-invite", "other-account");
    assert.equal(await qc.fetchQuery(newSession), null);
  } finally {
    unsubscribe();
    qc.clear();
  }
});

test("back navigation rechecks an invitation and a failed request cannot restore its old receipt", async () => {
  const qc = new QueryClient();
  let read = async (): Promise<InvitationState | null> => accepted;
  const options = invitationStateQuery(invitationApi(() => read()), "old-invite", "recipient");
  const first = new QueryObserver(qc, options);
  const leave = first.subscribe(() => {});
  assert.deepEqual((await first.refetch()).data, accepted);
  leave();
  // An unobserved invitation status is evicted rather than reused on browser Back.
  await nextTurn();
  assert.equal(qc.getQueryData(options.queryKey), undefined);

  const response = Promise.withResolvers<InvitationState | null>();
  read = () => response.promise;
  const back = new QueryObserver(qc, options);
  const unsubscribe = back.subscribe(() => {});
  try {
    assert.equal(back.getCurrentResult().data, undefined);
    assert.equal(back.getCurrentResult().isPending, true);
    const failure = assert.rejects(back.refetch({ throwOnError: true }), /unavailable/);
    response.reject(new Error("unavailable"));
    await failure;
    assert.equal(back.getCurrentResult().isError, true);
    assert.equal(back.getCurrentResult().data, undefined);
  } finally {
    unsubscribe();
    qc.clear();
  }
});
