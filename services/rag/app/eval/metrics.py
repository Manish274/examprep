"""Retrieval metrics.

Four measures, because each answers a different question and any one alone is
misleading:

- **Recall@K**  did the right material make it into the candidate set at all?
  This is the ceiling on everything downstream: a chunk retrieval misses cannot
  be reranked into place, and the LLM cannot cite it.
- **Precision@K**  how much of what we returned was actually relevant? Directly
  a measure of noise, which costs context budget and dilutes the answer.
- **MRR**  how far down is the first correct result? A system that always puts
  one right answer at rank 1 is better than one that puts three at ranks 8-10.
- **NDCG@K**  position-weighted, and the only one of the four that distinguishes
  "relevant at rank 1" from "relevant at rank 10" when both are retrieved.

All take a ranked list of chunk ids and a set of ids judged relevant.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def recall_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    """Fraction of relevant chunks appearing in the top k."""
    if not relevant:
        # No ground truth means nothing to recall. Returning 0 would drag an
        # average down for a question that simply has no answer in the corpus.
        return 0.0
    found = len(set(retrieved[:k]) & relevant)
    return found / len(relevant)


def precision_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    """Fraction of the top k that is relevant.

    Divided by k rather than by the number returned, so a system that returns
    three results when asked for ten is not rewarded for its reticence.
    """
    if k <= 0:
        return 0.0
    return len(set(retrieved[:k]) & relevant) / k


def reciprocal_rank(retrieved: Sequence[str], relevant: set[str]) -> float:
    """1/rank of the first relevant result, or 0 if none was retrieved."""
    for position, chunk_id in enumerate(retrieved, start=1):
        if chunk_id in relevant:
            return 1.0 / position
    return 0.0


def dcg_at_k(gains: Sequence[float], k: int) -> float:
    # log2(i+1) with i starting at 1, so the first position is undiscounted.
    return sum(
        gain / math.log2(position + 1)
        for position, gain in enumerate(gains[:k], start=1)
    )


def ndcg_at_k(
    retrieved: Sequence[str],
    relevant: set[str],
    k: int,
    *,
    graded: dict[str, float] | None = None,
) -> float:
    """Normalised discounted cumulative gain.

    `graded` allows relevance beyond binary -- a chunk that fully answers the
    question can be worth more than one that merely mentions the topic. Binary
    judgements are the common case and the default.
    """
    if not relevant:
        return 0.0

    def gain(chunk_id: str) -> float:
        if graded is not None:
            return graded.get(chunk_id, 0.0)
        return 1.0 if chunk_id in relevant else 0.0

    actual = dcg_at_k([gain(c) for c in retrieved], k)

    # The ideal ranking puts every relevant chunk first, best gain leading.
    ideal_gains = sorted(
        (graded.get(c, 0.0) if graded is not None else 1.0 for c in relevant),
        reverse=True,
    )
    ideal = dcg_at_k(ideal_gains, k)

    return actual / ideal if ideal > 0 else 0.0


def hit_rate(retrieved: Sequence[str], relevant: set[str], k: int) -> float:
    """1 if anything relevant is in the top k, else 0.

    Blunter than recall, and the right measure when a single good chunk is
    enough to answer -- which for a student's question it usually is.
    """
    return 1.0 if set(retrieved[:k]) & relevant else 0.0


def evaluate_query(
    retrieved: Sequence[str],
    relevant: set[str],
    *,
    k_values: Sequence[int] = (1, 3, 5, 10),
    graded: dict[str, float] | None = None,
) -> dict[str, float]:
    """Every metric for one query, at each requested cutoff."""
    scores: dict[str, float] = {"mrr": reciprocal_rank(retrieved, relevant)}
    for k in k_values:
        scores[f"recall@{k}"] = recall_at_k(retrieved, relevant, k)
        scores[f"precision@{k}"] = precision_at_k(retrieved, relevant, k)
        scores[f"ndcg@{k}"] = ndcg_at_k(retrieved, relevant, k, graded=graded)
        scores[f"hit@{k}"] = hit_rate(retrieved, relevant, k)
    return scores


def aggregate(per_query: Sequence[dict[str, float]]) -> dict[str, float]:
    """Macro-average across queries.

    Macro rather than micro: every question counts equally, so one question
    with many relevant chunks cannot dominate the result.
    """
    if not per_query:
        return {}
    keys = per_query[0].keys()
    return {
        key: sum(scores.get(key, 0.0) for scores in per_query) / len(per_query)
        for key in keys
    }
