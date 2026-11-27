import {
  ApiError,
  keys,
  type Me,
  meQuery,
  type OrgSummary,
  useAiStatus,
  useAuthConfig,
  type WorkspaceMode,
} from "@vortoone/client";
import { billingHoldOf } from "@vortoone/client/lib/billing";
import { accountOutOfReach } from "@vortoone/client/lib/deletion";
import { errorCode, errorText } from "@vortoone/client/lib/errors";
import { firstName } from "@vortoone/client/lib/format";
import { agentGrammar } from "@vortoone/client/lib/grammar";
import { settingUp } from "@vortoone/client/lib/guards";
import { sendsEmail } from "@vortoone/client/lib/join-requests";
import { type AiChoice, aiStateOf, planLabel } from "@vortoone/client/lib/no-ai";
import {
  landingOf,
  type OnboardingStepId,
  type RailStep,
  railState,
  stepIndex,
} from "@vortoone/client/lib/onboarding";
import { finishOnboarding, isRightHand, RIGHT_HAND_DEFAULT } from "@vortoone/client/lib/right-hand";
import { browserTimezone, timezoneName } from "@vortoone/client/lib/routines";
import { isPersonal } from "@vortoone/client/lib/workspace-mode";
import {
  Avatar,
  Button,
  Field,
  Icon,
  Input,
  Locked,
  Logo,
  Menu,
  MenuItem,
  setGaze,
  Textarea,
} from "@vortoone/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate, useNavigate } from "@tanstack/react-router";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useApi } from "@/api/context";
import { useAuth } from "@/auth/context";
import { AgentAvatar } from "@/components/AgentAvatar";
import { BillingLinkButton } from "@/components/BillingLinkButton";
import { DeleteAccountDialog } from "@/components/DeleteAccount";
import { DeleteWorkspaceDialog } from "@/components/DeleteWorkspace";
import { ErrorState } from "@/components/Loading";
import { COMPACT_QUERY, DESKTOP_QUERY, useMediaQuery } from "@/components/NavDrawer";
import { OrgProvider } from "@/components/org-context";
import { ImageCropDialog, OrgMark } from "@/components/ProfilePhoto";
import { BootSplash } from "@/components/Sky";
import { StepRail } from "@/components/StepRail";
import { ZonePicker } from "@/components/ZoneSelect";
import { useSignOut } from "@/hooks/useSignOut";
import { ARRIVAL_BETA_ONLY, ARRIVAL_PARAM } from "@/lib/arrival";
import { sendNewLink } from "@/lib/email-confirmation";
import { lookOf } from "@/lib/look";
import { useOpenParam } from "@/lib/open-param";
import { usePaidReturn } from "@/lib/paid-return";
import { usePlanOffer } from "@/lib/plan-offer";
import { returnPlace } from "@/lib/return-to";
import { STORAGE_KEYS, sessionStore, storage } from "@/lib/storage";
import { useToast } from "@/lib/toast";
import { AiHost, AiStage, AiStep } from "./onboarding/AiStep";
import { OnboardingPreview, type PreviewAgent } from "./onboarding/OnboardingPreview";
import { OnboardingProgress } from "./onboarding/OnboardingProgress";
import { OnboardingPresets } from "./onboarding/OnboardingPresets";
import { DesktopSetupPage } from "./onboarding/DesktopSetupPage";
import { PurposeCards } from "./onboarding/PurposeCards";
import { PlanStep } from "./onboarding/steps";
import "./onboarding.css";

/** Back from paying, `/me` is asked again this often until the hold lifts (as the hold's banner does). */
const PAID_POLL_MS = 5000;
/** On the step that connects an AI, its status is read this often: a connection made in another tab moves it on. */
const AI_POLL_MS = 10_000;
/** The bundled Desktop keeps its own durable setup. Other web workspaces use the workspace onboarding. */
export function OnboardingPage() {
  const api = useApi();
  const me = useQuery({
    ...meQuery(api),
    refetchOnWindowFocus: "always",
    refetchInterval: (query) => (query.state.data?.desktopSetup?.step === "model" ? 3000 : false),
  });
  // Stay mounted until the last answer removes the pending step and opens the workspace.
  const desktopOwner = useRef<string | null>(null);
  if (me.data?.desktopSetup) desktopOwner.current = me.data.user.id;
  if (me.data && desktopOwner.current === me.data.user.id) return <DesktopSetupPage me={me.data} />;
  if (me.isPending) return <BootSplash />;
  if (me.isError) return <ErrorState error={me.error} onRetry={() => void me.refetch()} />;
  if (
    me.data?.user.canCreateWorkspace === false &&
    me.data.organizations.length > 0 &&
    !me.data.organizations.some(settingUp)
  ) {
    return <Navigate to="/" replace />;
  }
  return <WorkspaceOnboardingPage />;
}

