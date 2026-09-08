"use client";

import { Badge, Button, Card, Display, QuizOption } from "@/ds";
import type { QuizState } from "@/hooks/use-study";

/**
 * The quiz surface.
 *
 * Multiple choice and true/false are graded without a model; a written answer
 * gets partial credit and is told what it was missing. The score is the sum of
 * what was awarded, not a count of right answers, so a half-answer reads as a
 * half-answer.
 */

const LETTERS = ["A", "B", "C", "D", "E", "F"] as const;

const WRITTEN_FIELD: React.CSSProperties = {
  width: "100%",
  minHeight: 84,
  padding: "var(--space-6) var(--space-7)",
  background: "transparent",
  border: "1px solid var(--border-subtle)",
  borderRadius: "var(--radius-lg)",
  color: "var(--paper-0)",
  fontFamily: "var(--font-sans)",
  fontSize: "var(--text-lg)",
  lineHeight: "var(--text-lg-lh)",
  resize: "vertical",
  outline: "none",
};

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
        serif="ten questions from"
        sans="the pages you skipped"
        body="Pick a document below and Examprep will write questions from it — the same index the chat reads, so a question you miss points back at the passage it came from."
      />
    );
  }

  if (state.status === "generating") {
    return (
      <Working
        label="Writing questions"
        produced={state.produced}
        total={state.total}
        note="Generation is several rate-limited model calls, so this takes a moment."
      />
    );
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
            color: "var(--paper-0)",
          }}
        >
          {state.title}
        </h2>
        {graded ? (
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: "var(--paper-0)",
            }}
          >
            {state.score} / {state.maxScore}
          </span>
        ) : (
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--text-faint)" }}>
            {state.questions.length} questions
          </span>
        )}
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
                  color: "var(--accent)",
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
                  color: "var(--paper-0)",
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
              {question.type === "mcq" && question.options
                ? question.options.map((option, optionIndex) => {
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
                  })
                : null}

              {question.type === "true_false"
                ? (["True", "False"] as const).map((option, optionIndex) => {
                    const selected = chosen === option;
                    const correct =
                      graded &&
                      result &&
                      String(result.correctAnswer).toLowerCase() ===
                        option.toLowerCase();
                    return (
                      <QuizOption
                        key={option}
                        letter={LETTERS[optionIndex]!}
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
                        onClick={() => onAnswer(question.id, option)}
                      >
                        {option}
                      </QuizOption>
                    );
                  })
                : null}

              {question.type === "short_answer" ? (
                <textarea
                  value={chosen ?? ""}
                  disabled={graded}
                  placeholder="Answer in your own words"
                  onChange={(e) => onAnswer(question.id, e.target.value)}
                  style={WRITTEN_FIELD}
                />
              ) : null}

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
                  <div style={{ display: "flex", alignItems: "center", gap: "var(--space-4)" }}>
                    <Badge
                      tone={
                        result.isCorrect
                          ? "correct"
                          : result.awarded > 0
                            ? "review"
                            : "wrong"
                      }
                    >
                      {result.awarded} awarded
                    </Badge>
                  </div>
                  <p
                    style={{
                      margin: 0,
                      fontSize: "var(--text-md)",
                      lineHeight: "var(--text-md-lh)",
                      color: "var(--text-body)",
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

      <div style={{ display: "flex", gap: "var(--space-5)", paddingLeft: 30 }}>
        {graded ? (
          <Button variant="outline" onClick={onReset}>
            New quiz
          </Button>
        ) : (
          <Button variant="primary" onClick={onSubmit}>
            Check answers
          </Button>
        )}
      </div>

      {state.message ? (
        <p style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--state-wrong)" }}>
          {state.message}
        </p>
      ) : null}
    </div>
  );
}

// ── shared states ──────────────────────────────────────────

export function Empty({
  serif,
  sans,
  body,
}: {
  serif: string;
  sans: string;
  body: string;
}) {
  return (
    <div
      className="ep-rise"
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: "var(--space-7)",
        textAlign: "center",
      }}
    >
      <Display size="md" align="center" serif={serif} sans={sans} />
      <p
        style={{
          margin: 0,
          maxWidth: "var(--reading-max)",
          fontSize: "var(--text-md)",
          lineHeight: "var(--text-md-lh)",
          color: "var(--text-muted)",
          textWrap: "pretty",
        }}
      >
        {body}
      </p>
    </div>
  );
}

export function Working({
  label,
  produced,
  total,
  note,
}: {
  label: string;
  produced: number;
  total: number;
  note: string;
}) {
  const percent = total > 0 ? Math.round((produced / total) * 100) : 0;

  return (
    <Card padding="var(--space-10)">
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-6)" }}>
        <span
          style={{
            fontSize: "var(--caps-size)",
            fontWeight: 500,
            letterSpacing: "var(--caps-track)",
            textTransform: "uppercase",
            color: "var(--text-faint)",
          }}
        >
          {label}
        </span>
        {/* A number, never a spinner: the system's own rule, and it is the only
            thing that tells a student whether anything is happening. */}
        <span
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 26,
            color: "var(--paper-0)",
          }}
        >
          {produced} / {total || "…"}
        </span>
        <div
          style={{
            height: 2,
            background: "var(--line-1)",
            borderRadius: "var(--radius-pill)",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              width: `${percent}%`,
              height: "100%",
              background: "var(--accent)",
              transition: "width var(--dur-slow) var(--ease-standard)",
            }}
          />
        </div>
        <p style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--text-muted)" }}>
          {note}
        </p>
      </div>
    </Card>
  );
}

export function Failed({
  message,
  onReset,
}: {
  message: string | null;
  onReset: () => void;
}) {
  return (
    <Card padding="var(--space-10)">
      <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-6)" }}>
        <Badge tone="wrong">Did not finish</Badge>
        <p
          style={{
            margin: 0,
            fontSize: "var(--text-md)",
            lineHeight: "var(--text-md-lh)",
            color: "var(--text-body)",
          }}
        >
          {message ??
            "Generation did not complete. Nothing is wrong with your document."}
        </p>
        <div>
          <Button variant="outline" onClick={onReset}>
            Try again
          </Button>
        </div>
      </div>
    </Card>
  );
}
