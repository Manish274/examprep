"use client";

import {
  useRef,
  useState,
  useSyncExternalStore,
  type ButtonHTMLAttributes,
  type CSSProperties,
  type HTMLAttributes,
  type ReactNode,
  type TextareaHTMLAttributes,
} from "react";
import {
  ArrowUp,
  FileText,
  Loader,
  Plus,
  X,
  type LucideIcon,
} from "lucide-react";

/**
 * The Examprep design system, ported from the Claude Design project's
 * `_ds_bundle.js` to typed React.
 *
 * The bundle is plain `React.createElement` calls with inline style objects and
 * a `window.lucide` CDN dependency, which is right for a design canvas and
 * wrong for an application: no types, no tree-shaking, and an icon component
 * that writes `innerHTML` on every render. These are the same components with
 * the same token references and the same numbers -- variants, sizes, hover
 * rules and transitions are copied, not reinterpreted.
 *
 * Two deliberate substitutions, both flagged in the system's own readme as
 * stand-ins rather than brand decisions:
 *
 * - **Icons** come from `lucide-react` rather than the CDN UMD build. Same set,
 *   same 1.75px stroke, but typed and bundled.
 * - **Fonts** are loaded by `next/font` in the root layout rather than by an
 *   `@import` from fonts.googleapis.com. Same three families, self-hosted, with
 *   no render-blocking request and no layout shift.
 *
 * Inline styles are kept rather than converted to Tailwind classes on purpose:
 * it keeps each component diffable against the bundle it came from, so a
 * re-import is a readable comparison instead of a translation exercise.
 */

// ── Icon ───────────────────────────────────────────────────

export function Icon({
  as: Glyph,
  size = 16,
  strokeWidth = 1.75,
  style,
}: {
  as: LucideIcon;
  size?: number;
  strokeWidth?: number;
  style?: CSSProperties;
}) {
  return (
    <Glyph
      size={size}
      strokeWidth={strokeWidth}
      aria-hidden="true"
      style={{ flex: "0 0 auto", ...style }}
    />
  );
}

// ── Button ─────────────────────────────────────────────────

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
    "background var(--dur-fast) var(--ease-standard), color var(--dur-fast) var(--ease-standard), border-color var(--dur-fast) var(--ease-standard), opacity var(--dur-fast) var(--ease-standard)",
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
    color: "var(--paper-0)",
    borderColor: "var(--border-default)",
  },
  ghost: {
    background: "transparent",
    color: "var(--text-body)",
    borderColor: "transparent",
  },
  outline: {
    background: "transparent",
    color: "var(--paper-0)",
    borderColor: "var(--border-strong)",
  },
};

// Filled buttons lighten on hover, never darken, and opacity never signals it.
const BTN_HOVER: Record<ButtonVariant, CSSProperties> = {
  primary: { background: "var(--blue-300)", borderColor: "var(--blue-300)" },
  paper: { background: "#FFFFFF", borderColor: "#FFFFFF" },
  secondary: { background: "var(--surface-hover)" },
  ghost: { background: "var(--surface-raised)", color: "var(--paper-0)" },
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
        ...(disabled ? { opacity: 0.38, cursor: "not-allowed" } : null),
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

// ── IconButton ─────────────────────────────────────────────

export function IconButton({
  icon,
  size = 32,
  active = false,
  disabled = false,
  label,
  style,
  ...rest
}: {
  icon: LucideIcon;
  size?: number;
  active?: boolean;
  label: string;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  const [hover, setHover] = useState(false);
  const lifted = active || (hover && !disabled);

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
        color: lifted ? "var(--paper-0)" : "var(--text-muted)",
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

// ── Badge ──────────────────────────────────────────────────

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

export function Badge({
  children,
  tone = "neutral",
  caps = true,
  style,
  ...rest
}: {
  children: ReactNode;
  tone?: BadgeTone;
  caps?: boolean;
} & HTMLAttributes<HTMLSpanElement>) {
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
        fontSize: caps ? "var(--caps-size)" : "var(--text-xs)",
        fontWeight: "var(--weight-medium)",
        letterSpacing: caps ? "var(--caps-track)" : "var(--track-wide)",
        textTransform: caps ? "uppercase" : "none",
        ...BADGE_TONES[tone],
        ...style,
      }}
      {...rest}
    >
      {children}
    </span>
  );
}

