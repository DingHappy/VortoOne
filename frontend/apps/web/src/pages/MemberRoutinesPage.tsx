import {
  type Agent,
  keys,
  type MemberRoutineProposalInput,
  useApi,
} from "@vortoone/client";
import { errorText } from "@vortoone/client/lib/errors";
import { can } from "@vortoone/client/lib/permissions";
import {
  browserTimezone,
  isTimeZone,
  timezoneName,
} from "@vortoone/client/lib/routines";
import {
  Button,
  Card,
  Chip,
  Dialog,
  DialogBody,
  DialogFooter,
  EmptyState,
  Field,
  Input,
  PageHeader,
  Select,
  Textarea,
  TimeSelect,
} from "@vortoone/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ErrorState, LoadingCards } from "@/components/Loading";
import { useOrg } from "@/components/org-context";
import { ZonePicker } from "@/components/ZoneSelect";
import { useDirectory } from "@/hooks/useDirectory";
import { describeRoutineSchedule, weekdayName } from "@/lib/routine-schedule-label";
import { useToast } from "@/lib/toast";

const weekdays = ["1", "2", "3", "4", "5", "6", "0"];
const chipTone = {
  pending: "waiting",
  applying: "info",
  approved: "done",
  rejected: "neutral",
} as const;

export function MemberRoutinesPage() {
  const { t, i18n } = useTranslation();
  const { org, me } = useOrg();
  const api = useApi();
  const dir = useDirectory(org.id);
  const qc = useQueryClient();
  const toast = useToast();
  const [creating, setCreating] = useState(false);
  const queryKey = keys.memberRoutineProposals(org.id, me.user.id);
  const proposals = useQuery({
    queryKey,
    queryFn: () => api.routines.memberProposals(org.id),
    refetchInterval: (q) =>
      q.state.status === "success" &&
      q.state.data?.some(
        ({ status }) => status === "pending" || status === "applying",
      )
        ? 10_000
        : false,
    meta: { quiet: true },
  });
  const ownZone = dir.memberById.get(me.user.id)?.timezone;
  const defaultZone =
    ownZone && isTimeZone(ownZone) ? ownZone : (browserTimezone() ?? "UTC");
  const agents = dir.agents.filter((agent) => agent.canTalk !== false);
  const mayPropose = can(org, "manage_routines");
  const rows = proposals.isError ? [] : (proposals.data ?? []);
  const formatDate = new Intl.DateTimeFormat(i18n.language, {
    dateStyle: "medium",
    timeStyle: "short",
  });

  return (
    <div className="page routines">
      <div className="routines__main">
        <PageHeader
          title={t("routines.memberProposals.title")}
          actions={
            <>
              {rows.length > 0 && (
                <Button
                  disabled={proposals.isFetching}
                  onClick={() => void proposals.refetch()}
                >
                  {t("routines.memberProposals.refresh")}
                </Button>
              )}
              {mayPropose && agents.length > 0 && (
                <Button
                  variant="primary"
                  icon="plus"
                  onClick={() => setCreating(true)}
                >
                  {t("routines.memberProposals.new")}
                </Button>
              )}
            </>
          }
        />
        {proposals.isPending && <LoadingCards count={2} />}
        {proposals.isError && (
          <ErrorState
            error={proposals.error}
            onRetry={() => void proposals.refetch()}
          />
        )}
        {proposals.isSuccess && rows.length === 0 && (
          <EmptyState icon="routines">
            {t("routines.memberProposals.empty")}
          </EmptyState>
        )}
        {rows.length > 0 && (
          <div className="routines-list">
            {rows.map((row) => (
              <Card key={row.id} as="article">
                <div className="settings-row ac-actrow">
                  <div className="settings-row__text ac-actrow__text">
                    <strong className="settings-row__label">{row.name}</strong>
                    <span className="settings-row__note">
                      {row.agentName} · {describeRoutineSchedule(row.schedule, t, i18n.language)} ·{" "}
                      {timezoneName(row.timezone, i18n.language)}
                    </span>
                    <time className="small muted" dateTime={row.createdAt}>
                      {formatDate.format(new Date(row.createdAt))}
                    </time>
                  </div>
                  <Chip tone={chipTone[row.status]}>
                    {t(`routines.memberProposals.status.${row.status}`)}
                  </Chip>
                </div>
              </Card>
            ))}
          </div>
        )}
        {!mayPropose && (
          <p className="small muted">
            {t("routines.memberProposals.noPermission")}
          </p>
        )}
        {mayPropose && !dir.loading && !agents.length && (
          <p className="small muted">
            {t("routines.memberProposals.noAgents")}
          </p>
        )}
      </div>
      {creating && mayPropose && (
        <MemberRoutineProposalDialog
          agents={agents}
          defaultZone={defaultZone}
          onClose={() => setCreating(false)}
          submit={(input) => api.routines.proposeForMember(org.id, input)}
          onSuccess={() => {
            setCreating(false);
            toast.show(t("routines.memberProposals.submitted"));
          }}
          onSettled={() => void qc.invalidateQueries({ queryKey })}
        />
      )}
    </div>
  );
}

