"""Builds the pipeline from configuration.

Every neural stage is selected by name here and nowhere else. That is what lets
an evaluation run describe a variant as a config diff, and what keeps the
pipeline itself free of "if provider == ..." branches.

Components are process-wide singletons: the Qdrant client holds a connection
pool and the embedding provider holds a rate limiter, and both are only correct
if there is exactly one of them.
"""

from __future__ import annotations

import logging

from qdrant_client import AsyncQdrantClient

from app.config import Settings
from app.core.interfaces import EmbeddingProvider, LLMProvider, Reranker, Tracer
from app.embedding.cache import (
    CachedEmbeddingProvider,
    EmbeddingCache,
    InMemoryEmbeddingCache,
    PostgresEmbeddingCache,
)
from app.embedding.gemini import GeminiEmbeddingProvider
from app.embedding.mock import MockEmbeddingProvider
from app.generation.chat import ChatService
from app.generation.context import ContextBuilder
from app.generation.llm import GeminiLLMProvider, MockLLMProvider
from app.observability.sinks import (
    InMemoryTracer,
    LangfuseTracer,
    MultiTracer,
    NoOpTracer,
    PostgresTracer,
)
from app.reranking.rerankers import (
    GeminiListwiseReranker,
    JinaReranker,
    NoOpReranker,
)
from app.retrieval.bm25 import Bm25Encoder
from app.retrieval.fusion import ReciprocalRankFusion
from app.retrieval.qdrant_store import QdrantStore
from app.retrieval.retrievers import (
    DenseRetriever,
    HybridRetriever,
    RetrievalService,
    SparseRetriever,
)
from app.vision.cache import InMemoryVisionCache, PostgresVisionCache, VisionCache
from app.vision.providers import (
    GeminiVisionProvider,
    MockVisionProvider,
    VisionProvider,
)

logger = logging.getLogger(__name__)


def build_embedder(
    settings: Settings, cache: EmbeddingCache | None = None
) -> EmbeddingProvider:
    """Selects the embedding model, behind the shared cache.

    `cache` replaces the Postgres cache a real provider is given; tests pass
    an in-memory one.
    """
    provider = settings.EMBEDDING_PROVIDER.lower()

    if provider == "mock":
        # Keeps the configured width, so a switch between providers does not
        # silently change the collection's vector size. Not cached: it costs
        # nothing to run, and caching would only add a database round trip.
        return MockEmbeddingProvider(dimensions=settings.EMBEDDING_DIMENSIONS)
    if provider == "gemini":
        if not settings.GEMINI_API_KEY:
            raise RuntimeError(
                "EMBEDDING_PROVIDER=gemini but GEMINI_API_KEY is empty. "
                "Set the key or switch the provider to 'mock'."
            )
        inner = GeminiEmbeddingProvider(
            settings.GEMINI_API_KEY,
            model_id=settings.EMBEDDING_MODEL,
            dimensions=settings.EMBEDDING_DIMENSIONS,
            max_rpm=settings.EMBEDDING_MAX_RPM,
            batch_size=settings.EMBEDDING_BATCH_SIZE,
        )
        return CachedEmbeddingProvider(
            inner, cache or PostgresEmbeddingCache(settings.DATABASE_URL)
        )

    raise ValueError(
        f"Unknown EMBEDDING_PROVIDER '{settings.EMBEDDING_PROVIDER}'. "
        "Available: gemini, mock"
    )


def build_reranker(settings: Settings) -> Reranker:
    """Selects the reranker.

    Defaults to noop rather than silently degrading to something weaker: a run
    labelled "hybrid_rerank" that quietly did no reranking would corrupt every
    evaluation comparison drawn from it.
    """
    provider = settings.RERANKER_PROVIDER.lower()

    if provider == "noop":
        return NoOpReranker()
    if provider == "jina":
        if not settings.JINA_API_KEY:
            raise RuntimeError(
                "RERANKER_PROVIDER=jina but JINA_API_KEY is empty. "
                "Set the key or switch the provider to 'noop'."
            )
        return JinaReranker(settings.JINA_API_KEY, max_rpm=settings.RERANKER_MAX_RPM)
    if provider == "gemini_listwise":
        if not settings.GEMINI_API_KEY:
            raise RuntimeError(
                "RERANKER_PROVIDER=gemini_listwise but GEMINI_API_KEY is empty."
            )
        return GeminiListwiseReranker(
            settings.GEMINI_API_KEY,
            model_id=settings.LLM_UTILITY_MODEL,
            max_rpm=settings.LLM_MAX_RPM,
        )

    raise ValueError(
        f"Unknown RERANKER_PROVIDER '{settings.RERANKER_PROVIDER}'. "
        "Available: noop, jina, gemini_listwise"
    )