// ── Card ───────────────────────────────────────────────────

/** Flat near-black fill, one hairline, 14px radius, no drop shadow. */
export function Card({
  children,
  padding = "var(--space-9)",
  wash = false,
  raised = false,
  interactive = false,
  style,
  ...rest
}: {
  children: ReactNode;
  padding?: string;
  wash?: boolean;
  raised?: boolean;
  interactive?: boolean;
} & HTMLAttributes<HTMLDivElement>) {
  const [hover, setHover] = useState(false);

  return (
    <div
      onMouseEnter={() => interactive && setHover(true)}
      onMouseLeave={() => interactive && setHover(false)}
      style={{
        position: "relative",
        overflow: "hidden",
        padding,
        background: raised ? "var(--surface-raised)" : "var(--surface-card)",
        border: `1px solid ${hover ? "var(--border-default)" : "var(--border-subtle)"}`,
        borderRadius: "var(--radius-card)",
        transition:
          "border-color var(--dur-base) var(--ease-standard), background var(--dur-base) var(--ease-standard)",
        cursor: interactive ? "pointer" : "default",
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

// ── Display ────────────────────────────────────────────────

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

// ── SegmentedControl ───────────────────────────────────────

export interface SegmentOption<T extends string> {
  value: T;
  label: string;
  icon?: LucideIcon;
}

/** The mode switch. Pill track, filled thumb, no travel animation. */
export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  size = "md",
  style,
}: {
  options: readonly SegmentOption<T>[];
  value: T;
  onChange: (value: T) => void;
  size?: "sm" | "md";
  style?: CSSProperties;
}) {
  const height = size === "sm" ? 26 : 32;

  return (
    <div
      role="tablist"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 2,
        padding: 3,
        background: "var(--surface-raised)",
        border: "1px solid var(--border-subtle)",
        borderRadius: "var(--radius-pill)",
        ...style,
      }}
    >
      {options.map((option) => {
        const on = option.value === value;
        return (
          <button
            key={option.value}
            role="tab"
            aria-selected={on}
            type="button"
            onClick={() => onChange(option.value)}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "var(--space-3)",
              height,
              padding: "0 14px",
              border: "none",
              borderRadius: "var(--radius-pill)",
              cursor: "pointer",
              background: on ? "var(--surface-pressed)" : "transparent",
              color: on ? "var(--paper-0)" : "var(--text-muted)",
              fontFamily: "var(--font-sans)",
              fontSize: size === "sm" ? "var(--text-sm)" : "var(--text-md)",
              fontWeight: on ? "var(--weight-medium)" : "var(--weight-regular)",
              transition:
                "background var(--dur-fast) var(--ease-standard), color var(--dur-fast) var(--ease-standard)",
            }}
          >
            {option.icon ? <Icon as={option.icon} size={14} /> : null}
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

// ── Composer ───────────────────────────────────────────────

/**
 * The primary input, and the only way material enters the product.
 *
 * Raised panel, 20px radius, textarea over a footer row: attach on the left,
 * the mode switch beside it, send on the right. There is no separate library
 * page by design -- the `+` is the whole upload story.
 */
export function Composer<T extends string>({
  value,
  onChange,
  onSubmit,
  onAttach,
  placeholder = "Ask anything about your notes",
  mode,
  onModeChange,
  modes,
  attachments,
  footerRight,
  body,
  hideSend = false,
  disabled = false,
  autoFocus = false,
  style,
  ...rest
}: {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onAttach: () => void;
  placeholder?: string;
  /**
   * The mode switch is optional.
   *
   * Examprep puts it in the rail instead, so the composer stays a text field
   * and an attach button and nothing else. Omit `modes` and the control is not
   * rendered at all.
   */
  mode?: T;
  onModeChange?: (mode: T) => void;
  modes?: readonly SegmentOption<T>[];
  attachments?: ReactNode;
  footerRight?: ReactNode;
  disabled?: boolean;
  autoFocus?: boolean;
  /**
   * Replaces the text field.
   *
   * Quiz and Cards do not take a written prompt -- the backend generates from
   * an indexed document, not from a sentence -- so they put their own controls
   * here rather than showing a box whose contents would be quietly ignored.
   * The panel, the attach button and the mode switch stay identical.
   */
  body?: ReactNode;
  /** Overrides the send affordance when `body` supplies its own action. */
  hideSend?: boolean;
} & Omit<TextareaHTMLAttributes<HTMLDivElement>, "onChange" | "onSubmit">) {
  const [focus, setFocus] = useState(false);
  const canSend = value.trim().length > 0 && !disabled;

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
        ...style,
      }}
      {...rest}
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
        autoFocus={autoFocus}
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
          color: "var(--paper-0)",
          fontFamily: "var(--font-sans)",
          fontSize: "var(--text-lg)",
          lineHeight: "var(--text-lg-lh)",
          padding: "2px 2px 14px",
        }}
      />
      )}

      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-6)" }}>
        <IconButton icon={Plus} label="Attach notes" onClick={onAttach} size={30} />
        {modes && mode && onModeChange ? (
          <SegmentedControl
            options={modes}
            value={mode}
            onChange={onModeChange}
            size="sm"
          />
        ) : null}
        <span style={{ flex: 1 }} />
        {footerRight}
        {/* Send appears only once there is something to send. The bundle showed
            a microphone in its place, but nothing behind it dictates -- an
            affordance that does nothing is worse than an empty corner. */}
        {!hideSend && canSend ? (
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

// ── SuggestionChip ─────────────────────────────────────────

export function SuggestionChip({
  icon,
  children,
  active = false,
  onClick,
  style,
}: {
  icon?: LucideIcon;
  children: ReactNode;
  active?: boolean;
  onClick?: () => void;
  style?: CSSProperties;
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
        background: active
          ? "var(--surface-pressed)"
          : hover
            ? "var(--surface-hover)"
            : "var(--surface-raised)",
        border: `1px solid ${active ? "var(--border-default)" : "var(--border-subtle)"}`,
        borderRadius: "var(--radius-md)",
        cursor: "pointer",
        color: hover || active ? "var(--paper-0)" : "var(--text-body)",
        fontFamily: "var(--font-sans)",
        fontSize: "var(--text-md)",
        transition:
          "background var(--dur-fast) var(--ease-standard), color var(--dur-fast) var(--ease-standard), border-color var(--dur-fast) var(--ease-standard)",
        ...style,
      }}
    >
      {icon ? <Icon as={icon} size={14} style={{ color: "var(--text-muted)" }} /> : null}
      {children}
    </button>
  );
}

