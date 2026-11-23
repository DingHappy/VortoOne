import assert from "node:assert/strict";
import test from "node:test";
import { setTimeout as nextTurn } from "node:timers/promises";
import { isCancelledError, QueryClient, QueryObserver } from "@tanstack/react-query";
import type { ApiClient } from "../packages/client/src/api/client";
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

test("signing out from a different invitation clears receipts and discards an in-flight old response", async () => {
  const qc = new QueryClient();
  const pending = Promise.withResolvers<InvitationState>();
  const api = invitationApi(() => pending.promise);
  const old = invitationStateQuery(api, "old-invite", "recipient");
  const active = invitationStateQuery(api, "active-invite", "recipient");
  qc.setQueryData(old.queryKey, accepted);
  qc.setQueryData(keys.invitation("another-link"), { preview: true });
  qc.setQueryData(keys.me, { user: { id: "recipient" } });
  const request = assert.rejects(qc.fetchQuery(active), isCancelledError);
  try {
    await refreshInvitationAfterSignOut(qc, "another-link");
    pending.resolve(accepted);
    await request;

    assert.equal(qc.getQueryData(old.queryKey), undefined);
    assert.equal(qc.getQueryData(active.queryKey), undefined);
    assert.equal(qc.getQueryData(keys.me), null);
    assert.deepEqual(qc.getQueryData(keys.invitation("another-link")), { preview: true });
    assert.equal(qc.getQueryState(keys.invitation("another-link"))?.isInvalidated, true);
    const newSession = invitationStateQuery(invitationApi(async () => null), "old-invite", "other-account");
    assert.equal(await qc.fetchQuery(newSession), null);
  } finally {
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
