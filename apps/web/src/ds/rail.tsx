"use client";

import { useRef, useState, type CSSProperties, type ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { Icon } from "./icon";
import { usePrefersReducedMotion } from "./motion";

/** Mirrors the epRipple duration in globals.css, with a frame to spare. */
const RIPPLE_MS = 560;

export function NavItem({
  icon,
  children,
  active = false,
  dot = false,
  onClick,
  title,
  glow = false,
}: {
  icon?: LucideIcon;
  children: ReactNode;
  active?: boolean;
  dot?: boolean;
  onClick?: () => void;
  title?: string;
  /** A light that follows the pointer, and a ripple on press. For the rail's
   *  main destinations, not for every row in a list. */
  glow?: boolean;
}) {
  const [hover, setHover] = useState(false);
  const [ripples, setRipples] = useState<{ id: number; x: number; y: number }[]>([]);
  const rippleId = useRef(0);
  // A hidden ripple never finishes animating, so it would never be removed.
  const reduced = usePrefersReducedMotion();

  // Written straight to the element: the light moves every frame the pointer
  // does, and a state update per frame would re-render the row to move it.
  function track(event: React.PointerEvent<HTMLButtonElement>) {
    if (!glow) return;
    const box = event.currentTarget.getBoundingClientRect();
    event.currentTarget.style.setProperty("--gx", `${event.clientX - box.left}px`);
    event.currentTarget.style.setProperty("--gy", `${event.clientY - box.top}px`);
  }

  function press(event: React.PointerEvent<HTMLButtonElement>) {
    if (!glow || event.button !== 0) return;
    track(event);
    if (reduced) return;
    const box = event.currentTarget.getBoundingClientRect();
    const id = ++rippleId.current;
    setRipples((live) => [
      ...live,
      { id, x: event.clientX - box.left, y: event.clientY - box.top },
    ]);
    // A timer rather than animationend, which a tab that is not painting --
    // backgrounded mid-press -- may never deliver.
    window.setTimeout(
      () => setRipples((live) => live.filter((r) => r.id !== id)),
      RIPPLE_MS,
    );
  }

  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className={glow ? "ep-glow" : undefined}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onPointerMove={glow ? track : undefined}
      onPointerDown={glow ? press : undefined}
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-6)",
        width: "100%",
        height: 32,
        padding: "0 10px",
        border: "1px solid transparent",
        borderRadius: "var(--radius-md)",
        cursor: "pointer",
        background: active
          ? "var(--surface-raised)"
          : hover
            ? "rgba(255,255,255,.04)"
            : "transparent",
        color: "var(--text)",
        fontFamily: "var(--font-sans)",
        fontSize: "var(--text-md)",
        fontWeight: active ? "var(--weight-medium)" : "var(--weight-regular)",
        textAlign: "left",
        transition: "background var(--dur-fast) var(--ease-standard)",
      }}
    >
      {dot ? (
        <span
          style={{
            width: 5,
            height: 5,
            borderRadius: "var(--radius-pill)",
            border: "1px solid var(--line-3)",
            flex: "0 0 auto",
            marginLeft: 4,
            marginRight: 3,
          }}
        />
      ) : null}
      {icon ? (
        <Icon
          as={icon}
          size={15}
          style={{ color: "var(--text)" }}
        />
      ) : null}
      <span
        style={{
          flex: 1,
          minWidth: 0,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {children}
      </span>
      {ripples.map((ripple) => (
        <span
          key={ripple.id}
          aria-hidden
          className="ep-ripple"
          style={{ left: ripple.x, top: ripple.y }}
        />
      ))}
    </button>
  );
}

/** A titled group in the rail, with a micro-caps title. */
export function RailSection({
  title,
  children,
  style,
}: {
  title: string;
  children: ReactNode;
  style?: CSSProperties;
}) {
  return (
    <div
      style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)", ...style }}
    >
      <span
        style={{
          display: "flex",
          alignItems: "center",
          padding: "0 10px",
          height: 28,
          fontSize: "var(--caps-size)",
          fontWeight: "var(--weight-medium)",
          letterSpacing: "var(--caps-track)",
          textTransform: "uppercase",
          color: "var(--text)",
        }}
      >
        {title}
      </span>
      {children}
    </div>
  );
}
