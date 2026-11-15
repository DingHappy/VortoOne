import { cx } from "../cx";
import { Icon } from "../icons/Icon";
import { Avatar } from "./Avatar";

/** The same live slime as the avatars, with a separate search glyph. */
export function AgentSearching({ className }: { className?: string }) {
  return (
    <span
      className={cx("ac-searchpx", className)}
      aria-hidden="true"
      style={{
        width: 88,
        height: 64,
        display: "inline-block",
        position: "relative",
      }}
    >
      <Avatar
        kind="agent"
        name="VortoOne"
        size="lg"
        framed={false}
        state="thinking"
      />
      <Icon
        name="search"
        size={20}
        style={{ position: "absolute", right: 10, bottom: 0 }}
      />
    </span>
  );
}
