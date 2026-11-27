import type { TFunction } from "i18next";
import type { Agent, AgentInput, OrgStanding, Routine } from "../api/types";
import { billingHoldOf } from "./billing";
import { AUTO } from "./providers";
import { handleFrom } from "./text";

/** The ready-made agents a new workspace is offered (in its right hand's DM, ADR 0137), in this order. */
export const PRESETS = ["theo", "ada", "lia", "atlas"] as const;
export type Preset = (typeof PRESETS)[number];

/** Each preset's own look, saved on the agent it creates (the emails draw the same faces). */
export const PRESET_LOOK: Record<Preset, { color: string; eyes: string; accessory: string }> = {
  theo: { color: "indigo", eyes: "round", accessory: "cap" },
  ada: { color: "green", eyes: "wide", accessory: "headphones" },
  lia: { color: "rose", eyes: "dots", accessory: "flower" },
  atlas: { color: "amber", eyes: "round", accessory: "antenna" },
};

/** The "Criar agente" role whose instructions each preset starts with; Lia has her own. */
const PRESET_ROLE: Record<Preset, "pm" | "dev" | "ops" | null> = { theo: "pm", ada: "dev", lia: null, atlas: "ops" };

/** A preset as the create route takes it: on Automático and the workspace's default key. */
export function presetInput(t: TFunction, preset: Preset): AgentInput {
  const name = t(`onboarding.presets.${preset}.name`);
  const role = PRESET_ROLE[preset];
  return {
    name,
    handle: handleFrom(name),
    role: t(`onboarding.presets.${preset}.role`),
    instructions: role ? t(`newAgent.roles.${role}.instructions`) : t("onboarding.presets.lia.instructions"),
    model: AUTO,
    credentialId: null,
    appearance: PRESET_LOOK[preset],
  };
}

/**
 * The onboarding's steps, in order (the server's `ONBOARDING_STEPS`, `packages/db/src/schema/tenancy.ts`, keeps the
 * furthest one reached).
 * "import" reads another product's export (apps/web/src/lib/import); its
 * agents arrive chosen in "agents", which only picks and creates the team.
 * "computer" comes before "ai": a computer the agents can use helps that step,
 * whose plan sign-in runs on it.
 * The web's rail reads them back as answers with no data of their own (`RailFunnel`, ADR 0162): "import" for a team
 * that brings no agents and goes on to its templates, "agents" for a team step left with no agents (a personal
 * workspace's "no agents yet", a team's "Skip this step" or "Create the agents later"), "computer" for a plan step that
 * was shown, "ai" for a team's AI step left for later ("Connect later", the people step next) and "team" for its
 * invitations sent.
 */
export const ONBOARDING_STEPS = [
  { id: "workspace", optional: false },
  { id: "context", optional: false },
  { id: "import", optional: true },
  { id: "agents", optional: false },
  { id: "computer", optional: true },
  { id: "ai", optional: false },
  { id: "team", optional: true },
  { id: "done", optional: false },
] as const;
export type OnboardingStepId = (typeof ONBOARDING_STEPS)[number]["id"];

export const stepIndex = (id: OnboardingStepId) => ONBOARDING_STEPS.findIndex((s) => s.id === id);

export type RailStep = "purpose" | "name" | "business" | "you" | "team" | "plan" | "ai" | "people" | "presets";
export const railSteps = (f: {
  plan: boolean;
  ai: boolean;
  personal: boolean;
  purpose?: boolean;
  people?: boolean;
  you?: boolean;
}): RailStep[] => [
  ...(f.purpose ? (["purpose"] as const) : []),
  ...(f.personal ? (f.purpose ? [] : (["name"] as const)) : (["name", "business"] as const)),
  ...(f.you ? (["you"] as const) : []),
  "team",
  ...(f.plan ? (["plan"] as const) : []),
  ...(f.ai ? (["ai"] as const) : []),
  ...(f.people ? (["people"] as const) : []),
];

/**
 * The step to show, read from the workspace's data (it exists, has its context, the person said who they are, has a
 * team, waits for its plan, its team thinks, its people were invited), so a workspace left at any step resumes there;
 * "open" once it is ready to open. `you`, `ai` and `people` say whether the rail has those steps at all.
 */
