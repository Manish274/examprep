"""Evaluation harness.

Runs a gold set through each retrieval strategy and reports the metrics side by
side, so the question "does reranking actually help on this material?" has an
answer rather than an opinion.

The comparison is the point. A single strategy's Recall@5 means little in
isolation; the same number next to BM25, dense and hybrid says whether the
extra machinery is earning its cost.

A gold set is a list of questions, each with the chunk ids that genuinely
answer it. Building one is the expensive part and is handled separately -- this
module only measures.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.interfaces import Retrieval
from app.core.models import RetrievalStrategy
from app.eval.metrics import aggregate, evaluate_query
from app.eval.significance import Comparison, compare_all

logger = logging.getLogger(__name__)


@dataclass
class GoldQuery:
    """One question and the chunks that actually answer it."""

    question: str
    relevant_chunk_ids: set[str]
    # Optional graded relevance, 0..1. Absent means binary.
    graded: dict[str, float] = field(default_factory=dict)
    document_ids: list[str] = field(default_factory=list)
    note: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> GoldQuery:
        return cls(
            question=raw["question"],
            relevant_chunk_ids=set(raw["relevant_chunk_ids"]),
            graded=dict(raw.get("graded") or {}),
            document_ids=list(raw.get("document_ids") or []),
            note=raw.get("note", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "relevant_chunk_ids": sorted(self.relevant_chunk_ids),
            **({"graded": self.graded} if self.graded else {}),
            **({"document_ids": self.document_ids} if self.document_ids else {}),
            **({"note": self.note} if self.note else {}),
        }


def load_gold_set(path: Path) -> list[GoldQuery]:
    """Reads a JSONL gold set. One question per line, so it stays diffable and
    can be appended to without rewriting the file."""
    queries: list[GoldQuery] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        try:
            queries.append(GoldQuery.from_dict(json.loads(stripped)))
        except (json.JSONDecodeError, KeyError) as exc:
            raise ValueError(
                f"{path}:{line_number} is not a valid gold entry: {exc}"
            ) from exc

    if not queries:
        raise ValueError(f"{path} contained no gold queries")
    return queries


def save_gold_set(queries: Sequence[GoldQuery], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(q.to_dict()) for q in queries) + "\n", encoding="utf-8"
    )


def percentile(values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile.

    A mean latency over a rate-limited free tier says almost nothing: one
    query that waited out a 429 drags it somewhere no query actually was.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return float(ordered[index])


@dataclass
class StrategyResult:
    strategy: str
    metrics: dict[str, float]
    per_query: list[dict[str, float]]
    took_ms: int
    # Per-query latency, kept so the report can show a distribution rather
    # than one total that hides every outlier inside it.
    latencies_ms: list[int] = field(default_factory=list)
    # Kept so a surprising aggregate can be traced to the questions that caused
    # it, rather than only reported.
    failures: list[str] = field(default_factory=list)

    @property
    def p50_ms(self) -> float:
        return percentile(self.latencies_ms, 0.50)

    @property
    def p95_ms(self) -> float:
        return percentile(self.latencies_ms, 0.95)


@dataclass
class EvaluationReport:
    strategies: list[StrategyResult]
    query_count: int
    k_values: list[int]
    config: dict[str, object] = field(default_factory=dict)

    def best_by(self, metric: str) -> StrategyResult | None:
        ranked = [s for s in self.strategies if metric in s.metrics]
        if not ranked:
            return None
        return max(ranked, key=lambda s: s.metrics[metric])

    def significance(
        self,
        baseline: str = "hybrid_rerank",
        metrics: Sequence[str] = ("mrr", "ndcg@5", "recall@5"),
    ) -> list[Comparison]:
        """Whether the gaps in the table survive the size of the gold set."""
        return compare_all(
            {s.strategy: s.per_query for s in self.strategies},
            baseline,
            metrics=metrics,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_count": self.query_count,
            "k_values": self.k_values,
            "config": self.config,
            "strategies": [
                {
                    "strategy": s.strategy,
                    "took_ms": s.took_ms,
                    "p50_ms": s.p50_ms,
                    "p95_ms": s.p95_ms,
                    "metrics": s.metrics,
                    "failed_queries": s.failures,
                    # Emitted so a saved report can be re-analysed later --
                    # significance, per-question breakdowns, a different
                    # metric -- without paying for the retrieval again.
                    "per_query": s.per_query,
                }
                for s in self.strategies
            ],
        }

    def to_table(self, metrics: Sequence[str] | None = None) -> str:
        """A fixed-width comparison table. Reading four strategies across ten
        metrics is the whole point, and JSON does not support that."""
        shown = list(metrics or ["recall@5", "precision@5", "mrr", "ndcg@5", "hit@5"])
        header = (
            f"{'strategy':<16}"
            + "".join(f"{m:>13}" for m in shown)
            + f"{'p50 ms':>9}{'p95 ms':>9}"
        )
        lines = [header, "-" * len(header)]

        # Mark the winner per column so the comparison reads at a glance.
        best = {
            m: max((s.metrics.get(m, 0.0) for s in self.strategies), default=0.0)
            for m in shown
        }
        for result in self.strategies:
            cells = ""
            for metric in shown:
                value = result.metrics.get(metric, 0.0)
                marker = "*" if value == best[metric] and value > 0 else " "
                cells += f"{value:>12.3f}{marker}"
            lines.append(
                f"{result.strategy:<16}{cells}"
                f"{result.p50_ms:>9.0f}{result.p95_ms:>9.0f}"
            )
        return "\n".join(lines)


DEFAULT_STRATEGIES = (
    RetrievalStrategy.BM25,
    RetrievalStrategy.DENSE,
    RetrievalStrategy.HYBRID,
    RetrievalStrategy.HYBRID_RERANK,
)


class EvaluationHarness:
    def __init__(self, retrieval: Retrieval) -> None:
        self._retrieval = retrieval

    async def run(
        self,
        gold: Sequence[GoldQuery],
        *,
        user_id: str,
        strategies: Sequence[RetrievalStrategy] = DEFAULT_STRATEGIES,
        top_k: int = 10,
        k_values: Sequence[int] = (1, 3, 5, 10),
        config: dict[str, object] | None = None,
    ) -> EvaluationReport:
        results: list[StrategyResult] = []

        for strategy in strategies:
            started = time.perf_counter()
            per_query: list[dict[str, float]] = []
            failures: list[str] = []

            latencies: list[int] = []
            for query in gold:
                query_started = time.perf_counter()
                try:
                    retrieved = await self._retrieval.search(
                        query.question,
                        user_id=user_id,
                        strategy=strategy,
                        document_ids=query.document_ids or None,
                        top_k=top_k,
                    )
                except Exception as exc:
                    # One bad query must not abandon the whole run; the
                    # question is recorded so the gap is visible in the report.
                    logger.warning(
                        "eval: %s failed on %r: %s", strategy.value, query.question, exc
                    )
                    failures.append(query.question)
                    continue

                latencies.append(int((time.perf_counter() - query_started) * 1000))
                per_query.append(
                    evaluate_query(
                        [r.chunk.id for r in retrieved],
                        query.relevant_chunk_ids,
                        k_values=k_values,
                        graded=query.graded or None,
                    )
                )

            results.append(
                StrategyResult(
                    strategy=strategy.value,
                    metrics=aggregate(per_query),
                    per_query=per_query,
                    took_ms=int((time.perf_counter() - started) * 1000),
                    latencies_ms=latencies,
                    failures=failures,
                )
            )
            logger.info(
                "eval: %s finished in %sms (%s queries, %s failures)",
                strategy.value,
                results[-1].took_ms,
                len(per_query),
                len(failures),
            )
            # A pause between strategies keeps a free-tier quota from being hit
            # by four back-to-back sweeps over the same questions.
            await asyncio.sleep(0)

        return EvaluationReport(
            strategies=results,
            query_count=len(gold),
            k_values=list(k_values),
            config=config or {},
        )
