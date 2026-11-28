import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import {
  type ChannelRecommendation,
  type ChannelRecommendationDispatch,
  keys,
} from "@vortoone/client";
import { looksLikeApiKey } from "@vortoone/client/lib/api-key-text";
import { errorCode } from "@vortoone/client/lib/errors";
import { overLimitText } from "@vortoone/client/lib/message-length";
import { Button, Dialog, DialogBody, DialogFooter, Icon } from "@vortoone/ui";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useApi } from "@/api/context";
import "./channel-assignee-recommendation.css";

interface Draft {
  orgId: string;
  channelId: string;
  text: string;
}

interface Confirmation {
  orgId: string;
  channelId: string;
  token: string;
}

const MAX_DRAFT_LENGTH = 4000;
const STALE_CONFIRMATION_CODES = new Set([
  "recommendation.expired",
  "recommendation.changed",
  "invalid",
  "not_found",
  "forbidden",
]);

export function ChannelAssigneeRecommendation({
  orgId,
  channelId,
  text,
}: Draft) {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const queryClient = useQueryClient();
  const [keyInDraft, setKeyInDraft] = useState(false);
  const [closed, setClosed] = useState(false);
  const { data, isError, isPending, mutate, reset, variables } = useMutation({
    mutationFn: (draft: Draft): Promise<ChannelRecommendation> =>
      api.chat.recommendation(draft.orgId, draft.channelId, draft.text),
    meta: { silent: true },
    gcTime: 0,
    networkMode: "always",
    retry: false,
  });
  const confirmationOptions = {
    mutationFn: ({ orgId, channelId, token }: Confirmation) =>
      api.chat.confirmRecommendation(orgId, channelId, token),
    meta: { silent: true },
    networkMode: "always" as const,
    retry: false,
    gcTime: 0,
    onSuccess: (
      result: ChannelRecommendationDispatch,
      { orgId }: Confirmation,
    ) => {
      if (result.issueId || result.issueNumber)
        void queryClient.invalidateQueries({ queryKey: keys.issues(orgId) });
    },
  };
  const confirmation = useMutation(confirmationOptions);
  const refresh = useMutation(confirmationOptions);
  const { reset: resetConfirmation } = confirmation;
  const { reset: resetRefresh } = refresh;

  // A changed draft cannot confirm the recommendation for the previous text.
  useEffect(() => {
    reset();
    resetConfirmation();
    resetRefresh();
  }, [orgId, channelId, text, reset, resetConfirmation, resetRefresh]);
  const currentPreview =
    variables?.orgId === orgId &&
    variables.channelId === channelId &&
    variables.text === text;
  const open = !closed && currentPreview;
  // Python counts Unicode code points rather than UTF-16 code units.
  const length =
    text.length > MAX_DRAFT_LENGTH ? Array.from(text).length : text.length;
  const tooLong = length > MAX_DRAFT_LENGTH;
  const eligible =
    text !== "" && !text.startsWith("/") && !/[@＠]/u.test(text) && !tooLong;
  const isCollaboration = data?.kind === "collaboration";
  const AssigneeList = isCollaboration ? "ol" : "ul";
  const token =
    currentPreview &&
    data?.primary &&
    data.kind !== "none" &&
    data.kind !== "clarify"
      ? data.confirmationToken
      : undefined;
  const matchesDraft = (sent: Confirmation | undefined) =>
    Boolean(
      token &&
      sent?.orgId === orgId &&
      sent.channelId === channelId &&
      sent.token === token,
    );
  const attempted = matchesDraft(confirmation.variables);
  const dispatchError = attempted && confirmation.error;
  const dispatching = attempted && confirmation.isPending;
  const stale = Boolean(
    dispatchError &&
    STALE_CONFIRMATION_CODES.has(errorCode(dispatchError) ?? ""),
  );
  const currentRefresh = matchesDraft(refresh.variables);
  const receipt =
    (currentRefresh && refresh.data) ||
    (attempted && confirmation.data) ||
    undefined;
  const inProgress =
    receipt?.status === "agent_started" || receipt?.status === "awaiting_draft";
  const requestPreview = () => {
    if (!eligible) return;
    if (looksLikeApiKey(text)) {
      setKeyInDraft(true);
      return;
    }
    setKeyInDraft(false);
    setClosed(false);
    resetConfirmation();
    resetRefresh();
    mutate({ orgId, channelId, text });
  };
  const close = () => {
    if (dispatching) return;
    // Preserve a receipt or the token for an unresolved attempt until the draft changes.
    if (!attempted) reset();
    setClosed(true);
  };
  const applyRecommendation = () => {
    if (!open || !token || dispatching || receipt || stale) return;
    confirmation.mutate({ orgId, channelId, token });
  };
  const refreshStatus = () => {
    if (!open || !token || !inProgress || refresh.isPending) return;
    refresh.mutate({ orgId, channelId, token });
  };

  return (
    <div className="channel-recommendation">
      <Button
        size="sm"
        type="button"
        onClick={() => (attempted ? setClosed(false) : requestPreview())}
        disabled={!eligible}
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        {t(
          attempted
            ? "channel.recommendation.dispatch.viewResult"
            : "channel.recommendation.action",
        )}
      </Button>
      {tooLong && (
        <p className="small muted" role="status">
          {overLimitText(t, i18n.language, length, MAX_DRAFT_LENGTH)}
        </p>
      )}
      {keyInDraft && looksLikeApiKey(text) && (
        <p role="status">{t("channel.recommendation.removeKey")}</p>
      )}
      <Dialog
        open={open}
        onOpenChange={(next) => !next && close()}
        title={t("channel.recommendation.title")}
        closeLabel={t("common.close")}
        width={480}
      >
        {/* The composer would otherwise pull focus back through the dialog's portal on mouse down. */}
        <DialogBody
          className="channel-recommendation__body"
          aria-live="polite"
          onMouseDown={(event) => event.stopPropagation()}
        >
          {isPending && <p>{t("channel.recommendation.pending")}</p>}
          {isError && <p>{t("channel.recommendation.unavailable")}</p>}
          {dispatching && <p>{t("channel.recommendation.dispatch.pending")}</p>}
          {dispatchError && (
            <p role="alert">
              {t(
                stale
                  ? "channel.recommendation.dispatch.stale"
                  : "channel.recommendation.dispatch.uncertain",
              )}
            </p>
          )}
          {receipt && (
            <>
              <p className="ac-iconline" role="status">
                <Icon
                  name={
                    receipt.status === "agent_failed"
                      ? "alert"
                      : inProgress
                        ? "waiting"
                        : "check"
                  }
                  size={16}
                />
                {t(`channel.recommendation.dispatch.status.${receipt.status}`)}
              </p>
              {receipt.result && (
                <div className="channel-recommendation__result">
                  <strong>{t("channel.recommendation.dispatch.result")}</strong>
                  <p>{receipt.result}</p>
                </div>
              )}
            </>
          )}
          {currentRefresh && refresh.isError && (
            <p role="alert">{t("common.loadFailed")}</p>
          )}
          {data && (
            <>
              <div>
                <strong>{t(`channel.recommendation.kind.${data.kind}`)}</strong>
                <p>{data.reason}</p>
              </div>
              {(data.primary || data.assistant) && (
                <AssigneeList className="channel-recommendation__assignees">
                  {[data.assistant, data.primary].map((actor) =>
                    actor ? (
                      <li key={`${actor.type}:${actor.id}`}>
                        <div className="channel-recommendation__assignee">
                          <strong>{actor.name}</strong>
                          <span className="small muted">
                            {t(
                              actor.type === "agent"
                                ? "profile.agent"
                                : isCollaboration
                                  ? "issues.form.assignee"
                                  : "approvalCover.person",
                            )}
                          </span>
                        </div>
                        <p>{actor.work}</p>
                      </li>
                    ) : null,
                  )}
                </AssigneeList>
              )}
              {data.humanReview && (
                <p className="ac-iconline">
                  <Icon name="waiting" size={16} />
                  {t("channel.recommendation.humanReview")}
                </p>
              )}
              <details className="channel-recommendation__draft">
                <summary>{t("channel.recommendation.draft")}</summary>
                <p>{text}</p>
              </details>
            </>
          )}
        </DialogBody>
        <DialogFooter>
          {isError && (
            <Button type="button" onClick={requestPreview}>
              {t("common.retry")}
            </Button>
          )}
          <Button type="button" disabled={dispatching} onClick={close}>
            {t("common.close")}
          </Button>
          {receipt?.issueNumber && (
            <Button asChild>
              <Link
                to="/org/$orgId/issues/$number"
                params={{ orgId, number: String(receipt.issueNumber) }}
              >
                {t("channel.recommendation.dispatch.openTask", {
                  number: receipt.issueNumber,
                })}
              </Link>
            </Button>
          )}
          {inProgress && (
            <Button
              type="button"
              loading={refresh.isPending}
              onClick={refreshStatus}
            >
              {t("channel.recommendation.dispatch.refresh")}
            </Button>
          )}
          {stale && (
            <Button type="button" variant="primary" onClick={requestPreview}>
              {t("channel.recommendation.action")}
            </Button>
          )}
          {token && !receipt && !stale && (
            <Button
              type="button"
              variant="primary"
              loading={dispatching}
              onClick={applyRecommendation}
            >
              {t(
                dispatchError
                  ? "common.retry"
                  : "channel.recommendation.dispatch.confirm",
              )}
            </Button>
          )}
        </DialogFooter>
      </Dialog>
    </div>
  );
}
