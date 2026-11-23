import {
  ApiError,
  createRequester,
  type DesktopSetup,
  type DesktopSetupStep,
  keys,
  type Me,
  useAgents,
  type WorkspaceMode,
} from "@vortoone/client";
import { errorText } from "@vortoone/client/lib/errors";
import { WORK_AREAS } from "@vortoone/client/lib/work-area";
import { Avatar, Button, Field, Input, Logo } from "@vortoone/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { AgentAvatar } from "@/components/AgentAvatar";
import { ErrorState, LoadingLine } from "@/components/Loading";
import { COMPACT_QUERY, useMediaQuery } from "@/components/NavDrawer";
import { OrgMark } from "@/components/ProfilePhoto";
import { BootSplash, OnboardingProgress } from "@/components/Sky";
import { StepRail } from "@/components/StepRail";
import { desktopMetadata } from "@/lib/desktop-metadata";
import { lookOf } from "@/lib/look";
import { PurposeCards } from "./PurposeCards";

const request = createRequester();
// The native live window intercepts this navigation before any HTTP request is made.
const modelSettingsPath = "/onboarding/native-model-settings";
const teamSteps = ["name", "business", "about", "team", "model", "invite"] as const;
const personalSteps = ["purpose", "about", "team", "model"] as const;

interface StepInput {
  step: DesktopSetupStep;
  mode?: WorkspaceMode;
  name?: string;
  context?: string;
  workArea?: string;
}

export function DesktopSetupPage({ me }: { me: Me }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const phone = useMediaQuery(COMPACT_QUERY);
  const [inviteAfterSetup, setInviteAfterSetup] = useState(false);
  const setup = me.desktopSetup;
  const org = me.organizations.find((candidate) => candidate.role === "owner");
  const orgId = org?.id;
  const advance = useMutation({
    meta: { silent: true },
    mutationFn: async (input: StepInput) => {
      let conflict: ApiError | undefined;
      try {
        await request<{ step: DesktopSetupStep | "done" }>("/api/app/desktop-setup", { method: "PATCH", json: input });
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 409) throw error;
        conflict = error;
      }
      // The server owns the step. A tab may have moved it on already, or a write's answer may have been lost.
      await qc.refetchQueries({ queryKey: keys.me, exact: true }, { throwOnError: true });
      if (conflict && qc.getQueryData<Me>(keys.me)?.desktopSetup?.step === input.step) throw conflict;
    },
  });

  useEffect(() => {
    if (setup || advance.isPending) return;
    if (inviteAfterSetup && orgId) {
      void navigate({ to: "/org/$orgId/settings/$section", params: { orgId, section: "members" }, replace: true });
    } else {
      void navigate({ to: "/", replace: true });
    }
  }, [setup, advance.isPending, inviteAfterSetup, orgId, navigate]);

  if (!setup) return <BootSplash />;
  const personal = setup.mode === "personal";
  const steps: readonly DesktopSetupStep[] = personal ? personalSteps : teamSteps;
  const current = Math.max(0, steps.indexOf(setup.step));
  const stepNames: Record<DesktopSetupStep, string> = {
    purpose: t("onboarding.flow.steps.namePersonal"),
    name: t("onboarding.flow.steps.name"),
    business: t("onboarding.flow.steps.business"),
    about: t("onboarding.flow.steps.you"),
    team: t(personal ? "onboarding.flow.steps.teamPersonal" : "onboarding.flow.steps.team"),
    model: t("onboarding.desktopSetup.modelTitle"),
    invite: t("onboarding.flow.steps.people"),
  };
  const title = {
    ...stepNames,
    purpose: t("onboarding.purpose.title"),
    name: t("onboarding.workspace.title"),
  }[setup.step];
  const progress = current / steps.length;

  return (
    <div className="onb onb--rail">
      <aside className="onb__rail">
        <div className="onb__top">
          <span className="onb__brand">
            <Logo size={22} />
          </span>
        </div>
        {!phone && (
          <div className="onb__progress">
            <span className="onb__progress-text">
              <span>{t("onboarding.progress.left", { count: steps.length - current })}</span>
              <span>{t("agentSetup.count", { done: current, total: steps.length })}</span>
            </span>
            <OnboardingProgress progress={progress} />
          </div>
        )}
        <StepRail
          label={t("onboarding.stepsLabel")}
          current={current}
          doneLabel={t("onboarding.stepDone")}
          onGo={() => undefined}
          steps={steps.map((id, i) => ({
            id,
            name: id === "name" && setup.step === "purpose" ? stepNames.purpose : stepNames[id],
            done: i < current,
            disabled: true,
          }))}
        />
      </aside>
      <main className="onb__main">
        <div className="onb__cols">
          {phone && (
            <div className="onb__phone-top">
              <OnboardingProgress progress={progress} />
            </div>
          )}
          <DesktopSetupForm
            key={setup.step}
            title={title}
            setup={setup}
            me={me}
            orgId={orgId}
            logo={org?.logo}
            busy={advance.isPending}
            error={advance.isError && advance.variables?.step === setup.step ? errorText(t, advance.error) : undefined}
            onSubmit={(input, invite = false) => {
              setInviteAfterSetup(invite);
              advance.mutate(input);
            }}
          />
        </div>
      </main>
    </div>
  );
}