function WorkspaceOnboardingPage() {
  const { t, i18n } = useTranslation();
  const api = useApi();
  const auth = useAuth();
  const { signOut } = useSignOut();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const toast = useToast();
  const phone = useMediaQuery(COMPACT_QUERY);
  const wide = useMediaQuery(DESKTOP_QUERY);

  // What it is for (ADR 0159), asked before the workspace exists; a company or team then goes on to its name.
  const [purpose, setPurpose] = useState<WorkspaceMode | null>(null);
  const [forTeam, setForTeam] = useState(false);
  const personal = purpose === "personal";
  const [orgDraft, setOrg] = useState<string | null>(null);
  const [contextDraft, setContext] = useState<string | null>(null);
  // The zone the person picked here, if they did.
  const [zone, setZone] = useState<string | null>(null);
  const [zoneOpen, setZoneOpen] = useState(false);
  const [logo, setLogo] = useState<{ imageBase64: string; contentType: string } | null>(null);
  const [logoDirty, setLogoDirty] = useState(false);
  const [logoOpen, setLogoOpen] = useState(false);
  // Made here; else the one this person is setting up (another tab, a visit cut short), which resumes.
  const [created, setCreated] = useState<string | null>(null);
  // A done step the person went back to, else the step the data says.
  const [picked, setPicked] = useState<RailStep | null>(null);
  // Stay on a successful AI step until its dialog closes and the person chooses Continue.
  const [aiSeen, setAiSeen] = useState(false);
  const [aiAdvanced, setAiAdvanced] = useState(false);
  // What the AI step's dialog connected last in this visit, said on the done screen and the rail ("Claude"), with
  // whether the workspace had a key then.
  const [aiPick, setAiPick] = useState<{ choice: AiChoice; key: boolean } | null>(null);
  const [polling, setPolling] = useState(false);
  // "Apagar este workspace" (`?delete=1`, which the phone's onboarding opens) and "Apagar conta", kept in the address.
  const [deleting, setDeleting] = useOpenParam("delete");
  const [account, setAccount] = useOpenParam("account");
  const titleRef = useRef<HTMLHeadingElement>(null);
  const mainRef = useRef<HTMLElement>(null);

  const me = useQuery({ ...meQuery(api), refetchOnWindowFocus: "always" });
  const config = useAuthConfig();
  const authConfig = config.data;
  const unfinished = me.data?.organizations.find(settingUp);
  // Finished and on its way into the workspace, the data no longer says a step (`onboarding` is gone): the workspace
  // and its last step stay until it opens.
  const leaving = useRef(false);
  const lastOrgId = useRef<string | null>(null);
  const orgId = created ?? unfinished?.id ?? (leaving.current ? lastOrgId.current : null);
  lastOrgId.current = orgId;
  const mine = me.data?.organizations.find((o) => o.id === orgId);
  const org = orgDraft ?? mine?.name ?? "";
  // Asked again whenever the tab comes back, as `me` is: a step answered meanwhile in another tab or on the phone moves
  // this one on, so its button never makes a second team.
  const settings = useQuery({
    queryKey: keys.settings(orgId ?? ""),
    queryFn: () => api.orgs.settings(orgId ?? ""),
    enabled: orgId !== null,
    refetchOnWindowFocus: "always",
  });
  const agents = useQuery({
    queryKey: keys.agents(orgId ?? ""),
    queryFn: () => api.agents.list(orgId ?? ""),
    enabled: orgId !== null,
    refetchOnWindowFocus: "always",
  });
  const lodi = agents.data?.find(isRightHand);
  const team = (agents.data ?? []).filter((a) => !isRightHand(a));
  const context = contextDraft ?? settings.data?.context ?? "";
  const passed = mine?.onboarding?.step;
  const aiLaterSaved = passed !== undefined && stepIndex(passed) >= stepIndex("ai");
  const held = billingHoldOf(mine?.standing) === "payment";
  // Back from paying, until the billing service lifts the hold: `/me` again meanwhile, as the hold's banner does.
  const paid = usePaidReturn(orgId ?? "", held);
  useQuery({ ...meQuery(api), enabled: paid, refetchInterval: PAID_POLL_MS });
  // Made here as "For me", one being set up that is, or "For me" picked and not made yet: the rail shows the personal
  // path from the first question on (ADR 0159).
  const personalNow = isPersonal(mine) || (personal && (created !== null || orgId === null));

  // Only the right hand needs to be usable. Older workspaces can already have other disconnected agents.
  const status = useAiStatus(orgId ?? "", orgId !== null && lodi !== undefined, polling ? AI_POLL_MS : false);
  const thinks =
    lodi === undefined ? false : status.isStale && status.isFetching
      ? undefined : status.data && aiStateOf(status.data, lodi.id) === "ready";
  const railAt = (pick: RailStep | null) =>
    railState({
      orgId,
      standing: mine?.standing,
      billingUrl: authConfig?.billingUrl,
      context,
      team: team.length,
      picked: pick,
      personal: personalNow,
      namingTeam: forTeam,
      funnel: { thinks, passed },
    });
  const reachedRail = railAt(picked);
  const keepAi = picked === null && aiSeen && !aiAdvanced && reachedRail.at === "presets";
  const rail = keepAi ? railAt("ai") : reachedRail;
  const loaded =
    orgId === null ||
    (settings.data !== undefined &&
      agents.data !== undefined &&
      mine !== undefined &&
      (authConfig !== undefined || config.isError));
  // Do not flash unconnected choices while the current AI status is being loaded.
  const deciding = lodi !== undefined && thinks === undefined && !status.isError && rail.at === "ai";
  const lastRail = useRef<typeof rail | null>(null);
  const frozen = deciding || leaving.current ? lastRail.current : null;
  const { steps, at, reached, step, index, thinks: thinksNow } = frozen ?? rail;
  useEffect(() => {
    if (loaded && !deciding && !leaving.current) lastRail.current = rail;
  });
  useEffect(() => setPolling(step === "ai"), [step]);
  // Before the workspace exists, what it is for comes first.
  const asking = step === "purpose";
  // On that question the chosen answer's agents look at the pointer (`PurposeScene`, its `data-gaze`).
  useEffect(() => {
    if (!asking) return;
    const look = (e: PointerEvent) => setGaze({ x: e.clientX, y: e.clientY });
    window.addEventListener("pointermove", look);
    return () => {
      window.removeEventListener("pointermove", look);
      setGaze(null);
    };
  }, [asking]);
  // Read from the first step, so the plan step has it when it opens.
  const plans = usePlanOffer(i18n.language);
  const offer = plans.offer;
  // Read once is enough: a refetch that fails later keeps the step on screen with what it read. The plan step opens
  // whole, back from the billing page too: with its price, or with none once the list is late, never a line that changes.
  // A first visit that only the AI's answer can place (its step or opening) waits for it, as for the workspace's data.
  const known = loaded && !(deciding && frozen === null) && !(step === "plan" && plans.loading);
  const name = (mine?.name ?? org).trim();
  // Back on step 1, the workspace's own zone (saved on another visit, maybe another device), never this browser's.
  const zoneNow = zone ?? settings.data?.timezone ?? browserTimezone() ?? "UTC";
  useEffect(() => {
    if (known && at === "ai" && !deciding) setAiSeen(true);
  }, [known, at, deciding]);
  const aiDone = step === "ai" && thinksNow;
  // If the default changed in another window, use the current connection instead of the old choice.
  const keyNow = status.data?.apiKey === true;
  const aiNamed = aiPick && (aiPick.choice === "key" ? keyNow : !keyNow || aiPick.key) ? aiPick.choice : null;
  const stay = picked !== null && at !== "open";

  /** A step the server saved, in `/me` as it answered, where the rail reads it. */
  const remember = (id: string, saved: { step: OnboardingStepId } | null) => {
    const now = qc.getQueryData<Me>(keys.me);
    if (!saved || !now) return;
    const organizations = now.organizations.map((o) =>
      o.id === id && o.onboarding ? { ...o, onboarding: { ...o.onboarding, step: saved.step } } : o,
    );
    qc.setQueryData<Me>(keys.me, { ...now, organizations });
  };
  // A plan step shown is marked as passed, so that once paid it stays on the rail as answered; a server that never
  // held the workspace for a plan has no plan step at all (ADR 0162).
  const markPlan = useMutation({
    meta: { silent: true },
    mutationFn: (id: string) => api.orgs.saveOnboarding(id, { step: "computer" }),
    onSuccess: (saved, id) => remember(id, saved),
  });
  useEffect(() => {
    if (!known || !orgId || step !== "plan" || !held || markPlan.status !== "idle") return;
    if (passed !== undefined && stepIndex(passed) >= stepIndex("computer")) return;
    markPlan.mutate(orgId);
  });

  // On VortoOne Cloud, while sign-up is by invitation, only accounts early access allowed make workspaces (ADR 0078).
  const inviteOnly = me.data?.user.canCreateWorkspace === false && orgId === null;
  const emails = sendsEmail(authConfig);
  const email = me.data?.user.email ?? "";
  // Their face in the first question's drawings: the account's photo (Google's), else their initials.
  const you = { name: me.data?.user.name || email, image: me.data?.user.image ?? null };
  // The address this tab's sign-up was emailed a link to confirm: the step says where it went. Read once and
  // forgotten once confirmed or set up, so a later onboarding (another workspace) says nothing of it.
  const [sentTo] = useState(() => sessionStore.get(STORAGE_KEYS.confirmationSent));
  const verified = me.data?.user.emailVerified === true;
  const confirmationSent = email !== "" && sentTo === email && !verified;
  // Early access counts only for a confirmed address (ADR 0078 §4): held here unconfirmed, a new link is the way on.
  const confirmFirst = inviteOnly && emails && email !== "" && !verified && !confirmationSent;
  useEffect(() => {
    if (verified) sessionStore.remove(STORAGE_KEYS.confirmationSent);
  }, [verified]);
  const resend = useMutation({ mutationFn: () => sendNewLink({ auth, t, show: toast.show }, email) });

  // Someone who already has a workspace can give up on a new one: back to the one they were in (or any other).
  const others = (me.data?.organizations ?? []).filter((o) => o.id !== orgId && !settingUp(o));
  const leaveTo = others.find((o) => o.id === storage.get(STORAGE_KEYS.lastOrg)) ?? others[0];

  const open = useMutation({
    mutationFn: async (how: "done" | "later" | "skip-ai" | "skipped") => {
      if (orgId === null) return;
      leaving.current = true;
      const done = await finishOnboarding(api, orgId);
      sessionStore.remove(STORAGE_KEYS.confirmationSent);
      // The answer that finished it says who the person is now: it opens without asking the server again.
      if (done.me) qc.setQueryData(keys.me, done.me);
      else await qc.invalidateQueries({ queryKey: keys.me });
      const params = { orgId };
      // Wherever it opens, on its arrival (ADR 0160): the beta notice, then Connect an AI; the notice alone once the
      // rail offered the AI (in this visit, or left for later in an earlier one), or when the plan was left for later
      // (the right hand's card leads with the plan).
      const offered = aiSeen || aiLaterSaved || how !== "done";
      const search = { [ARRIVAL_PARAM]: offered ? ARRIVAL_BETA_ONLY : 1 } as never;
      if (how !== "skip-ai" && landingOf(how === "skipped" ? "skipped" : "done", personalNow) === "rightHand" && done.agentId)
        await navigate({ to: "/org/$orgId/dm/agent/$agentId", params: { ...params, agentId: done.agentId }, search });
      else
        await navigate({ to: "/org/$orgId/home", params, search });
    },
    // Opening by itself, a failure is the page's own (below); from a button, the step stays and a toast says it.
    onError: (e, how) => {
      leaving.current = false;
      if (how !== "done") toast.show(errorText(t, e), "error");
    },
  });

  const save = useMutation({
    // Owning a workspace that waits for its first plan is said in place, with the way to that plan.
    meta: { silent: true },
    onError: (e) => {
      if (errorCode(e) !== "org.create_unpaid") toast.show(errorText(t, e), "error");
    },
    mutationFn: async () => {
      let made: string | null = null;
      // `/me` as the create answered it, while nothing after it changed it.
      let now: Me | undefined;
      if (!orgId) {
        const res = await api.orgs.create({
          // "For me": no name to ask, the word for it in the person's language (renamed in Settings like any other).
          name: personal ? t("onboarding.personal.name") : org.trim(),
          ...(personal ? { mode: "personal" as const } : { context: context.trim() || undefined }),
          timezone: zoneNow,
          // The person's own zone, detected here: agents read the local time from it (the language is `useServerLocale`'s).
          memberTimezone: browserTimezone() ?? undefined,
        });
        made = res.organization.id;
        now = res.me;
      } else {
        await api.orgs.updateSettings(orgId, {
          name: org.trim(),
          ...(contextDraft !== null ? { context: context.trim() } : {}),
          ...(zone ? { timezone: zone } : {}),
        });
      }
      const id = made ?? orgId ?? "";
      // The logo needs the workspace: picked before it existed, sent now (a team's only: "For me" asks none).
      if (logoDirty && !personal) {
        if (logo) await api.orgs.setLogo(id, logo);
        else await api.orgs.removeLogo(id);
        setLogoDirty(false);
        now = undefined;
      }
      // The next step's data first, then the workspace among mine: it shows at once, with no splash between.
      await Promise.all([
        qc.prefetchQuery({ queryKey: keys.settings(id), queryFn: () => api.orgs.settings(id) }),
        qc.prefetchQuery({ queryKey: keys.agents(id), queryFn: () => api.agents.list(id) }),
      ]);
      if (now) qc.setQueryData(keys.me, now);
      else await qc.invalidateQueries({ queryKey: keys.me });
      if (made) setCreated(made);
      setOrg(null);
      setContext(null);
      setPicked(null);
    },
  });

  // Everything answered, and no plan to wait for: it opens (back here from another tab, or paid meanwhile), unless the
  // person stays on a step of their own.
  const opening = useRef(false);
  useEffect(() => {
    if (!known || at !== "open" || stay || opening.current || deleting !== null) return;
    opening.current = true;
    open.mutate("done");
  });

  // Give the new heading focus when a step (or the AI connection result) replaces the previous controls.
  const shownView = step === "ai" ? `${step}:${aiDone}` : step;
  const shown = useRef<string | null>(null);
  useEffect(() => {
    if (!known || shown.current === shownView) return;
    if (shown.current !== null) titleRef.current?.focus();
    shown.current = shownView;
  });
  // A dialog that closes on a button gone meanwhile (Connect an AI, over the cards its pick replaced) leaves the focus
  // on the page's body, out of the step's reach: Enter there still presses the step's action.
  useEffect(() => {
    const press = (e: KeyboardEvent) => {
      if (e.key !== "Enter" || e.shiftKey || e.isComposing || e.defaultPrevented || e.target !== document.body) return;
      // Never behind a dialog or a menu still open.
      if (document.querySelector("[role='dialog'], [role='menu']")) return;
      const go = mainRef.current?.querySelector<HTMLButtonElement>("button[data-enter]:not(:disabled)");
      if (!go) return;
      e.preventDefault();
      go.click();
    };
    document.addEventListener("keydown", press);
    return () => document.removeEventListener("keydown", press);
  }, []);

  const refused = save.error;
  const unpaidElsewhere =
    refused instanceof ApiError && refused.code === "org.create_unpaid" && typeof refused.params?.orgId === "string"
      ? refused.params.orgId
      : null;
  const busy = save.isPending || open.isPending;
  const savedLogo = logoDirty ? null : mine?.logo;
  const shownLogo = logo ? `data:${logo.contentType};base64,${logo.imageBase64}` : (savedLogo ?? null);
  const lodiName = lodi?.name ?? RIGHT_HAND_DEFAULT.name;

  // A read that failed, or a workspace that wouldn't open by itself: said, with the way on, never a sky that waits for
  // nothing (from a button, its toast says it and the step stays).
  const failed =
    orgId === null
      ? null
      : ([settings, agents].find((q) => q.data === undefined && q.isError && !q.isFetching)?.error ??
        (at === "open" && open.isError && open.variables === "done" ? open.error : null));
  if (failed)
    return (
      <div className="page page--mid">
        <ErrorState
          error={failed}
          onRetry={() => {
            opening.current = false;
            open.reset();
            if (settings.isError) void settings.refetch();
            if (agents.isError) void agents.refetch();
          }}
        />
      </div>
    );
  if (!me.data || !known || (at === "open" && !stay && deleting === null)) return <BootSplash />;

  const lodiWords = { first: firstName(me.data.user.name), ...agentGrammar(lodiName) };
  const aiLabel = aiNamed && aiNamed !== "key" ? planLabel(t, aiNamed) : null;
  const say = step === "presets"
    ? t("onboarding.flow.presets.say")
    : step === "plan"
      ? t("onboarding.flow.lodi.planThen", lodiWords)
      : step === "ai"
        ? t(aiDone ? "onboarding.flow.lodi.connected" : "onboarding.flow.lodi.aiPersonal", lodiWords)
        : t("onboarding.flow.lodi.name", lodiWords);
  const title = step === "purpose"
    ? t("onboarding.purpose.title")
    : step === "name"
      ? t("onboarding.workspace.title")
      : step === "plan"
        ? t(personalNow ? "onboarding.flow.plan.titlePersonal" : "onboarding.flow.plan.title")
        : step === "presets"
          ? t("onboarding.flow.presets.title")
          : aiDone
            ? t("onboarding.flow.ai.doneTitlePersonal", agentGrammar(lodiName))
            : personalNow
              ? t("onboarding.flow.ai.titlePersonal")
              : t("onboarding.flow.ai.titleAlone", agentGrammar(lodiName));
  const lead = step === "purpose"
    ? t("onboarding.purpose.lead")
    : step === "name"
      ? t("onboarding.workspace.lead")
      : step === "plan"
        ? t(personalNow ? "onboarding.flow.plan.leadPersonal" : "onboarding.flow.plan.leadAlone")
        : step === "presets"
          ? t("onboarding.flow.presets.lead")
          : aiDone
            ? aiLabel
              ? t("onboarding.flow.ai.doneLead", { label: aiLabel })
              : t("onboarding.flow.ai.doneLeadAny")
            : t("onboarding.flow.ai.lead");
  const goTo = (destination: RailStep | undefined) => {
    if (!destination) return;
    if (destination === "purpose" && orgId === null) {
      setForTeam(false);
      return;
    }
    if (destination === "presets") setAiAdvanced(true);
    setPicked(destination === at ? null : destination);
  };
  const canVisit = (candidate: RailStep) =>
    (candidate !== "purpose" || orgId === null) &&
    (candidate !== "plan" || held) &&
    (candidate !== "presets" || at === "presets");
  const previous = steps.slice(0, index).reverse().find(canVisit);
  let back: { label: string; go: () => void } | null = null;
  if (step === "purpose" && leaveTo) {
    const href = returnPlace(leaveTo.id);
    back = { label: t("onboarding.leave", { name: leaveTo.name }), go: () => void navigate({ href }) };
  } else if (previous) {
    back = { label: t("onboarding.back"), go: () => goTo(previous) };
  }
  const answered = Math.min(reached, steps.length);
  const stepName = (s: RailStep) =>
    s === "purpose"
      ? t("onboarding.flow.steps.namePersonal")
      : s === "presets"
        ? t("onboarding.flow.presets.title")
        : personalNow && (s === "plan" || s === "ai")
          ? t(`onboarding.flow.steps.${s}Personal`)
          : t(`onboarding.flow.steps.${s}`);
  const values: Partial<Record<RailStep, string>> = {
    purpose: t(personalNow ? "onboarding.purpose.personal" : "onboarding.purpose.team"),
    name: name || undefined,
    plan: steps.includes("plan") && !held ? t("onboarding.railValue.on") : undefined,
    ai: thinksNow ? aiLabel ?? t("onboarding.railValue.ai") : undefined,
  };

  const preview: PreviewAgent[] = [
    { name: lodiName, role: t("rightHand.label"), look: lookOf(lodi) ?? RIGHT_HAND_DEFAULT.appearance },
    ...team.map((agent) => ({ name: agent.name, role: agent.role, look: lookOf(agent) })),
  ];
  const rightHand = lodi ? [lodi] : [];

  return (
    <div className="onb onb--rail">
      <aside className="onb__rail">
        <div className="onb__top">
          <span className="onb__brand">
            <Logo size={22} />
          </span>
          <Menu
            align={phone ? "end" : "start"}
            label={t("onboarding.flow.account")}
            trigger={
              <button type="button" className="onb__me" aria-label={t("onboarding.flow.account")}>
                <Avatar kind="person" name={me.data.user.name} imageUrl={me.data.user.image} size="sm" />
              </button>
            }
          >
            {orgId && (
              <MenuItem icon="trash" danger onSelect={() => setDeleting(1)}>
                {t("onboarding.deleteLink")}
              </MenuItem>
            )}
            {accountOutOfReach(me.data.organizations) && (
              <MenuItem icon="trash" danger onSelect={() => setAccount("delete")}>
                {t("deleteAccount.menu")}
              </MenuItem>
            )}
            <MenuItem icon="arrowRight" danger onSelect={() => void signOut()}>
              {t("auth.signOut")}
            </MenuItem>
          </Menu>
        </div>
        {!phone && <OnboardingProgress done={answered} total={steps.length} current={index} />}
        <StepRail
          label={t("onboarding.stepsLabel")}
          current={index}
          onGo={(i) => goTo(steps[i])}
          doneLabel={t("onboarding.stepDone")}
          steps={steps.map((s, i) => ({
            id: s,
            name: stepName(s),
            done: i < reached,
            disabled: i > reached || busy || !canVisit(s),
            value: values[s],
          }))}
        />
        {!phone && (
          <div className="onb__lodi">
            <AgentAvatar name={lodiName} appearance={lookOf(lodi) ?? RIGHT_HAND_DEFAULT.appearance} size="sm" />
            <p className="onb__say" key={step}>
              {say}
            </p>
          </div>
        )}
        {orgId && (
          <DeleteWorkspaceDialog
            org={{ id: orgId, name }}
            open={deleting !== null}
            onClose={() => setDeleting(null)}
            onDeleted={() => void navigate({ to: "/org/$orgId", params: { orgId } })}
          />
        )}
        <DeleteAccountDialog open={account === "delete"} onClose={() => setAccount(null)} />
      </aside>

      <main
        className="onb__main"
        ref={mainRef}
        onKeyDown={(e) => {
          // Enter can trigger the step's shortcut when focus is outside another interactive control.
          const el = e.target as HTMLElement;
          if (e.key !== "Enter" || e.shiftKey || e.nativeEvent.isComposing) return;
          // On an answer of the first question too: a radio takes no Enter of its own (it stops it, though).
          const answer = el.matches("[role='radio']");
          if (!answer && e.defaultPrevented) return;
          if (
            !answer &&
            el.closest("button, a, textarea, [role='dialog'], [role='listbox'], [role='combobox']")
          )
            return;
          const go = e.currentTarget.querySelector<HTMLButtonElement>("button[data-enter]:not(:disabled)");
          if (!go) return;
          e.preventDefault();
          go.click();
        }}
      >
        <div className="onb__cols">
          {phone && (
            <div className="onb__phone-top">
              <OnboardingProgress done={answered} total={steps.length} current={index} />
              <div className="onb__lodi-row">
                <AgentAvatar name={lodiName} appearance={lookOf(lodi) ?? RIGHT_HAND_DEFAULT.appearance} size="sm" />
                <p className="onb__say" key={step}>
                  {say}
                </p>
              </div>
            </div>
          )}
          <StepHost step={step} org={mine} me={me.data}>
            <div className="onb__body" key={step}>
              {back && (
                <button type="button" className="onb__back" onClick={back.go} disabled={busy}>
                  <Icon name="chevronLeft" size={16} />
                  {back.label}
                </button>
              )}
              <header className="onb__head">
                {!asking && (
                  <p className="onb__eyebrow">{t("onboarding.flow.stepOf", { n: index + 1, total: steps.length })}</p>
                )}
                <h1 className="onb__title" ref={titleRef} tabIndex={-1}>
                  {title}
                </h1>
                <p className="onb__lead">{lead}</p>
              </header>

              {(step === "purpose" || step === "name") && (
                <>
                  <Locked when={busy}>
                    {asking ? (
                      <div className="onb__answer">
                        <PurposeCards value={purpose} onValueChange={setPurpose} you={you} />
                        {unpaidElsewhere && (
                          <UnpaidNote
                            orgId={unpaidElsewhere}
                            name={me.data.organizations.find((o) => o.id === unpaidElsewhere)?.name}
                            billing={Boolean(authConfig?.billingUrl)}
                          />
                        )}
                      </div>
                    ) : (
                      <div className="onb__answer">
                        <div className="onb__ws">
                          <button
                            type="button"
                            className="onb__logo"
                            aria-label={
                              logo ? t("onboarding.workspace.logoChange") : t("onboarding.workspace.logoPick")
                            }
                            title={t("onboarding.workspace.logoHelp")}
                            onClick={() => setLogoOpen(true)}
                          >
                            {shownLogo ? (
                              <OrgMark logo={shownLogo} size={48} />
                            ) : org.trim() || name ? (
                              <span className="onb__logo-letter">{(org.trim() || name).charAt(0).toUpperCase()}</span>
                            ) : (
                              <Icon name="upload" />
                            )}
                          </button>
                          <Input
                            className="onb__big"
                            aria-label={t("onboarding.workspace.name")}
                            value={org}
                            onChange={(e) => setOrg(e.target.value)}
                            placeholder={t("onboarding.workspace.namePlaceholder")}
                            spellCheck={false}
                            autoFocus
                          />
                        </div>
                        <Field label={t("onboarding.workspace.contextOptional")} id="workspace-context">
                          <Textarea
                            value={context}
                            maxLength={500}
                            rows={2}
                            onChange={(event) => setContext(event.target.value)}
                          />
                        </Field>
                        {zoneOpen ? (
                          <Field
                            label={t("onboarding.workspace.timezone")}
                            id="org-zone"
                            hint={{
                              name: t("common.about", { label: t("onboarding.workspace.timezone") }),
                              text: t("onboarding.workspace.timezoneHelp"),
                            }}
                          >
                            <ZonePicker
                              id="org-zone"
                              value={zoneNow}
                              onChange={setZone}
                              suggested={[browserTimezone() ?? "", "UTC"].filter(Boolean)}
                            />
                          </Field>
                        ) : (
                          <p className="onb__help">
                            {t("onboarding.workspace.zone", { zone: timezoneName(zoneNow, i18n.language) })} ·{" "}
                            <button type="button" className="ac-link onb__link" onClick={() => setZoneOpen(true)}>
                              {t("onboarding.workspace.zoneChange")}
                            </button>
                          </p>
                        )}
                        <ImageCropDialog
                          open={logoOpen}
                          onClose={() => setLogoOpen(false)}
                          title={t("orgLogo.title")}
                          help={t("orgLogo.help")}
                          placeholder={<OrgMark size={56} />}
                          hasImage={shownLogo !== null}
                          removeLabel={t("orgLogo.remove")}
                          format="image/png"
                          save={async (image) => {
                            setLogo(image);
                            setLogoDirty(true);
                          }}
                          remove={async () => {
                            setLogo(null);
                            setLogoDirty(true);
                          }}
                          onSaved={() => setLogoOpen(false)}
                          onRemoved={() => setLogoOpen(false)}
                        />
                        {unpaidElsewhere && (
                          <UnpaidNote
                            orgId={unpaidElsewhere}
                            name={me.data.organizations.find((o) => o.id === unpaidElsewhere)?.name}
                            billing={Boolean(authConfig?.billingUrl)}
                          />
                        )}
                      </div>
                    )}
                  </Locked>
                  {inviteOnly && (
                    <p className="onb__help onb__note ac-iconline" role="status">
                      <Icon name="lock" size={14} />
                      <span>
                        {authConfig?.supportEmail
                          ? t("onboarding.workspace.inviteOnlyAsk", { email: authConfig.supportEmail })
                          : t("onboarding.workspace.inviteOnly")}
                      </span>
                    </p>
                  )}
                  {(confirmationSent || confirmFirst) && (
                    <p className="onb__help onb__note ac-iconline" role="status">
                      <Icon name="mail" size={14} />
                      <span>
                        {confirmationSent
                          ? t("onboarding.confirmation.sent", { email })
                          : t("onboarding.confirmation.first")}
                        {!resend.data && (
                          <>
                            <br />
                            <button
                              type="button"
                              className="ac-link onb__link"
                              onClick={() => resend.mutate()}
                              disabled={resend.isPending}
                            >
                              {t("auth.sendNewLink")}
                            </button>
                          </>
                        )}
                      </span>
                    </p>
                  )}
                  <div className="onb__actions">
                    <Button
                      variant="primary"
                      block
                      kbd={["↵"]}
                      data-enter=""
                      onClick={() => (asking && !personal ? setForTeam(true) : save.mutate())}
                      disabled={(asking ? !purpose : !org.trim()) || inviteOnly || busy}
                      loading={save.isPending}
                    >
                      {t("onboarding.actions.continue")}
                    </Button>
                  </div>
                </>
              )}
              {step === "plan" && orgId && (
                <PlanStep
                  orgId={orgId}
                  team={team.length > 0 ? team : rightHand}
                  offer={offer}
                  skipping={open.isPending}
                  onSkip={() => open.mutate("skipped")}
                  next
                  confirming={paid}
                  personal={personalNow}
                />
              )}
              {step === "ai" && orgId && (
                <AiStep
                  orgId={orgId}
                  agents={rightHand}
                  done={aiDone}
                  leaving={open.isPending}
                  picked={aiNamed}
                  next
                  onConnected={(choice) => setAiPick({ choice, key: keyNow })}
                  onLater={() => open.mutate("skip-ai")}
                  onEnter={() => goTo("presets")}
                />
              )}
              {step === "presets" && thinksNow && orgId && (
                <OnboardingPresets orgId={orgId} finishing={open.isPending} onFinish={() => open.mutate("later")} />
              )}
            </div>
            {wide &&
              (step === "ai" && mine ? (
                <AiStage agents={rightHand} lead={lodi} awake={aiDone} personal={personalNow} />
              ) : (
                <OnboardingPreview
                  org={name || t("onboarding.flow.preview.name")}
                  logo={shownLogo}
                  // A personal workspace's #general is out of sight (ADR 0159).
                  channels={personalNow ? [] : ["general"]}
                  fresh={[]}
                  orgId={team.length > 0 && !personalNow ? orgId : null}
                  agents={preview}
                  person={me.data.user}
                  personal={personalNow}
                />
              ))}
          </StepHost>
        </div>
      </main>
    </div>
  );
}

