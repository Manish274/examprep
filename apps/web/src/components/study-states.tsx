"use client";

import { Badge, Button, Card, Display, ProgressRing } from "@/ds";

/**
 * The three states a quiz or a flashcard set passes through before there is
 * anything to show, shared by both panels.
 */

/** Before anything is generated: what this mode will make. */
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
          color: "var(--text)",
          textWrap: "pretty",
        }}
      >
        {body}
      </p>
    </div>
  );
}

/**
 * A ring and a word.
 *
 * Generation is one long job whose result arrives all at once, so there is no
 * count to show on the way; the ring says that something is happening, and
 * nothing more precise than that.
 */
export function Generating() {
  return (
    <div
      className="ep-rise"
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: "var(--space-7)",
        padding: "var(--space-11) 0",
      }}
    >
      <ProgressRing size={72} indeterminate />
      <span
        style={{
          fontSize: "var(--caps-size)",
          fontWeight: 500,
          letterSpacing: "var(--caps-track)",
          textTransform: "uppercase",
          color: "var(--text)",
        }}
      >
        Generating
      </span>
    </div>
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
            color: "var(--text)",
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
