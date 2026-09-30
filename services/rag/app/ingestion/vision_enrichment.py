"""Turns extracted images into FIGURE blocks.

Runs between parsing and chunking. Parsers stay synchronous and API-free; this
stage owns every vision call, so it can be skipped, swapped or rate-limited
without touching them.

Every image costs quota, so an image only reaches the model past four filters:

  size      a 40x40 image is a bullet glyph or a logo, never study content
  identity  the same logo on all 60 slides is read once, not 60 times
  cache     an image read before -- by a retry, or in another student's copy
            of the same slides -- is not read again
  budget    at most `max_images` new readings per document

What is left is read several images to a request and a couple of requests at a
time. The first sign that the day's quota is spent stops the rest, and so do
two failed requests in a row -- an overloaded model: those images are left for
a later attempt instead of each failing in turn after its own round of
retries, which is what used to turn a quota problem into a job that ran for a
quarter of an hour and then timed out.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Sequence

from app.core.models import (
    BlockType,
    ContentSource,
    ExtractedImage,
    ParsedBlock,
    ParsedDocument,
)
from app.vision.cache import VisionCache
from app.vision.providers import (
    VisionProvider,
    VisionQuotaExhaustedError,
    VisionUnavailableError,
)

logger = logging.getLogger(__name__)

# Below this, an image is decoration. A genuine screenshot of a table or a
# diagram is far larger; icons, bullets and rules are far smaller.
DEFAULT_MIN_PIXELS = 40_000  # e.g. 200x200

# A ceiling on new readings per document, so one pathological file cannot
# exhaust a day's quota.
DEFAULT_MAX_IMAGES = 40

# Image bytes per request. Gemini refuses a request over 20 MB, and base64
# grows the payload by a third.
_MAX_BATCH_BYTES = 12 * 1024 * 1024

# Failed batches in a row before the rest of the pass is given up on.
_FAILURES_BEFORE_STOPPING = 2

# A heading as the reading prompt asks for one: "# Title", "## Section".
_HEADING_LINE = re.compile(r"^(#{1,6})\s+(.+?)(?:\s+#+)?$")

# A caption the model marked as a heading anyway. Kept as text: as a heading
# it would become the section of every page after it -- "Table 1.3" was filed
# as the section of seven pages of careers advice.
_CAPTION = re.compile(
    r"^(table|fig(ure)?\.?|chart|diagram|graph|plate|image)\s*(\d|[IVX]+\b)",
    re.IGNORECASE,
)

# "1.2.4 Pharmaceutical Associations". The numbering says how deep a heading
# sits, and says it the same way on every page -- where the model chooses its
# "#" marks one page at a time, and gives the largest line on each page "#".
_NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+\S")

# Called with (images attempted so far, images to read) as the reading goes.
ProgressCallback = Callable[[int, int], None]


class VisionEnrichmentResult:
    def __init__(self) -> None:
        # Distinct images that became a figure, whether read now or cached.
        self.described = 0
        # Distinct images the model judged to carry no study content.
        self.no_content = 0
        # How many of those two came from the cache rather than a call.
        self.cached = 0
        self.skipped_small = 0
        self.skipped_duplicate = 0
        self.skipped_budget = 0
        # Left unread because the day's quota ran out part way.
        self.skipped_quota = 0
        # Kept apart from no_content: a failed call is not the same finding as
        # an image the model judged decorative.
        self.failed = 0
        # Not read because this pass was asked to use the cache only.
        self.pending = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "described": self.described,
            "no_content": self.no_content,
            "cached": self.cached,
            "skipped_small": self.skipped_small,
            "skipped_duplicate": self.skipped_duplicate,
            "skipped_budget": self.skipped_budget,
            "skipped_quota": self.skipped_quota,
            "failed": self.failed,
            "pending": self.pending,
        }


def select_images(
    images: Sequence[ExtractedImage],
    *,
    min_pixels: int = DEFAULT_MIN_PIXELS,
) -> tuple[list[ExtractedImage], VisionEnrichmentResult]:
    """Chooses which images are worth reading at all.

    Duplicates are collapsed to the first occurrence; the caller fans the
    reading back out to every position the image appeared at.
    """
    result = VisionEnrichmentResult()
    chosen: list[ExtractedImage] = []
    seen: set[str] = set()

    for image in images:
        if image.pixels and image.pixels < min_pixels:
            result.skipped_small += 1
            continue
        if image.content_hash and image.content_hash in seen:
            result.skipped_duplicate += 1
            continue
        seen.add(image.content_hash)
        chosen.append(image)

    return chosen, result


def batch_images(
    images: Sequence[ExtractedImage], size: int
) -> list[list[ExtractedImage]]:
    """Groups images into requests of at most `size`, in document order, so
    that when the quota runs short it is the end of the document that waits."""
    batches: list[list[ExtractedImage]] = []
    current: list[ExtractedImage] = []
    current_bytes = 0
    for image in images:
        if current and (
            len(current) >= size or current_bytes + len(image.data) > _MAX_BATCH_BYTES
        ):
            batches.append(current)
            current, current_bytes = [], 0
        current.append(image)
        current_bytes += len(image.data)
    if current:
        batches.append(current)
    return batches


async def _read(
    images: list[ExtractedImage],
    provider: VisionProvider,
    cache: VisionCache,
    result: VisionEnrichmentResult,
    *,
    batch_size: int,
    concurrency: int,
    on_progress: ProgressCallback | None,
) -> dict[str, str | None]:
    readings: dict[str, str | None] = {}
    gate = asyncio.Semaphore(max(concurrency, 1))
    quota_spent = False
    # A model answering "high demand" to one batch after its retries usually
    # answers the same to the next. Two failures in a row stop the pass rather
    # than each remaining batch waiting out its own round of retries.
    failures_in_a_row = 0
    attempted = 0

    async def read(batch: list[ExtractedImage]) -> None:
        nonlocal quota_spent, failures_in_a_row, attempted
        async with gate:
            try:
                if quota_spent:
                    result.skipped_quota += len(batch)
                    return
                if failures_in_a_row >= _FAILURES_BEFORE_STOPPING:
                    result.failed += len(batch)
                    return
                try:
                    got = await provider.describe(batch)
                except VisionQuotaExhaustedError as exc:
                    if not quota_spent:
                        logger.warning("vision: %s; leaving the rest for later", exc)
                    quota_spent = True
                    result.skipped_quota += len(batch)
                    return
                except VisionUnavailableError as exc:
                    # The ingest continues without these figures, but the count
                    # says plainly that content is missing, not absent.
                    result.failed += len(batch)
                    failures_in_a_row += 1
                    if failures_in_a_row == _FAILURES_BEFORE_STOPPING:
                        logger.warning(
                            "vision: %s; leaving the rest for a later attempt", exc
                        )
                    return

                failures_in_a_row = 0
                result.failed += sum(1 for i in batch if i.content_hash not in got)
                readings.update(got)
                await cache.put_many(provider.cache_key, got)
            finally:
                attempted += len(batch)
                if on_progress is not None:
                    on_progress(attempted, len(images))

    if on_progress is not None:
        on_progress(0, len(images))
    await asyncio.gather(*(read(b) for b in batch_images(images, batch_size)))
    return readings


def reading_blocks(image: ExtractedImage, reading: str) -> list[ParsedBlock]:
    """Turns one reading into blocks at the image's place.

    The model marks titles and headings with "#", "##" and "###". On a page
    rendered whole from a scan those become real headings, because the reading
    is the page: without them every chunk of a scanned document would be cited
    with no section at all. A numbered heading takes its level from its
    number, and a caption marked as a heading stays text.

    On a picture inside a page the document's own typography already sets the
    headings, and a title inside a screenshot taking over the section would
    misfile everything after it -- so there the marks are dropped and the
    words kept as text.
    """
    blocks: list[ParsedBlock] = []
    body: list[str] = []

    def add(text: str, block_type: BlockType, level: int | None = None) -> None:
        blocks.append(
            ParsedBlock(
                text=text,
                type=block_type,
                level=level,
                page_number=image.page_number,
                slide_number=image.slide_number,
                order=image.order,
                # The marker that follows this text all the way to the citation.
                source=ContentSource.VISION,
            )
        )

    def flush() -> None:
        text = "\n".join(body).strip()
        if text:
            add(text, BlockType.FIGURE)
        body.clear()

    for line in reading.splitlines():
        match = _HEADING_LINE.match(line.strip())
        # Models add bold to a heading as often as not.
        title = match.group(2).strip("*_ ") if match else ""
        if match is None or not title:
            body.append(line)
        elif image.whole_page and not _CAPTION.match(title):
            flush()
            numbered = _NUMBERED.match(title)
            level = (
                min(numbered.group(1).count(".") + 1, 6)
                if numbered
                else len(match.group(1))
            )
            add(title, BlockType.HEADING, level)
        else:
            body.append(title)
    flush()
    return blocks


async def enrich_with_vision(
    document: ParsedDocument,
    provider: VisionProvider,
    cache: VisionCache,
    *,
    min_pixels: int = DEFAULT_MIN_PIXELS,
    max_images: int = DEFAULT_MAX_IMAGES,
    batch_size: int = 4,
    concurrency: int = 2,
    read: bool = True,
    on_progress: ProgressCallback | None = None,
) -> VisionEnrichmentResult:
    """Reads the document's images and inserts them as FIGURE blocks.

    With `read=False` only the cache is consulted: images read before become
    figures, and the rest are counted as pending for a later pass. That is
    what lets a document be searchable in seconds while its figures are read
    in the background.

    Mutates `document.blocks` in place, keeping reading order, so a figure
    lands where it sat on the page rather than at the end.
    """
    if not document.images:
        return VisionEnrichmentResult()

    chosen, result = select_images(document.images, min_pixels=min_pixels)
    if not chosen:
        logger.debug(
            "vision: nothing worth reading in %s (%s images)",
            document.filename,
            len(document.images),
        )
        return result

    readings = await cache.get_many(
        provider.cache_key, [image.content_hash for image in chosen]
    )
    result.cached = len(readings)
    unread = [image for image in chosen if image.content_hash not in readings]

    if unread and not read:
        result.pending = len(unread)
    elif unread:
        to_read = unread[:max_images]
        result.skipped_budget = len(unread) - len(to_read)
        readings.update(
            await _read(
                to_read,
                provider,
                cache,
                result,
                batch_size=batch_size,
                concurrency=concurrency,
                on_progress=on_progress,
            )
        )

    result.described = sum(1 for text in readings.values() if text)
    result.no_content = len(readings) - result.described

    new_blocks = [
        block
        for image in document.images
        if (text := readings.get(image.content_hash))
        for block in reading_blocks(image, text)
    ]
    if new_blocks:
        document.blocks.extend(new_blocks)
        # Stable, so the blocks of one reading -- which share its image's
        # place -- keep the order the model read them in.
        document.blocks.sort(key=lambda b: b.order)

    logger.info("vision: %s (%s)", document.filename, result.as_dict())
    return result
