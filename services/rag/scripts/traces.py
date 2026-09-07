"""Read the trace table back.

    python scripts/traces.py                       # latency and errors by stage
    python scripts/traces.py --kind chat --hours 6
    python scripts/traces.py --correlation <id>    # one operation, span by span

Tracing that can only be written is not observability. This is the smallest
thing that makes the table answer questions: which stage is slow, which stage
is failing, and what happened during one particular answer.

Percentiles rather than means throughout. On a rate-limited free tier a single
query that waited out a 429 pulls a mean somewhere no request actually was.
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

SUMMARY = """
SELECT kind,
       name,
       count(*)                                              AS calls,
       count(*) FILTER (WHERE error IS NOT NULL)             AS errors,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY duration_ms)  AS p50,
       percentile_disc(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95,
       max(duration_ms)                                      AS worst
FROM traces
WHERE created_at > now() - ($1 || ' hours')::interval
  AND ($2::text IS NULL OR kind = $2)
GROUP BY kind, name
ORDER BY kind, p95 DESC NULLS LAST
"""

SPANS = """
SELECT name, duration_ms, error, input, output, metadata, created_at
FROM traces
WHERE correlation_id = $1
ORDER BY created_at
"""

RECENT_ERRORS = """
SELECT kind, name, error, created_at, correlation_id
FROM traces
WHERE error IS NOT NULL
  AND created_at > now() - ($1 || ' hours')::interval
ORDER BY created_at DESC
LIMIT $2
"""


def _decode(value: object) -> object:
    """asyncpg hands jsonb back as text unless a codec is registered."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


async def summarise(pool: asyncpg.Pool, hours: int, kind: str | None) -> None:
    rows = await pool.fetch(SUMMARY, str(hours), kind)
    if not rows:
        print(f"No traces in the last {hours}h" + (f" for kind={kind}" if kind else ""))
        return

    header = (
        f"{'kind':<22}{'stage':<20}{'calls':>7}{'errors':>8}"
        f"{'p50 ms':>9}{'p95 ms':>9}{'worst':>9}"
    )
    print(header)
    print("-" * len(header))
    for row in rows:
        errors = row["errors"]
        # Marked rather than merely printed: a column of numbers hides the one
        # stage that is failing.
        mark = " !" if errors else ""
        print(
            f"{row['kind']:<22}{row['name']:<20}{row['calls']:>7}"
            f"{errors:>8}{row['p50'] or 0:>9}{row['p95'] or 0:>9}"
            f"{row['worst'] or 0:>9}{mark}"
        )


async def show_errors(pool: asyncpg.Pool, hours: int, limit: int) -> None:
    rows = await pool.fetch(RECENT_ERRORS, str(hours), limit)
    if not rows:
        return
    print(f"\nrecent failures ({len(rows)}):")
    for row in rows:
        stamp = row["created_at"].strftime("%H:%M:%S")
        print(f"  {stamp} {row['kind']}/{row['name']}: {row['error'][:120]}")
        print(f"           {row['correlation_id']}")


async def show_trace(pool: asyncpg.Pool, correlation_id: str) -> None:
    rows = await pool.fetch(SPANS, correlation_id)
    if not rows:
        print(f"No spans for correlation id {correlation_id}")
        return

    print(f"{len(rows)} spans for {correlation_id}\n")
    for row in rows:
        marker = "FAILED" if row["error"] else "ok"
        # ASCII only: the Windows console this runs on is cp1252.
        print(f"- {row['name']}  ({row['duration_ms']}ms, {marker})")
        for label in ("input", "output", "metadata"):
            payload = _decode(row[label])
            if payload:
                print(f"     {label}: {json.dumps(payload, default=str)[:400]}")
        if row["error"]:
            print(f"     error: {row['error'][:400]}")


async def run(args: argparse.Namespace) -> int:
    dsn = args.dsn or os.environ.get("DATABASE_URL") or get_settings().DATABASE_URL
    try:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    except Exception as exc:
        print(f"cannot reach the database: {exc}", file=sys.stderr)
        return 1
    if pool is None:
        return 1

    try:
        if args.correlation:
            await show_trace(pool, args.correlation)
        else:
            await summarise(pool, args.hours, args.kind)
            if args.errors:
                await show_errors(pool, args.hours, args.errors)
    finally:
        await pool.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--kind", help="chat, ingestion, test_generation, ...")
    parser.add_argument("--correlation", help="dump one operation's spans in order")
    parser.add_argument(
        "--errors",
        type=int,
        default=10,
        help="how many recent failures to list (0 to skip)",
    )
    parser.add_argument("--dsn", help="override DATABASE_URL")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
