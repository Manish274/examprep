"""Liveness and readiness endpoints."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter

from app.api.deps import SettingsDep

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(settings: SettingsDep) -> dict[str, Any]:
    """Reports reachability of Qdrant and which providers are actually wired.

    Deliberately unauthenticated: the Node readiness probe calls this before it
    has anything else to say.
    """
    qdrant_ok = False
    qdrant_detail: str | None = None
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{settings.QDRANT_URL}/readyz")
            qdrant_ok = response.status_code == 200
            if not qdrant_ok:
                qdrant_detail = f"status {response.status_code}"
    except Exception as exc:
        qdrant_detail = str(exc)

    return {
        "status": "ok" if qdrant_ok else "degraded",
        "service": "rag",
        "qdrant": qdrant_ok,
        "qdrant_detail": qdrant_detail,
        "providers": {
            "embedding": settings.EMBEDDING_PROVIDER,
            "llm": settings.LLM_PROVIDER,
            "reranker": settings.RERANKER_PROVIDER,
            "tracer": settings.TRACER,
        },
        "mock_mode": settings.uses_mock_providers,
        "gemini_key_present": settings.has_gemini_key,
    }


@router.get("/health/config")
async def config_summary(settings: SettingsDep) -> dict[str, Any]:
    """The retrieval knobs currently in force.

    Useful when an eval result looks surprising and the first question is
    always which configuration actually produced it.
    """
    return {
        "chunking": {
            "target_tokens": settings.CHUNK_TARGET_TOKENS,
            "overlap_tokens": settings.CHUNK_OVERLAP_TOKENS,
            "min_tokens": settings.CHUNK_MIN_TOKENS,
        },
        "retrieval": {
            "dense_top_k": settings.RETRIEVAL_DENSE_TOP_K,
            "sparse_top_k": settings.RETRIEVAL_SPARSE_TOP_K,
            "rrf_k": settings.RRF_K,
            "rerank_top_n": settings.RERANK_TOP_N,
            "context_top_n": settings.CONTEXT_TOP_N,
            "context_max_tokens": settings.CONTEXT_MAX_TOKENS,
        },
        "embedding": {
            "model": settings.EMBEDDING_MODEL,
            "dimensions": settings.EMBEDDING_DIMENSIONS,
        },
    }