export function railStepAt(f: {
  created: boolean;
  askingPurpose?: boolean;
  context: boolean;
  you?: boolean;
  /** The person's step answered: their own area said, or skipped (`users.work_area`). */
  about?: boolean;
  team: boolean;
  waitsForPlan: boolean;
  personal: boolean;
  ai: boolean;
  thinks: boolean;
  people?: boolean;
  invited?: boolean;
}): RailStep | "open" {
  if (!f.created) return f.askingPurpose ? "purpose" : "name";
  if (!f.personal && !f.context) return "business";
  if (f.you && !f.about) return "you";
  if (!f.team) return "team";
  if (f.waitsForPlan) return "plan";
  if (f.ai && !f.thinks) return "ai";
  return f.people && !f.invited ? "people" : "open";
}

export interface RailFunnel {
  /** Whether the right hand can use AI. Undefined while its status is being read. */
  thinks: boolean | undefined;
  /** Last saved server step, including progress from the older workspace flow. */
  passed: OnboardingStepId | undefined;
}

/**
 * The web asks for purpose and optional workspace details, honors an active billing hold, and offers presets once AI
 * is ready. The same persisted step IDs are used by older clients. A saved AI deferral or completed people step still
 * opens the workspace. A null funnel uses the phone's existing path through business, team and billing.
 */
export function railState(f: {
  orgId: string | null;
  standing: OrgStanding | undefined;
  billingUrl: string | null | undefined;
  context: string;
  team: number;
  picked: RailStep | null;
  personal?: boolean;
  /** A team workspace's purpose was chosen, and its name is now being entered. */
  namingTeam?: boolean;
  funnel: RailFunnel | null;
}) {
  const waitsForPlan = f.orgId ? billingHoldOf(f.standing) === "payment" : Boolean(f.billingUrl);
  const funnel = f.funnel;
  const personal = Boolean(f.personal);
  const passed = stepIndex(funnel?.passed ?? "workspace");
  const computer = stepIndex("computer");
  // A plan step shown once stays on the rail, answered, after paying; a server that never held the workspace has none.
  // Past it ("ai", "team"), only a server with billing showed it: a self-hosted one keeps none.
  const hasPlanStep =
    waitsForPlan || (funnel !== null && (passed === computer || (passed > computer && Boolean(f.billingUrl))));
  const steps: RailStep[] = funnel
    ? ["purpose", ...(personal ? [] : ["name"] as const), ...(hasPlanStep ? ["plan"] as const : []), "ai", "presets"]
    : railSteps({ plan: waitsForPlan, ai: false, personal: false });
  let at: RailStep | "open";
  if (funnel) {
    if (f.orgId === null) at = !personal && f.namingTeam ? "name" : "purpose";
    else if (waitsForPlan) at = "plan";
    else if (passed >= stepIndex("ai")) at = "open";
    else at = funnel.thinks ? "presets" : "ai";
  } else {
    at = personal && f.orgId !== null ? "open" : railStepAt({
      created: f.orgId !== null,
      context: f.context.trim() !== "",
      team: f.team > 0,
      waitsForPlan,
      personal: false,
      ai: false,
      thinks: false,
    });
  }
  const reached = at === "open" ? steps.length : steps.indexOf(at);
  const back = f.picked ? steps.indexOf(f.picked) : -1;
  const step = back >= 0 && back < reached ? steps[back] : steps[Math.min(reached, steps.length - 1)];
  return {
    steps,
    at,
    reached,
    step: step ?? "name",
    index: steps.indexOf(step ?? "name"),
    waitsForPlan,
    hasPlanStep,
    /** The right hand can use AI. */
    thinks: funnel?.thinks === true,
  };
}

/** What an agent does first, as the plan step lists it: its first routine, else its role. */
export const firstJob = (
  agent: Pick<Agent, "id" | "role">,
  routines: readonly Pick<Routine, "agentId" | "name">[] = [],
) => routines.find((r) => r.agentId === agent.id)?.name ?? agent.role;

/**
 * Where the workspace opens when the onboarding ends: #general, or its right hand's DM when the plan was left. A
 * personal workspace always opens in its right hand's DM, its card there (ADR 0159): its #general is out of sight.
 */
export const landingOf = (how: "paid" | "done" | "skipped", personal = false) =>
  personal || how === "skipped" ? "rightHand" : "general";
