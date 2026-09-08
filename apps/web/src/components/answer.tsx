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
 * A marker with no matching source is left as plain text. The pipeline already
 * refuses to invent them, and silently deleting one would hide the case where
 * it did.
 */

const MARKER = /\[(S\d+)\]/g;
const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\n]+\*)/g;

function inline(text: string, key: string): ReactNode[] {
  return text.split(INLINE).filter(Boolean).map((part, index) => {
    const id = `${key}-${index}`;
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={id} style={{ color: "var(--paper-0)", fontWeight: 500 }}>
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
    const marker = match[1]!;
    const source = sources.find((s) => s.marker === marker);

    out.push(...inline(text.slice(last, match.index), `${key}-t${match.index}`));

    if (source) {
      const where = [
        source.documentName,
        source.slideNumber ? `slide ${source.slideNumber}` : null,
        source.pageNumber ? `p.${source.pageNumber}` : null,
        source.headingPath?.length ? source.headingPath.join(" › ") : null,
      ]
        .filter(Boolean)
        .join(" · ");
      out.push(
        <CitationChip
          key={`${key}-c${match.index}`}
          index={marker.slice(1)}
          source={where}
        />,
      );
    } else {
      out.push(<Fragment key={`${key}-c${match.index}`}>{match[0]}</Fragment>);
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
            color: "var(--paper-0)",
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
            color: "var(--text-muted)",
            border: "1px solid var(--line-1)",
            borderRadius: "var(--radius-xs)",
            padding: "5px 8px",
          }}
        >
          {[
            source.documentName,
            source.slideNumber
              ? `slide ${source.slideNumber}`
              : source.pageNumber
                ? `p.${source.pageNumber}`
                : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </span>
      ))}
    </div>
  );
}
