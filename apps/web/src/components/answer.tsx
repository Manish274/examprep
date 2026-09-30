"use client";

import { Fragment, type ReactNode } from "react";
import { CitationChip } from "@/ds";
import type { MessageSource } from "@/lib/api";

/**
 * Renders a grounded answer: the little Markdown the models emit, with `[S1]`
 * markers turned into citation chips that name their source on hover.
 *
 * Built as React nodes rather than `innerHTML`. The text is model output about
 * documents a student uploaded -- neither is something to hand to a raw HTML
 * sink, and escaping-then-parsing is more work than simply not doing it.
 *
 * Models cite in groups as often as singly -- "[S1, S2]", or a run, "[S2-S4]" --
 * and each marker in a group becomes its own chip.
 *
 * A marker with no matching source is left as plain text. The pipeline already
 * refuses to invent them, and silently deleting one would hide the case where
 * it did.
 */

const MARKER = /\[(S\d+(?:\s*[,;\-–]\s*S\d+)*)\]/g;
const RUN = /^S(\d+)\s*[-–]\s*S(\d+)$/;

// Beyond this a "run" is a malformed marker, not a citation of fifty sources.
const MAX_RUN = 12;

/** The markers one bracket names: "S1, S3" is S1 and S3; "S2-S4" is S2 to S4. */
function markersIn(group: string): string[] {
  const numbers: number[] = [];
  for (const part of group.split(/\s*[,;]\s*/)) {
    const run = RUN.exec(part);
    const from = run ? Number(run[1]) : 0;
    const to = run ? Number(run[2]) : 0;
    if (run && to >= from && to - from <= MAX_RUN) {
      for (let n = from; n <= to; n += 1) numbers.push(n);
    } else {
      for (const found of part.matchAll(/S(\d+)/g)) numbers.push(Number(found[1]));
    }
  }
  return [...new Set(numbers)].map((n) => `S${n}`);
}

/** "p.4", or "pp.4–5" for a passage that runs across a page break. */
function pages(source: MessageSource): string | null {
  if (!source.pageNumber) return null;
  return source.pageEnd
    ? `pp.${source.pageNumber}–${source.pageEnd}`
    : `p.${source.pageNumber}`;
}
const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\n]+\*)/g;

function inline(text: string, key: string): ReactNode[] {
  return text.split(INLINE).filter(Boolean).map((part, index) => {
    const id = `${key}-${index}`;
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={id} style={{ color: "var(--text)", fontWeight: 500 }}>
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return (
        <code
          key={id}
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "0.86em",
            background: "var(--surface-raised)",
            padding: "1px 5px",
            borderRadius: "var(--radius-xs)",
          }}
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.startsWith("*") && part.endsWith("*")) {
      return <em key={id}>{part.slice(1, -1)}</em>;
    }
    return <Fragment key={id}>{part}</Fragment>;
  });
}

function withCitations(
  text: string,
  sources: MessageSource[],
  key: string,
): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let match: RegExpExecArray | null;

  MARKER.lastIndex = 0;
  while ((match = MARKER.exec(text)) !== null) {
    out.push(...inline(text.slice(last, match.index), `${key}-t${match.index}`));

    for (const marker of markersIn(match[1]!)) {
      const source = sources.find((s) => s.marker === marker);
      const chipKey = `${key}-c${match.index}-${marker}`;
      if (source) {
        const where = [
          source.documentName,
          source.slideNumber ? `slide ${source.slideNumber}` : null,
          pages(source),
          source.headingPath?.length ? source.headingPath.join(" › ") : null,
        ]
          .filter(Boolean)
          .join(" · ");
        out.push(
          <CitationChip key={chipKey} index={marker.slice(1)} source={where} />,
        );
      } else {
        out.push(<Fragment key={chipKey}>{`[${marker}]`}</Fragment>);
      }
    }
    last = match.index + match[0].length;
  }

  out.push(...inline(text.slice(last), `${key}-tail`));
  return out;
}

export function Answer({
  text,
  sources,
}: {
  text: string;
  sources: MessageSource[];
}) {
  const blocks: ReactNode[] = [];
  let bullets: string[] = [];

  const flushBullets = (key: string) => {
    if (bullets.length === 0) return;
    blocks.push(
      <ul key={key} style={{ margin: "6px 0", paddingLeft: 20 }}>
        {bullets.map((item, index) => (
          <li key={`${key}-${index}`} style={{ margin: "3px 0" }}>
            {withCitations(item, sources, `${key}-${index}`)}
          </li>
        ))}
      </ul>,
    );
    bullets = [];
  };

  text.split("\n").forEach((line, index) => {
    const key = `l${index}`;
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    if (bullet) {
      bullets.push(bullet[1]!);
      return;
    }
    flushBullets(`${key}-ul`);

    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      blocks.push(
        <h3
          key={key}
          style={{
            margin: "18px 0 6px",
            fontSize: "var(--text-lg)",
            fontWeight: 500,
            color: "var(--text)",
          }}
        >
          {withCitations(heading[2]!, sources, key)}
        </h3>,
      );
      return;
    }

    if (line.trim() === "") return;

    blocks.push(
      <p key={key} style={{ margin: "0 0 10px" }}>
        {withCitations(line, sources, key)}
      </p>,
    );
  });

  flushBullets("tail-ul");

  return <div style={{ maxWidth: "var(--reading-max)" }}>{blocks}</div>;
}

/** The source list under an answer: what it actually cited, and where from. */
export function Sources({ sources }: { sources: MessageSource[] }) {
  if (sources.length === 0) return null;

  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: "var(--space-3)",
        paddingTop: "var(--space-6)",
        borderTop: "1px solid var(--line-1)",
      }}
    >
      {sources.map((source) => (
        <span
          key={`${source.marker}-${source.chunkId}`}
          title={source.snippet}
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 10,
            color: "var(--text)",
            border: "1px solid var(--line-1)",
            borderRadius: "var(--radius-xs)",
            padding: "5px 8px",
          }}
        >
          {[
            source.documentName,
            source.slideNumber ? `slide ${source.slideNumber}` : pages(source),
          ]
            .filter(Boolean)
            .join(" · ")}
        </span>
      ))}
    </div>
  );
}
