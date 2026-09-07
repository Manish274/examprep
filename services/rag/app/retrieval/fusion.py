"""Reciprocal Rank Fusion.

Dense similarity and BM25 produce scores on incompatible scales -- cosine sits
in [-1, 1] while BM25 is unbounded and depends on corpus statistics. Normalising
them against each other requires assumptions that break as the corpus changes.

RRF sidesteps the problem by discarding the scores and using only the ranks:

    score(d) = sum over retrievers of  1 / (k + rank(d))

A document ranked first by either retriever scores well; one ranked highly by
both scores better still. k damps the contribution of top ranks so a single
retriever's confident-but-wrong first place cannot dominate the result. k=60 is
the value from the original paper and the usual default.

Implemented here rather than delegated to Qdrant's built-in fusion so the
per-retriever ranks survive into ScoredChunk, which is what lets the evaluation
harness attribute a win to fusion specifically.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.core.models import ScoredChunk
from app.core.registry import fusers

DEFAULT_K = 60


class ReciprocalRankFusion:
    name = "rrf"

    def __init__(self, k: int = DEFAULT_K) -> None:
        if k <= 0:
            raise ValueError("RRF k must be positive")
        self.k = k

    def fuse(
        self, rankings: Sequence[Sequence[ScoredChunk]], *, top_k: int
    ) -> list[ScoredChunk]:
        merged: dict[str, ScoredChunk] = {}
        scores: dict[str, float] = {}

        for ranking in rankings:
            for rank, scored in enumerate(ranking, start=1):
                chunk_id = scored.chunk.id
                scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (self.k + rank)

                existing = merged.get(chunk_id)
                if existing is None:
                    # Copy so the per-retriever result lists stay untouched and
                    # can still be inspected by the eval harness.
                    merged[chunk_id] = scored.model_copy(deep=True)
                    existing = merged[chunk_id]

                # Carry across whichever per-stage scores this retriever knows,
                # so a fused result records how each side rated it.
                if scored.dense_score is not None:
                    existing.dense_score = scored.dense_score
                    existing.dense_rank = rank
                if scored.sparse_score is not None:
                    existing.sparse_score = scored.sparse_score
                    existing.sparse_rank = rank

        for chunk_id, scored in merged.items():
            scored.rrf_score = scores[chunk_id]
            scored.score = scores[chunk_id]

        ordered = sorted(
            merged.values(),
            # Ties are broken deterministically, or evaluation results stop
            # being reproducible between runs. Chunk index alone is not enough:
            # chunks from different documents share indices, so the id settles
            # the remaining case.
            key=lambda s: (
                -(s.rrf_score or 0.0),
                s.chunk.metadata.chunk_index,
                s.chunk.id,
            ),
        )
        return ordered[:top_k]


class WeightedScoreFusion:
    """Score-based alternative, kept as a comparison point for evaluation.

    Requires min-max normalising each ranking first, which is exactly the
    fragility RRF avoids -- included so that claim can be measured rather than
    asserted.
    """

    name = "weighted"

    def __init__(self, weights: Sequence[float] | None = None) -> None:
        self.weights = list(weights) if weights else None

    @staticmethod
    def _normalized(ranking: Sequence[ScoredChunk]) -> dict[str, float]:
        if not ranking:
            return {}
        values = [s.score for s in ranking]
        low, high = min(values), max(values)
        spread = high - low
        if spread == 0:
            return {s.chunk.id: 1.0 for s in ranking}
        return {s.chunk.id: (s.score - low) / spread for s in ranking}

    def fuse(
        self, rankings: Sequence[Sequence[ScoredChunk]], *, top_k: int
    ) -> list[ScoredChunk]:
        weights = self.weights or [1.0] * len(rankings)
        merged: dict[str, ScoredChunk] = {}
        totals: dict[str, float] = {}

        for ranking, weight in zip(rankings, weights, strict=False):
            normalized = self._normalized(ranking)
            for scored in ranking:
                chunk_id = scored.chunk.id
                totals[chunk_id] = (
                    totals.get(chunk_id, 0.0) + weight * normalized[chunk_id]
                )
                merged.setdefault(chunk_id, scored.model_copy(deep=True))

        for chunk_id, scored in merged.items():
            scored.score = totals[chunk_id]

        ordered = sorted(
            merged.values(),
            key=lambda s: (-s.score, s.chunk.metadata.chunk_index, s.chunk.id),
        )
        return ordered[:top_k]


@fusers.register("rrf")
def _create_rrf(k: int = DEFAULT_K) -> ReciprocalRankFusion:
    return ReciprocalRankFusion(k=k)


@fusers.register("weighted")
def _create_weighted(weights: Sequence[float] | None = None) -> WeightedScoreFusion:
    return WeightedScoreFusion(weights=weights)
