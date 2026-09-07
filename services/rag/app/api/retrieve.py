"""Retrieval endpoint.

Exposes all four strategies rather than only the one in production use, so the
evaluation harness and manual inspection go through exactly the same code path
the student's questions will.

Per-stage scores are returned alongside the final ordering. Seeing that a chunk
was ranked 1st by BM25 and 30th by dense retrieval explains a result in a way a
single fused number never can.
"""

from __future__ import annotations

import logging
import time
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import CorrelationId, InternalAuth, SettingsDep
from app.container import get_container
from app.core.models import RetrievalStrategy, ScoredChunk

logger = logging.getLogger(__name__)

router = APIRouter(tags=["retrieval"], dependencies=[InternalAuth])


class RetrieveRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    user_id: str
    # Omit to search everything the user owns; supply to scope to one document.
    document_ids: list[str] | None = None
    strategy: RetrievalStrategy = RetrievalStrategy.HYBRID
    top_k: Annotated[int, Field(ge=1, le=100)] = 25
    dense_top_k: Annotated[int, Field(ge=1, le=200)] | None = None
    sparse_top_k: Annotated[int, Field(ge=1, le=200)] | None = None


class RetrievedChunkResponse(BaseModel):
    chunk_id: str
    text: str
    token_count: int
    score: float
    dense_score: float | None
    sparse_score: float | None
    rrf_score: float | None
    # Present only for hybrid_rerank. Without it there is no way to tell a
    # reranked result from one where the reranker silently fell back.
    rerank_score: float | None
    dense_rank: int | None
    sparse_rank: int | None

    document_id: str
    document_name: str
    chunk_index: int
    page_number: int | None
    slide_number: int | None
    section: str | None
    heading: str | None
    heading_path: list[str]

    @classmethod
    def from_scored(cls, scored: ScoredChunk) -> RetrievedChunkResponse:
        meta = scored.chunk.metadata
        return cls(
            chunk_id=scored.chunk.id,
            text=scored.chunk.text,
            token_count=scored.chunk.token_count,
            score=scored.score,
            dense_score=scored.dense_score,
            sparse_score=scored.sparse_score,
            rrf_score=scored.rrf_score,
            rerank_score=scored.rerank_score,
            dense_rank=scored.dense_rank,
            sparse_rank=scored.sparse_rank,
            document_id=meta.document_id,
            document_name=meta.document_name,
            chunk_index=meta.chunk_index,
            page_number=meta.page_number,
            slide_number=meta.slide_number,
            section=meta.section,
            heading=meta.heading,
            heading_path=meta.heading_path,
        )


class RetrieveResponse(BaseModel):
    query: str
    strategy: RetrievalStrategy
    count: int
    took_ms: int
    results: list[RetrievedChunkResponse]


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(
    request: RetrieveRequest,
    settings: SettingsDep,
    correlation_id: CorrelationId = None,
) -> RetrieveResponse:
    container = get_container()

    started = time.perf_counter()
    try:
        results = await container.retrieval.search(
            request.query,
            user_id=request.user_id,
            strategy=request.strategy,
            document_ids=request.document_ids,
            top_k=request.top_k,
            dense_top_k=request.dense_top_k or settings.RETRIEVAL_DENSE_TOP_K,
            sparse_top_k=request.sparse_top_k or settings.RETRIEVAL_SPARSE_TOP_K,
        )
    except Exception as exc:
        logger.exception("retrieval failed (correlation_id=%s)", correlation_id)
        raise HTTPException(status_code=502, detail=f"Retrieval failed: {exc}") from exc

    took_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "retrieved %s chunks for %r via %s in %sms",
        len(results),
        request.query[:60],
        request.strategy.value,
        took_ms,
    )

    return RetrieveResponse(
        query=request.query,
        strategy=request.strategy,
        count=len(results),
        took_ms=took_ms,
        results=[RetrievedChunkResponse.from_scored(r) for r in results],
    )


@router.get("/retrieve/stats")
async def stats(user_id: str | None = None) -> dict[str, Any]:
    """How much is actually indexed.

    The first question when retrieval returns nothing is whether the corpus is
    empty or the query is bad, and this separates the two.
    """
    container = get_container()
    return {
        "collection": container.store.collection,
        "dimensions": container.settings.EMBEDDING_DIMENSIONS,
        "embedding_model": container.settings.EMBEDDING_MODEL,
        "total_chunks": await container.store.count(),
        **(
            {"user_chunks": await container.store.count(user_id=user_id)}
            if user_id
            else {}
        ),
    }
