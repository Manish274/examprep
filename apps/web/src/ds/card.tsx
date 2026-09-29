import type { HTMLAttributes, ReactNode } from "react";

/** Flat near-black fill, one hairline, 14px radius, no drop shadow. */
export function Card({
  children,
  padding = "var(--space-9)",
  wash = false,
  style,
  ...rest
}: {
  children: ReactNode;
  padding?: string;
  /** Lays the aurora gradient under the content. */
  wash?: boolean;
} & HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      style={{
        position: "relative",
        overflow: "hidden",
        padding,
        background: "var(--surface-card)",
        border: "1px solid var(--border-subtle)",
        borderRadius: "var(--radius-card)",
        ...style,
      }}
      {...rest}
    >
      {wash ? (
        <div
          aria-hidden="true"
          style={{
            position: "absolute",
            inset: 0,
            background: "var(--wash-aurora)",
            pointerEvents: "none",
          }}
        />
      ) : null}
      <div style={{ position: "relative" }}>{children}</div>
    </div>
  );
}
