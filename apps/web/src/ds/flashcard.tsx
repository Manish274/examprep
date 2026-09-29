"use client";

import { useRef, useState, type CSSProperties } from "react";
import { usePrefersReducedMotion } from "./motion";

/** Degrees of tilt at the very edge of the card. Deliberately small. */
const TILT_MAX = 5;
/** How far the card rises toward the viewer while the pointer is on it. */
const TILT_LIFT = 6;
/** Radius of the sheen under the cursor, in px. */
const SHEEN = 110;

/**
 * The card stock: a light printed gradient, so everything on it is set in
 * ink. White type over it is unreadable at the sizes a card uses.
 */
const CARD_STOCK = "url(/flashcard-face.webp) center / cover no-repeat";

/** On the stock a light wash would flatten the gradient; a soft dark edge gives
 *  the card a shape instead. */
const STOCK_EDGE =
  "radial-gradient(130% 100% at 50% 0%, transparent 42%, rgba(21,18,28,.16))";

/** Everything on the card, labels included, is set in the same full black:
 *  one strength of ink, as the rest of the app keeps one brightness of text. */
const INK = "#000000";

const FACE: CSSProperties = {
  position: "absolute",
  inset: 0,
  display: "flex",
  flexDirection: "column",
  justifyContent: "center",
  padding: "var(--space-11)",
  background: CARD_STOCK,
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
  color: INK,
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
  color: INK,
};

/** The wording on either face: set the same, so turning the card changes the
 *  words and nothing else. */
const FACE_TEXT: CSSProperties = {
  margin: 0,
  position: "relative",
  maxHeight: "100%",
  overflowY: "auto",
  fontFamily: "var(--font-display)",
  fontStyle: "italic",
  fontSize: "var(--display-md)",
  lineHeight: "var(--display-md-lh)",
  letterSpacing: "var(--track-tight)",
};

const FACE_EDGE: CSSProperties = {
  position: "absolute",
  inset: 0,
  background: STOCK_EDGE,
  pointerEvents: "none",
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
  hint,
  height = 260,
  flipped,
  onFlip,
  onActivate,
}: {
  question: string;
  answer: string;
  hint?: string;
  height?: number;
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
          <div style={FACE}>
            <div aria-hidden="true" style={FACE_EDGE} />
            <span style={FACE_LABEL}>Question</span>
            <p className="ep-scroll" style={{ ...FACE_TEXT, color: INK }}>
              {question}
            </p>
            <span style={FACE_FOOT}>
              <span style={{ marginLeft: "auto" }}>{hint ?? "Click to reveal"}</span>
            </span>
          </div>

          <div style={{ ...FACE, transform: "rotateY(180deg)" }}>
            <div aria-hidden="true" style={FACE_EDGE} />
            <span style={FACE_LABEL}>Answer</span>
            <p className="ep-scroll" style={{ ...FACE_TEXT, color: INK }}>
              {answer}
            </p>
            <span style={FACE_FOOT}>
              <span style={{ marginLeft: "auto" }}>{hint ?? "Click for the question"}</span>
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