function MemberRoutineProposalDialog({
  agents,
  defaultZone,
  onClose,
  submit,
  onSuccess,
  onSettled,
}: {
  agents: Pick<Agent, "id" | "name">[];
  defaultZone: string;
  onClose: () => void;
  submit: (input: MemberRoutineProposalInput) => Promise<unknown>;
  onSuccess: () => void;
  onSettled: () => void;
}) {
  const { t, i18n } = useTranslation();
  const [name, setName] = useState("");
  const [agentId, setAgentId] = useState(
    agents.length === 1 ? (agents[0]?.id ?? "") : "",
  );
  const [frequency, setFrequency] = useState<"daily" | "weekly">("daily");
  const [weekday, setWeekday] = useState("1");
  const [time, setTime] = useState("09:00");
  const [zone, setZone] = useState(defaultZone);
  const [prompt, setPrompt] = useState("");
  const [reason, setReason] = useState("");
  const lastSubmission = useRef<{ id: string; body: string }>(null);
  const save = useMutation({
    mutationFn: submit,
    onSuccess,
    onSettled,
    meta: { silent: true },
  });
  const canSubmit = !!(
    agents.some((agent) => agent.id === agentId) &&
    name.trim() &&
    prompt.trim() &&
    reason.trim()
  );
  const [hour, minute] = time.split(":").map(Number);
  const schedule =
    frequency === "daily" ? `at ${time}` : `${minute} ${hour} * * ${weekday}`;

  return (
    <Dialog
      open
      unsaved={!!(name.trim() || prompt.trim() || reason.trim())}
      onOpenChange={(open) => !open && !save.isPending && onClose()}
      title={t("routines.memberProposals.new")}
      description={t("routines.memberProposals.intro")}
      closeLabel={t("common.close")}
      width={520}
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (!canSubmit || save.isPending) return;
          const definition = {
            name: name.trim(),
            prompt: prompt.trim(),
            schedule,
            timezone: zone,
          };
          const body = JSON.stringify({
            agentId,
            definition,
            reason: reason.trim(),
          });
          if (lastSubmission.current?.body !== body) {
            const suffix = Array.from(
              crypto.getRandomValues(new Uint8Array(16)),
              (byte) => byte.toString(16).padStart(2, "0"),
            ).join("");
            lastSubmission.current = { id: `routine-${suffix}`, body };
          }
          save.mutate({
            agentId,
            definition: { ...definition, id: lastSubmission.current.id },
            reason: reason.trim(),
          });
        }}
      >
        <DialogBody>
          <div className="ac-form">
            <Field label={t("routines.memberProposals.name")}>
              <Input
                required
                maxLength={80}
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={save.isPending}
              />
            </Field>
            <Field label={t("routines.memberProposals.agent")}>
              <Select
                value={agentId || undefined}
                onValueChange={setAgentId}
                disabled={save.isPending}
                placeholder={t("routines.memberProposals.chooseAgent")}
                options={agents.map(({ id, name }) => ({
                  value: id,
                  label: name,
                }))}
              />
            </Field>
            <Field label={t("routines.memberProposals.frequency")}>
              <Select
                value={frequency}
                onValueChange={setFrequency}
                disabled={save.isPending}
                options={(["daily", "weekly"] as const).map((value) => ({
                  value,
                  label: t(`routines.memberProposals.${value}`),
                }))}
              />
            </Field>
            {frequency === "weekly" && (
              <Field label={t("routines.memberProposals.weekday")}>
                <Select
                  value={weekday}
                  onValueChange={setWeekday}
                  disabled={save.isPending}
                  options={weekdays.map((value) => ({
                    value,
                    label: weekdayName(value, i18n.language),
                  }))}
                />
              </Field>
            )}
            <Field label={t("routines.memberProposals.time")}>
              <TimeSelect
                value={time}
                onChange={setTime}
                disabled={save.isPending}
              />
            </Field>
            <Field
              id="routine-proposal-zone"
              label={t("routines.memberProposals.timezone")}
            >
              <ZonePicker
                id="routine-proposal-zone"
                value={zone}
                onChange={setZone}
                disabled={save.isPending}
                suggested={[defaultZone, "UTC"]}
              />
            </Field>
            <Field label={t("routines.memberProposals.prompt")}>
              <Textarea
                required
                maxLength={4000}
                rows={3}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                disabled={save.isPending}
              />
            </Field>
            <Field label={t("routines.memberProposals.reason")}>
              <Textarea
                required
                maxLength={2000}
                rows={2}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                disabled={save.isPending}
              />
            </Field>
            {save.isError && (
              <p role="alert" className="small">
                {errorText(t, save.error)}
              </p>
            )}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button type="button" onClick={onClose} disabled={save.isPending}>
            {t("common.cancel")}
          </Button>
          <Button
            type="submit"
            variant="primary"
            loading={save.isPending}
            disabled={!canSubmit}
          >
            {t("routines.memberProposals.submit")}
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  );
}
