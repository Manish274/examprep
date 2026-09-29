"use client";

import { useState, type ReactNode } from "react";

/**
 * Inline retrieval marker inside answer text. Hovering names the source; the
 * full list sits under the answer.
 */
export function CitationChip({
  index,
  source,
}: {
  index: string | number;
  source?: string;
}) {
  const [hover, setHover] = useState(false);

  return (
    <span
      title={source}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        minWidth: 17,
        height: 17,
        padding: "0 4px",
        marginLeft: 3,
        verticalAlign: "1px",
        background: hover ? "var(--blue-tint-32)" : "var(--accent-quiet)",
        border: "1px solid var(--blue-tint-32)",
        borderRadius: "var(--radius-xs)",
        cursor: "help",
        color: hover ? "var(--text)" : "var(--text-accent)",
        fontFamily: "var(--font-mono)",
        fontSize: 10,
        lineHeight: 1,
        transition:
          "background var(--dur-fast) var(--ease-standard), color var(--dur-fast) var(--ease-standard)",
      }}
    >
      {index}
    </span>
  );
}

/**
 * Student turns sit in a raised bubble on the right; answers are unboxed
 * reading text on the canvas.
 */
export function Message({
  role = "assistant",
  children,
}: {
  role?: "user" | "assistant";
  children: ReactNode;
}) {
  const isUser = role === "user";

  return (
    <div style={{ display: "flex", justifyContent: isUser ? "flex-end" : "flex-start" }}>
      <div
        style={{
          maxWidth: isUser ? "78%" : "100%",
          padding: isUser ? "var(--space-6) var(--space-7)" : 0,
          background: isUser ? "var(--surface-raised)" : "transparent",
          border: isUser ? "1px solid var(--border-subtle)" : "none",
          borderRadius: isUser ? "var(--radius-xl)" : 0,
          color: "var(--text)",
          fontSize: isUser ? "var(--text-md)" : "var(--text-lg)",
          lineHeight: isUser ? "var(--text-md-lh)" : 1.68,
        }}
      >
        {children}
      </div>
    </div>
  );
}
