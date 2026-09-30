import type { CSSProperties, ReactNode } from "react";

type BadgeTone = "neutral" | "accent" | "correct" | "review" | "wrong";

const BADGE_TONES: Record<BadgeTone, CSSProperties> = {
  neutral: {
    background: "var(--surface-raised)",
    color: "var(--text)",
    borderColor: "var(--border-default)",
  },
  accent: {
    background: "var(--accent-quiet)",
    color: "var(--text-accent)",
    borderColor: "var(--blue-tint-32)",
  },
  correct: {
    background: "color-mix(in srgb, var(--state-correct) 14%, transparent)",
    color: "var(--text-correct)",
    borderColor: "color-mix(in srgb, var(--state-correct) 32%, transparent)",
  },
  review: {
    background: "color-mix(in srgb, var(--state-review) 14%, transparent)",
    color: "var(--text-review)",
    borderColor: "color-mix(in srgb, var(--state-review) 32%, transparent)",
  },
  wrong: {
    background: "color-mix(in srgb, var(--state-wrong) 14%, transparent)",
    color: "var(--text-wrong)",
    borderColor: "color-mix(in srgb, var(--state-wrong) 32%, transparent)",
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