/** The AI and preset steps need workspace context before the workspace shell opens. */
function StepHost({
  step,
  org,
  me,
  children,
}: {
  step: RailStep;
  org: OrgSummary | undefined;
  me: Me;
  children: ReactNode;
}) {
  if (org && step === "ai")
    return (
      <AiHost org={org} me={me}>
        {children}
      </AiHost>
    );
  if (org && step === "presets")
    return (
      <OrgProvider org={org} me={me}>
        {children}
      </OrgProvider>
    );
  return <>{children}</>;
}

/**
 * Owning a workspace that waits for its first plan: said in place, with the way into that workspace (someone who came
 * back to this page after making it was looking for it, not for another) and the way to its plan.
 */
function UnpaidNote({ orgId, name, billing }: { orgId: string; name: string | undefined; billing: boolean }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  return (
    <>
      <p className="onb__status ac-iconline" role="alert">
        <Icon name="pause" size={14} />
        <span>{t("standing.createUnpaid")}</span>
      </p>
      {(name || billing) && (
        <div className="row">
          {name && (
            <Button onClick={() => void navigate({ href: returnPlace(orgId) })}>
              {t("standing.createUnpaidOpen", { name })}
            </Button>
          )}
          {billing && (
            <BillingLinkButton orgId={orgId} from="new_workspace" icon="external">
              {t("standing.billing.cta.payment")}
            </BillingLinkButton>
          )}
        </div>
      )}
    </>
  );
}
