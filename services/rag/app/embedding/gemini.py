"""Gemini embedding provider.

Two details matter more than the rest for retrieval quality:

1. **Task type.** Gemini encodes a passage being indexed and a question being
   asked differently. Sending both as the same type is a silent accuracy loss
   -- nothing errors, results are just quietly worse.

2. **Renormalisation after truncation.** Vectors are unit-normalised at the
   model's native 3072 dimensions. Truncating to fewer (Matryoshka) breaks that
   invariant, and cosine similarity against non-unit vectors is subtly wrong.
   Truncated output is renormalised here.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence

import httpx

from app.core.models import EmbeddingVector
from app.embedding.rate_limit import (
    RateLimiter,
    RateLimitError,
    TransientError,
    with_retries,
)

logger = logging.getLogger(__name__)

_API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"

# The model's native output width. Anything smaller is a Matryoshka prefix.
_NATIVE_DIMENSIONS = 3072

# Measured against the live API: 64 succeeds comfortably, 100 draws a 429.
_MAX_BATCH = 64


class GeminiEmbeddingProvider:
    def __init__(
        self,
        api_key: str,
        *,
        model_id: str = "gemini-embedding-2",
        dimensions: int = 3072,
        max_rpm: int = 100,
        batch_size: int = _MAX_BATCH,
        timeout: float = 180.0,
    ) -> None:
        if not api_key:
            raise ValueError("GeminiEmbeddingProvider requires an API key")
        if dimensions <= 0 or dimensions > _NATIVE_DIMENSIONS:
            raise ValueError(f"dimensions must be in 1..{_NATIVE_DIMENSIONS}")

        self.model_id = model_id
        self.dimensions = dimensions
        self._api_key = api_key
        self._batch_size = min(batch_size, _MAX_BATCH)
        self._timeout = timeout
        self._limiter = RateLimiter(max_rpm)

    # ── http ────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        # Header rather than a query parameter, so the key never lands in a
        # request log or a proxy trace.
        return {"x-goog-api-key": self._api_key, "content-type": "application/json"}

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code == 200:
            return

        detail = response.text[:300]
        if response.status_code == 429:
            hint = response.headers.get("retry-after")
            raise RateLimitError(
                f"embedding rate limited: {detail}",
                retry_after=float(hint) if hint and hint.isdigit() else None,
            )
        if response.status_code >= 500:
            raise TransientError(f"embedding upstream {response.status_code}: {detail}")
        # 4xx other than 429 will not improve on retry.
        raise RuntimeError(
            f"embedding request failed ({response.status_code}): {detail}"
        )

    def _normalize(self, values: list[float]) -> list[float]:
        if self.dimensions >= _NATIVE_DIMENSIONS:
            return values
        norm = math.sqrt(sum(v * v for v in values))
        if norm == 0.0:
            return values
        return [v / norm for v in values]

    async def _embed_batch(
        self, texts: Sequence[str], task_type: str
    ) -> list[EmbeddingVector]:
        payload = {
            "requests": [
                {
                    "model": f"models/{self.model_id}",
                    "content": {"parts": [{"text": text}]},
                    "taskType": task_type,
                    "outputDimensionality": self.dimensions,
                }
                for text in texts
            ]
        }

        async def call() -> list[EmbeddingVector]:
            await self._limiter.acquire()
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                try:
                    response = await client.post(
                        f"{_API_ROOT}/{self.model_id}:batchEmbedContents",
                        headers=self._headers(),
                        json=payload,
                    )
                except httpx.HTTPError as exc:
                    raise TransientError(f"embedding request errored: {exc}") from exc

            self._raise_for_status(response)
            embeddings = response.json().get("embeddings", [])
            if len(embeddings) != len(texts):
                # A short response would silently misalign vectors with chunks,
                # which is far worse than failing here.
                raise TransientError(
                    f"expected {len(texts)} embeddings, received {len(embeddings)}"
                )

            return [
                EmbeddingVector(
                    values=self._normalize(item["values"]),
                    model_id=self.model_id,
                    dimensions=self.dimensions,
                )
                for item in embeddings
            ]

        return await with_retries(
            call, description=f"embed {len(texts)} texts ({task_type})"
        )

    # ── interface ───────────────────────────────────────────

    async def embed_documents(self, texts: Sequence[str]) -> list[EmbeddingVector]:
        if not texts:
            return []

        results: list[EmbeddingVector] = []
        for start in range(0, len(texts), self._batch_size):
            window = list(texts[start : start + self._batch_size])
            results.extend(await self._embed_batch(window, "RETRIEVAL_DOCUMENT"))
            logger.debug(
                "embedded %s/%s chunks",
                min(start + self._batch_size, len(texts)),
                len(texts),
            )
        return results

    async def embed_query(self, text: str) -> EmbeddingVector:
        # RETRIEVAL_QUERY, not RETRIEVAL_DOCUMENT: the asymmetry is the point.
        vectors = await self._embed_batch([text], "RETRIEVAL_QUERY")
        return vectors[0]