// ── AttachmentTile ─────────────────────────────────────────

/** An uploaded document in the composer. Indexing shows a number, not a spinner. */
export function AttachmentTile({
  name,
  meta,
  progress = 100,
  failed = false,
  onRemove,
}: {
  name: string;
  meta?: string;
  progress?: number;
  failed?: boolean;
  onRemove?: () => void;
}) {
  const indexing = progress < 100 && !failed;

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
            ? "var(--state-wrong)"
            : indexing
              ? "var(--accent)"
              : "var(--text-muted)",
        }}
      />
      <span style={{ flex: 1, minWidth: 0 }}>
        <span
          style={{
            display: "block",
            fontSize: "var(--text-sm)",
            color: "var(--paper-0)",
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
            color: failed ? "var(--state-wrong)" : "var(--text-faint)",
          }}
        >
          {failed ? "failed" : indexing ? `indexing ${progress}%` : (meta ?? "")}
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
            color: "var(--text-muted)",
            padding: 4,
          }}
        >
          <Icon as={X} size={13} />
        </button>
      ) : null}
    </div>
  );
}

// ── CitationChip ───────────────────────────────────────────

/** Inline retrieval marker inside answer text. Click opens the source. */
export function CitationChip({
  index,
  source,
  onClick,
}: {
  index: string | number;
  source?: string;
  onClick?: () => void;
}) {
  const [hover, setHover] = useState(false);

  return (
    <button
      type="button"
      onClick={onClick}
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
        cursor: "pointer",
        color: hover ? "var(--blue-100)" : "var(--blue-300)",
        fontFamily: "var(--font-mono)",
        fontSize: 10,
        lineHeight: 1,
        transition:
          "background var(--dur-fast) var(--ease-standard), color var(--dur-fast) var(--ease-standard)",
      }}
    >
      {index}
    </button>
  );
}

