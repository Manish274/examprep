"""Rerankers.

Retrieval is fast and approximate: it compares a query against pre-computed
vectors, one document at a time, with no ability to weigh them against each
other. Reranking is slow and precise: a cross-encoder reads the query and the
document *together*, so it can tell that a passage mentioning "candidate key"
three times is still not answering a question about normal forms.

This is the last filter before the LLM sees anything, so its precision sets the
ceiling on how grounded an answer can be. Retrieving 50 candidates and reranking
to 8 gets better material in front of the model than retrieving 8 directly.

Three implementations, all satisfying the same shape:
  JinaReranker    a real cross-encoder, the production default
  NoOpReranker    passthrough, the baseline the eval measures against
  GeminiListwise  RankGPT-style, reorders with an LLM when no reranker API is
                  available
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence

import httpx

from app.core.models import ScoredChunk
from app.core.registry import rerankers
from app.embedding.rate_limit import (
    RateLimiter,
    RateLimitError,
    TransientError,
    with_retries,
)

logger = logging.getLogger(__name__)

_JINA_URL = "https://api.jina.ai/v1/rerank"

# Jina v2 truncates beyond this; sending more wastes quota for no benefit.
_MAX_DOCUMENT_CHARS = 4000


def _document_text(scored: ScoredChunk) -> str:
    """What the reranker actually reads.

    The heading trail is included for the same reason it is included when
    embedding: a chunk reading "It must also satisfy 2NF" is close to
    meaningless alone, and a cross-encoder judging it against a question about
    normalization needs to know what section it came from.
    """
    return scored.chunk.embedding_text()[:_MAX_DOCUMENT_CHARS]


class NoOpReranker:
    """Passthrough. Keeps retrieval order and truncates to top_n.

    Exists so the evaluation harness can measure what reranking actually buys.
    If hybrid+rerank cannot beat hybrid alone, that is a finding worth having
    rather than a cost to keep paying.
    """

    name = "noop"
    # Whether `rerank_score` is a calibrated relevance that can be compared
    # against a fixed threshold, rather than a position in an ordering.
    calibrated = False

    async def rerank(
        self, query: str, candidates: Sequence[ScoredChunk], *, top_n: int
    ) -> list[ScoredChunk]:
        return list(candidates[:top_n])


class JinaReranker:
    """Hosted cross-encoder.

    Falls back to retrieval order rather than failing the request: a degraded
    answer built from unreranked candidates is far better than no answer, and
    the fallback is logged so it does not pass unnoticed.
    """

    name = "jina"
    calibrated = True

    def __init__(
        self,
        api_key: str,
        *,
        model_id: str = "jina-reranker-v2-base-multilingual",
        max_rpm: int = 60,
        timeout: float = 60.0,
        fail_open: bool = True,
    ) -> None:
        if not api_key:
            raise ValueError("JinaReranker requires an API key")
        self.model_id = model_id
        self._api_key = api_key
        self._timeout = timeout
        self._fail_open = fail_open
        self._limiter = RateLimiter(max_rpm)

    async def _call(self, query: str, documents: list[str], top_n: int) -> list[dict]:
        async def request() -> list[dict]:
            await self._limiter.acquire()
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                try:
                    response = await client.post(
                        _JINA_URL,
                        headers={
                            "Authorization": f"Bearer {self._api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": self.model_id,
                            "query": query,
                            "documents": documents,
                            "top_n": top_n,
                        },
                    )
                except httpx.HTTPError as exc:
                    raise TransientError(f"rerank request errored: {exc}") from exc

            if response.status_code == 429:
                raise RateLimitError(f"rerank rate limited: {response.text[:200]}")
            if response.status_code >= 500:
                raise TransientError(
                    f"rerank upstream {response.status_code}: {response.text[:200]}"
                )
            if response.status_code != 200:
                raise RuntimeError(
                    f"rerank failed ({response.status_code}): {response.text[:300]}"
                )

            return list(response.json().get("results", []))

        return await with_retries(request, attempts=3, description="rerank")

    async def rerank(
        self, query: str, candidates: Sequence[ScoredChunk], *, top_n: int
    ) -> list[ScoredChunk]:
        if not candidates:
            return []

        documents = [_document_text(c) for c in candidates]
        try:
            results = await self._call(query, documents, min(top_n, len(candidates)))
        except Exception as exc:
            if not self._fail_open:
                raise
            logger.warning(
                "reranker unavailable (%s); falling back to retrieval order", exc
            )
            return list(candidates[:top_n])

        reranked: list[ScoredChunk] = []
        for item in results:
            index = item.get("index")
            if not isinstance(index, int) or not 0 <= index < len(candidates):
                # A bad index would silently attach one chunk's score to
                # another's text.
                logger.warning("reranker returned out-of-range index %r", index)
                continue

            scored = candidates[index].model_copy(deep=True)
            scored.rerank_score = float(item.get("relevance_score", 0.0))
            # The reranker's judgement supersedes the fused rank; the earlier
            # scores stay on the object for the eval harness.
            scored.score = scored.rerank_score
            reranked.append(scored)

        if not reranked:
            logger.warning("reranker returned nothing usable; keeping retrieval order")
            return list(candidates[:top_n])

        return reranked[:top_n]


class GeminiListwiseReranker:
    """RankGPT-style listwise reranking.

    An LLM sees all the candidates at once and returns an ordering. Listwise
    rather than pointwise because seeing the alternatives is what lets a model
    say "the third one answers this, the others are merely about the topic".

    Slower and less precise than a real cross-encoder, but it needs no extra
    credential beyond the one the pipeline already has.
    """

    name = "gemini_listwise"
    # Scores are derived from rank (1, 1/2, 1/3...): the top passage always
    # scores 1.0, however irrelevant it is.
    calibrated = False

    _PROMPT = (
        "You are ranking passages by how well each one answers a question.\n\n"
        "Question: {query}\n\n"
        "Passages:\n{passages}\n\n"
        "Return ONLY a JSON array of passage numbers, most relevant first, "
        "including every number exactly once. Example: [3, 1, 2]"
    )

    def __init__(
        self,
        api_key: str,
        *,
        model_id: str = "gemini-3.5-flash-lite",
        max_rpm: int = 15,
        timeout: float = 90.0,
        max_candidates: int = 30,
    ) -> None:
        if not api_key:
            raise ValueError("GeminiListwiseReranker requires an API key")
        self.model_id = model_id
        self._api_key = api_key
        self._timeout = timeout
        # Ordering quality degrades and latency climbs past roughly this many
        # passages in one prompt.
        self._max_candidates = max_candidates
        self._limiter = RateLimiter(max_rpm)

    async def rerank(
        self, query: str, candidates: Sequence[ScoredChunk], *, top_n: int
    ) -> list[ScoredChunk]:
        if not candidates:
            return []

        window = list(candidates[: self._max_candidates])
        passages = "\n\n".join(
            f"[{i + 1}] {_document_text(c)[:1200]}" for i, c in enumerate(window)
        )
        prompt = self._PROMPT.format(query=query, passages=passages)

        try:
            order = await self._ask(prompt, len(window))
        except Exception as exc:
            logger.warning(
                "listwise reranker unavailable (%s); keeping retrieval order", exc
            )
            return list(candidates[:top_n])

        reranked: list[ScoredChunk] = []
        for rank, position in enumerate(order):
            scored = window[position].model_copy(deep=True)
            # A listwise model returns an ordering, not scores; derive a
            # descending score so downstream code can treat it uniformly.
            scored.rerank_score = 1.0 / (rank + 1)
            scored.score = scored.rerank_score
            reranked.append(scored)

        # Anything the model omitted keeps its retrieval order behind the rest,
        # so a truncated response cannot silently drop candidates.
        seen = set(order)
        for i, scored in enumerate(window):
            if i not in seen:
                tail = scored.model_copy(deep=True)
                tail.rerank_score = 0.0
                tail.score = 0.0
                reranked.append(tail)

        return reranked[:top_n]

    async def _ask(self, prompt: str, count: int) -> list[int]:
        await self._limiter.acquire()
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model_id}:generateContent"
        )
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                url,
                headers={
                    "x-goog-api-key": self._api_key,
                    "content-type": "application/json",
                },
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.0,
                        "maxOutputTokens": 2000,
                        # Ordering passages needs no deliberation, and thinking
                        # tokens here are pure latency and quota.
                        "thinkingConfig": {"thinkingBudget": 0},
                    },
                },
            )
        response.raise_for_status()

        parts = response.json()["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts)
        return self._parse_order(text, count)

    @staticmethod
    def _parse_order(text: str, count: int) -> list[int]:
        """Extracts a 0-based ordering, tolerating prose around the JSON."""
        match = re.search(r"\[[^\]]*\]", text, re.DOTALL)
        if match is None:
            raise ValueError(f"no ranking found in response: {text[:200]}")

        raw = json.loads(match.group(0))
        order: list[int] = []
        for value in raw:
            index = int(value) - 1
            # Duplicates and out-of-range numbers would corrupt the mapping
            # back onto candidates.
            if 0 <= index < count and index not in order:
                order.append(index)
        if not order:
            raise ValueError("ranking contained no usable positions")
        return order


@rerankers.register("noop")
def _create_noop(**_: object) -> NoOpReranker:
    return NoOpReranker()


@rerankers.register("jina")
def _create_jina(
    api_key: str,
    model_id: str = "jina-reranker-v2-base-multilingual",
    max_rpm: int = 60,
) -> JinaReranker:
    return JinaReranker(api_key, model_id=model_id, max_rpm=max_rpm)


@rerankers.register("gemini_listwise")
def _create_gemini_listwise(
    api_key: str,
    model_id: str = "gemini-3.5-flash-lite",
    max_rpm: int = 15,
) -> GeminiListwiseReranker:
    return GeminiListwiseReranker(api_key, model_id=model_id, max_rpm=max_rpm)
