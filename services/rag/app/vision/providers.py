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

Images are read several to a request. On a free tier the binding limit is
requests per day, not tokens, so four images in one call is four times the
material read before the day runs out -- which for a scanned document is the
difference between every page being searchable and a third of them missing.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Sequence
from typing import Any, Protocol

import httpx

from app.core.gemini import rate_limit_error
from app.core.models import ExtractedImage
from app.embedding.rate_limit import (
    QuotaExhaustedError,
    RateLimiter,
    TransientError,
    with_retries,
)

logger = logging.getLogger(__name__)

# Part of every cache key. Bump it when the prompt changes, so readings made
# under the old instructions are not served as if they followed the new ones.
PROMPT_VERSION = 2


class VisionUnavailableError(Exception):
    """The images could not be read -- rate limits, an outage, a bad response.

    Distinct from a reading of None, which means the model looked and found
    nothing worth indexing. Collapsing the two would report a quota failure as
    "this document had no images worth reading", which is actively misleading
    when the images were the point.
    """


class VisionQuotaExhaustedError(VisionUnavailableError):
    """The model's daily allowance is spent. Every further call today fails the
    same way, so a caller should stop asking rather than work through the rest
    of its images one refusal at a time."""


class VisionProvider(Protocol):
    model_id: str
    # What a cached reading is filed under: the model and the prompt version.
    cache_key: str

    async def describe(self, images: Sequence[ExtractedImage]) -> dict[str, str | None]:
        """Reads a batch of images.

        Returns a reading per image content hash: the text, or None when the
        image carries no study content. An image missing from the result was
        not read, and should be tried again another time rather than recorded
        as empty.
        """
        ...


class MockVisionProvider:
    """Deterministic stand-in, so the ingestion path can be tested end to end
    with no API key and no quota."""

    model_id = "mock-vision"
    cache_key = f"mock-vision:v{PROMPT_VERSION}"

    def __init__(self, response: str = "A diagram showing a worked example.") -> None:
        self.response = response
        self.calls = 0
        self.images_read = 0

    async def describe(self, images: Sequence[ExtractedImage]) -> dict[str, str | None]:
        self.calls += 1
        self.images_read += len(images)
        # Mirrors the real provider's contract: an empty image yields nothing.
        return {
            image.content_hash: self.response if image.data else None
            for image in images
        }


_PROMPT = """You are reading {count} image(s) taken from a student's study material. \
Each image follows its label -- "Image 1", "Image 2" and so on -- and sometimes \
the text around it on the page or slide.

For each image:

If it is text, a table, or a formula: transcribe it exactly. Preserve table \
structure using " | " between cells and a newline between rows. Do not \
summarise, do not add commentary, do not correct anything.

If it is a diagram, chart, or illustration: describe what it shows in two or \
three sentences, naming the parts and their relationships, so that a student \
searching for this concept would match your description. Include any labels or \
axis titles verbatim.

If it is decorative and carries no study content -- a logo, a border, a stock \
photograph -- its content is exactly: NO_CONTENT

Read every image on its own. Never carry text from one image into another's \
entry.

Return one entry per image: its number, and the transcription or description."""

_SCHEMA: dict[str, Any] = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "image": {"type": "INTEGER"},
            "content": {"type": "STRING"},
        },
        "required": ["image", "content"],
    },
}

# Room for a dense page transcribed in full, per image in the request.
_OUTPUT_TOKENS_PER_IMAGE = 4096


def _as_reading(content: str) -> str | None:
    text = content.strip()
    if not text or text.upper().startswith("NO_CONTENT"):
        return None
    return text


