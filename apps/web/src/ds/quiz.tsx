"use client";

import { useState, type ReactNode } from "react";
import { usePrefersReducedMotion } from "./motion";

type QuizState = "idle" | "selected" | "correct" | "wrong";

export function QuizOption({
  letter,
  children,
  state = "idle",
  onClick,
  disabled = false,
}: {
  letter: string;
  children: ReactNode;
  state?: QuizState;
  onClick?: () => void;
  disabled?: boolean;
}) {
  const [hover, setHover] = useState(false);

  const lit = hover && !disabled;

  // The letter takes the state's hue, lightened to read at text brightness;
  // the option's words stay at the one text colour in every state.
  const tone = {
    idle: {
      // Hover lifts the row onto the accent rather than merely a rung up the
      // ink ladder: an answer is a thing you are about to choose, and the
      // design system reserves blue for exactly that -- the one live action.
      border: lit ? "var(--blue-tint-32)" : "var(--border-subtle)",
      bg: lit ? "var(--blue-tint-08)" : "transparent",
      key: lit ? "var(--text-accent)" : "var(--text)",
    },
    selected: {
      border: "var(--blue-tint-32)",
      bg: "var(--blue-tint-08)",
      key: "var(--text-accent)",
    },
    correct: {
      border: "rgba(127,179,163,.4)",
      bg: "rgba(127,179,163,.1)",
      key: "var(--text-correct)",
    },
    wrong: {
      border: "rgba(229,105,91,.4)",
      bg: "rgba(229,105,91,.1)",
      key: "var(--text-wrong)",
    },
  }[state];

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-7)",
        width: "100%",
        padding: "var(--space-6) var(--space-7)",
        background: tone.bg,
        border: `1px solid ${tone.border}`,
        borderRadius: "var(--radius-lg)",
        cursor: disabled ? "default" : "pointer",
        color: "var(--text)",
        fontFamily: "var(--font-sans)",
        fontSize: "var(--text-lg)",
        textAlign: "left",
        // A soft accent halo on hover. No scale and no travel -- the system
        // rules both out -- so the row lights up where it stands.
        boxShadow: state === "idle" && lit ? "0 0 0 3px var(--blue-tint-08)" : "none",
        transition:
          "background var(--dur-fast) var(--ease-standard), border-color var(--dur-fast) var(--ease-standard), box-shadow var(--dur-fast) var(--ease-standard)",
      }}
    >
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: 24,
          height: 24,
          flex: "0 0 auto",
          borderRadius: "var(--radius-pill)",
          border: `1px solid ${tone.border}`,
          background: state === "idle" && lit ? "var(--blue-tint-16)" : "transparent",
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          color: tone.key,
          transition:
            "background var(--dur-fast) var(--ease-standard), color var(--dur-fast) var(--ease-standard), border-color var(--dur-fast) var(--ease-standard)",
        }}
      >
        {letter}
      </span>
      <span style={{ flex: 1, minWidth: 0 }}>{children}</span>
    </button>
  );
}

/** The looping film shown wherever a ring would turn with nothing to report. */
const LOADER_SRC = "/loader.webm";

/** Stroke of the ring, in px: the hairline from the reference material. */
const THICKNESS = 1.5;

/**
 * The thin concentric ring from the reference material.
 *
 * Determinate by default. `indeterminate` is for work whose duration genuinely
 * is not known -- generation runs in batches, so a determinate ring would sit
 * at zero for most of it and read as broken. It plays the loader film, or,
 * under reduced motion, shows a still quarter arc.
 *
 * This is the one place Examprep animates a loop, which its own guidance
 * otherwise rules out. The alternative on offer was a number that stays at 0/10
 * for a minute, and that says less honestly that anything is happening.
 */
export function ProgressRing({
  value = 0,
  size = 64,
  label,
  indeterminate = false,
}: {
  value?: number;
  size?: number;
  label?: ReactNode;
  indeterminate?: boolean;
}) {
  const radius = (size - THICKNESS) / 2;
  const circumference = 2 * Math.PI * radius;
  const shown = indeterminate ? 0.26 : Math.max(0, Math.min(1, value));
  const reduced = usePrefersReducedMotion();

  return (
    <span
      style={{
        position: "relative",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: size,
        height: size,
      }}
    >
      {/* Waiting with nothing to report is the film's job; a ring with a real
          fraction behind it stays a ring, because the fraction is the point.
          Reduced motion keeps the still arc below rather than a still film. */}
      {indeterminate && !reduced ? (
        <video
          src={LOADER_SRC}
          autoPlay
          muted
          loop
          playsInline
          preload="auto"
          aria-hidden="true"
          tabIndex={-1}
          style={{
            width: size,
            height: size,
            objectFit: "cover",
            borderRadius: "var(--radius-pill)",
          }}
        />
      ) : (
        <svg
          width={size}
          height={size}
          // Rotated so a fraction fills from twelve o'clock.
          style={{ transform: "rotate(-90deg)" }}
          aria-hidden="true"
        >
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke="var(--line-2)"
            strokeWidth={THICKNESS}
          />
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            stroke="var(--accent)"
            strokeWidth={THICKNESS}
            strokeDasharray={circumference}
            strokeDashoffset={circumference * (1 - shown)}
            strokeLinecap="round"
            style={{
              transition: "stroke-dashoffset var(--dur-slow) var(--ease-out)",
            }}
          />
        </svg>
      )}
      {label ? (
        <span
          style={{
            position: "absolute",
            fontFamily: "var(--font-mono)",
            fontSize: size > 52 ? 12 : 10,
            color: "var(--text)",
          }}
        >
          {label}
        </span>
      ) : null}
    </span>
  );
}