def build_vision(settings: Settings) -> VisionProvider | None:
    """Selects the vision provider, or None when images are not read at all.

    Defaults to noop, which is exactly the behaviour before images were read
    -- so enabling it is a deliberate choice rather than a surprise on the
    quota bill.
    """
    provider = settings.VISION_PROVIDER.lower()

    if provider == "noop":
        return None
    if provider == "mock":
        return MockVisionProvider()
    if provider == "gemini":
        if not settings.GEMINI_API_KEY:
            raise RuntimeError(
                "VISION_PROVIDER=gemini but GEMINI_API_KEY is empty. "
                "Set the key or switch the provider to 'noop'."
            )
        return GeminiVisionProvider(
            settings.GEMINI_API_KEY,
            model_id=settings.VISION_MODEL,
            max_rpm=settings.VISION_MAX_RPM,
        )

    raise ValueError(
        f"Unknown VISION_PROVIDER '{settings.VISION_PROVIDER}'. "
        "Available: noop, mock, gemini"
    )


def build_vision_cache(settings: Settings) -> VisionCache:
    """The shared cache for a real model; an in-process one for the stand-in,
    whose readings mean nothing outside the process that made them."""
    if settings.VISION_PROVIDER.lower() == "gemini":
        return PostgresVisionCache(settings.DATABASE_URL)
    return InMemoryVisionCache()


def build_llm(settings: Settings, *, utility: bool = False) -> LLMProvider:
    """Selects the generation model.

    The utility model is a separate, cheaper one for query rewriting: high
    frequency, low difficulty, and keeping it apart preserves the answering
    model's quota for answering. Thinking is disabled on it -- rewriting a
    question into standalone form needs no deliberation, and the tokens are
    pure latency.
    """
    provider = settings.LLM_PROVIDER.lower()

    if provider == "mock":
        return MockLLMProvider()
    if provider == "gemini":
        if not settings.GEMINI_API_KEY:
            raise RuntimeError(
                "LLM_PROVIDER=gemini but GEMINI_API_KEY is empty. "
                "Set the key or switch the provider to 'mock'."
            )
        return GeminiLLMProvider(
            settings.GEMINI_API_KEY,
            model_id=settings.LLM_UTILITY_MODEL if utility else settings.LLM_MODEL,
            max_rpm=settings.LLM_MAX_RPM,
            thinking_budget=0 if utility else None,
        )

    raise ValueError(
        f"Unknown LLM_PROVIDER '{settings.LLM_PROVIDER}'. Available: gemini, mock"
    )


def build_tracer(settings: Settings) -> Tracer:
    """Selects where trace records go.

    Accepts a comma-separated list, because the local table and Langfuse answer
    different questions and there is no reason to pick one: `postgres,langfuse`
    writes to both.

    A misconfigured sink raises at startup rather than degrading to noop. The
    whole value of a trace is that it is there when something surprising has
    already happened -- discovering then that tracing had quietly switched
    itself off is the one failure worth being loud about.
    """
    names = [n.strip().lower() for n in settings.TRACER.split(",") if n.strip()]
    if not names or names == ["none"]:
        return NoOpTracer()

    sinks: list[Tracer] = []
    for name in names:
        if name in {"none", "noop"}:
            sinks.append(NoOpTracer())
        elif name == "memory":
            sinks.append(InMemoryTracer())
        elif name == "postgres":
            sinks.append(PostgresTracer(settings.DATABASE_URL))
        elif name == "langfuse":
            if not (settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY):
                raise RuntimeError(
                    "TRACER includes 'langfuse' but LANGFUSE_PUBLIC_KEY or "
                    "LANGFUSE_SECRET_KEY is empty."
                )
            sinks.append(
                LangfuseTracer(
                    settings.LANGFUSE_PUBLIC_KEY,
                    settings.LANGFUSE_SECRET_KEY,
                    settings.LANGFUSE_HOST,
                )
            )
        else:
            raise ValueError(
                f"Unknown TRACER '{name}'. "
                "Available: postgres, langfuse, memory, none"
            )

    return sinks[0] if len(sinks) == 1 else MultiTracer(sinks)


