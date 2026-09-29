"""The four retrieval strategies the evaluation harness compares.

Each satisfies the same interface, so the pipeline and the eval harness can
swap between them by name without a branch anywhere else.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from app.core.interfaces import EmbeddingProvider, Reranker
from app.core.models import RetrievalStrategy, ScoredChunk
from app.observability.trace import span
from app.retrieval.bm25 import Bm25Encoder
from app.retrieval.fusion import ReciprocalRankFusion
from app.retrieval.qdrant_store import QdrantStore

logger = logging.getLogger(__name__)


class DenseRetriever:
    """Semantic search. Finds material that means the same thing in different
    words -- "why split tables to avoid duplicate data" reaching a passage on
    normalization that shares almost no vocabulary with the question."""

    name = "dense"

    def __init__(self, store: QdrantStore, embedder: EmbeddingProvider) -> None:
        self._store = store
        self._embedder = embedder

    async def search(
        self,
        query: str,
        *,
        user_id: str,
        document_ids: Sequence[str] | None = None,
        top_k: int = 50,
    ) -> list[ScoredChunk]:
        vector = await self._embedder.embed_query(query)
        return await self._store.search_dense(
            vector.values, user_id=user_id, document_ids=document_ids, top_k=top_k
        )


class SparseRetriever:
    """Keyword search. Finds exact technical terms -- "3NF", "BCNF", "O(n log
    n)" -- that a dense vector tends to blur into nearby concepts."""

    name = "bm25"

    def __init__(self, store: QdrantStore, encoder: Bm25Encoder) -> None:
        self._store = store
        self._encoder = encoder

    async def search(
        self,
        query: str,
        *,
        user_id: str,
        document_ids: Sequence[str] | None = None,
        top_k: int = 50,
    ) -> list[ScoredChunk]:
        return await self._store.search_sparse(
            self._encoder.encode_query(query),
            user_id=user_id,
            document_ids=document_ids,
            top_k=top_k,
        )


class HybridRetriever:
    """Both retrievers, fused by rank.

    Educational material is exactly the case that needs both: definitions and
    formulas are matched literally, while a student's phrasing of a question
    rarely matches the textbook's. Running them concurrently means hybrid costs
    roughly what the slower of the two costs, not their sum.
    """

    name = "hybrid"

    def __init__(
        self,
        dense: DenseRetriever,
        sparse: SparseRetriever,
        fuser: ReciprocalRankFusion | None = None,
    ) -> None:
        self._dense = dense
        self._sparse = sparse
        self._fuser = fuser or ReciprocalRankFusion()

    async def search(
        self,
        query: str,
        *,
        user_id: str,
        document_ids: Sequence[str] | None = None,
        top_k: int = 50,
        dense_top_k: int | None = None,
        sparse_top_k: int | None = None,
    ) -> list[ScoredChunk]:
        dense_results, sparse_results = await asyncio.gather(
            self._dense.search(
                query,
                user_id=user_id,
                document_ids=document_ids,
                top_k=dense_top_k or top_k,
            ),
            self._sparse.search(
                query,
                user_id=user_id,
                document_ids=document_ids,
                top_k=sparse_top_k or top_k,
            ),
        )

        logger.debug(
            "hybrid: dense=%s sparse=%s", len(dense_results), len(sparse_results)
        )
        fused = self._fuser.fuse([dense_results, sparse_results], top_k=top_k)

        dense_ids = [r.chunk.id for r in dense_results]
        sparse_ids = [r.chunk.id for r in sparse_results]
        async with span("fuse", top_k=top_k) as observed:
            # Agreement between the two retrievers is the number that explains
            # a fusion result. When they overlap barely at all, RRF is
            # interleaving two disagreeing lists rather than reinforcing a
            # consensus, and the fused ranking is worse than the better half.
            observed.output(
                dense=len(dense_ids),
                sparse=len(sparse_ids),
                overlap=len(set(dense_ids) & set(sparse_ids)),
                fused=len(fused),
                dense_only_in_top=sum(
                    1
                    for r in fused[:5]
                    if r.chunk.id in set(dense_ids) - set(sparse_ids)
                ),
            )
        return fused


class RetrievalService:
    """Entry point that resolves a strategy name to a retriever."""

    def __init__(
        self,
        dense: DenseRetriever,
        sparse: SparseRetriever,
        hybrid: HybridRetriever,
        reranker: Reranker | None = None,
        *,
        rerank_candidates: int = 25,
    ) -> None:
        self.dense = dense
        self.sparse = sparse
        self.hybrid = hybrid
        self.reranker = reranker
        # How many fused candidates the reranker sees. Retrieving wide and
        # reranking down is the whole point: a chunk that never enters this
        # pool cannot be recovered by any amount of reranking.
        self.rerank_candidates = rerank_candidates

    async def search(
        self,
        query: str,
        *,
        user_id: str,
        strategy: RetrievalStrategy = RetrievalStrategy.HYBRID,
        document_ids: Sequence[str] | None = None,
        top_k: int = 25,
        dense_top_k: int = 50,
        sparse_top_k: int = 50,
    ) -> list[ScoredChunk]:
        if strategy is RetrievalStrategy.DENSE:
            return await self.dense.search(
                query, user_id=user_id, document_ids=document_ids, top_k=top_k
            )
        if strategy is RetrievalStrategy.BM25:
            return await self.sparse.search(
                query, user_id=user_id, document_ids=document_ids, top_k=top_k
            )

        # Both hybrid modes share a retrieval stage. The reranked variant
        # pulls a deliberately wider candidate pool, since its job is to pick
        # well from many rather than to trust the fused order.
        reranker = (
            self.reranker if strategy is RetrievalStrategy.HYBRID_RERANK else None
        )
        candidates = await self.hybrid.search(
            query,
            user_id=user_id,
            document_ids=document_ids,
            top_k=max(self.rerank_candidates, top_k) if reranker else top_k,
            dense_top_k=dense_top_k,
            sparse_top_k=sparse_top_k,
        )

        if reranker is None:
            return candidates

        before = {r.chunk.id: position for position, r in enumerate(candidates)}
        async with span("rerank", candidates=len(candidates), top_n=top_k) as observed:
            reranked = await reranker.rerank(query, candidates, top_n=top_k)
            # How far the reranker actually moved things. A reranker that
            # changes nothing is costing latency for no benefit, and one that
            # rewrites the order completely is worth a second look -- neither
            # is visible from the results alone.
            shifts = [
                before[r.chunk.id] - position
                for position, r in enumerate(reranked)
                if r.chunk.id in before
            ]
            observed.output(
                returned=len(reranked),
                top_changed=bool(reranked)
                and bool(candidates)
                and reranked[0].chunk.id != candidates[0].chunk.id,
                max_promotion=max(shifts, default=0),
                mean_abs_shift=(
                    round(sum(abs(s) for s in shifts) / len(shifts), 2)
                    if shifts
                    else 0.0
                ),
                dropped=len(candidates) - len(reranked),
            )
        return reranked
