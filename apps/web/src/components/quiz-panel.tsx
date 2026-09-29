"use client";

import { Badge, Button, Card, ProgressRing, QuizOption } from "@/ds";
import type { QuizState } from "@/hooks/use-study";
import { Empty, Failed, Generating } from "./study-states";

/**
 * The quiz surface.
 *
 * Multiple choice only, so every question is graded exactly and instantly with
 * no model call and no judgement call. The score lands at the bottom, under the
 * answers it came from, rather than in a header the student has already
 * scrolled past.
 */

const LETTERS = ["A", "B", "C", "D", "E", "F"] as const;

export function QuizPanel({
  state,
  onAnswer,
  onSubmit,
  onReset,
}: {
  state: QuizState;
  onAnswer: (questionId: string, response: string) => void;
  onSubmit: () => void;
  onReset: () => void;
}) {
  if (state.status === "idle") {
    return (
      <Empty
        serif="questions from"
        sans="the pages you skipped"
        body="Pick a document below and Examprep will write multiple-choice questions from it — the same index the chat reads, so an answer you miss points back at the passage it came from."
      />
    );
  }

  if (state.status === "generating") {
    return <Generating />;
  }

  if (state.status === "failed") {
    return <Failed message={state.message} onReset={onReset} />;
  }

  const graded = state.results !== null;
  const resultFor = (questionId: string) =>
    state.results?.find((r) => r.questionId === questionId);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-9)" }}>
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
            fontSize: 10,
            color: "var(--text)",
          }}
        >
          {state.questions.length} questions
        </span>
      </div>

      {state.questions.map((question, index) => {
        const result = resultFor(question.id);
        const chosen = state.answers[question.id];

        return (
          <div
            key={question.id}
            style={{ display: "flex", flexDirection: "column", gap: "var(--space-6)" }}
          >
            <div style={{ display: "flex", gap: "var(--space-6)" }}>
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  color: "var(--text-accent)",
                  paddingTop: 4,
                }}
              >
                {String(index + 1).padStart(2, "0")}
              </span>
              <p
                style={{
                  margin: 0,
                  fontSize: "var(--text-xl)",
                  lineHeight: "var(--text-xl-lh)",
                  color: "var(--text)",
                }}
              >
                {question.prompt}
              </p>
            </div>

            <div
              style={{
                display: "flex",
                flexDirection: "column",
                gap: "var(--space-4)",
                paddingLeft: 30,
              }}
            >
              {question.options?.map((option, optionIndex) => {
                const value = String(optionIndex);
                const selected = chosen === value;
                const correct =
                  graded && result && String(result.correctAnswer) === value;
                return (
                  <QuizOption
                    key={value}
                    letter={LETTERS[optionIndex] ?? String(optionIndex + 1)}
                    disabled={graded}
                    state={
                      correct
                        ? "correct"
                        : graded && selected
                          ? "wrong"
                          : selected
                            ? "selected"
                            : "idle"
                    }
                    onClick={() => onAnswer(question.id, value)}
                  >
                    {option}
                  </QuizOption>
                );
              })}

              {graded && result ? (
                <div
                  style={{
                    display: "flex",
                    flexDirection: "column",
                    gap: "var(--space-4)",
                    padding: "var(--space-6) var(--space-7)",
                    background: "var(--surface-card)",
                    border: "1px solid var(--border-subtle)",
                    borderRadius: "var(--radius-lg)",
                  }}
                >
                  <div
                    style={{ display: "flex", alignItems: "center", gap: "var(--space-4)" }}
                  >
                    <Badge tone={result.isCorrect ? "correct" : "wrong"}>
                      {result.isCorrect ? "Correct" : "Missed"}
                    </Badge>
                  </div>
                  <p
                    style={{
                      margin: 0,
                      fontSize: "var(--text-md)",
                      lineHeight: "var(--text-md-lh)",
                      color: "var(--text)",
                    }}
                  >
                    {result.feedback}
                  </p>
                </div>
              ) : null}
            </div>
          </div>
        );
      })}

      {/* The grade, under the answers it came from. */}
      {graded ? (
        <ScoreCard
          score={state.score ?? 0}
          maxScore={state.maxScore ?? state.questions.length}
          onReset={onReset}
        />
      ) : (
        <div style={{ display: "flex", gap: "var(--space-5)", paddingLeft: 30 }}>
          <Button variant="primary" onClick={onSubmit}>
            Submit and grade
          </Button>
        </div>
      )}

      {state.message ? (
        <p style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--text-wrong)" }}>
          {state.message}
        </p>
      ) : null}
    </div>
  );
}

// ── the grade ──────────────────────────────────────────────

function ScoreCard({
  score,
  maxScore,
  onReset,
}: {
  score: number;
  maxScore: number;
  onReset: () => void;
}) {
  const fraction = maxScore > 0 ? score / maxScore : 0;
  const percent = Math.round(fraction * 100);

  return (
    <Card padding="var(--space-10)" wash>
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          alignItems: "center",
          gap: "var(--space-9)",
        }}
      >
        <ProgressRing value={fraction} size={84} label={`${percent}%`} />

        <div style={{ flex: 1, minWidth: 200, display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
          <span
            style={{
              fontSize: "var(--caps-size)",
              fontWeight: 500,
              letterSpacing: "var(--caps-track)",
              textTransform: "uppercase",
              color: "var(--text)",
            }}
          >
            Your grade
          </span>
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 30,
              color: "var(--text)",
              lineHeight: 1.1,
            }}
          >
            {score} / {maxScore}
          </span>
          <span style={{ fontSize: "var(--text-sm)", color: "var(--text)" }}>
            {/* Literal, not congratulatory: the product states what it found. */}
            {score === maxScore
              ? "Every question correct."
              : `${maxScore - score} to review above.`}
          </span>
        </div>

        <Button variant="outline" onClick={onReset}>
          New quiz
        </Button>
      </div>
    </Card>
  );
}
