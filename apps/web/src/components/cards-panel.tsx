"use client";

import { useState } from "react";
import { ArrowLeft, ArrowRight, RotateCcw } from "lucide-react";
import { Button, Flashcard, IconButton } from "@/ds";
import { Empty, Failed, Working } from "./quiz-panel";
import type { CardsState } from "@/hooks/use-study";

/**
 * The flashcard surface: one card at a time, click to flip.
 *
 * A deck rather than a grid. Seeing every answer at once is reading, not
 * recall, and recall is the only reason to make cards.
 */
export function CardsPanel({
  state,
  onReset,
}: {
  state: CardsState;
  onReset: () => void;
}) {
  const [index, setIndex] = useState(0);
  const [flipped, setFlipped] = useState(false);

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
    return (
      <Working
        label="Writing cards"
        produced={state.produced}
        total={state.total}
        note="Cards are generated from the indexed material, a batch at a time."
      />
    );
  }

  if (state.status === "failed") {
    return <Failed message={state.message} onReset={onReset} />;
  }

  const card = state.cards[Math.min(index, state.cards.length - 1)];
  if (!card) {
    return <Failed message="That set came back empty." onReset={onReset} />;
  }

  const go = (delta: number) => {
    setFlipped(false);
    setIndex((current) =>
      Math.min(state.cards.length - 1, Math.max(0, current + delta)),
    );
  };

  return (
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
            color: "var(--paper-0)",
          }}
        >
          {state.title}
        </h2>
        <span
          style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-faint)" }}
        >
          {index + 1} / {state.cards.length}
        </span>
      </div>

      <Flashcard
        question={card.front}
        answer={card.back}
        flipped={flipped}
        onFlip={setFlipped}
      />

      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-5)" }}>
        <IconButton
          icon={ArrowLeft}
          label="Previous card"
          onClick={() => go(-1)}
          disabled={index === 0}
        />
        <IconButton
          icon={ArrowRight}
          label="Next card"
          onClick={() => go(1)}
          disabled={index >= state.cards.length - 1}
        />
        <IconButton
          icon={RotateCcw}
          label="Flip"
          onClick={() => setFlipped((was) => !was)}
        />
        <span style={{ flex: 1 }} />
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            setIndex(0);
            setFlipped(false);
            onReset();
          }}
        >
          New set
        </Button>
      </div>
    </div>
  );
}