def read_batch_response(
    payload: dict[str, Any], images: Sequence[ExtractedImage]
) -> dict[str, str | None]:
    """Maps the model's numbered entries back onto the images they describe.

    Entries are matched by number, never by position: a model that skips an
    image or answers out of order must not shift every later description onto
    the wrong picture. An image with no entry is left out, so it is retried
    later rather than recorded as having nothing in it.
    """
    candidates = payload.get("candidates") or []
    parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise VisionUnavailableError("the model returned no readings")

    try:
        entries = json.loads(text)
    except ValueError as exc:
        raise VisionUnavailableError(f"unreadable response: {text[:120]}") from exc
    if not isinstance(entries, list):
        raise VisionUnavailableError("the response was not a list of readings")

    readings: dict[str, str | None] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        number, content = entry.get("image"), entry.get("content")
        if (
            isinstance(number, int)
            and 1 <= number <= len(images)
            and isinstance(content, str)
        ):
            readings[images[number - 1].content_hash] = _as_reading(content)
    return readings


class GeminiVisionProvider:
    """Gemini multimodal generation.

    Uses the key the pipeline already holds, so image extraction adds no new
    credential. Failures surface as VisionUnavailableError and the ingest
    carries on without those figures: a document that loses one figure is far
    better than a document the student cannot upload at all.
    """

    def __init__(
        self,
        api_key: str,
        *,
        model_id: str = "gemini-3.1-flash-lite",
        max_rpm: int = 15,
        timeout: float = 180.0,
    ) -> None:
        if not api_key:
            raise ValueError("GeminiVisionProvider requires an API key")
        self.model_id = model_id
        self.cache_key = f"{model_id}:v{PROMPT_VERSION}"
        self._api_key = api_key
        self._timeout = timeout
        self._limiter = RateLimiter(max_rpm)
        # Reading a picture needs no deliberation, and thinking tokens are
        # pure latency. Models without thinking reject the setting with a 400;
        # the first rejection turns it off, as the LLM provider does.
        self._thinking_unsupported = False

    def _payload(self, images: Sequence[ExtractedImage]) -> dict[str, Any]:
        parts: list[dict[str, Any]] = [{"text": _PROMPT.format(count=len(images))}]
        for number, image in enumerate(images, start=1):
            hint = image.context_hint.strip()
            parts.append(
                {
                    "text": f"Image {number}"
                    + (f" -- the surrounding text says: {hint[:400]}" if hint else "")
                }
            )
            parts.append(
                {
                    "inline_data": {
                        "mime_type": image.mime_type,
                        "data": base64.b64encode(image.data).decode("ascii"),
                    }
                }
            )

        config: dict[str, Any] = {
            "temperature": 0.0,
            "maxOutputTokens": _OUTPUT_TOKENS_PER_IMAGE * len(images),
            "responseMimeType": "application/json",
            "responseSchema": _SCHEMA,
        }
        if not self._thinking_unsupported:
            config["thinkingConfig"] = {"thinkingBudget": 0}
        return {"contents": [{"parts": parts}], "generationConfig": config}

    async def describe(self, images: Sequence[ExtractedImage]) -> dict[str, str | None]:
        images = [image for image in images if image.data]
        if not images:
            return {}

        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model_id}:generateContent"
        )

        async def call() -> dict[str, str | None]:
            await self._limiter.acquire()
            payload = self._payload(images)
            sent_thinking = "thinkingConfig" in payload["generationConfig"]
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
                raise rate_limit_error(response, "vision")
            if response.status_code >= 500:
                raise TransientError(f"vision upstream {response.status_code}")
            if response.status_code == 400 and sent_thinking:
                self._thinking_unsupported = True
                logger.info(
                    "%s rejected thinkingConfig; disabling it for vision",
                    self.model_id,
                )
                raise TransientError("retrying without thinkingConfig")
            if response.status_code != 200:
                raise RuntimeError(
                    f"vision failed ({response.status_code}): {response.text[:300]}"
                )
            return read_batch_response(response.json(), images)

        try:
            return await with_retries(
                call, attempts=4, base_delay=4.0, description="vision describe"
            )
        except QuotaExhaustedError as exc:
            raise VisionQuotaExhaustedError(str(exc)) from exc
        except VisionUnavailableError:
            raise
        except Exception as exc:
            logger.warning("vision reading failed: %s", exc)
            raise VisionUnavailableError(str(exc)) from exc
