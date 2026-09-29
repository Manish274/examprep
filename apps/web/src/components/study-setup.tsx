"use client";

import { useState, type CSSProperties } from "react";
import type { StudyMode } from "./rail";

const SELECT: CSSProperties = {
  height: 32,
  padding: "0 10px",
  background: "var(--surface-card)",
  border: "1px solid var(--border-subtle)",
  borderRadius: "var(--radius-md)",
  color: "var(--text)",
  fontFamily: "var(--font-sans)",
  fontSize: "var(--text-sm)",
  outline: "none",
  maxWidth: 220,
};

/**
 * The quiz and flashcard controls, shown inside the composer shell in place of
 * the text field: which document, how many, and for a quiz how hard.
 */
export function StudySetup({
  mode,
  documents,
  busy,
  onGenerate,
}: {
  mode: Exclude<StudyMode, "chat">;
  documents: { id: string; filename: string }[];
  busy: boolean;
  onGenerate: (documentId: string, count: number, difficulty: string) => void;
}) {
  const [documentId, setDocumentId] = useState("");
  const [count, setCount] = useState(mode === "quiz" ? 5 : 8);
  const [difficulty, setDifficulty] = useState("mixed");

  const chosen = documentId || documents[0]?.id || "";
  const unavailable = busy || !chosen;

  if (documents.length === 0) {
    return (
      <p
        style={{
          margin: "2px 2px 14px",
          fontSize: "var(--text-md)",
          color: "var(--text)",
        }}
      >
        Add a document with the + first — {mode === "quiz" ? "questions" : "cards"}{" "}
        are generated from your own material, so there is nothing to work from yet.
      </p>
    );
  }

  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: "var(--space-5)",
        padding: "2px 2px 14px",
      }}
    >
      <select
        value={chosen}
        onChange={(e) => setDocumentId(e.target.value)}
        style={SELECT}
        aria-label="Document"
      >
        {documents.map((document) => (
          <option key={document.id} value={document.id}>
            {document.filename}
          </option>
        ))}
      </select>

      <select
        value={count}
        onChange={(e) => setCount(Number(e.target.value))}
        style={{ ...SELECT, maxWidth: 130 }}
        aria-label={mode === "quiz" ? "Questions" : "Cards"}
      >
        {[3, 5, 8, 10, 15].map((n) => (
          <option key={n} value={n}>
            {n} {mode === "quiz" ? "questions" : "cards"}
          </option>
        ))}
      </select>

      {mode === "quiz" ? (
        <select
          value={difficulty}
          onChange={(e) => setDifficulty(e.target.value)}
          style={{ ...SELECT, maxWidth: 120 }}
          aria-label="Difficulty"
        >
          <option value="mixed">Mixed</option>
          <option value="easy">Easy</option>
          <option value="hard">Hard</option>
        </select>
      ) : null}

      <button
        type="button"
        disabled={unavailable}
        onClick={() => onGenerate(chosen, count, difficulty)}
        style={{
          height: 32,
          padding: "0 16px",
          borderRadius: "var(--radius-pill)",
          // Unavailable turns the button neutral rather than fading it, so
          // "Generating" stays as readable as every other word on the page.
          border: `1px solid ${unavailable ? "var(--border-default)" : "var(--accent)"}`,
          background: unavailable ? "var(--surface-raised)" : "var(--accent)",
          color: unavailable ? "var(--text)" : "var(--text-oncolor)",
          fontFamily: "var(--font-sans)",
          fontSize: "var(--text-sm)",
          fontWeight: 500,
          letterSpacing: "var(--track-wide)",
          cursor: unavailable ? "not-allowed" : "pointer",
          transition: "background var(--dur-fast) var(--ease-standard)",
        }}
      >
        {busy ? "Generating" : mode === "quiz" ? "Quiz me" : "Make cards"}
      </button>
    </div>
  );
}
