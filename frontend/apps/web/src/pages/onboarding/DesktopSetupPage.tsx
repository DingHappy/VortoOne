import {
  ApiError,
  createRequester,
  type DesktopSetup,
  type DesktopSetupStep,
  keys,
  type Me,
  type WorkspaceMode,
} from "@vortoone/client";
import { errorText } from "@vortoone/client/lib/errors";
import { Button, Field, Input, Logo, SettingNote, Textarea } from "@vortoone/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { COMPACT_QUERY, useMediaQuery } from "@/components/NavDrawer";
import { OrgMark } from "@/components/ProfilePhoto";
import { BootSplash } from "@/components/Sky";
import { StepRail } from "@/components/StepRail";
import { desktopMetadata, NATIVE_MODEL_SETTINGS_PATH } from "@/lib/desktop-metadata";
import { OnboardingPresets } from "./OnboardingPresets";
import { OnboardingProgress } from "./OnboardingProgress";
import { PurposeCards } from "./PurposeCards";

const request = createRequester();
const teamSteps = ["purpose", "name", "model", "presets"] as const;
const personalSteps = ["purpose", "model", "presets"] as const;

interface StepInput {
  step: DesktopSetupStep;
  mode?: WorkspaceMode;
  name?: string;
  context?: string;
  skipModel?: true;
}

interface StepResult {
  step: DesktopSetupStep | "done";
  rightHandId?: string;
}

export function DesktopSetupPage({ me }: { me: Me }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const phone = useMediaQuery(COMPACT_QUERY);
  const [purpose, setPurpose] = useState<WorkspaceMode | null>(null);
  const setup = me.desktopSetup;
  const org = me.organizations.find((candidate) => candidate.role === "owner");
  const orgId = org?.id;
  const advance = useMutation({
    meta: { silent: true },
    mutationFn: async (input: StepInput) => {
      let conflict: ApiError | undefined;
      let result: StepResult | undefined;
      try {
        result = await request<StepResult>("/api/app/desktop-setup", { method: "PATCH", json: input });
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 409) throw error;
        conflict = error;
      }
      // The server owns the step. A tab may have moved it on already, or a write's answer may have been lost.
      await qc.refetchQueries({ queryKey: keys.me, exact: true }, { throwOnError: true });
      if (conflict && qc.getQueryData<Me>(keys.me)?.desktopSetup?.step === input.step) throw conflict;
      return result;
    },
  });

  useEffect(() => {
    if (setup || advance.isPending) return;
    const agentId = advance.data?.rightHandId;
    if (orgId && agentId) {
      void navigate({ to: "/org/$orgId/dm/agent/$agentId", params: { orgId, agentId }, replace: true });
    } else if (orgId) {
      void navigate({ to: "/org/$orgId/home", params: { orgId }, replace: true });
    } else {
      void navigate({ to: "/", replace: true });
    }
  }, [setup, advance.isPending, advance.data?.rightHandId, orgId, navigate]);

  if (!setup) return <BootSplash />;
  const mode = setup.mode ?? purpose;
  const personal = mode === "personal";
  const steps: readonly DesktopSetupStep[] = personal ? personalSteps : teamSteps;
  const current = Math.max(0, steps.indexOf(setup.step));
  const stepNames: Record<DesktopSetupStep, string> = {
    purpose: t("onboarding.flow.steps.namePersonal"),
    name: t("onboarding.flow.steps.name"),
    model: t("onboarding.desktopSetup.modelTitle"),
    presets: t("onboarding.flow.steps.teamPersonal"),
  };
  const title = {
    ...stepNames,
    purpose: t("onboarding.purpose.title"),
    name: t("onboarding.workspace.title"),
    presets: t("onboarding.flow.presets.title"),
  }[setup.step];

  return (
    <div className="onb onb--rail">
      <aside className="onb__rail">
        <div className="onb__top">
          <span className="onb__brand">
            <Logo size={22} />
          </span>
        </div>
        {!phone && <OnboardingProgress done={current} total={steps.length} current={current} />}
        <StepRail
          label={t("onboarding.stepsLabel")}
          current={current}
          doneLabel={t("onboarding.stepDone")}
          onGo={() => undefined}
          steps={steps.map((id, i) => ({
            id,
            name: stepNames[id],
            done: i < current,
            disabled: true,
          }))}
        />
      </aside>
      <main className="onb__main">
        <div className="onb__cols">
          {phone && (
            <div className="onb__phone-top">
              <OnboardingProgress done={current} total={steps.length} current={current} />
            </div>
          )}
          <DesktopSetupForm
            key={setup.step}
            title={title}
            setup={setup}
            mode={mode}
            onModeChange={setPurpose}
            me={me}
            orgId={orgId}
            logo={org?.logo}
            busy={advance.isPending}
            error={advance.isError && advance.variables?.step === setup.step ? errorText(t, advance.error) : undefined}
            onSubmit={(input) => advance.mutate(input)}
          />
        </div>
      </main>
    </div>
  );
}

