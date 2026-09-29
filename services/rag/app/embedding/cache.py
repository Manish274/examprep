"""Embedding cache.

Embedding is the only genuinely expensive step in ingestion, and the same text
gets embedded repeatedly in practice: a re-ingest after a chunker change, an
evaluation sweep over the same corpus, a document two students both upload.
Caching turns all of those into zero API calls, which matters a great deal on a
free tier.

Keyed on (model, dimensions, sha256 of text). Dimensions belong in the key
because the same model at 768 and at 3072 returns different vectors -- omitting
it would serve a truncated vector to a caller expecting a full one.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from collections.abc import Sequence
from typing import Protocol

import asyncpg

from app.core.interfaces import EmbeddingProvider
from app.core.models import EmbeddingVector
from app.core.text import content_hash

logger = logging.getLogger(__name__)


class EmbeddingCache(Protocol):
    async def get_many(
        self, model_key: str, hashes: Sequence[str]
    ) -> dict[str, list[float]]: ...

    async def put_many(
        self, model_key: str, dimensions: int, items: dict[str, list[float]]
    ) -> None: ...

    async def close(self) -> None: ...


def model_key(model_id: str, dimensions: int) -> str:
    return f"{model_id}@{dimensions}"


class InMemoryEmbeddingCache:
    """Process-local cache, for tests: they must not need Postgres, nor fill
    the shared cache with vectors from throwaway fixtures."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], list[float]] = {}

    async def get_many(
        self, model_key: str, hashes: Sequence[str]
    ) -> dict[str, list[float]]:
        found = {}
        for h in hashes:
            hit = self._store.get((model_key, h))
            if hit is not None:
                found[h] = hit
        return found

    async def put_many(
        self, model_key: str, dimensions: int, items: dict[str, list[float]]
    ) -> None:
        for h, vector in items.items():
            self._store[(model_key, h)] = vector

    async def close(self) -> None:
        self._store.clear()


class PostgresEmbeddingCache:
    """Shared cache in the application database.

    Reads and writes are best-effort: a cache failure degrades throughput and
    cost, never correctness, so it is logged and swallowed rather than raised.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool | None:
        if self._pool is None:
            try:
                self._pool = await asyncpg.create_pool(
                    self._dsn, min_size=1, max_size=4, command_timeout=15
                )
            except Exception as exc:
                logger.warning("embedding cache unavailable: %s", exc)
                return None
        return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def get_many(
        self, model_key: str, hashes: Sequence[str]
    ) -> dict[str, list[float]]:
        if not hashes:
            return {}
        pool = await self._get_pool()
        if pool is None:
            return {}

        try:
            rows = await pool.fetch(
                "SELECT content_hash, vector FROM embedding_cache "
                "WHERE model_id = $1 AND content_hash = ANY($2::text[])",
                model_key,
                list(hashes),
            )
        except Exception as exc:
            logger.warning("embedding cache read failed: %s", exc)
            return {}

        result: dict[str, list[float]] = {}
        for row in rows:
            raw = row["vector"]
            # jsonb comes back as a string through asyncpg unless a codec is
            # registered; accept either shape.
            result[row["content_hash"]] = (
                json.loads(raw) if isinstance(raw, str) else raw
            )
        return result

    async def put_many(
        self, model_key: str, dimensions: int, items: dict[str, list[float]]
    ) -> None:
        if not items:
            return
        pool = await self._get_pool()
        if pool is None:
            return

        records = [
            (model_key, h, dimensions, json.dumps(vector))
            for h, vector in items.items()
        ]
        try:
            await pool.executemany(
                "INSERT INTO embedding_cache "
                "(model_id, content_hash, dimensions, vector) "
                "VALUES ($1, $2, $3, $4::jsonb) "
                "ON CONFLICT (model_id, content_hash) DO NOTHING",
                records,
            )
        except Exception as exc:
            logger.warning("embedding cache write failed: %s", exc)


class CachedEmbeddingProvider:
    """Wraps any EmbeddingProvider with a read-through cache.

    Document embeddings go to the shared, persistent cache. Query embeddings do
    not: they are typed differently by the model, a full-width vector is tens
    of kilobytes as JSON, and most questions are asked once -- persisting them
    would grow the table with every question for a low hit rate.

    Queries get a small in-process LRU instead, for the repeats that do happen
    close together: the retrieval inspector running one question through
    several strategies, an evaluation sweep, a student re-asking after an
    error. Each of those used to be a fresh embedding round trip.
    """

    def __init__(
        self,
        inner: EmbeddingProvider,
        cache: EmbeddingCache,
        *,
        query_cache_size: int = 64,
    ) -> None:
        self._inner = inner
        self._cache = cache
        self.model_id = inner.model_id
        self.dimensions = inner.dimensions
        # Document counters only: ingestion reports these as its cache hits.
        self.hits = 0
        self.misses = 0
        # About 100 KB per full-width vector held as Python floats, so the
        # default bounds this near 6 MB.
        self._queries: OrderedDict[str, EmbeddingVector] = OrderedDict()
        self._query_cache_size = query_cache_size
        self.query_hits = 0
        self.query_misses = 0

    async def embed_documents(self, texts: Sequence[str]) -> list[EmbeddingVector]:
        if not texts:
            return []

        key = model_key(self.model_id, self.dimensions)
        hashes = [content_hash(text) for text in texts]
        cached = await self._cache.get_many(key, list(dict.fromkeys(hashes)))

        # Identical text can appear more than once in one document; embed each
        # distinct string once and fan the result back out.
        pending: dict[str, str] = {}
        for text, h in zip(texts, hashes, strict=True):
            if h not in cached and h not in pending:
                pending[h] = text

        self.hits += len(texts) - sum(1 for h in hashes if h in pending)
        self.misses += len(pending)

        if pending:
            order = list(pending)
            fresh = await self._inner.embed_documents([pending[h] for h in order])
            new_vectors = {h: vec.values for h, vec in zip(order, fresh, strict=True)}
            await self._cache.put_many(key, self.dimensions, new_vectors)
            cached.update(new_vectors)

        return [
            EmbeddingVector(
                values=cached[h], model_id=self.model_id, dimensions=self.dimensions
            )
            for h in hashes
        ]

    async def close(self) -> None:
        await self._cache.close()

    async def embed_query(self, text: str) -> EmbeddingVector:
        key = content_hash(text)
        cached = self._queries.get(key)
        if cached is not None:
            self._queries.move_to_end(key)
            self.query_hits += 1
            return cached

        self.query_misses += 1
        vector = await self._inner.embed_query(text)
        if self._query_cache_size > 0:
            self._queries[key] = vector
            while len(self._queries) > self._query_cache_size:
                self._queries.popitem(last=False)
        return vector
