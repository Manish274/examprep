"""Deterministic stand-in embedding provider.

Exists so the whole pipeline -- ingestion, indexing, retrieval, fusion -- can be
exercised with no API key, no network and no quota. That keeps the test suite
fast and free, and it keeps a broken credential from looking like a broken
pipeline.

The vectors carry real signal rather than being random: they are built from
hashed word features, so texts sharing vocabulary land closer together than
texts that do not. Retrieval tests can therefore assert on ordering instead of
merely asserting that something came back. It is a bag-of-words model, so it
cannot match paraphrases -- that is what the real provider is for.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

from app.core.models import EmbeddingVector
from app.core.registry import embedders

_WORD = re.compile(r"[a-z0-9]+")


class MockEmbeddingProvider:
    model_id = "mock-embedding"

    def __init__(self, dimensions: int = 768) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self.dimensions = dimensions

    def _vector(self, text: str) -> list[float]:
        values = [0.0] * self.dimensions
        words = _WORD.findall(text.lower())

        for word in words:
            digest = hashlib.sha256(word.encode("utf-8")).digest()
            # Two independent buckets per word makes accidental collisions
            # between unrelated words far less likely to dominate a vector.
            for offset in (0, 8):
                raw = int.from_bytes(digest[offset : offset + 4], "big")
                index = raw % self.dimensions
                sign = 1.0 if digest[offset + 4] % 2 == 0 else -1.0
                values[index] += sign

        norm = math.sqrt(sum(v * v for v in values))
        if norm == 0.0:
            # An empty or symbol-only string still needs a usable unit vector.
            values[0] = 1.0
            return values
        return [v / norm for v in values]

    def _embedding(self, text: str) -> EmbeddingVector:
        return EmbeddingVector(
            values=self._vector(text),
            model_id=self.model_id,
            dimensions=self.dimensions,
        )

    async def embed_documents(self, texts: Sequence[str]) -> list[EmbeddingVector]:
        return [self._embedding(text) for text in texts]

    async def embed_query(self, text: str) -> EmbeddingVector:
        return self._embedding(text)


@embedders.register("mock")
def _create_mock_embedder(dimensions: int = 768, **_: object) -> MockEmbeddingProvider:
    return MockEmbeddingProvider(dimensions=dimensions)
