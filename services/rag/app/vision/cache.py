"""Vision cache.

A vision call is the scarcest thing ingestion spends: a free-tier model allows
a few dozen a day, and a scanned document wants one per page. Without a cache,
every retry of a failed upload -- and every student uploading the same slides
-- pays for every image again, and a document that ran out of quota half way
can never finish, because each attempt starts from the first image.

Keyed on the provider's cache key (model and prompt version) and the sha256 of
the image bytes. A reading of "no content" is cached too: the model's verdict
that an image is a logo is as stable as a transcription, and asking again only
spends another call to hear it. A failure is never cached.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

import asyncpg

logger = logging.getLogger(__name__)


class VisionCache(Protocol):
    async def get_many(
        self, key: str, hashes: Sequence[str]
    ) -> dict[str, str | None]: ...

    async def put_many(self, key: str, readings: dict[str, str | None]) -> None: ...

    async def close(self) -> None: ...


class InMemoryVisionCache:
    """Process-local cache, for tests and for the stand-in providers, whose
    readings are worth nothing outside the process that made them."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str | None] = {}

    async def get_many(self, key: str, hashes: Sequence[str]) -> dict[str, str | None]:
        return {h: self._store[(key, h)] for h in hashes if (key, h) in self._store}

    async def put_many(self, key: str, readings: dict[str, str | None]) -> None:
        for h, reading in readings.items():
            self._store[(key, h)] = reading

    async def close(self) -> None:
        self._store.clear()


class PostgresVisionCache:
    """Shared cache in the application database.

    Best-effort, like the embedding cache: a failure here costs quota, never
    correctness, so it is logged and swallowed rather than raised.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool | None:
        if self._pool is None:
            try:
                self._pool = await asyncpg.create_pool(
                    self._dsn, min_size=1, max_size=2, command_timeout=15
                )
            except Exception as exc:
                logger.warning("vision cache unavailable: %s", exc)
                return None
        return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def get_many(self, key: str, hashes: Sequence[str]) -> dict[str, str | None]:
        if not hashes:
            return {}
        pool = await self._get_pool()
        if pool is None:
            return {}
        try:
            rows = await pool.fetch(
                "SELECT content_hash, reading FROM vision_cache "
                "WHERE model_key = $1 AND content_hash = ANY($2::text[])",
                key,
                list(hashes),
            )
        except Exception as exc:
            logger.warning("vision cache read failed: %s", exc)
            return {}
        return {row["content_hash"]: row["reading"] for row in rows}

    async def put_many(self, key: str, readings: dict[str, str | None]) -> None:
        if not readings:
            return
        pool = await self._get_pool()
        if pool is None:
            return
        try:
            await pool.executemany(
                "INSERT INTO vision_cache (model_key, content_hash, reading) "
                "VALUES ($1, $2, $3) "
                "ON CONFLICT (model_key, content_hash) DO NOTHING",
                [(key, h, reading) for h, reading in readings.items()],
            )
        except Exception as exc:
            logger.warning("vision cache write failed: %s", exc)