// ── Message ────────────────────────────────────────────────

/**
 * Student turns sit in a raised bubble on the right; answers are unboxed
 * reading text on the canvas.
 */
export function Message({
  role = "assistant",
  children,
  footer,
}: {
  role?: "user" | "assistant";
  children: ReactNode;
  footer?: ReactNode;
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
          color: isUser ? "var(--paper-0)" : "var(--text-body)",
          fontSize: isUser ? "var(--text-md)" : "var(--text-lg)",
          lineHeight: isUser ? "var(--text-md-lh)" : 1.68,
        }}
      >
        {children}
        {footer ? <div style={{ marginTop: "var(--space-7)" }}>{footer}</div> : null}
      </div>
    </div>
  );
}

// ── NavItem / RailSection ──────────────────────────────────

/** Mirrors the epRipple duration in globals.css, with a frame to spare. */
const RIPPLE_MS = 560;

export function NavItem({
  icon,
  children,
  active = false,
  badge,
  dot = false,
  muted = false,
  onClick,
  title,
  glow = false,
}: {
  icon?: LucideIcon;
  children: ReactNode;
  active?: boolean;
  badge?: string;
  dot?: boolean;
  muted?: boolean;
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
        color: active
          ? "var(--paper-0)"
          : muted
            ? "var(--text-muted)"
            : "var(--text-body)",
        fontFamily: "var(--font-sans)",
        fontSize: "var(--text-md)",
        fontWeight: active ? "var(--weight-medium)" : "var(--weight-regular)",
        textAlign: "left",
        transition:
          "background var(--dur-fast) var(--ease-standard), color var(--dur-fast) var(--ease-standard)",
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
          style={{
            color:
              active || (glow && hover) ? "var(--paper-0)" : "var(--text-muted)",
            transition: "color var(--dur-fast) var(--ease-standard)",
          }}
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
      {badge ? (
        <span
          style={{
            fontSize: "var(--caps-size)",
            letterSpacing: "var(--caps-track)",
            textTransform: "uppercase",
            color: "var(--text-faint)",
            border: "1px solid var(--border-default)",
            borderRadius: "var(--radius-xs)",
            padding: "1px 5px",
          }}
        >
          {badge}
        </span>
      ) : null}
    </button>
  );
}

/** A titled group in the rail. Title is micro-caps; optional trailing action. */
export function RailSection({
  title,
  action,
  actionLabel,
  onAction,
  children,
  style,
}: {
  title?: string;
  action?: LucideIcon;
  actionLabel?: string;
  onAction?: () => void;
  children: ReactNode;
  style?: CSSProperties;
}) {
  return (
    <div
      style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)", ...style }}
    >
      {title ? (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "0 10px",
            height: 28,
          }}
        >
          <span
            style={{
              fontSize: "var(--caps-size)",
              fontWeight: "var(--weight-medium)",
              letterSpacing: "var(--caps-track)",
              textTransform: "uppercase",
              color: "var(--text-faint)",
            }}
          >
            {title}
          </span>
          {action ? (
            <button
              type="button"
              onClick={onAction}
              aria-label={actionLabel ?? title}
              style={{
                display: "inline-flex",
                background: "transparent",
                border: "none",
                cursor: "pointer",
                color: "var(--text-muted)",
                padding: 2,
              }}
            >
              <Icon as={action} size={14} />
            </button>
          ) : null}
        </div>
      ) : null}
      {children}
    </div>
  );
}