function DesktopSetupForm({
  title,
  setup,
  me,
  orgId,
  logo,
  busy,
  error,
  onSubmit,
}: {
  title: string;
  setup: DesktopSetup;
  me: Me;
  orgId?: string;
  logo?: string | null;
  busy: boolean;
  error?: string;
  onSubmit: (input: StepInput, invite?: boolean) => void;
}) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<WorkspaceMode | null>(setup.mode ?? null);
  const [name, setName] = useState(setup.name ?? "");
  const [context, setContext] = useState(setup.context ?? "");
  const [workArea, setWorkArea] = useState<string | null>(setup.workArea ?? null);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => heading.current?.focus(), []);
  const step = setup.step;
  const canOpenModel = step === "model" && desktopMetadata()?.desktopSetupModelSettings === true;
  const input: StepInput = { step };
  if (step === "purpose" && mode) input.mode = mode;
  if (step === "name") input.name = name.trim();
  if (step === "business") input.context = context.trim();
  if (step === "about") input.workArea = workArea ?? "skipped";
  const blocked =
    (step === "purpose" && !mode) || (step === "name" && !input.name) || (step === "business" && !input.context);
  const action =
    step === "invite"
      ? t("onboarding.desktopSetup.openWorkspace")
      : step === "model" && !setup.modelConfigured
        ? t("onboarding.desktopSetup.configureLater")
        : t("onboarding.actions.continue");
  const openModel = canOpenModel && (
    <Button
      variant={setup.modelConfigured ? "default" : "primary"}
      block
      disabled={busy}
      onClick={() => window.location.assign(modelSettingsPath)}
    >
      {t("onboarding.desktopSetup.openModelSettings")}
    </Button>
  );

  return (
    <form
      className="onb__body"
      onSubmit={(event) => {
        event.preventDefault();
        if (!busy && !blocked) onSubmit(input);
      }}
    >
      <header className="onb__head">
        <h1 className="onb__title" ref={heading} tabIndex={-1}>
          {title}
        </h1>
      </header>
      <div className="onb__answer" inert={busy || undefined}>
        {step === "purpose" && <PurposeCards value={mode} onValueChange={setMode} you={me.user} />}
        {step === "name" && (
          <div className="onb__ws">
            <span className="onb__logo" aria-hidden="true">
              <OrgMark logo={logo} size={logo ? 48 : 32} />
            </span>
            <Input
              className="onb__big"
              aria-label={t("onboarding.workspace.name")}
              placeholder={t("onboarding.workspace.namePlaceholder")}
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={80}
              autoComplete="organization"
              required
            />
          </div>
        )}
        {step === "business" && (
          <Field label={t("onboarding.desktopSetup.businessHint")} id="desktop-setup-context">
            <textarea
              className="ac-input onb__line"
              value={context}
              onChange={(event) => setContext(event.target.value)}
              rows={3}
              maxLength={600}
              required
            />
          </Field>
        )}
        {step === "about" && (
          <>
            <span className="ac-iconline">
              <Avatar kind="person" name={me.user.name} imageUrl={me.user.image} size="sm" />
              {me.user.name}
            </span>
            <Field label={t("onboarding.flow.you.area")} group>
              <div className="tp-chips__row">
                {WORK_AREAS.map((area) => (
                  <button
                    key={area}
                    type="button"
                    className="tp-chip"
                    aria-pressed={workArea === area}
                    onClick={() => setWorkArea(workArea === area ? null : area)}
                  >
                    {t(`onboarding.flow.area.options.${area}`)}
                  </button>
                ))}
              </div>
            </Field>
          </>
        )}
        {step === "team" && orgId && <LocalAgents orgId={orgId} />}
        {step === "model" && (
          <>
            <p className="onb__help" role="status">
              {t(setup.modelConfigured ? "onboarding.desktopSetup.modelSaved" : "onboarding.desktopSetup.modelMissing")}
            </p>
            {!canOpenModel && <p className="onb__help">{t("onboarding.desktopSetup.modelInApp")}</p>}
          </>
        )}
        {step === "invite" && <p className="onb__help">{t("onboarding.desktopSetup.inviteHint")}</p>}
      </div>
      {error && (
        <p className="onb__error" role="alert">
          {error}
        </p>
      )}
      <div className="onb__actions">
        {!setup.modelConfigured && openModel}
        <Button
          type="submit"
          variant={canOpenModel && !setup.modelConfigured ? "default" : "primary"}
          block
          loading={busy}
          disabled={blocked}
        >
          {action}
        </Button>
        {setup.modelConfigured && openModel}
        {step === "invite" && (
          <Button block disabled={busy} onClick={() => onSubmit(input, true)}>
            {t("onboarding.desktopSetup.openMembers")}
          </Button>
        )}
      </div>
    </form>
  );
}

function LocalAgents({ orgId }: { orgId: string }) {
  const { t } = useTranslation();
  const agents = useAgents(orgId);
  return (
    <>
      <p className="onb__help">{t("onboarding.desktopSetup.agentsHint")}</p>
      {agents.isPending && <LoadingLine />}
      {agents.isError && <ErrorState error={agents.error} onRetry={() => void agents.refetch()} />}
      {agents.data?.map((agent) => (
        <span key={agent.id} className="ac-iconline">
          <AgentAvatar name={agent.name} appearance={lookOf(agent)} size="sm" />
          {agent.name}
        </span>
      ))}
    </>
  );
}
