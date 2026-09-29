"""Run a retrieval evaluation from the command line.

    python scripts/evaluate.py --user <id> --gold gold_sets/study-guide.jsonl

Talks to a running service rather than constructing the pipeline in-process, so
what is measured is exactly what a student's question goes through -- including
the rate limiting and the reranker fallback behaviour.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = "http://localhost:8000"


def post(url: str, token: str, path: str, payload: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        f"{url}{path}",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json", "x-internal-token": token},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:400]
        raise SystemExit(f"{path} failed ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"cannot reach {url}: {exc.reason}") from exc


def analyse(path: Path, baseline: str) -> int:
    """Recomputes significance from a saved report.

    The per-query metrics are in the file, so asking a new question of an old
    run -- a different baseline, a different metric -- needs no retrieval, no
    embeddings and no quota.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.eval.significance import compare_all, render

    if not path.is_file():
        raise SystemExit(f"report not found: {path}")
    report = json.loads(path.read_text(encoding="utf-8"))

    per_query = {
        s["strategy"]: s.get("per_query", []) for s in report.get("strategies", [])
    }
    if not any(per_query.values()):
        raise SystemExit(
            f"{path} carries no per-query metrics. It predates them; re-run the "
            "evaluation to produce a report that can be analysed."
        )

    print(f"{report.get('query_count', '?')} queries, baseline {baseline}")
    print()
    print(render(compare_all(per_query, baseline)))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", help="user id whose corpus to search")
    parser.add_argument("--gold", help="path to a JSONL gold set")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--token", default="dev_internal_token_change_me")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument(
        "--save", help="write the full report as JSON to this path", default=None
    )
    parser.add_argument(
        "--baseline",
        default="hybrid_rerank",
        help="strategy the others are tested against for significance",
    )
    parser.add_argument(
        "--analyse",
        metavar="REPORT.json",
        help=(
            "re-run the significance analysis on a saved report instead of "
            "evaluating again -- costs nothing and touches no quota"
        ),
    )
    args = parser.parse_args()

    if args.analyse:
        return analyse(Path(args.analyse), args.baseline)
    if not (args.user and args.gold):
        parser.error("--user and --gold are required unless --analyse is given")

    gold_path = Path(args.gold)
    if not gold_path.is_file():
        raise SystemExit(f"gold set not found: {gold_path}")

    queries = [
        json.loads(line)
        for line in gold_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("//")
    ]
    print(f"Evaluating {len(queries)} queries from {gold_path}\n")

    report = post(
        args.url,
        args.token,
        "/eval/retrieval",
        {
            "user_id": args.user,
            "queries": queries,
            "top_k": args.top_k,
            "baseline": args.baseline,
        },
        # Four strategies over the gold set, one of which reranks every query.
        timeout=1800,
    )

    if args.save:
        # Saved before anything is printed. A run costs real quota and
        # real minutes; losing it to a console that cannot encode a
        # character in the output would be a poor trade.
        destination = Path(args.save)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"full report written to {args.save}")

    print(report["table"])
    print()
    print(report["significance_table"])
    print("\nconfig:", json.dumps(report["config"]))

    failures = {
        s["strategy"]: s["failed_queries"]
        for s in report["strategies"]
        if s["failed_queries"]
    }
    if failures:
        # A silent failure would quietly lower a strategy's average and look
        # like a quality difference.
        print("\nqueries that errored (excluded from the averages):")
        for strategy, questions in failures.items():
            print(f"  {strategy}: {len(questions)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
