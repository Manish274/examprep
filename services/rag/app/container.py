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
from app.embedding.cache import (
    CachedEmbeddingProvider,
    InMemoryEmbeddingCache,
    PostgresEmbeddingCache,
)
from app.embedding.gemini import GeminiEmbeddingProvider
from app.embedding.mock import MockEmbeddingProvider
from app.retrieval.bm25 import Bm25Encoder
from app.retrieval.fusion import ReciprocalRankFusion
from app.retrieval.qdrant_store import QdrantStore
from app.retrieval.retrievers import (
    DenseRetriever,
    HybridRetriever,
    RetrievalService,
    SparseRetriever,
)

logger = logging.getLogger(__name__)


def build_embedder(settings: Settings) -> object:
    provider = settings.EMBEDDING_PROVIDER.lower()

    if provider == "gemini":
        if not settings.GEMINI_API_KEY:
            raise RuntimeError(
                "EMBEDDING_PROVIDER=gemini but GEMINI_API_KEY is empty. "
                "Set the key or switch the provider to 'mock'."
            )
        inner: object = GeminiEmbeddingProvider(
            settings.GEMINI_API_KEY,
            model_id=settings.EMBEDDING_MODEL,
            dimensions=settings.EMBEDDING_DIMENSIONS,
            max_rpm=settings.EMBEDDING_MAX_RPM,
            batch_size=settings.EMBEDDING_BATCH_SIZE,
        )
    elif provider == "mock":
        # The mock keeps the configured width so a switch between providers
        # does not silently change the collection's vector size.
        inner = MockEmbeddingProvider(dimensions=settings.EMBEDDING_DIMENSIONS)
    else:
        raise ValueError(
            f"Unknown EMBEDDING_PROVIDER '{settings.EMBEDDING_PROVIDER}'. "
            "Available: gemini, mock"
        )

    # The mock costs nothing to run, so caching it would only add a database
    # round trip and make cache-hit assertions in tests meaningless.
    if provider == "mock":
        return inner

    return CachedEmbeddingProvider(inner, PostgresEmbeddingCache(settings.DATABASE_URL))


class Container:
    """Holds the wired pipeline and owns the lifetimes of its clients."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.embedder = build_embedder(settings)
        self.sparse_encoder = Bm25Encoder()

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
        cache = getattr(self.embedder, "_cache", None)
        if isinstance(cache, PostgresEmbeddingCache):
            await cache.close()
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
    """A container with the embedding cache held in memory.

    Tests must not depend on Postgres being reachable, and must not pollute the
    shared cache with vectors from throwaway fixtures.
    """
    container = Container(settings)
    if isinstance(container.embedder, CachedEmbeddingProvider):
        container.embedder = CachedEmbeddingProvider(
            container.embedder._inner, InMemoryEmbeddingCache()
        )
        dense = DenseRetriever(container.store, container.embedder)
        sparse = SparseRetriever(container.store, container.sparse_encoder)
        container.retrieval = RetrievalService(
            dense=dense,
            sparse=sparse,
            hybrid=HybridRetriever(
                dense, sparse, ReciprocalRankFusion(k=settings.RRF_K)
            ),
        )
    return container
