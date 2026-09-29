"""Turns extracted images into FIGURE blocks.

Runs between parsing and chunking. Parsers stay synchronous and API-free; this
stage owns every vision call, so it can be skipped, swapped or rate-limited
without touching them.

Two filters keep the quota honest, because every image costs a call:

  size      a 40x40 image is a bullet glyph or a logo, never study content
  identity  the same logo on all 60 slides is described once, not 60 times

Both matter more than they look. A deck with a header graphic on every slide
would otherwise spend one call per slide describing the same picture, and on a
15 RPM free tier that is the difference between a four-minute ingest and a
one-hour one.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from app.core.models import (
    BlockType,
    ContentSource,
    ExtractedImage,
    ParsedBlock,
    ParsedDocument,
)
from app.vision.providers import VisionProvider, VisionUnavailableError

logger = logging.getLogger(__name__)

# Below this, an image is decoration. A genuine screenshot of a table or a
# diagram is far larger; icons, bullets and rules are far smaller.
DEFAULT_MIN_PIXELS = 40_000  # e.g. 200x200

# A ceiling on calls per document, so one pathological file cannot exhaust a
# day's quota.
DEFAULT_MAX_IMAGES = 40


class VisionEnrichmentResult:
    def __init__(
        self,
        described: int = 0,
        skipped_small: int = 0,
        skipped_duplicate: int = 0,
        skipped_budget: int = 0,
        no_content: int = 0,
        failed: int = 0,
    ) -> None:
        self.described = described
        self.skipped_small = skipped_small
        self.skipped_duplicate = skipped_duplicate
        self.skipped_budget = skipped_budget
        self.no_content = no_content
        # Kept apart from no_content: a quota failure is not the same finding
        # as an image the model judged decorative.
        self.failed = failed

    def as_dict(self) -> dict[str, int]:
        return {
            "described": self.described,
            "skipped_small": self.skipped_small,
            "skipped_duplicate": self.skipped_duplicate,
            "skipped_budget": self.skipped_budget,
            "no_content": self.no_content,
            "failed": self.failed,
        }


def select_images(
    images: Sequence[ExtractedImage],
    *,
    min_pixels: int = DEFAULT_MIN_PIXELS,
    max_images: int = DEFAULT_MAX_IMAGES,
) -> tuple[list[ExtractedImage], VisionEnrichmentResult]:
    """Chooses which images are worth a vision call.

    Duplicates are collapsed to the first occurrence; the caller is expected to
    fan the resulting description back out to every position the image
    appeared at.
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
        if len(chosen) >= max_images:
            result.skipped_budget += 1
            continue

        seen.add(image.content_hash)
        chosen.append(image)

    return chosen, result


async def enrich_with_vision(
    document: ParsedDocument,
    provider: VisionProvider,
    *,
    min_pixels: int = DEFAULT_MIN_PIXELS,
    max_images: int = DEFAULT_MAX_IMAGES,
) -> VisionEnrichmentResult:
    """Describes the document's images and inserts them as FIGURE blocks.

    Mutates `document.blocks` in place, keeping reading order, so a figure
    lands where it sat on the page rather than at the end.
    """
    if not document.images:
        return VisionEnrichmentResult()

    chosen, result = select_images(
        document.images, min_pixels=min_pixels, max_images=max_images
    )
    if not chosen:
        logger.debug(
            "vision: nothing worth describing in %s (%s images)",
            document.filename,
            len(document.images),
        )
        return result

    # One description per distinct image, reused everywhere it appears.
    descriptions: dict[str, str] = {}
    for image in chosen:
        try:
            text = await provider.describe(
                image.data, mime_type=image.mime_type, context_hint=image.context_hint
            )
        except VisionUnavailableError:
            # The ingest continues without this figure rather than failing, but
            # the count says plainly that content is missing, not absent.
            result.failed += 1
            continue

        if not text:
            result.no_content += 1
            continue
        descriptions[image.content_hash] = text
        result.described += 1

    if not descriptions:
        return result

    new_blocks: list[ParsedBlock] = []
    for image in document.images:
        text = descriptions.get(image.content_hash)
        if not text:
            continue
        new_blocks.append(
            ParsedBlock(
                text=text,
                type=BlockType.FIGURE,
                page_number=image.page_number,
                slide_number=image.slide_number,
                order=image.order,
                # The marker that follows this text all the way to the
                # citation the student sees.
                source=ContentSource.VISION,
            )
        )

    document.blocks.extend(new_blocks)
    document.blocks.sort(key=lambda b: b.order)

    logger.info(
        "vision: described %s image(s) in %s (%s)",
        result.described,
        document.filename,
        result.as_dict(),
    )
    return result
