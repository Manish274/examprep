"""Vision providers: reading content out of images.

Lecture material routinely puts substantive content in pictures -- a table
screenshotted from a textbook, a formula pasted as an image, a diagram. None of
it survives text extraction, so a student asking about it gets nothing back
from a document that plainly contains the answer.

A vision model handles both shapes this takes, which pure OCR does not:

  transcription  a screenshot of text or a table, wanted verbatim
  description    a real diagram, where the useful output is a sentence saying
                 what it shows, because that is what a question will match

The output is generated text, not extracted text. Everything downstream marks
it as such, and nothing here should ever return something indistinguishable
from words actually present in the file.
"""

from __future__ import annotations

import base64
import logging
from typing import Protocol

import httpx

from app.embedding.rate_limit import (
    RateLimiter,
    RateLimitError,
    TransientError,
    with_retries,
)

logger = logging.getLogger(__name__)


class VisionUnavailableError(Exception):
    """The image could not be read -- rate limits, an outage, a bad response.

    Distinct from returning None, which means the model looked and found
    nothing worth indexing. Collapsing the two would report a quota failure as
    "this document had no images worth reading", which is actively misleading
    when the images were the point.
    """


_PROMPT = """You are reading an image taken from a student's study material.

If the image is text, a table, or a formula: transcribe it exactly. Preserve
table structure using " | " between cells and a newline between rows. Do not
summarise, do not add commentary, do not correct anything.

If the image is a diagram, chart, or illustration: describe what it shows in
two or three sentences, naming the parts and their relationships, so that a
student searching for this concept would match your description. Include any
labels or axis titles verbatim.

If the image is decorative and carries no study content -- a logo, a border, a
stock photograph -- reply with exactly: NO_CONTENT

{hint}Return only the transcription or description."""


class VisionProvider(Protocol):
    model_id: str

    async def describe(
        self, image: bytes, *, mime_type: str, context_hint: str = ""
    ) -> str | None: ...


class NoOpVisionProvider:
    """Extracts nothing. The default, and the baseline the others are measured
    against -- it is exactly the behaviour before vision existed."""

    model_id = "noop-vision"

    async def describe(
        self, image: bytes, *, mime_type: str, context_hint: str = ""
    ) -> str | None:
        return None


class MockVisionProvider:
    """Deterministic stand-in, so the ingestion path can be tested end to end
    with no API key and no quota."""

    model_id = "mock-vision"

    def __init__(self, response: str = "A diagram showing a worked example.") -> None:
        self.response = response
        self.calls = 0

    async def describe(
        self, image: bytes, *, mime_type: str, context_hint: str = ""
    ) -> str | None:
        self.calls += 1
        # Mirrors the real provider's contract: an empty image yields nothing.
        return self.response if image else None


class GeminiVisionProvider:
    """Gemini multimodal generation.

    Uses the key the pipeline already holds, so image extraction adds no new
    credential. Failures degrade to no description rather than failing the
    ingest: a document that loses one figure is far better than a document the
    student cannot upload at all.
    """

    def __init__(
        self,
        api_key: str,
        *,
        model_id: str = "gemini-3.5-flash-lite",
        max_rpm: int = 15,
        timeout: float = 120.0,
    ) -> None:
        if not api_key:
            raise ValueError("GeminiVisionProvider requires an API key")
        self.model_id = model_id
        self._api_key = api_key
        self._timeout = timeout
        self._limiter = RateLimiter(max_rpm)

    async def describe(
        self, image: bytes, *, mime_type: str, context_hint: str = ""
    ) -> str | None:
        if not image:
            return None

        hint = (
            f"The surrounding slide or page says: {context_hint[:400]}\n\n"
            if context_hint.strip()
            else ""
        )
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model_id}:generateContent"
        )
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": _PROMPT.format(hint=hint)},
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": base64.b64encode(image).decode("ascii"),
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 4000,
            },
        }

        async def call() -> str:
            await self._limiter.acquire()
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                try:
                    response = await client.post(
                        url,
                        headers={
                            "x-goog-api-key": self._api_key,
                            "content-type": "application/json",
                        },
                        json=payload,
                    )
                except httpx.HTTPError as exc:
                    raise TransientError(f"vision request errored: {exc}") from exc

            if response.status_code == 429:
                raise RateLimitError(f"vision rate limited: {response.text[:160]}")
            if response.status_code >= 500:
                raise TransientError(f"vision upstream {response.status_code}")
            if response.status_code != 200:
                raise RuntimeError(
                    f"vision failed ({response.status_code}): {response.text[:300]}"
                )

            candidates = response.json().get("candidates") or []
            if not candidates:
                return ""
            parts = candidates[0].get("content", {}).get("parts", [])
            return "".join(p.get("text", "") for p in parts).strip()

        try:
            text = await with_retries(
                call, attempts=4, base_delay=4.0, description="vision describe"
            )
        except Exception as exc:
            logger.warning("vision description failed: %s", exc)
            raise VisionUnavailableError(str(exc)) from exc

        if not text or text.strip().upper().startswith("NO_CONTENT"):
            return None
        return text
