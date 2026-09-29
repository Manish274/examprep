"""Builds a hand-written gold set for the study-guide fixture.

Written by hand rather than generated, for one reason: a synthetic question
produced *from* a passage reuses that passage's vocabulary, which flatters
keyword retrieval and quietly inflates BM25 in every comparison drawn from it.

These questions are deliberately phrased as a student would ask them, using
words the source text mostly does not. "What happens to my data if the server
crashes" contains none of the terms in the write-ahead-logging passage. That is
the point: it is the case dense retrieval should win and BM25 should struggle,
and a test set that never poses it is not measuring retrieval.

Questions are mapped to their answering section by heading, and the chunk ids
come from the chunks the service actually indexed, so the set stays correct as
long as the document is the one that was ingested.

Fetch the chunks from GET /api/documents/<id>/chunks, save the "chunks"
array as JSON, then:

    python scripts/build_reference_gold_set.py --chunks chunks.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# (question, heading of the section that answers it)
QUESTIONS: list[tuple[str, str]] = [
    # Deliberately avoids "transitive", the term the passage uses.
    (
        "which normal form stops one column depending on another non-key column",
        "Third Normal Form",
    ),
    (
        "what happens to my data if the server loses power halfway through saving",
        "Write Ahead Logging",
    ),
    (
        "why is my query ignoring the index when I filter on the second column",
        "Composite Indexes",
    ),
    (
        "when is it acceptable to store the same fact in two places on purpose",
        "Denormalization",
    ),
    (
        "how do I pick which unique identifier to use for a table",
        "Keys and Constraints",
    ),
    (
        "what stops two people editing the same row from corrupting it",
        "Locking and Deadlock",
    ),
    (
        "which lookup structure cannot answer 'between two values' queries",
        "Hash Indexes",
    ),
    ("how does the database decide which way to run my query", "Query Planning"),
    (
        "what does it mean for a transaction to be all or nothing",
        "Transactions and ACID",
    ),
    ("how can I avoid reading the table at all for a hot query", "Covering Indexes"),
    (
        "what goes wrong when a column depends on only part of a composite key",
        "Second Normal Form",
    ),
    ("can a table have a value that is itself a list", "First Normal Form"),
    ("why might splitting a table lose a business rule", "Boyce Codd Normal Form"),
    ("how do I spread data across several machines", "Sharding"),
    ("what is the cost of keeping a standby copy perfectly up to date", "Replication"),
    (
        "which join method suits two big unsorted tables matched on equality",
        "Join Algorithms",
    ),
    (
        "what reads can I still see that are wrong at the loosest setting",
        "Isolation Levels",
    ),
    ("what rule lets me infer one dependency from others", "Functional Dependencies"),
    (
        "how many rows and columns does a table have, formally",
        "Relational Model Basics",
    ),
]


def load_questions(path: str | None) -> list[tuple[str, str]]:
    """Questions from a JSON file, or the built-in study-guide set.

    The file is a list of [question, heading] pairs, so a gold set for a new
    document needs no code change.
    """
    if path is None:
        return QUESTIONS
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [(item["question"], item["heading"]) for item in raw]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks", required=True, help="JSON file of chunks")
    parser.add_argument(
        "--questions",
        help="JSON list of {question, heading}; omit for the built-in set",
    )
    parser.add_argument("--out", default="gold_sets/study-guide.jsonl")
    args = parser.parse_args()

    chunks = json.loads(Path(args.chunks).read_text(encoding="utf-8"))
    by_heading: dict[str, list[str]] = {}
    for chunk in chunks:
        heading = chunk.get("heading") or ""
        by_heading.setdefault(heading, []).append(chunk["id"])

    entries: list[dict] = []
    missing: list[str] = []
    for question, heading in load_questions(args.questions):
        ids = by_heading.get(heading)
        if not ids:
            missing.append(heading)
            continue
        entries.append(
            {
                "question": question,
                # Chunk overlap can split one section across several chunks;
                # any of them answering counts as a hit.
                "relevant_chunk_ids": sorted(ids),
                "note": heading,
            }
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")

    print(f"wrote {len(entries)} gold queries to {out}")
    if missing:
        print(f"headings not found in the index: {missing}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
