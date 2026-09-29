"use client";

import {
  useState,
  type ButtonHTMLAttributes,
  type CSSProperties,
  type ReactNode,
} from "react";
import type { LucideIcon } from "lucide-react";
import { Icon } from "./icon";

type ButtonVariant = "primary" | "paper" | "secondary" | "ghost" | "outline";
type ButtonSize = "sm" | "md" | "lg";

const BTN_BASE: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  gap: "var(--space-3)",
  fontFamily: "var(--font-sans)",
  fontWeight: "var(--weight-medium)",
  letterSpacing: "var(--track-wide)",
  border: "1px solid transparent",
  borderRadius: "var(--radius-pill)",
  cursor: "pointer",
  transition:
    "background var(--dur-fast) var(--ease-standard), color var(--dur-fast) var(--ease-standard), border-color var(--dur-fast) var(--ease-standard)",
  whiteSpace: "nowrap",
  textDecoration: "none",
};

const BTN_SIZES: Record<ButtonSize, CSSProperties> = {
  sm: { height: 30, padding: "0 14px", fontSize: "var(--text-sm)" },
  md: { height: 38, padding: "0 20px", fontSize: "var(--text-md)" },
  lg: { height: 46, padding: "0 28px", fontSize: "var(--text-lg)" },
};

const BTN_VARIANTS: Record<ButtonVariant, CSSProperties> = {
  primary: {
    background: "var(--accent)",
    color: "#0A0A0B",
    borderColor: "var(--accent)",
  },
  paper: {
    background: "var(--paper-0)",
    color: "var(--text-oncolor)",
    borderColor: "var(--paper-0)",
  },
  secondary: {
    background: "var(--surface-raised)",
    color: "var(--text)",
    borderColor: "var(--border-default)",
  },
  ghost: {
    background: "transparent",
    color: "var(--text)",
    borderColor: "transparent",
  },
  outline: {
    background: "transparent",
    color: "var(--text)",
    borderColor: "var(--border-strong)",
  },
};

/** Unavailable reads as a flat, neutral button with its label at full
 *  brightness: faded text would be the one place the app's text dims. */
const BTN_DISABLED: CSSProperties = {
  background: "var(--surface-raised)",
  borderColor: "var(--border-default)",
  color: "var(--text)",
  cursor: "not-allowed",
};

// Filled buttons lighten on hover, never darken, and opacity never signals it.
const BTN_HOVER: Record<ButtonVariant, CSSProperties> = {
  primary: { background: "var(--blue-300)", borderColor: "var(--blue-300)" },
  paper: { background: "#FFFFFF", borderColor: "#FFFFFF" },
  secondary: { background: "var(--surface-hover)" },
  ghost: { background: "var(--surface-raised)", color: "var(--text)" },
  outline: { background: "rgba(255,255,255,.06)" },
};

export function Button({
  children,
  variant = "secondary",
  size = "md",
  icon,
  iconEnd,
  caps = false,
  fullWidth = false,
  disabled = false,
  style,
  ...rest
}: {
  children?: ReactNode;
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: LucideIcon;
  iconEnd?: LucideIcon;
  caps?: boolean;
  fullWidth?: boolean;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  const [hover, setHover] = useState(false);
  const glyph = size === "lg" ? 17 : 15;

  return (
    <button
      type="button"
      disabled={disabled}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        ...BTN_BASE,
        ...BTN_SIZES[size],
        ...BTN_VARIANTS[variant],
        ...(hover && !disabled ? BTN_HOVER[variant] : null),
        ...(caps
          ? {
              textTransform: "uppercase",
              fontSize: "var(--caps-size)",
              letterSpacing: "var(--caps-track)",
            }
          : null),
        ...(disabled ? BTN_DISABLED : null),
        ...(fullWidth ? { width: "100%" } : null),
        ...style,
      }}
      {...rest}
    >
      {icon ? <Icon as={icon} size={glyph} /> : null}
      {children}
      {iconEnd ? <Icon as={iconEnd} size={glyph} /> : null}
    </button>
  );
}

export function IconButton({
  icon,
  size = 32,
  disabled = false,
  label,
  style,
  ...rest
}: {
  icon: LucideIcon;
  size?: number;
  label: string;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  const [hover, setHover] = useState(false);
  const lifted = hover && !disabled;

  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        width: size,
        height: size,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        borderRadius: "var(--radius-md)",
        border: "1px solid transparent",
        cursor: disabled ? "not-allowed" : "pointer",
        background: lifted ? "var(--surface-raised)" : "transparent",
        color: "var(--text)",
        opacity: disabled ? 0.38 : 1,
        transition:
          "background var(--dur-fast) var(--ease-standard), color var(--dur-fast) var(--ease-standard)",
        ...style,
      }}
      {...rest}
    >
      <Icon as={icon} size={Math.round(size * 0.53)} />
    </button>
  );
}
