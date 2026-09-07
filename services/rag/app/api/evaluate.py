"""Evaluation endpoint.

Runs a gold set through every retrieval strategy and returns the comparison.
Exposed as an API rather than only a script so the same code path serves a
one-off check during tuning and a recorded run later.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import InternalAuth, SettingsDep
from app.container import get_container
from app.core.models import RetrievalStrategy
from app.eval.gold_set import SyntheticGoldSetBuilder
from app.eval.harness import (
    DEFAULT_STRATEGIES,
    EvaluationHarness,
    GoldQuery,
    load_gold_set,
    save_gold_set,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["evaluation"], dependencies=[InternalAuth])


class GoldQueryInput(BaseModel):
    question: str
    relevant_chunk_ids: list[str]
    graded: dict[str, float] | None = None
    document_ids: list[str] | None = None
    note: str = ""


class EvaluateRequest(BaseModel):
    user_id: str
    # Supply questions inline, or name a gold set file on disk.
    queries: list[GoldQueryInput] | None = None
    gold_set_path: str | None = None
    strategies: list[RetrievalStrategy] | None = None
    top_k: Annotated[int, Field(ge=1, le=100)] = 10
    k_values: list[int] = Field(default_factory=lambda: [1, 3, 5, 10])


class EvaluateResponse(BaseModel):
    query_count: int
    k_values: list[int]
    config: dict[str, Any]
    strategies: list[dict[str, Any]]
    table: str


def _resolve_gold(request: EvaluateRequest) -> list[GoldQuery]:
    if request.queries:
        return [
            GoldQuery(
                question=q.question,
                relevant_chunk_ids=set(q.relevant_chunk_ids),
                graded=q.graded or {},
                document_ids=q.document_ids or [],
                note=q.note,
            )
            for q in request.queries
        ]

    if request.gold_set_path:
        path = Path(request.gold_set_path)
        if not path.is_file():
            raise HTTPException(404, f"Gold set not found at {path}")
        try:
            return load_gold_set(path)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    raise HTTPException(400, "Supply either 'queries' or 'gold_set_path'")


@router.post("/eval/retrieval", response_model=EvaluateResponse)
async def evaluate_retrieval(
    request: EvaluateRequest, settings: SettingsDep
) -> EvaluateResponse:
    gold = _resolve_gold(request)
    container = get_container()

    report = await EvaluationHarness(container.retrieval).run(
        gold,
        user_id=request.user_id,
        strategies=request.strategies or list(DEFAULT_STRATEGIES),
        top_k=request.top_k,
        k_values=request.k_values,
        # Recorded with the result: a metric without the configuration that
        # produced it cannot be compared against anything later.
        config={
            "embedding_model": settings.EMBEDDING_MODEL,
            "dimensions": settings.EMBEDDING_DIMENSIONS,
            "reranker": settings.RERANKER_PROVIDER,
            "rrf_k": settings.RRF_K,
            "rerank_candidates": settings.RERANK_TOP_N,
            "chunk_target_tokens": settings.CHUNK_TARGET_TOKENS,
        },
    )

    payload = report.to_dict()
    return EvaluateResponse(**payload, table=report.to_table())


class GenerateGoldRequest(BaseModel):
    user_id: str
    document_ids: list[str] | None = None
    limit: Annotated[int, Field(ge=1, le=200)] = 25
    # Where to write the generated set, relative to the service directory.
    output_path: str | None = None


@router.post("/eval/gold-set")
async def generate_gold_set(
    request: GenerateGoldRequest, settings: SettingsDep
) -> dict[str, Any]:
    """Generates a synthetic gold set from indexed chunks.

    Useful as a starting point, but the questions are written *from* the
    passages, which flatters keyword retrieval. Sound for comparing strategies
    against each other on identical data; not an absolute quality score.
    """
    if not settings.GEMINI_API_KEY:
        raise HTTPException(400, "GEMINI_API_KEY is required to generate a gold set")

    container = get_container()
    chunks = await container.store.sample_chunks(
        user_id=request.user_id,
        document_ids=request.document_ids,
        limit=request.limit,
    )
    if not chunks:
        raise HTTPException(404, "No indexed chunks found for that user")

    builder = SyntheticGoldSetBuilder(
        settings.GEMINI_API_KEY,
        model_id=settings.LLM_MODEL,
        max_rpm=settings.LLM_MAX_RPM,
    )
    gold = await builder.build(chunks)

    written: str | None = None
    if request.output_path:
        path = Path(request.output_path)
        save_gold_set(gold, path)
        written = str(path)

    return {
        "generated": len(gold),
        "from_chunks": len(chunks),
        "written_to": written,
        "queries": [g.to_dict() for g in gold],
    }
