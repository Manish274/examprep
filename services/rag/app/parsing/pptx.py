"""PowerPoint parsing via python-pptx.

Unlike a PDF, a deck carries real structure: slides are explicit units, titles
are declared placeholders, and list nesting is recorded per paragraph. So this
parser reads structure rather than inferring it.

Speaker notes are extracted as first-class blocks. Lecture slides are usually
terse -- the sentence that actually explains the bullet often lives only in the
notes, and dropping it would lose the most useful text in the file.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.shapes.picture import Picture

from app.core.models import (
    BlockType,
    DocumentKind,
    ExtractedImage,
    ParsedBlock,
    ParsedDocument,
)
from app.core.registry import parsers
from app.core.text import content_hash, normalize

logger = logging.getLogger(__name__)

# Placeholder type ids that hold a slide title.
_TITLE_PLACEHOLDERS = {0, 13}  # TITLE, CENTER_TITLE


def _is_title(shape: Any) -> bool:
    try:
        if not shape.is_placeholder:
            return False
        return int(shape.placeholder_format.idx) == 0 or (
            int(shape.placeholder_format.type) in _TITLE_PLACEHOLDERS
        )
    except (AttributeError, ValueError, TypeError):
        return False


def _table_to_text(table: Any) -> str:
    rows: list[str] = []
    for row in table.rows:
        cells = [normalize(cell.text).replace("\n", " ") for cell in row.cells]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _shape_sort_key(shape: Any) -> tuple[int, int]:
    """Reading order: top to bottom, then left to right.

    Shape order in the XML reflects the order they were added in the editor,
    which frequently is not the order a reader sees them in.
    """
    top = getattr(shape, "top", None)
    left = getattr(shape, "left", None)
    return (int(top) if top is not None else 0, int(left) if left is not None else 0)


def _reading_order(shapes: Any) -> list[Any]:
    """Every leaf shape, in reading order, with groups opened in place.

    A group is how a hand-drawn diagram usually arrives: boxes, labels and
    pictures bundled so they move together. Skipping the group skips all of it,
    so its members are read where the group sits on the slide.
    """
    ordered: list[Any] = []
    for shape in sorted(shapes, key=_shape_sort_key):
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            ordered.extend(_reading_order(shape.shapes))
        else:
            ordered.append(shape)
    return ordered


def _extract_body_blocks(
    shape: Any, slide_number: int, order_start: int
) -> tuple[list[ParsedBlock], int]:
    """Turns one text-bearing shape into blocks, preserving list nesting."""
    order = order_start
    blocks: list[ParsedBlock] = []

    for paragraph in shape.text_frame.paragraphs:
        text = normalize("".join(run.text for run in paragraph.runs))
        if not text:
            continue

        # python-pptx reports nesting depth as level 0..8; anything indented is
        # a list item, and depth is preserved so the chunker can keep an
        # outline together.
        depth = int(getattr(paragraph, "level", 0) or 0)
        blocks.append(
            ParsedBlock(
                text=text,
                type=BlockType.LIST_ITEM if depth > 0 else BlockType.PARAGRAPH,
                slide_number=slide_number,
                level=depth + 2 if depth > 0 else None,
                order=order,
            )
        )
        order += 1

    return blocks, order


class PythonPptxParser:
    name = "python-pptx"

    def supports(self, kind: DocumentKind) -> bool:
        return kind in {DocumentKind.PPTX, DocumentKind.PPT}

    @staticmethod
    def _extract_image(
        shape: Any, slide_number: int, order: int
    ) -> ExtractedImage | None:
        """Pulls the picture bytes and its natural size.

        Natural pixel size is used rather than the size it was scaled to on the
        slide: a large diagram shrunk into a corner is still a large diagram,
        and a tiny logo stretched across a slide is still a logo.
        """
        try:
            image = shape.image
        except (AttributeError, ValueError):
            return None

        width, height = 0, 0
        with contextlib.suppress(AttributeError, ValueError, TypeError):
            width, height = image.size

        blob = image.blob
        return ExtractedImage(
            data=blob,
            mime_type=f"image/{image.ext}" if image.ext else "image/png",
            order=order,
            slide_number=slide_number,
            width=width,
            height=height,
            content_hash=content_hash(blob.hex()),
        )

    def _parse_sync(
        self, path: Path, document_id: str, filename: str
    ) -> ParsedDocument:
        presentation = Presentation(str(path))
        blocks: list[ParsedBlock] = []
        images: list[ExtractedImage] = []
        order = 0
        notes_count = 0
        table_count = 0

        for index, slide in enumerate(presentation.slides):
            slide_number = index + 1

            title_shapes = [s for s in slide.shapes if _is_title(s)]
            body_shapes = [s for s in slide.shapes if not _is_title(s)]

            # The slide title becomes a level-1 heading, which makes each slide
            # a natural section boundary for the chunker.
            title_text = ""
            for shape in title_shapes:
                if shape.has_text_frame:
                    title_text = normalize(shape.text_frame.text)
                    break

            blocks.append(
                ParsedBlock(
                    text=title_text or f"Slide {slide_number}",
                    type=BlockType.HEADING,
                    slide_number=slide_number,
                    level=1,
                    order=order,
                )
            )
            order += 1

            for shape in _reading_order(body_shapes):
                try:
                    if shape.has_table:
                        text = normalize(_table_to_text(shape.table))
                        if text:
                            blocks.append(
                                ParsedBlock(
                                    text=text,
                                    type=BlockType.TABLE,
                                    slide_number=slide_number,
                                    order=order,
                                )
                            )
                            order += 1
                            table_count += 1
                        continue

                    # By class, not shape type: a picture inserted through a
                    # content placeholder -- PowerPoint's default -- reports
                    # itself as PLACEHOLDER, and a type check skips it.
                    if isinstance(shape, Picture):
                        # Held aside for the vision stage; parsers stay free of
                        # API calls.
                        extracted = self._extract_image(shape, slide_number, order)
                        if extracted is not None:
                            images.append(extracted)
                            order += 1
                        continue

                    if shape.has_text_frame:
                        new_blocks, order = _extract_body_blocks(
                            shape, slide_number, order
                        )
                        blocks.extend(new_blocks)
                except (AttributeError, ValueError) as exc:
                    logger.debug(
                        "skipped shape on slide %s: %s", slide_number, exc
                    )

            if slide.has_notes_slide:
                notes = normalize(slide.notes_slide.notes_text_frame.text)
                if notes:
                    blocks.append(
                        ParsedBlock(
                            text=notes,
                            type=BlockType.SPEAKER_NOTE,
                            slide_number=slide_number,
                            order=order,
                        )
                    )
                    order += 1
                    notes_count += 1

        # Give each image the slide's own text, so the vision model can tell a
        # decorative graphic from the diagram the slide is explaining.
        by_slide: dict[int | None, list[str]] = {}
        for block in blocks:
            by_slide.setdefault(block.slide_number, []).append(block.text)
        for image in images:
            image.context_hint = " ".join(by_slide.get(image.slide_number, []))[:400]

        slide_count = len(presentation.slides)
        return ParsedDocument(
            document_id=document_id,
            filename=filename,
            kind=DocumentKind.PPTX,
            blocks=blocks,
            images=images,
            page_count=slide_count,
            parser=self.name,
            metadata={
                "slide_count": slide_count,
                "notes_count": notes_count,
                "table_count": table_count,
                "image_count": len(images),
            },
        )

    async def parse(
        self, path: Path, *, document_id: str, filename: str
    ) -> ParsedDocument:
        return await asyncio.to_thread(self._parse_sync, path, document_id, filename)


@parsers.register("python-pptx")
def _create_pptx_parser() -> PythonPptxParser:
    return PythonPptxParser()
