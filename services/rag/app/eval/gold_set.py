"""Gold-set construction.

A gold set is the ground truth every retrieval metric is measured against, so a
weak one makes the metrics meaningless in a way that is hard to notice: the
numbers still look like numbers.

Two ways to build one:

**Synthetic (here).** For each indexed chunk, ask an LLM to write a question
that chunk answers. The chunk is then that question's known-relevant target.
This is cheap and scales, but it has a real bias worth naming: questions
generated *from* a chunk tend to reuse its vocabulary, which flatters keyword
retrieval. Prompting for a student's phrasing rather than the passage's reduces
that but does not eliminate it.

**Manual.** A person writes questions and marks which chunks answer them. Far
better, far slower. The synthetic set is a starting point, not a substitute --
and the honest way to use it is as a relative comparison between strategies on
identical data, not as an absolute quality score.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Sequence

import httpx

from app.core.models import Chunk
from app.embedding.rate_limit import (
    RateLimiter,
    RateLimitError,
    TransientError,
    with_retries,
)
from app.eval.harness import GoldQuery

logger = logging.getLogger(__name__)

_PROMPT = """You are helping build a retrieval test set from a student's study material.

Read the passage and write ONE exam-style question that this passage answers.

Rules:
- Phrase it the way a student would ask, not the way the passage is written.
- Avoid copying distinctive phrases from the passage verbatim.
- The question must be answerable from this passage alone.
- If the passage is a heading, a fragment, or has no substantive content,
  return exactly: SKIP

Passage:
{passage}

Return only the question, or SKIP."""


class SyntheticGoldSetBuilder:
    def __init__(
        self,
        api_key: str,
        *,
        model_id: str = "gemini-3.8-flash",
        max_rpm: int = 15,
        timeout: float = 90.0,
    ) -> None:
        if not api_key:
            raise ValueError("SyntheticGoldSetBuilder requires an API key")
        self._api_key = api_key
        self._model_id = model_id
        self._timeout = timeout
        self._limiter = RateLimiter(max_rpm)

    async def _generate(self, passage: str) -> str | None:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self._model_id}:generateContent"
        )

        async def call() -> str:
            await self._limiter.acquire()
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    url,
                    headers={
                        "x-goog-api-key": self._api_key,
                        "content-type": "application/json",
                    },
                    json={
                        "contents": [
                            {
                                "parts": [
                                    {"text": _PROMPT.format(passage=passage[:4000])}
                                ]
                            }
                        ],
                        "generationConfig": {
                            "temperature": 0.4,
                            "maxOutputTokens": 2000,
                        },
                    },
                )

            # Free-tier generation returns 429 and 503 routinely under any
            # sustained load. Without retries a single sweep loses most of the
            # gold set, and a gold set with holes in it silently weakens every
            # metric measured against it.
            if response.status_code == 429:
                raise RateLimitError(
                    f"gold generation rate limited: {response.text[:160]}"
                )
            if response.status_code >= 500:
                raise TransientError(f"gold generation upstream {response.status_code}")
            response.raise_for_status()

            parts = response.json()["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts).strip()

        text = await with_retries(
            call, attempts=5, base_delay=4.0, description="gold generation"
        )

        if not text or "SKIP" in text.upper()[:20]:
            return None
        # Models sometimes wrap the question in quotes or prefix it.
        return re.sub(r'^["\']|["\']$', "", text.split("\n")[0].strip())

    async def build(
        self, chunks: Sequence[Chunk], *, concurrency: int = 1
    ) -> list[GoldQuery]:
        """One question per chunk, skipping chunks with nothing to ask about."""
        semaphore = asyncio.Semaphore(concurrency)

        async def one(chunk: Chunk) -> GoldQuery | None:
            async with semaphore:
                try:
                    question = await self._generate(chunk.text)
                except Exception as exc:
                    logger.warning("gold generation failed for %s: %s", chunk.id, exc)
                    return None

            if question is None:
                return None
            return GoldQuery(
                question=question,
                relevant_chunk_ids={chunk.id},
                document_ids=[chunk.metadata.document_id],
                note=" › ".join(chunk.metadata.heading_path),
            )

        results = await asyncio.gather(*(one(c) for c in chunks))
        gold = [g for g in results if g is not None]

        logger.info("generated %s gold queries from %s chunks", len(gold), len(chunks))
        return gold


def gold_from_json(payload: str) -> list[GoldQuery]:
    """Parses a hand-written gold set supplied inline rather than as a file."""
    data = json.loads(payload)
    entries = data["queries"] if isinstance(data, dict) else data
    return [GoldQuery.from_dict(entry) for entry in entries]
