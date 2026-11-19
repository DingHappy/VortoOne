import { keys, type RoutineProposalReview, useApi } from "@agenteschat/client";
import { errorText } from "@agenteschat/client/lib/errors";
import { timezoneName } from "@agenteschat/client/lib/routines";
import {
  Button,
  Card,
  Chip,
  Dialog,
  DialogBody,
  DialogFooter,
  GroupHeading,
} from "@agenteschat/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ErrorState } from "@/components/Loading";
import { useOrg } from "@/components/org-context";
import { describeRoutineSchedule } from "@/lib/routine-schedule-label";
import { useToast } from "@/lib/toast";

export function RoutineProposalReviews() {
  const { t, i18n } = useTranslation();
  const { org, me } = useOrg();
  const api = useApi();
  const qc = useQueryClient();
  const toast = useToast();
  const [review, setReview] = useState<RoutineProposalReview | null>(null);
  const queryKey = keys.routineProposalReviews(org.id, me.user.id);
  const queue = useQuery({
    queryKey,
    queryFn: () => api.routines.proposalReviews(org.id),
    refetchInterval: 15_000,
    meta: { quiet: true },
  });
  const decision = useMutation({
    mutationFn: ({
      item,
      approve,
    }: {
      item: RoutineProposalReview;
      approve: boolean;
    }) =>
      api.routines.decideProposal(org.id, item.id, { ...item.proof, approve }),
    meta: { silent: true },
    onSuccess: (result) => {
      setReview(null);
      qc.setQueryData<RoutineProposalReview[]>(queryKey, (rows) =>
        rows?.filter(({ id }) => id !== result.id),
      );
      toast.show(
        t(
          result.status === "approved"
            ? "routines.proposalReview.approved"
            : "routines.proposalReview.rejected",
        ),
      );
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey });
      void qc.invalidateQueries({ queryKey: keys.routines(org.id) });
    },
  });
  const rows = queue.isSuccess ? queue.data : [];
  const current = rows.find(({ id }) => id === review?.id);
  const sameReview =
    review &&
    current &&
    review.status === current.status &&
    review.submitterName === current.submitterName &&
    review.proof.instance === current.proof.instance &&
    review.proof.digest === current.proof.digest;
  const author = (item: RoutineProposalReview) =>
    item.submitterName
      ? t("routines.proposalReview.submittedBy", { name: item.submitterName })
      : t("routines.proposalReview.departed");
  const schedule = (item: RoutineProposalReview) =>
    `${describeRoutineSchedule(item.schedule, t, i18n.language)} · ${timezoneName(item.timezone, i18n.language)}`;
  const title = t("routines.proposalReview.title");
  const showList = (values: string[]) =>
    values.length ? values.join(" · ") : t("routines.takeover.empty");
  const refresh = async () => {
    if (!review) return;
    decision.reset();
    const latest = await queue.refetch();
    if (latest.isSuccess) {
      const item = latest.data.find(({ id }) => id === review.id);
      if (item) setReview(item);
    }
  };
  if (!review && !rows.length && !queue.isError) return null;
  return (
    <section className="routines-group" aria-label={title}>
      <GroupHeading title={title} count={rows.length} />
      {queue.isError && (
        <ErrorState error={queue.error} onRetry={() => void queue.refetch()} />
      )}
      <div className="routines-list">
        {rows.map((item) => (
          <Card key={item.id} as="article">
            <div className="settings-row ac-actrow">
              <div className="settings-row__text ac-actrow__text">
                <strong className="settings-row__label">{item.name}</strong>
                <span className="settings-row__note">
                  {author(item)} · {item.agentName} · {schedule(item)}
                </span>
              </div>
              <Chip tone={item.status === "pending" ? "waiting" : "info"}>
                {t(`routines.memberProposals.status.${item.status}`)}
              </Chip>
              <Button
                size="sm"
                onClick={() => {
                  decision.reset();
                  setReview(item);
                }}
              >
                {t("routines.proposalReview.open")}
              </Button>
            </div>
          </Card>
        ))}
      </div>
      {review && (
        <Dialog
          open
          onOpenChange={(open) =>
            !open && !decision.isPending && setReview(null)
          }
          title={review.name}
          description={t("routines.proposalReview.description")}
          closeLabel={t("common.close")}
          width={560}
        >
          <DialogBody>
            <div className="ac-form">
              <p>
                {author(review)} · {review.agentName}
              </p>
              <dl className="stack-4">
                <div>
                  <dt className="small muted">
                    {t("routines.takeover.schedule")}
                  </dt>
                  <dd>{schedule(review)}</dd>
                </div>
                <div>
                  <dt className="small muted">
                    {t("routines.memberProposals.prompt")}
                  </dt>
                  <dd
                    style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
                  >
                    {review.prompt}
                  </dd>
                </div>
                <div>
                  <dt className="small muted">
                    {t("routines.proposalReview.reason")}
                  </dt>
                  <dd
                    style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
                  >
                    {review.reason}
                  </dd>
                </div>
                {review.acceptance.length > 0 && (
                  <div>
                    <dt className="small muted">
                      {t("routines.takeover.acceptance")}
                    </dt>
                    <dd>
                      <ul>
                        {review.acceptance.map((criterion, index) => (
                          <li key={index}>{criterion}</li>
                        ))}
                      </ul>
                    </dd>
                  </div>
                )}
                <div>
                  <dt className="small muted">
                    {t("routines.proposalReview.operations")}
                  </dt>
                  <dd>{showList(review.operations)}</dd>
                </div>
                <div>
                  <dt className="small muted">
                    {t("routines.proposalReview.tools")}
                  </dt>
                  <dd style={{ overflowWrap: "anywhere" }}>
                    {showList(review.tools)}
                  </dd>
                </div>
                <div>
                  <dt className="small muted">
                    {t("routines.proposalReview.delivery")}
                  </dt>
                  <dd>{t(`routines.proposalReview.${review.delivery}`)}</dd>
                </div>
                <div>
                  <dt className="small muted">
                    {t("routines.proposalReview.budget")}
                  </dt>
                  <dd>
                    {review.tokenBudget
                      ? review.tokenBudget.toLocaleString(i18n.language)
                      : t("routines.proposalReview.budgetUnset")}
                  </dd>
                </div>
              </dl>
              {decision.isError && (
                <p role="alert">{errorText(t, decision.error)}</p>
              )}
              {queue.isSuccess && !sameReview && !decision.isPending && (
                <p role="status">
                  {t(
                    current
                      ? "routines.proposalReview.changed"
                      : "routines.proposalReview.handled",
                  )}
                </p>
              )}
            </div>
          </DialogBody>
          <DialogFooter>
            <Button
              disabled={decision.isPending}
              onClick={() => setReview(null)}
            >
              {t("common.close")}
            </Button>
            {(!sameReview || decision.isError) && (
              <Button
                disabled={queue.isFetching || decision.isPending}
                onClick={() => void refresh()}
              >
                {t("routines.takeover.refresh")}
              </Button>
            )}
            {review.status === "pending" && (
              <Button
                variant="danger"
                disabled={!sameReview || decision.isPending}
                onClick={() =>
                  decision.mutate({ item: review, approve: false })
                }
              >
                {t("routines.proposalReview.reject")}
              </Button>
            )}
            <Button
              variant="primary"
              loading={decision.isPending}
              disabled={!sameReview}
              onClick={() => decision.mutate({ item: review, approve: true })}
            >
              {t(
                review.status === "applying"
                  ? "routines.proposalReview.finish"
                  : review.enabled
                    ? "routines.proposalReview.approve"
                    : "routines.proposalReview.approvePaused",
              )}
            </Button>
          </DialogFooter>
        </Dialog>
      )}
    </section>
  );
}
