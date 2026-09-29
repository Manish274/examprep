import type { CSSProperties, HTMLAttributes, ReactNode } from "react";

type DisplaySize = "xl" | "lg" | "md" | "sm";

const DISPLAY_SIZES: Record<DisplaySize, { serif: string; lh: string }> = {
  xl: { serif: "var(--display-xl)", lh: "var(--display-xl-lh)" },
  lg: { serif: "var(--display-lg)", lh: "var(--display-lg-lh)" },
  md: { serif: "var(--display-md)", lh: "var(--display-md-lh)" },
  sm: { serif: "var(--display-sm)", lh: "var(--display-sm-lh)" },
};

/**
 * The headline lockup: an italic serif line completed by a sans line.
 *
 * The break is rhetorical, not a wrap -- "study what you" / "actually forgot".
 * The serif is never set upright and never used alone at display sizes.
 */
export function Display({
  serif,
  sans,
  size = "lg",
  align = "left",
  as: Tag = "h2",
  style,
  ...rest
}: {
  serif: ReactNode;
  sans?: ReactNode;
  size?: DisplaySize;
  align?: CSSProperties["textAlign"];
  as?: "h1" | "h2" | "h3";
} & HTMLAttributes<HTMLHeadingElement>) {
  const s = DISPLAY_SIZES[size];

  return (
    <Tag
      style={{
        margin: 0,
        textAlign: align,
        fontSize: s.serif,
        lineHeight: s.lh,
        letterSpacing: "var(--track-tight)",
        ...style,
      }}
      {...rest}
    >
      <span
        style={{
          display: "block",
          fontFamily: "var(--font-display)",
          fontStyle: "italic",
          fontWeight: 400,
          color: "var(--text-display)",
        }}
      >
        {serif}
      </span>
      {sans ? (
        <span
          style={{
            display: "block",
            fontFamily: "var(--font-sans)",
            fontWeight: "var(--weight-light)",
            color: "var(--paper-0)",
            fontSize: "0.82em",
          }}
        >
          {sans}
        </span>
      ) : null}
    </Tag>
  );
}

/**
 * No logo file was supplied with the design system, so the mark is type: the
 * micro-caps lockup with the middot in accent.
 */
export function Wordmark({ tone = "primary" }: { tone?: "primary" | "muted" }) {
  return (
    <span
      style={{
        fontSize: 11,
        fontWeight: 500,
        letterSpacing: "var(--caps-track)",
        textTransform: "uppercase",
        color: tone === "primary" ? "var(--paper-0)" : "var(--paper-1)",
        whiteSpace: "nowrap",
      }}
    >
      Examprep <span style={{ color: "var(--accent)" }}>&#9679;</span> Study
    </span>
  );
}
