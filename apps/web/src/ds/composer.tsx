"use client";

import { useState, type ReactNode } from "react";
import { ArrowUp, FileText, Loader, Plus, X, type LucideIcon } from "lucide-react";
import { IconButton } from "./button";
import { Icon } from "./icon";

/**
 * The primary input, and the only way material enters the product.
 *
 * Raised panel, 20px radius, a text field over a footer row: attach on the
 * left, send on the right. There is no separate library page by design -- the
 * `+` is the whole upload story. Switching between chat, quiz and cards lives
 * in the rail, so this stays a text field and an attach button.
 */
export function Composer({
  value,
  onChange,
  onSubmit,
  onAttach,
  placeholder = "Ask anything about your notes",
  attachments,
  body,
  disabled = false,
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onAttach: () => void;
  placeholder?: string;
  attachments?: ReactNode;
  /**
   * Replaces the text field, and with it the send button.
   *
   * Quiz and Cards do not take a written prompt -- the backend generates from
   * an indexed document, not from a sentence -- so they put their own controls
   * and their own action here rather than showing a box whose contents would
   * be quietly ignored. The panel and the attach button stay identical.
   */
  body?: ReactNode;
  disabled?: boolean;
}) {
  const [focus, setFocus] = useState(false);
  const canSend = !body && value.trim().length > 0 && !disabled;

  return (
    <div
      style={{
        background: "var(--surface-raised)",
        border: `1px solid ${focus ? "var(--border-default)" : "var(--border-subtle)"}`,
        borderRadius: "var(--radius-2xl)",
        padding: "var(--space-7)",
        // The composer is one of only two things that genuinely float.
        boxShadow: focus ? "var(--shadow-md)" : "none",
        transition:
          "border-color var(--dur-base) var(--ease-standard), box-shadow var(--dur-base) var(--ease-standard)",
      }}
    >
      {attachments ? (
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            gap: "var(--space-4)",
            marginBottom: "var(--space-6)",
          }}
        >
          {attachments}
        </div>
      ) : null}

      {body ?? (
        <textarea
          value={value}
          placeholder={placeholder}
          rows={1}
          onChange={(event) => onChange(event.target.value)}
          onFocus={() => setFocus(true)}
          onBlur={() => setFocus(false)}
          onKeyDown={(event) => {
            // Enter sends, shift+enter breaks the line.
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              if (canSend) onSubmit();
            }
          }}
          style={{
            display: "block",
            width: "100%",
            minHeight: 26,
            maxHeight: 160,
            resize: "none",
            background: "transparent",
            border: "none",
            outline: "none",
            color: "var(--text)",
            fontFamily: "var(--font-sans)",
            fontSize: "var(--text-lg)",
            lineHeight: "var(--text-lg-lh)",
            padding: "2px 2px 14px",
          }}
        />
      )}

      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-6)" }}>
        <IconButton icon={Plus} label="Attach notes" onClick={onAttach} size={30} />
        <span style={{ flex: 1 }} />
        {/* Send appears only once there is something to send. The bundle showed
            a microphone in its place, but nothing behind it dictates -- an
            affordance that does nothing is worse than an empty corner. */}
        {canSend ? (
          <IconButton
            icon={ArrowUp}
            label="Send"
            onClick={onSubmit}
            size={30}
            style={{ background: "var(--accent)", color: "#0A0A0B" }}
          />
        ) : null}
      </div>
    </div>
  );
}

/** A starter prompt under the empty composer. */
export function SuggestionChip({
  icon,
  children,
  onClick,
}: {
  icon?: LucideIcon;
  children: ReactNode;
  onClick?: () => void;
}) {
  const [hover, setHover] = useState(false);

  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "var(--space-4)",
        height: 32,
        padding: "0 14px",
        background: hover ? "var(--surface-hover)" : "var(--surface-raised)",
        border: "1px solid var(--border-subtle)",
        borderRadius: "var(--radius-md)",
        cursor: "pointer",
        color: "var(--text)",
        fontFamily: "var(--font-sans)",
        fontSize: "var(--text-md)",
        transition:
          "background var(--dur-fast) var(--ease-standard), color var(--dur-fast) var(--ease-standard)",
      }}
    >
      {icon ? <Icon as={icon} size={14} style={{ color: "var(--text)" }} /> : null}
      {children}
    </button>
  );
}

/**
 * An uploaded document in the composer.
 *
 * While something is happening to it the tile says what, with a number when
 * there is one to show -- never a fraction that sits at zero while the real
 * work happens elsewhere. A ready document can still be busy: its figures are
 * read after its text is searchable.
 */
export function AttachmentTile({
  name,
  meta,
  status,
  activity,
  onRemove,
}: {
  name: string;
  meta?: string;
  status: "working" | "ready" | "failed";
  /** What is happening to it right now, e.g. "indexing 40%". */
  activity?: string | null;
  onRemove?: () => void;
}) {
  const failed = status === "failed";
  const indexing = status === "working" || Boolean(activity);

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-5)",
        height: 38,
        padding: "0 8px 0 10px",
        background: "var(--ink-2)",
        border: `1px solid ${failed ? "rgba(229,105,91,.32)" : "var(--border-subtle)"}`,
        borderRadius: "var(--radius-md)",
        maxWidth: 260,
      }}
    >
      <Icon
        as={indexing ? Loader : FileText}
        size={15}
        style={{
          color: failed
            ? "var(--text-wrong)"
            : indexing
              ? "var(--text-accent)"
              : "var(--text)",
        }}
      />
      <span style={{ flex: 1, minWidth: 0 }}>
        <span
          style={{
            display: "block",
            fontSize: "var(--text-sm)",
            color: "var(--text)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {name}
        </span>
        <span
          style={{
            display: "block",
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: failed ? "var(--text-wrong)" : "var(--text)",
          }}
        >
          {failed ? "failed" : (activity ?? meta ?? "")}
        </span>
      </span>
      {onRemove ? (
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Remove ${name}`}
          style={{
            display: "inline-flex",
            background: "transparent",
            border: "none",
            cursor: "pointer",
            color: "var(--text)",
            padding: 4,
          }}
        >
          <Icon as={X} size={13} />
        </button>
      ) : null}
    </div>
  );
}
