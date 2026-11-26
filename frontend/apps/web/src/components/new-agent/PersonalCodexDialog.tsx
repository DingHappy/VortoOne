import { type Agent, keys, type PersonalCodexAgentInput, type PersonalCodexStatus } from "@vortoone/client";
import { errorText } from "@vortoone/client/lib/errors";
import { AGENT_HANDLE, freeHandle, handleForName } from "@vortoone/client/lib/new-agent";
import { Button, Dialog, DialogBody, DialogFooter, Field, Input, Select, SettingNote } from "@vortoone/ui";
import { useMutation, useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { useApi } from "@/api/context";
import { useOrg } from "@/components/org-context";

export function usePersonalCodexStatus(enabled = true) {
  const { org, me } = useOrg();
  const api = useApi();
  return useQuery({
    queryKey: [...keys.org(org.id), "personal-codex-agent", me.user.id],
    queryFn: () => api.agents.personalCodexStatus(org.id),
    enabled: enabled && org.role === "owner",
    retry: false,
  });
}

function StatusNote({ preflight }: { preflight: UseQueryResult<PersonalCodexStatus> }) {
  const { t } = useTranslation();
  const status = preflight.data?.status;
  if (preflight.isError) {
    return <SettingNote icon="alert" live>{errorText(t, preflight.error)}</SettingNote>;
  }
  if (!status) return <SettingNote icon="waiting" live>{t("common.loading")}</SettingNote>;
  return (
    <SettingNote icon={status === "ready" ? "check" : "alert"} live>
      {t(`personalCodex.status.${status}`)}
    </SettingNote>
  );
}

export function PersonalCodexConnectionDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const { org } = useOrg();
  const qc = useQueryClient();
  const preflight = usePersonalCodexStatus();
  const ready = preflight.isSuccess && preflight.data.status === "ready";
  useEffect(() => {
    if (ready) void qc.invalidateQueries({ queryKey: keys.aiStatus(org.id) });
  }, [ready, org.id, qc]);
  if (org.role !== "owner") return null;
  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      title={t("personalCodex.check")}
      description={t("personalCodex.description")}
      closeLabel={t("common.close")}
    >
      <DialogBody>
        <StatusNote preflight={preflight} />
      </DialogBody>
      <DialogFooter>
        <Button onClick={onClose}>{t("common.close")}</Button>
        {!ready && (
          <Button onClick={() => void preflight.refetch()} disabled={preflight.isFetching} loading={preflight.isFetching}>
            {t("common.retry")}
          </Button>
        )}
      </DialogFooter>
    </Dialog>
  );
}

export function PersonalCodexDialog({
  orgId,
  handles,
  preflight,
  onClose,
  onCreated,
}: {
  orgId: string;
  handles: Set<string>;
  preflight: UseQueryResult<PersonalCodexStatus>;
  onClose: () => void;
  onCreated: (agentId: string) => void;
}) {
  const { t } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const id = useId();
  const [name, setName] = useState("Codex");
  const [project, setProject] = useState("");
  const proposed = handleForName(name);
  const base = AGENT_HANDLE.test(proposed) ? proposed : "codex";
  const handle = handles.has(base) ? freeHandle(base, handles) : base;
  const create = useMutation({
    meta: { silent: true },
    mutationFn: (input: PersonalCodexAgentInput) => api.agents.createPersonalCodex(orgId, input),
    onSuccess: (agent) => {
      qc.setQueryData<Agent[]>(keys.agents(orgId), (current) =>
        current && !current.some((row) => row.id === agent.id) ? [...current, agent] : current,
      );
      for (const queryKey of [keys.agents(orgId), keys.aiStatus(orgId), keys.overview(orgId), keys.team(orgId)]) {
        void qc.invalidateQueries({ queryKey });
      }
      onCreated(agent.id);
    },
  });
  const status = preflight.data?.status;
  const projects = preflight.data?.projects ?? [];
  const ready = preflight.isSuccess && status === "ready";
  const validProject = !project || projects.includes(project);
  const canSubmit = ready && !!name.trim() && validProject && !preflight.isFetching && !create.isPending;

  return (
    <Dialog
      open
      onOpenChange={(open) => !open && !create.isPending && onClose()}
      title={t("personalCodex.title")}
      description={t("personalCodex.description")}
      closeLabel={t("common.close")}
    >
      <form
        style={{ display: "contents" }}
        onSubmit={(event) => {
          event.preventDefault();
          if (canSubmit) create.mutate({ name: name.trim(), handle, ...(project ? { project } : {}) });
        }}
      >
        <DialogBody>
          <StatusNote preflight={preflight} />
          {ready && (
            <>
              <Field id={`${id}-name`} label={t("newAgentCard.name")} example={t("newAgentCard.handle", { handle })}>
                <Input
                  value={name}
                  maxLength={80}
                  disabled={create.isPending}
                  onChange={(event) => setName(event.target.value)}
                  autoFocus
                />
              </Field>
              <Field
                id={`${id}-project`}
                label={t("personalCodex.project")}
                error={validProject ? undefined : t("personalCodex.projectUnavailable")}
              >
                <Select
                  value={validProject ? project : undefined}
                  onValueChange={setProject}
                  disabled={create.isPending || (projects.length === 0 && validProject)}
                  options={[
                    { value: "", label: t("personalCodex.chatOnly") },
                    ...projects.map((value) => ({ value, label: value })),
                  ]}
                />
              </Field>
            </>
          )}
          {create.isError && <p role="alert" className="ac-field__error">{errorText(t, create.error)}</p>}
        </DialogBody>
        <DialogFooter>
          <Button onClick={onClose} disabled={create.isPending}>{t("common.cancel")}</Button>
          {ready ? (
            <Button type="submit" variant="primary" disabled={!canSubmit} loading={create.isPending}>
              {t("personalCodex.create")}
            </Button>
          ) : (
            <Button onClick={() => void preflight.refetch()} disabled={preflight.isFetching} loading={preflight.isFetching}>
              {t("common.retry")}
            </Button>
          )}
        </DialogFooter>
      </form>
    </Dialog>
  );
}