def recheck_threshold(settings: Settings, reranker: Reranker) -> float | None:
    """The refusal-recheck threshold, or None when the scores cannot support it.

    A rank-derived score gives the top passage 1.0 however irrelevant it is, so
    against one every refusal would look like a contradiction.
    """
    if not reranker.calibrated:
        return None
    return settings.CHAT_RECHECK_MIN_RELEVANCE


def off_topic_thresholds(
    settings: Settings, reranker: Reranker
) -> tuple[float, float] | None:
    """(similarity, relevance) bounds for skipping an off-topic question.

    Needs a calibrated reranker too: with rank-derived scores the relevance
    half of the test means nothing, and similarity alone overlaps between
    terse real questions and small talk.
    """
    if not reranker.calibrated:
        return None
    return (
        settings.CHAT_OFF_TOPIC_MAX_SIMILARITY,
        settings.CHAT_OFF_TOPIC_MAX_RELEVANCE,
    )


class Container:
    """Holds the wired pipeline and owns the lifetimes of its clients."""

    def __init__(
        self,
        settings: Settings,
        *,
        tracer: Tracer | None = None,
        embedding_cache: EmbeddingCache | None = None,
        vision_cache: VisionCache | None = None,
    ) -> None:
        self.settings = settings
        self.tracer = tracer or build_tracer(settings)
        self.embedder = build_embedder(settings, embedding_cache)
        self.sparse_encoder = Bm25Encoder()
        self.reranker = build_reranker(settings)
        self.vision = build_vision(settings)
        self.vision_cache = vision_cache or build_vision_cache(settings)
        self.llm = build_llm(settings)
        self.utility_llm = build_llm(settings, utility=True)
        self.context_builder = ContextBuilder(
            max_tokens=settings.CONTEXT_MAX_TOKENS,
            max_chunks=settings.CONTEXT_TOP_N,
        )
        self.overview_context_builder = ContextBuilder(
            max_tokens=settings.CHAT_OVERVIEW_MAX_TOKENS,
            max_chunks=settings.CHAT_OVERVIEW_MAX_CHUNKS,
        )

        self.qdrant = AsyncQdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY or None,
            timeout=60,
        )
        self.store = QdrantStore(
            self.qdrant,
            settings.QDRANT_COLLECTION,
            settings.EMBEDDING_DIMENSIONS,
        )

        dense = DenseRetriever(self.store, self.embedder)
        sparse = SparseRetriever(self.store, self.sparse_encoder)
        self.retrieval = RetrievalService(
            dense=dense,
            sparse=sparse,
            hybrid=HybridRetriever(
                dense, sparse, ReciprocalRankFusion(k=settings.RRF_K)
            ),
            reranker=self.reranker,
            rerank_candidates=settings.RERANK_TOP_N,
            dense_top_k=settings.RETRIEVAL_DENSE_TOP_K,
            sparse_top_k=settings.RETRIEVAL_SPARSE_TOP_K,
        )
        self.chat = ChatService(
            self.retrieval,
            self.context_builder,
            self.llm,
            utility_llm=self.utility_llm,
            history_turns=settings.CHAT_HISTORY_TURNS,
            history_tokens=settings.CHAT_HISTORY_MAX_TOKENS,
            recheck_min_relevance=recheck_threshold(settings, self.reranker),
            off_topic_below=off_topic_thresholds(settings, self.reranker),
            corpus=self.store,
            overview_context_builder=self.overview_context_builder,
        )

    async def startup(self) -> None:
        created = await self.store.ensure_collection()
        logger.info(
            "qdrant collection %s %s (dims=%s)",
            self.store.collection,
            "created" if created else "ready",
            self.settings.EMBEDDING_DIMENSIONS,
        )

    async def shutdown(self) -> None:
        # Flushed first: the last spans of a run are the ones explaining why it
        # is shutting down.
        await self.tracer.close()
        if isinstance(self.embedder, CachedEmbeddingProvider):
            await self.embedder.close()
        await self.vision_cache.close()
        await self.qdrant.close()


_container: Container | None = None


def get_container() -> Container:
    if _container is None:
        raise RuntimeError("Container not initialised; app startup has not run")
    return _container


def set_container(container: Container | None) -> None:
    global _container
    _container = container


def build_test_container(settings: Settings) -> Container:
    """A container that needs neither Postgres nor a trace table.

    The caches and the trace sink are held in memory: tests must not depend on
    a database being reachable, must not fill the shared caches with readings
    of throwaway fixtures, and read what was traced back from memory.
    """
    return Container(
        settings,
        tracer=InMemoryTracer(),
        embedding_cache=InMemoryEmbeddingCache(),
        vision_cache=InMemoryVisionCache(),
    )