function DesktopSetupForm({
  title,
  setup,
  mode,
  onModeChange,
  me,
  orgId,
  logo,
  busy,
  error,
  onSubmit,
}: {
  title: string;
  setup: DesktopSetup;
  mode: WorkspaceMode | null;
  onModeChange: (mode: WorkspaceMode | null) => void;
  me: Me;
  orgId?: string;
  logo?: string | null;
  busy: boolean;
  error?: string;
  onSubmit: (input: StepInput) => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState(setup.name ?? "");
  const [context, setContext] = useState(setup.context ?? "");
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => heading.current?.focus(), []);
  const step = setup.step;
  const canOpenModel = step === "model" && desktopMetadata()?.desktopSetupModelSettings === true;
  const needsModel = step === "model" && !setup.modelConfigured;
  const input: StepInput = { step };
  if (step === "purpose" && mode) input.mode = mode;
  if (step === "name") {
    input.name = name.trim();
    input.context = context.trim();
  }
  const blocked = (step === "purpose" && !mode) || (step === "name" && !input.name);
  const action = t(step === "model" ? "onboarding.flow.ai.model.connect" : "onboarding.actions.continue");
  const openModel = canOpenModel && (
    <Button
      variant={setup.modelConfigured ? "default" : "primary"}
      block
      disabled={busy}
      onClick={() => window.location.assign(NATIVE_MODEL_SETTINGS_PATH)}
    >
      {t("onboarding.desktopSetup.openModelSettings")}
    </Button>
  );
  const problem = error && (
    <p className="onb__error" role="alert">
      {error}
    </p>
  );

  if (step === "presets") {
    return (
      <section className="onb__body">
        <header className="onb__head">
          <h1 className="onb__title" ref={heading} tabIndex={-1}>
            {title}
          </h1>
          <p className="onb__lead">{t("onboarding.flow.presets.lead")}</p>
        </header>
        {problem}
        {orgId && setup.modelConfigured && (
          <OnboardingPresets orgId={orgId} finishing={busy} onFinish={() => onSubmit({ step: "presets" })} />
        )}
      </section>
    );
  }

  return (
    <form
      className="onb__body"
      onSubmit={(event) => {
        event.preventDefault();
        if (!busy && !blocked && !needsModel) onSubmit(input);
      }}
    >
      <header className="onb__head">
        <h1 className="onb__title" ref={heading} tabIndex={-1}>
          {title}
        </h1>
      </header>
      <div className="onb__answer" inert={busy || undefined}>
        {step === "purpose" && <PurposeCards value={mode} onValueChange={onModeChange} you={me.user} />}
        {step === "name" && (
          <>
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
            <Field label={t("onboarding.workspace.contextOptional")} id="desktop-setup-context">
              <Textarea
                className="onb__line"
                value={context}
                onChange={(event) => setContext(event.target.value)}
                rows={2}
                maxLength={600}
              />
            </Field>
          </>
        )}
        {step === "model" && (
          <>
            <SettingNote icon={setup.modelConfigured ? "check" : "key"} live>
              {t(setup.modelConfigured ? "onboarding.desktopSetup.modelSaved" : "onboarding.desktopSetup.modelMissing")}
            </SettingNote>
            {needsModel && !canOpenModel && <p className="onb__help">{t("onboarding.desktopSetup.modelInApp")}</p>}
          </>
        )}
      </div>
      {problem}
      <div className="onb__actions">
        {needsModel ? (
          openModel
        ) : (
          <>
            <Button type="submit" variant="primary" block loading={busy} disabled={blocked}>
              {action}
            </Button>
            {openModel}
          </>
        )}
        {step === "model" && (
          <Button block disabled={busy} onClick={() => onSubmit({ step: "model", skipModel: true })}>
            {t("onboarding.desktopSetup.configureLater")}
          </Button>
        )}
      </div>
    </form>
  );
}
