import type { CSSProperties, ReactNode } from "react";

type BadgeTone = "neutral" | "accent" | "correct" | "review" | "wrong";

const BADGE_TONES: Record<BadgeTone, CSSProperties> = {
  neutral: {
    background: "var(--surface-raised)",
    color: "var(--text-body)",
    borderColor: "var(--border-default)",
  },
  accent: {
    background: "var(--accent-quiet)",
    color: "var(--blue-300)",
    borderColor: "var(--blue-tint-32)",
  },
  correct: {
    background: "rgba(127,179,163,.14)",
    color: "var(--state-correct)",
    borderColor: "rgba(127,179,163,.32)",
  },
  review: {
    background: "rgba(232,197,71,.14)",
    color: "var(--state-review)",
    borderColor: "rgba(232,197,71,.32)",
  },
  wrong: {
    background: "rgba(229,105,91,.14)",
    color: "var(--state-wrong)",
    borderColor: "rgba(229,105,91,.32)",
  },
};

/** A small micro-caps label: a status, a mode, a verdict. */
export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: BadgeTone;
}) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "var(--space-2)",
        height: 20,
        padding: "0 8px",
        borderRadius: "var(--radius-xs)",
        border: "1px solid",
        fontFamily: "var(--font-sans)",
        fontSize: "var(--caps-size)",
        fontWeight: "var(--weight-medium)",
        letterSpacing: "var(--caps-track)",
        textTransform: "uppercase",
        ...BADGE_TONES[tone],
      }}
    >
      {children}
    </span>
  );
}
