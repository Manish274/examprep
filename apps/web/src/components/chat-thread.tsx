"use client";

import { Badge, Message } from "@/ds";
import { Answer, Sources } from "./answer";
import type { ChatStage, Turn } from "@/hooks/use-chat";

/**
 * What the wait is, said plainly. Most of it comes before the first word,
 * while the question is rewritten, searched for and ranked.
 */
function stageLabel(stage: ChatStage | undefined, passages?: number): string {
  if (stage === "writing") {
    return passages
      ? `writing from ${passages} ${passages === 1 ? "passage" : "passages"}`
      : "writing";
  }
  if (stage === "rechecking") return "reading the passages again";
  return "searching your notes";
}

/**
 * The conversation.
 *
 * A refusal is rendered as an outcome rather than an error: the material not
 * covering something is the correct answer to a question it does not cover,
 * and dressing that up as a failure would push a student to trust an answer
 * the system deliberately declined to give.
 */
export function ChatThread({
  turns,
  error,
}: {
  turns: Turn[];
  error: string | null;
}) {
  if (turns.length === 0 && !error) return null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-10)" }}>
      {turns.map((turn) => (
        <Message key={turn.id} role={turn.role}>
          {turn.role === "user" ? (
            <span style={{ whiteSpace: "pre-wrap" }}>{turn.content}</span>
          ) : (
            <>
              {turn.unsupported ? (
                <div style={{ marginBottom: "var(--space-6)" }}>
                  <Badge tone="review">Not in your material</Badge>
                </div>
              ) : null}
              <Answer text={turn.content} sources={turn.sources} />
              {turn.streaming && turn.content === "" ? (
                <span
                  role="status"
                  aria-live="polite"
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 8,
                    fontFamily: "var(--font-mono)",
                    fontSize: 11,
                    color: "var(--text-faint)",
                  }}
                >
                  <span aria-hidden="true" className="ep-pulse" />
                  {stageLabel(turn.stage, turn.passages)}
                </span>
              ) : null}
              {!turn.streaming ? <Sources sources={turn.sources} /> : null}
            </>
          )}
        </Message>
      ))}

      {error ? (
        <p
          style={{
            margin: 0,
            fontSize: "var(--text-sm)",
            color: "var(--state-wrong)",
          }}
        >
          {error}
        </p>
      ) : null}
    </div>
  );
}