// ── QuizOption ─────────────────────────────────────────────

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

  const tone = {
    idle: {
      // Hover lifts the row onto the accent rather than merely a rung up the
      // ink ladder: an answer is a thing you are about to choose, and the
      // design system reserves blue for exactly that -- the one live action.
      border: lit ? "var(--blue-tint-32)" : "var(--border-subtle)",
      bg: lit ? "var(--blue-tint-08)" : "transparent",
      key: lit ? "var(--blue-300)" : "var(--text-muted)",
      text: lit ? "var(--paper-0)" : "var(--text-body)",
    },
    selected: {
      border: "var(--blue-tint-32)",
      bg: "var(--blue-tint-08)",
      key: "var(--blue-300)",
      text: "var(--paper-0)",
    },
    correct: {
      border: "rgba(127,179,163,.4)",
      bg: "rgba(127,179,163,.1)",
      key: "var(--state-correct)",
      text: "var(--paper-0)",
    },
    wrong: {
      border: "rgba(229,105,91,.4)",
      bg: "rgba(229,105,91,.1)",
      key: "var(--state-wrong)",
      text: "var(--paper-1)",
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
        color: tone.text,
        fontFamily: "var(--font-sans)",
        fontSize: "var(--text-lg)",
        textAlign: "left",
        // A soft accent halo on hover. No scale and no travel -- the system
        // rules both out -- so the row lights up where it stands.
        boxShadow: state === "idle" && lit ? "0 0 0 3px var(--blue-tint-08)" : "none",
        transition:
          "background var(--dur-fast) var(--ease-standard), border-color var(--dur-fast) var(--ease-standard), box-shadow var(--dur-fast) var(--ease-standard), color var(--dur-fast) var(--ease-standard)",
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

// ── ProgressRing ───────────────────────────────────────────

/**
 * The thin concentric ring from the reference material.
 *
 * Determinate by default. `indeterminate` rotates a quarter arc instead, for
 * work whose duration genuinely is not known -- generation runs in batches, so
 * a determinate ring would sit at zero for most of it and read as broken.
 *
 * This is the one place Examprep animates a loop, which its own guidance
 * otherwise rules out. The alternative on offer was a number that stays at 0/10
 * for a minute, and that says less honestly that anything is happening.
 * Reduced-motion preferences stop it, leaving a static arc.
 */
export function ProgressRing({
  value = 0,
  size = 64,
  thickness = 1.5,
  label,
  indeterminate = false,
  style,
}: {
  value?: number;
  size?: number;
  thickness?: number;
  label?: ReactNode;
  indeterminate?: boolean;
  style?: CSSProperties;
}) {
  const radius = (size - thickness) / 2;
  const circumference = 2 * Math.PI * radius;
  const shown = indeterminate ? 0.26 : Math.max(0, Math.min(1, value));

  return (
    <span
      style={{
        position: "relative",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: size,
        height: size,
        ...style,
      }}
    >
      <svg
        width={size}
        height={size}
        className={indeterminate ? "ep-spin" : undefined}
        style={indeterminate ? undefined : { transform: "rotate(-90deg)" }}
        aria-hidden="true"
      >
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--line-2)"
          strokeWidth={thickness}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--accent)"
          strokeWidth={thickness}
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - shown)}
          strokeLinecap="round"
          style={{
            transition: "stroke-dashoffset var(--dur-slow) var(--ease-out)",
          }}
        />
      </svg>
      {label ? (
        <span
          style={{
            position: "absolute",
            fontFamily: "var(--font-mono)",
            fontSize: size > 52 ? 12 : 10,
            color: "var(--paper-0)",
          }}
        >
          {label}
        </span>
      ) : null}
    </span>
  );
}

// ── Flashcard ──────────────────────────────────────────────

/** Degrees of tilt at the very edge of the card. Deliberately small. */
const TILT_MAX = 5;
/** How far the card rises toward the viewer while the pointer is on it. */
const TILT_LIFT = 6;
/** Radius of the sheen under the cursor, in px. */
const SHEEN = 110;

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

/**
 * True while the operating system asks for reduced motion.
 *
 * Subscribed rather than read into state, so the first paint already knows the
 * answer and a change of the OS setting reaches every card at once. The server
 * snapshot is `false`: the stylesheet already stops the animations, and this
 * hook only governs the effects that CSS cannot reach.
 */
