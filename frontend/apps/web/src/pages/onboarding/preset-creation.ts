import type { Agent, AgentInput, ApiClient } from "@vortoone/client";
import type { Preset } from "@vortoone/client/lib/onboarding";

export function agentHandles(agents: readonly Pick<Agent, "handle" | "previousHandles">[]): Set<string> {
  return new Set(agents.flatMap((agent) => [agent.handle, ...(agent.previousHandles ?? [])]));
}

export async function createSelectedPresets(
  api: Pick<ApiClient["agents"], "list" | "create">,
  orgId: string,
  selected: readonly Preset[],
  input: (preset: Preset) => AgentInput,
) {
  const existing = agentHandles(await api.list(orgId));
  const agents: Agent[] = [];
  const failures = new Map<Preset, unknown>();
  for (const preset of selected) {
    if (existing.has(preset)) continue;
    try {
      // Agent names and roles follow the locale, but the server uses a unique handle.
      const created = await api.create(orgId, { ...input(preset), handle: preset });
      if (created?.handle !== preset) throw new Error("The server did not confirm the new agent");
      agents.push(created);
      existing.add(preset);
    } catch (error) {
      failures.set(preset, error);
    }
  }
  // A request can fail after the agent was registered. Check the roster before asking the user to retry it.
  if (failures.size > 0) {
    const latest = await api.list(orgId).catch(() => []);
    for (const [preset] of failures) {
      const found = latest.find((agent) => agent.handle === preset || agent.previousHandles?.includes(preset));
      if (!found) continue;
      agents.push(found);
      failures.delete(preset);
    }
  }
  return { agents, failures, completed: selected.filter((preset) => !failures.has(preset)) };
}
