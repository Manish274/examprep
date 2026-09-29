"""Liveness and readiness endpoints."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter

from app.api.deps import SettingsDep
from app.container import get_container

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
        # Reported rather than assumed. A sink that is dropping or failing
        # every record looks exactly like a healthy one from the outside,
        # which is the failure mode observability can least afford.
        "tracing": _tracer_stats(),
    }


def _tracer_stats() -> dict[str, Any]:
    try:
        tracer = get_container().tracer
    except RuntimeError:
        return {"sink": "unavailable"}
    return {
        "sink": getattr(tracer, "name", "unknown"),
        **getattr(tracer, "stats", dict)(),
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
            "provider": settings.EMBEDDING_PROVIDER,
            "model": settings.EMBEDDING_MODEL,
            "dimensions": settings.EMBEDDING_DIMENSIONS,
        },
        "generation": {
            "provider": settings.LLM_PROVIDER,
            "model": settings.LLM_MODEL,
            "utility_model": settings.LLM_UTILITY_MODEL,
        },
        "reranker": {"provider": settings.RERANKER_PROVIDER},
        "vision": {
            "provider": settings.VISION_PROVIDER,
            "model": settings.VISION_MODEL,
            "min_pixels": settings.VISION_MIN_PIXELS,
            "max_images": settings.VISION_MAX_IMAGES,
        },
        "chat": {
            "history_turns": settings.CHAT_HISTORY_TURNS,
            "history_max_tokens": settings.CHAT_HISTORY_MAX_TOKENS,
            "recheck_min_relevance": settings.CHAT_RECHECK_MIN_RELEVANCE,
            "off_topic_max_similarity": settings.CHAT_OFF_TOPIC_MAX_SIMILARITY,
            "off_topic_max_relevance": settings.CHAT_OFF_TOPIC_MAX_RELEVANCE,
            "overview_max_chunks": settings.CHAT_OVERVIEW_MAX_CHUNKS,
            "overview_max_tokens": settings.CHAT_OVERVIEW_MAX_TOKENS,
        },
    }