export function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const query = window.matchMedia(REDUCED_MOTION);
      query.addEventListener("change", onChange);
      return () => query.removeEventListener("change", onChange);
    },
    () => window.matchMedia(REDUCED_MOTION).matches,
    () => false,
  );
}

const FACE: CSSProperties = {
  position: "absolute",
  inset: 0,
  display: "flex",
  flexDirection: "column",
  justifyContent: "center",
  padding: "var(--space-11)",
  background: "var(--surface-card)",
  border: "1px solid var(--border-subtle)",
  borderRadius: "var(--radius-card)",
  overflow: "hidden",
  // Without this both faces paint at once and the flip shows mirrored text.
  backfaceVisibility: "hidden",
  WebkitBackfaceVisibility: "hidden",
};

const FACE_LABEL: CSSProperties = {
  position: "absolute",
  top: "var(--space-7)",
  left: "var(--space-11)",
  fontSize: "var(--caps-size)",
  letterSpacing: "var(--caps-track)",
  textTransform: "uppercase",
  color: "var(--text-faint)",
};

const FACE_FOOT: CSSProperties = {
  position: "absolute",
  bottom: "var(--space-7)",
  left: "var(--space-11)",
  right: "var(--space-11)",
  display: "flex",
  gap: "var(--space-5)",
  fontFamily: "var(--font-mono)",
  fontSize: 10,
  color: "var(--text-faint)",
};

/**
 * A two-sided card: the question on the front, the answer on the back, and a
 * real rotation between them rather than a swap of text.
 *
 * Three transforms are stacked on separate layers on purpose, because they
 * need different durations:
 *
 * - the *scene* holds the perspective, and never moves;
 * - the *plate* carries the pointer tilt and the lift, on a short easing so it
 *   trails the cursor closely and settles flat when the pointer leaves;
 * - the *leaf* carries the flip alone, on the system's slow duration.
 *
 * Both faces are absolutely positioned, so the card needs an explicit height;
 * a long answer scrolls inside its own face rather than resizing the deck.
 */
