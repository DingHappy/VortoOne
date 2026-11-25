import { cx } from "../cx";
import type { BadgePresence } from "../presence/badge";

export type { BadgePresence } from "../presence/badge";

export interface PresenceBadgeProps {
  presence: BadgePresence;
  /**
   * The badge's size: `sm` 9 px across (faces up to 36 px: the sidebar, lists, messages; 11 px with its ring), `lg`
   * 1.5 times that (the profile cards' 56 px faces and larger).
   */
  size?: "sm" | "lg";
  /** The one-pixel ring in the surface's color (`--presence-ring`); off where the badge stands alone (a row's lead). */
  ring?: boolean;
  className?: string;
}

const SCALE = { sm: 1, lg: 1.5 } as const;
/** The badge's radius in its own units; the ring adds one around it. */
const R = 4.5;

/** The white mark inside, around the centre (0, 0): busy a bar, away a clock's hands, out an arrow out the door. */
const LINE_MARKS: Partial<Record<BadgePresence, string>> = {
  busy: "M-2 0H2",
  away: "M0 -2.2V0H1.9",
  out: "M-2 0H2M0.8 -1.3L2 0L0.8 1.3",
};

/**
 * The presence badge (ADR 0084): a round dot in the presence's color with a white mark (busy: a bar, away: a clock,
 * out of office: an arrow; an agent at work: three dots, one waiting on you: a small ring), an empty outline for
 * offline and a dotted one for hidden. The colors come from the stylesheet (`.ac-presence`), so it follows the theme.
 * Hidden from screen readers: words beside it say the presence.
 */
export function PresenceBadge({ presence, size = "sm", ring = true, className }: PresenceBadgeProps) {
  const half = R + (ring ? 1 : 0);
  const px = 2 * half * SCALE[size];
  const hollow = presence === "offline" || presence === "hidden";
  const line = LINE_MARKS[presence];
  return (
    <svg
      className={cx("ac-presence", className)}
      data-presence={presence}
      viewBox={`${-half} ${-half} ${2 * half} ${2 * half}`}
      width={px}
      height={px}
      aria-hidden="true"
      focusable="false"
    >
      {ring && <circle className="ac-presence__ring" r={half} />}
      {hollow ? (
        <circle
          className="ac-presence__outline"
          r={R - 0.6}
          strokeDasharray={presence === "hidden" ? "1.2 1.2" : undefined}
        />
      ) : (
        <circle className="ac-presence__base" r={R} />
      )}
      {line && <path className="ac-presence__markline" d={line} />}
      {presence === "working" &&
        [-2, 0, 2].map((x) => <circle key={x} className="ac-presence__mark" cx={x} r={0.75} />)}
      {presence === "waiting" && <circle className="ac-presence__markline" r={1.4} />}
    </svg>
  );
}
