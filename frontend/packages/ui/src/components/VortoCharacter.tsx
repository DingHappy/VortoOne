import type { AgentAppearance } from "./AgentLook";
import { Avatar, type AgentState } from "./Avatar";

/** Compatibility entry for the runtime prototype; all characters use the live renderer. */
export function VortoCharacter({
  appearance,
  state = "idle",
}: {
  appearance?: AgentAppearance;
  state?: AgentState;
}) {
  return (
    <Avatar
      kind="agent"
      name="VortoOne"
      appearance={appearance}
      state={state}
      size="xl"
      framed={false}
      className="vorto-character"
      style={{ width: "100%", height: "100%" }}
    />
  );
}
