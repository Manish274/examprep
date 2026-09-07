"""Repoint a gold set at the current chunk ids.

    python scripts/remap_gold_set.py --user <id> --gold gold_sets/nlp-lecture.jsonl

Chunk ids are `uuid5(document_id, sha256(text))`, which is what makes them
reproducible -- and what makes them change the moment parsing or chunking
changes. Re-ingesting a document after enabling the vision pass gives every
chunk a new id, and a gold set written before that names nothing that exists.

The questions are still good; only the pointers are stale. Each entry carries
the section it was written from in `note`, and that section still exists, so
the mapping is mechanical rather than a matter of judgement.

Refuses to guess. A note that matches no heading, or several, is reported and
left alone -- a gold set quietly pointed at the wrong passage is worse than one
that is obviously broken, because it produces numbers.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncpg

from app.config import get_settings

CHUNKS = """
SELECT c.id, c.heading, c.document_id
FROM document_chunks c
JOIN documents d ON d.id = c.document_id
WHERE d.user_id = $1
  AND ($2::uuid IS NULL OR c.document_id = $2)
"""


def _normalise(value: str) -> str:
    return " ".join(value.split()).casefold()


async def remap(args: argparse.Namespace) -> int:
    path = Path(args.gold)
    if not path.is_file():
        print(f"gold set not found: {path}", file=sys.stderr)
        return 1

    entries = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("//")
    ]

    dsn = args.dsn or os.environ.get("DATABASE_URL") or get_settings().DATABASE_URL
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    if pool is None:
        print("cannot reach the database", file=sys.stderr)
        return 1

    try:
        rows = await pool.fetch(CHUNKS, args.user, args.document)
    finally:
        await pool.close()

    by_heading: dict[str, list[str]] = {}
    for row in rows:
        if row["heading"]:
            by_heading.setdefault(_normalise(row["heading"]), []).append(
                str(row["id"])
            )

    remapped = 0
    unchanged = 0
    problems: list[str] = []

    for entry in entries:
        note = entry.get("note", "")
        if not note:
            problems.append(f"{entry['question'][:50]!r}: no note to match on")
            continue

        matches = by_heading.get(_normalise(note), [])
        if len(matches) != 1:
            problems.append(
                f"{note!r}: matched {len(matches)} chunks; left unchanged"
            )
            continue

        if entry.get("relevant_chunk_ids") == matches:
            unchanged += 1
        else:
            entry["relevant_chunk_ids"] = matches
            remapped += 1

    print(f"{len(entries)} entries: {remapped} remapped, {unchanged} already correct")
    if problems:
        print(f"\n{len(problems)} could not be remapped:")
        for problem in problems:
            print(f"  {problem}")

    if args.dry_run:
        print("\n(dry run; nothing written)")
        return 1 if problems else 0

    if remapped:
        # Written next to the original by default: overwriting the file that a
        # recorded result was produced from would make that result
        # unreproducible.
        out = Path(args.out) if args.out else path
        out.write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8"
        )
        print(f"\nwritten to {out}")

    return 1 if problems else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True)
    parser.add_argument("--gold", required=True)
    parser.add_argument("--document", help="restrict to one document id")
    parser.add_argument("--out", help="write here instead of in place")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--dsn", help="override DATABASE_URL")
    return asyncio.run(remap(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
