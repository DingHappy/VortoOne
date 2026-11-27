import { type Agent, keys, useAgents, useMe } from "@vortoone/client";
import { errorText } from "@vortoone/client/lib/errors";
import { PRESETS, type Preset, presetInput } from "@vortoone/client/lib/onboarding";
import { makesAgents } from "@vortoone/client/lib/permissions";
import { Button, Checkbox, SettingNote } from "@vortoone/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useApi } from "@/api/context";
import { AgentAvatar } from "@/components/AgentAvatar";
import { LoadingLine } from "@/components/Loading";
import { agentHandles, createSelectedPresets } from "./preset-creation";
import "./onboarding-presets.css";

export function OnboardingPresets({
  orgId,
  onFinish,
  finishing = false,
}: {
  orgId: string;
  onFinish: () => void;
  finishing?: boolean;
}) {
  const { t } = useTranslation();
  const api = useApi();
  const qc = useQueryClient();
  const me = useMe();
  const allowed = makesAgents(me.data?.organizations.find((org) => org.id === orgId));
  const agents = useAgents(orgId, allowed);
  const formId = useId();
  const [selected, setSelected] = useState<ReadonlySet<Preset>>(new Set());
  const [completed, setCompleted] = useState<ReadonlySet<Preset>>(new Set());
  const [failures, setFailures] = useState<ReadonlyMap<Preset, unknown>>(new Map());
  const existing = useMemo(() => agentHandles(agents.data ?? []), [agents.data]);
  const hasPreset = (preset: Preset) => existing.has(preset) || completed.has(preset);
  const available = PRESETS.filter((preset) => !hasPreset(preset));
  const pending = available.filter((preset) => selected.has(preset));
  const create = useMutation({
    mutationKey: [...keys.agents(orgId), "onboardingPresets"],
    meta: { silent: true },
    mutationFn: (presets: readonly Preset[]) =>
      createSelectedPresets(api.agents, orgId, presets, (preset) => presetInput(t, preset)),
    onSuccess: ({ agents: registered }) => {
      if (registered.length > 0) {
        qc.setQueryData<Agent[]>(keys.agents(orgId), (current) => {
          if (!current) return undefined;
          const ids = new Set(current.map((agent) => agent.id));
          return [...current, ...registered.filter((agent) => !ids.has(agent.id))];
        });
      }
      for (const queryKey of [keys.agents(orgId), keys.overview(orgId), keys.team(orgId), keys.aiStatus(orgId)]) {
        void qc.invalidateQueries({ queryKey });
      }
    },
  });
  const busy = finishing || create.isPending;
  const listReady = agents.data !== undefined;
  const choose = (preset: Preset, checked: boolean) => {
    setSelected((current) => {
      const next = new Set(current);
      if (checked) next.add(preset);
      else next.delete(preset);
      return next;
    });
    if (!checked) setFailures((current) => {
      const next = new Map(current);
      next.delete(preset);
      return next;
    });
  };
  const submit = () => {
    if (!allowed || !listReady || busy || pending.length === 0) return;
    setFailures(new Map());
    create.mutate(pending, {
      onSuccess: (result) => {
        setCompleted((current) => new Set([...current, ...result.completed]));
        setSelected((current) => new Set([...current].filter((preset) => !result.completed.includes(preset))));
        setFailures(result.failures);
        if (result.failures.size === 0) onFinish();
      },
    });
  };
  const retry = pending.some((preset) => failures.has(preset));
  const continueLabel = completed.size > 0 || (listReady && available.length === 0) || !allowed;
  return (
    <>
      <div className="onb__answer">
        {me.isPending || (allowed && agents.isPending) ? <LoadingLine /> : null}
        {allowed && agents.isError && !listReady && (
          <SettingNote icon="alert" live action={<Button onClick={() => void agents.refetch()}>{t("common.retry")}</Button>}>
            {errorText(t, agents.error)}
          </SettingNote>
        )}
        {allowed && listReady && (
          <fieldset className="onb-presets" aria-label={t("onboarding.presetPicker.label")}>
            {PRESETS.map((preset) => {
              const input = presetInput(t, preset);
              const id = `${formId}-${preset}`;
              const description = `${id}-description`;
              const title = `${input.name} · ${input.role}`;
              const present = hasPreset(preset);
              const checked = present || selected.has(preset);
              return (
                <label key={preset} htmlFor={id} className="ac-option onb-presets__card" data-state={checked ? "checked" : "unchecked"}>
                  <Checkbox
                    id={id}
                    aria-label={title}
                    aria-describedby={description}
                    checked={checked}
                    disabled={busy || present}
                    onCheckedChange={(value) => choose(preset, value)}
                  />
                  <AgentAvatar name={input.name} agent={input} size="sm" aria-hidden="true" />
                  <span className="ac-option__text">
                    <span className="ac-option__title">{title}</span>
                    <span className="ac-option__desc" id={description}>{t(`onboarding.presets.${preset}.desc`)}</span>
                    {present && <span className="ac-option__desc">{t("onboarding.presetPicker.present")}</span>}
                    {!present && failures.has(preset) && (
                      <span className="ac-field__error" role="alert">{errorText(t, failures.get(preset))}</span>
                    )}
                  </span>
                </label>
              );
            })}
          </fieldset>
        )}
        {create.isError && <p className="onb__error" role="alert">{errorText(t, create.error)}</p>}
      </div>
      <div className="onb__actions">
        {allowed && listReady && available.length > 0 && (
          <Button variant="primary" block disabled={busy || pending.length === 0} loading={create.isPending} onClick={submit}>
            {pending.length === 0
              ? t("onboarding.presetPicker.addSelected")
              : t(retry ? "onboarding.presetPicker.retry" : "onboarding.presetPicker.create", { count: pending.length })}
          </Button>
        )}
        <Button block disabled={busy} loading={finishing} onClick={onFinish}>
          {t(continueLabel ? "onboarding.actions.continue" : "onboarding.presetPicker.skip")}
        </Button>
      </div>
    </>
  );
}
