import { gradientOf, luminance } from "../agent/engine.js";
import { mix } from "./math";

/** A cool, quiet backdrop separates the character from its own identity color. */
export function characterBackdrop(
  color: string | undefined,
  dark: boolean,
): [string, string] {
  const lightness = luminance(gradientOf(color)[0]);
  // Custom near-black/white bodies need the opposite surface, even within the current theme.
  if (dark && lightness < 0.13) return ["#c3d0df", "#b4c6d8"];
  if (!dark && lightness > 0.9) return ["#62778c", "#4c637a"];
  return dark ? ["#172331", "#26384c"] : ["#f4f7fb", "#e7edf4"];
}

/** Subdued neutral light behind a separate character, rather than a second copy of its body color. */
export function characterGlow(
  color: string | undefined,
  surface: string,
): string[] {
  const background = mix(surface, surface, 0);
  const [, backdrop] = characterBackdrop(color, luminance(background) < 0.25);
  return [
    background,
    ...Array.from({ length: 7 }, (_, i) =>
      mix(background, backdrop, ((i + 1) / 7) * 0.7),
    ),
  ];
}