export function Flashcard({
  question,
  answer,
  source,
  hint,
  height = 260,
  surface = "var(--surface-card)",
  flipped,
  onFlip,
  onActivate,
}: {
  question: string;
  answer: string;
  source?: string;
  hint?: string;
  height?: number;
  /** The face fill. Lifted in study mode so the card reads as the lit object. */
  surface?: string;
  flipped?: boolean;
  onFlip?: (next: boolean) => void;
  /** When given, a click opens the card instead of flipping it. */
  onActivate?: () => void;
}) {
  const [self, setSelf] = useState(false);
  const plate = useRef<HTMLDivElement>(null);
  const glare = useRef<HTMLDivElement>(null);
  const reduced = usePrefersReducedMotion();
  const isFlipped = flipped !== undefined ? flipped : self;

  const activate = () => {
    if (onActivate) {
      onActivate();
      return;
    }
    if (onFlip) onFlip(!isFlipped);
    else setSelf(!isFlipped);
  };

  /**
   * Written straight to the node rather than held in state: this runs on every
   * mouse move, and a re-render per frame to move a card six pixels is not a
   * trade worth making.
   */
  function settle(rx: number, ry: number, lift: number, tracking: boolean) {
    const node = plate.current;
    if (!node) return;
    node.style.transition = `transform ${
      tracking ? "var(--dur-fast)" : "var(--dur-base)"
    } var(--ease-standard), box-shadow var(--dur-base) var(--ease-standard)`;
    node.style.transform = `translateY(${-lift}px) rotateX(${rx}deg) rotateY(${ry}deg)`;
    node.style.boxShadow = lift > 0 ? "var(--shadow-lg)" : "none";
  }

  function track(event: React.MouseEvent<HTMLDivElement>) {
    if (reduced) return;
    const box = event.currentTarget.getBoundingClientRect();
    const px = (event.clientX - box.left) / box.width;
    const py = (event.clientY - box.top) / box.height;

    // The corner under the pointer is the one that goes back, as though the
    // pointer were pressing it away. Positive rotateX brings the bottom edge
    // toward the viewer and positive rotateY pushes the right edge away, which
    // is where the two signs come from.
    settle(-(py - 0.5) * 2 * TILT_MAX, (px - 0.5) * 2 * TILT_MAX, TILT_LIFT, true);

    const sheen = glare.current;
    if (sheen) {
      // A small pool right under the cursor, sized in pixels so it stays the
      // same on a deck card and a study card. Faint enough to read through --
      // it is a hint of a surface catching the light, not a spotlight.
      sheen.style.opacity = "1";
      sheen.style.background = `radial-gradient(${SHEEN}px ${SHEEN}px at ${px * 100}% ${py * 100}%, rgba(255,255,255,.05), transparent 68%)`;
    }
  }

  function rest() {
    settle(0, 0, 0, false);
    if (glare.current) glare.current.style.opacity = "0";
  }

  return (
    <div style={{ perspective: 1200, perspectiveOrigin: "50% 50%" }}>
      <div
        ref={plate}
        role="button"
        tabIndex={0}
        aria-pressed={isFlipped}
        aria-label={onActivate ? "Study this card" : "Flip this card"}
        onClick={activate}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            activate();
          }
        }}
        onMouseMove={track}
        onMouseLeave={rest}
        onBlur={rest}
        style={{
          position: "relative",
          height,
          cursor: "pointer",
          borderRadius: "var(--radius-card)",
          transformStyle: "preserve-3d",
          willChange: "transform",
          outlineOffset: 3,
        }}
      >
        <div
          style={{
            position: "absolute",
            inset: 0,
            transformStyle: "preserve-3d",
            transform: isFlipped ? "rotateY(180deg)" : "rotateY(0deg)",
            transition: "transform var(--dur-slow) var(--ease-standard)",
          }}
        >
          <div style={{ ...FACE, background: surface }}>
            <div
              aria-hidden="true"
              style={{
                position: "absolute",
                inset: 0,
                background: "var(--wash-vignette)",
                pointerEvents: "none",
              }}
            />
            <span style={FACE_LABEL}>Question</span>
            <p
              className="ep-scroll"
              style={{
                margin: 0,
                position: "relative",
                maxHeight: "100%",
                overflowY: "auto",
                fontFamily: "var(--font-display)",
                fontStyle: "italic",
                fontSize: "var(--display-md)",
                lineHeight: "var(--display-md-lh)",
                letterSpacing: "var(--track-tight)",
                color: "var(--text-display)",
              }}
            >
              {question}
            </p>
            <span style={FACE_FOOT}>
              {source ? <span>{source}</span> : null}
              <span style={{ marginLeft: "auto" }}>{hint ?? "Click to reveal"}</span>
            </span>
          </div>

          <div
            style={{ ...FACE, background: surface, transform: "rotateY(180deg)" }}
          >
            <div
              aria-hidden="true"
              style={{
                position: "absolute",
                inset: 0,
                background: "var(--wash-vignette)",
                pointerEvents: "none",
              }}
            />
            <span style={FACE_LABEL}>Answer</span>
            <p
              className="ep-scroll"
              style={{
                margin: 0,
                position: "relative",
                maxHeight: "100%",
                overflowY: "auto",
                fontSize: "var(--text-lg)",
                lineHeight: 1.62,
                color: "var(--text-body)",
              }}
            >
              {answer}
            </p>
            <span style={FACE_FOOT}>
              {source ? <span>{source}</span> : null}
              <span style={{ marginLeft: "auto" }}>
                {hint ?? "Click for the question"}
              </span>
            </span>
          </div>
        </div>

        {/* The sheen sits a hair in front of both faces, so it stays put while
            the card turns underneath it. */}
        <div
          ref={glare}
          aria-hidden="true"
          style={{
            position: "absolute",
            inset: 0,
            transform: "translateZ(1px)",
            borderRadius: "var(--radius-card)",
            opacity: 0,
            pointerEvents: "none",
            transition: "opacity var(--dur-base) var(--ease-standard)",
          }}
        />
      </div>
    </div>
  );
}

// ── Wordmark ───────────────────────────────────────────────

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
