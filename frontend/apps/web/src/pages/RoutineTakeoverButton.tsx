import { keys, type Routine, useApi } from "@vortoone/client";
import { errorText } from "@vortoone/client/lib/errors";
import { timezoneName } from "@vortoone/client/lib/routines";
import { Button, Dialog, DialogBody, DialogFooter } from "@vortoone/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useOrg } from "@/components/org-context";
import { useToast } from "@/lib/toast";
import { describeRoutineSchedule } from "@/lib/routine-schedule-label";

export function RoutineTakeoverButton({
  routineId,
  size,
}: {
  routineId: string;
  size: "sm";
}) {
  const { t, i18n } = useTranslation();
  const { org, me } = useOrg();
  const toast = useToast();
  const api = useApi();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const queryKey = keys.routineTakeover(org.id, routineId, me.user.id);
  const review = useQuery({
    queryKey,
    enabled: open,
    queryFn: () => api.routines.takeoverPreview(org.id, routineId),
    retry: false,
    staleTime: 0,
    refetchOnWindowFocus: false,
  });
  const confirm = useMutation({
    mutationFn: (digest: string) =>
      api.routines.takeOver(org.id, routineId, digest),
    meta: { silent: true },
    onSuccess: (saved) => {
      qc.setQueryData<Routine[]>(keys.routines(org.id), (rows) =>
        rows?.map((row) => (row.id === saved.id ? saved : row)),
      );
      void qc.invalidateQueries({ queryKey: keys.routines(org.id) });
      void qc.invalidateQueries({ queryKey: keys.calendar(org.id) });
      setOpen(false);
      toast.show(t("routines.takeover.done"));
    },
  });
  const title = t("routines.fix.take_over");
  const preview =
    review.isSuccess && !review.isFetching ? review.data : undefined;
  const showList = (list: string[]) =>
    list.length ? list.join(" · ") : t("routines.takeover.empty");
  const refresh = () => {
    confirm.reset();
    void review.refetch();
  };
  return (
    <>
      <Button
        size={size}
        variant="primary"
        icon="person"
        onClick={() => {
          confirm.reset();
          setOpen(true);
        }}
      >
        {title}
      </Button>
      <Dialog
        open={open}
        onOpenChange={(next) => !confirm.isPending && setOpen(next)}
        title={title}
        description={t("routines.takeover.description")}
        closeLabel={t("common.close")}
        width={560}
      >
        <DialogBody>
          {review.isFetching && <p role="status">{t("common.loading")}</p>}
          {review.isError && <p role="alert">{errorText(t, review.error)}</p>}
          {preview && (
            <div className="ac-form">
              <strong>{preview.name}</strong>
              <dl className="stack-4">
                <div>
                  <dt className="small muted">
                    {t("routines.takeover.agent")}
                  </dt>
                  <dd>{preview.agentName}</dd>
                </div>
                <div>
                  <dt className="small muted">
                    {t("routines.takeover.schedule")}
                  </dt>
                  <dd>
                    {describeRoutineSchedule(
                      preview.schedule,
                      t,
                      i18n.language,
                    )}
                    {" · "}
                    {timezoneName(preview.timezone, i18n.language)}
                  </dd>
                </div>
                <div>
                  <dt className="small muted">
                    {t("routines.takeover.prompt")}
                  </dt>
                  <dd
                    style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
                  >
                    {preview.prompt}
                  </dd>
                </div>
                {preview.acceptance.length > 0 && (
                  <div>
                    <dt className="small muted">
                      {t("routines.takeover.acceptance")}
                    </dt>
                    <dd>
                      <ul>
                        {preview.acceptance.map((criterion, index) => (
                          <li key={index}>{criterion}</li>
                        ))}
                      </ul>
                    </dd>
                  </div>
                )}
                <div>
                  <dt className="small muted">
                    {t("routines.takeover.operations")}
                  </dt>
                  <dd>{showList(preview.operations)}</dd>
                </div>
                <div>
                  <dt className="small muted">
                    {t("routines.takeover.tools")}
                  </dt>
                  <dd style={{ overflowWrap: "anywhere" }}>
                    {showList(preview.tools)}
                  </dd>
                </div>
                <div>
                  <dt className="small muted">
                    {t("routines.takeover.sources")}
                  </dt>
                  <dd style={{ overflowWrap: "anywhere" }}>
                    {showList(preview.sources)}
                  </dd>
                </div>
              </dl>
            </div>
          )}
          {confirm.isError && <p role="alert">{errorText(t, confirm.error)}</p>}
        </DialogBody>
        <DialogFooter>
          <Button disabled={confirm.isPending} onClick={() => setOpen(false)}>
            {t("common.cancel")}
          </Button>
          {(review.isError || confirm.isError) && (
            <Button
              disabled={review.isFetching || confirm.isPending}
              onClick={refresh}
            >
              {t("routines.takeover.refresh")}
            </Button>
          )}
          <Button
            variant="primary"
            loading={confirm.isPending}
            disabled={!preview}
            onClick={() => preview && confirm.mutate(preview.digest)}
          >
            {title}
          </Button>
        </DialogFooter>
      </Dialog>
    </>
  );
}
