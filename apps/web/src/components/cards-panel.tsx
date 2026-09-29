"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  ChevronLeft,
  ChevronRight,
  Maximize2,
  X,
  type LucideIcon,
} from "lucide-react";
import { Button, Flashcard, Icon, usePrefersReducedMotion } from "@/ds";
import { Empty, Failed, Generating } from "./study-states";
import type { CardsState } from "@/hooks/use-study";

/**
 * The flashcard surface: one card at a time, click to study.
 *
 * A deck rather than a grid. Seeing every answer at once is reading, not
 * recall, and recall is the only reason to make cards.
 *
 * Clicking a card opens it on a dimmed page, where the card is the only lit
 * thing and the only controls are flip, forward and back. The deck view keeps
 * the same arrows, so browsing and studying are the same gesture at two
 * scales.
 *
 * A new set is a new deck, so the page keys this panel by set id. Position and
 * flip reset because the component is replaced, not because an effect noticed.
 */

// Mirrors of the two durations in globals.css. The swap has to happen while
// the outgoing card is invisible, so the timer and the animation must agree.
const OUT_MS = 140; // --dur-fast
const IN_MS = 220; // --dur-base

export function CardsPanel({
  state,
  onReset,
}: {
  state: CardsState;
  onReset: () => void;
}) {
  const [index, setIndex] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const [studying, setStudying] = useState(false);
  const [move, setMove] = useState<{
    dir: 1 | -1;
    stage: "out" | "in";
  } | null>(null);

  const timer = useRef<number | null>(null);
  const moving = useRef(false);
  const reduced = usePrefersReducedMotion();

  const total = state.cards.length;
  const at = total > 0 ? Math.min(index, total - 1) : 0;

  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );

  /**
   * Out, then in. Two phases rather than a cross-fade because the deck is one
   * card deep: the card has to leave before its replacement can use the space.
   */
  const go = useCallback(
    (dir: 1 | -1) => {
      const next = at + dir;
      if (moving.current || next < 0 || next >= total) return;

      if (reduced) {
        setIndex(next);
        setFlipped(false);
        return;
      }

      moving.current = true;
      setMove({ dir, stage: "out" });
      timer.current = window.setTimeout(() => {
        setIndex(next);
        setFlipped(false);
        setMove({ dir, stage: "in" });
        timer.current = window.setTimeout(() => {
          setMove(null);
          moving.current = false;
        }, IN_MS);
      }, OUT_MS);
    },
    [at, total, reduced],
  );

  // Arrow keys and Escape, but only while a card is open. Flipping stays on
  // the card itself, which handles Enter and Space when it has focus.
  useEffect(() => {
    if (!studying) return;

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setStudying(false);
      else if (event.key === "ArrowRight") go(1);
      else if (event.key === "ArrowLeft") go(-1);
      else return;
      event.preventDefault();
    };

    const restore = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = restore;
    };
  }, [studying, go]);

  if (state.status === "idle") {
    return (
      <Empty
        serif="turn a chapter into"
        sans="something you can recall"
        body="Pick a document below. Each card is one fact from your own material, front and back, with nothing invented to fill a gap."
      />
    );
  }

  if (state.status === "generating") {
    return <Generating />;
  }

  if (state.status === "failed") {
    return <Failed message={state.message} onReset={onReset} />;
  }

  const card = state.cards[at];
  if (!card) {
    return <Failed message="That set came back empty." onReset={onReset} />;
  }

  const slide = move
    ? move.stage === "out"
      ? move.dir === 1
        ? "ep-card-out-left"
        : "ep-card-out-right"
      : move.dir === 1
        ? "ep-card-in-right"
        : "ep-card-in-left"
    : undefined;

  const first = at === 0;
  const last = at >= total - 1;
  // Fewer cards than asked for is a result, not an error: the generator will
  // not invent cards the material cannot support. Said plainly, so a short set
  // does not read as a bug.
  const shortBy = state.requested > total ? state.requested - total : 0;

  return (
    <>
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-8)" }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: "var(--space-7)",
          }}
        >
          <h2
            style={{
              margin: 0,
              fontSize: "var(--text-2xl)",
              fontWeight: 400,
              color: "var(--text)",
            }}
          >
            {state.title}
          </h2>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: "var(--text)",
            }}
          >
            {at + 1} / {total}
          </span>
        </div>

        {shortBy > 0 ? (
          <p
            style={{
              margin: "calc(var(--space-6) * -1) 0 0",
              fontSize: "var(--text-sm)",
              color: "var(--text)",
            }}
          >
            {total} of the {state.requested} cards you asked for. The rest would have
            needed material this document does not contain.
          </p>
        ) : null}

        <div className={slide}>
          <Flashcard
            question={card.front}
            answer={card.back}
            hint="Click to study"
            onActivate={() => setStudying(true)}
            flipped={false}
          />
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-5)" }}>
          <Round
            icon={ChevronLeft}
            label="Previous card"
            size={38}
            onClick={() => go(-1)}
            disabled={first}
          />
          <Round
            icon={ChevronRight}
            label="Next card"
            size={38}
            onClick={() => go(1)}
            disabled={last}
          />
          <Button
            variant="ghost"
            size="sm"
            icon={Maximize2}
            onClick={() => setStudying(true)}
          >
            Study
          </Button>
          <span style={{ flex: 1 }} />
          {/* The page keys this panel by set, so resetting replaces it and
              the position and flip start over with the next deck. */}
          <Button variant="outline" size="sm" onClick={onReset}>
            New set
          </Button>
        </div>
      </div>

      {studying
        ? createPortal(
            <div
              role="dialog"
              aria-modal="true"
              aria-label={`${state.title}, card ${at + 1} of ${total}`}
              className="ep-fade"
              onClick={(event) => {
                if (event.target === event.currentTarget) setStudying(false);
              }}
              style={{
                position: "fixed",
                inset: 0,
                zIndex: 60,
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                gap: "var(--space-9)",
                padding: "var(--space-11) var(--space-7)",
                // Dim, not black: the page stays legible behind the card.
                background: "rgba(10,10,11,.82)",
                backdropFilter: "blur(6px)",
                WebkitBackdropFilter: "blur(6px)",
              }}
            >
              <div
                style={{
                  position: "absolute",
                  top: "var(--space-7)",
                  right: "var(--space-7)",
                }}
              >
                <Round icon={X} label="Close" size={38} onClick={() => setStudying(false)} />
              </div>

              <div style={{ position: "relative", width: "min(760px, 92vw)" }}>
                {/* A soft aura so the card reads as the lit object rather than
                    a lighter rectangle on a dark one. */}
                <div
                  aria-hidden="true"
                  style={{
                    position: "absolute",
                    inset: "-14%",
                    background:
                      "radial-gradient(55% 55% at 50% 50%, rgba(91,141,239,.14), transparent 72%)",
                    pointerEvents: "none",
                  }}
                />
                <div
                  className={slide}
                  style={{
                    position: "relative",
                    // A card on its way out is not the card you meant to flip.
                    pointerEvents: move ? "none" : undefined,
                  }}
                >
                  <Flashcard
                    question={card.front}
                    answer={card.back}
                    height={340}
                    flipped={flipped}
                    onFlip={setFlipped}
                  />
                </div>
              </div>

              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--space-8)",
                }}
              >
                <Round
                  icon={ChevronLeft}
                  label="Previous card"
                  size={52}
                  onClick={() => go(-1)}
                  disabled={first}
                />
                <span
                  style={{
                    minWidth: 64,
                    textAlign: "center",
                    fontFamily: "var(--font-mono)",
                    fontSize: 13,
                    color: "var(--text)",
                  }}
                >
                  {at + 1} / {total}
                </span>
                <Round
                  icon={ChevronRight}
                  label="Next card"
                  size={52}
                  onClick={() => go(1)}
                  disabled={last}
                />
              </div>

              <p
                style={{
                  margin: 0,
                  fontSize: "var(--text-sm)",
                  color: "var(--text)",
                  textAlign: "center",
                }}
              >
                Click the card to flip it. Arrow keys move through the deck, Esc
                closes.
              </p>
            </div>,
            document.body,
          )
        : null}
    </>
  );
}

// ── the round nav button ───────────────────────────────────

/**
 * Bigger and rounder than the rail's IconButton, because in study mode these
 * two are the only things on the page to press.
 */
function Round({
  icon,
  label,
  size,
  disabled = false,
  onClick,
}: {
  icon: LucideIcon;
  label: string;
  size: number;
  disabled?: boolean;
  onClick: () => void;
}) {
  const [hover, setHover] = useState(false);
  const lit = hover && !disabled;

  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        width: size,
        height: size,
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        borderRadius: "var(--radius-pill)",
        border: `1px solid ${lit ? "var(--border-strong)" : "var(--border-subtle)"}`,
        background: lit ? "var(--surface-hover)" : "var(--surface-card)",
        color: "var(--text)",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.3 : 1,
        transition:
          "background var(--dur-fast) var(--ease-standard), border-color var(--dur-fast) var(--ease-standard)",
      }}
    >
      <Icon as={icon} size={Math.round(size * 0.42)} />
    </button>
  );
}
