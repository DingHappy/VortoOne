import { type CSSProperties, useLayoutEffect, useRef } from "react";
import { drawAgent } from "../agent/engine.js";
import { cx } from "../cx";

/** Same material, proportions and neutral face as the live slime. */
export function LogoMark({
  size = 32,
  className,
  label,
}: {
  size?: number;
  className?: string;
  label?: string;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useLayoutEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1),
      side = Math.round(size * dpr);
    el.width = el.height = side;
    const ctx = el.getContext("2d");
    if (ctx) drawAgent(ctx, 0, 0, side);
  }, [size]);
  return (
    <canvas
      ref={canvas}
      className={cx("ac-logo__mark", className)}
      style={{ width: size, height: size }}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    />
  );
}

export interface LogoProps {
  variant?: "main" | "expanded";
  mono?: boolean;
  size?: number;
  className?: string;
  style?: CSSProperties;
}

export function Logo({
  variant = "main",
  mono,
  size = 24,
  className,
  style,
}: LogoProps) {
  return (
    <span
      className={cx("ac-logo", mono && "ac-logo--mono", className)}
      style={{ fontSize: size, ...style }}
      role="img"
      aria-label="VortoOne"
      data-variant={variant}
    >
      <LogoMark size={size * 1.25} />
      <span aria-hidden="true">VortoOne</span>
    </span>
  );
}
