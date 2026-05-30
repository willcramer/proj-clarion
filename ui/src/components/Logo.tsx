/**
 * Proj-Clarion mark.
 *
 * Same SVG as `/public/favicon.svg` but inlined as a React component so
 * the TopBar (and any future surface, login screen, OG image fallback,
 * empty-state hero) can render it without an extra HTTP request and can
 * size/colour it via props.
 *
 * The mark says "signal radiating from a source": an origin dot with
 * three concentric arcs opening rightward. Reads as a clarion call,
 * a Wi-Fi signal, or a heartbeat ping, all of which fit Proj-Clarion's
 * job of broadcasting customer-shaped telemetry into Grafana Cloud.
 *
 * Set `monochrome` when you want the mark to inherit the surrounding
 * text colour (e.g. inside a button), the orange highlight gets
 * dropped in that mode for a cleaner glyph.
 *
 * Colour palette (Signal direction):
 *   - `currentColor`, origin dot + middle + outer arc. Inherits from
 *                             the parent's `color` (typically
 *                             `var(--color-accent)`) so the mark tracks the
 *                             theme, teal `#2dd4bf` on dark, deeper teal
 *                             `#0d9488` on light. The wrapping brand tile
 *                             in Layout.tsx sets `color: var(--color-accent)`.
 *   - `#FF8833` (orange), inner-arc highlight (drops in monochrome).
 *                             Grafana brand, stays fixed across themes.
 */
import { useId } from "react";

import { cn } from "@/lib/cn";

export type LogoProps = {
  /** Pixel size, applied to both width and height. Default 24. */
  size?: number;
  /** Drop the orange highlight + halo and inherit `currentColor`. */
  monochrome?: boolean;
  /** Optional Tailwind className for layout/positioning. */
  className?: string;
  /** Decorative by default. Pass a label to make it semantic. */
  title?: string;
};

export function Logo({ size = 24, monochrome = false, className, title }: LogoProps) {
  // Stable, render-pure unique suffix so multiple Logo instances on the
  // same page don't collide on the gradient id (would otherwise share the
  // halo). useId is SSR-safe and deterministic; strip its colons so the
  // value is a clean `url(#…)` reference.
  const id = `clarion-halo-${useId().replace(/:/g, "")}`;

  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 32 32"
      width={size}
      height={size}
      className={cn("shrink-0", className)}
      role={title ? "img" : "presentation"}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      fill="none"
    >
      {title && <title>{title}</title>}
      {!monochrome && (
        <defs>
          <radialGradient id={id} cx="35%" cy="50%" r="55%">
            <stop offset="0%"   stopColor="currentColor" stopOpacity="0.35" />
            <stop offset="60%"  stopColor="currentColor" stopOpacity="0.05" />
            <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
          </radialGradient>
        </defs>
      )}
      {!monochrome && <circle cx="11" cy="16" r="14" fill={`url(#${id})`} />}
      {/* Origin dot, inherits accent via currentColor */}
      <circle
        cx="10" cy="16" r="2.4"
        fill="currentColor"
      />
      {/* Inner arc, Grafana orange highlight (drops in monochrome mode) */}
      <path
        d="M 13.5 12.6 A 4 4 0 0 1 13.5 19.4"
        stroke={monochrome ? "currentColor" : "#FF8833"}
        strokeWidth="2.2"
        strokeLinecap="round"
        fill="none"
      />
      {/* Middle arc */}
      <path
        d="M 17 9.2 A 8 8 0 0 1 17 22.8"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        fill="none"
        opacity={monochrome ? "0.7" : "0.78"}
      />
      {/* Outer arc */}
      <path
        d="M 20.5 5.8 A 12 12 0 0 1 20.5 26.2"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        fill="none"
        opacity={monochrome ? "0.4" : "0.42"}
      />
    </svg>
  );
}
