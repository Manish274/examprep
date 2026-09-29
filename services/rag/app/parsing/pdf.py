"""PDF parsing via PyMuPDF.

PyMuPDF is used rather than a text-only extractor because chunk quality depends
on typography and geometry: font size drives heading detection, and bounding
boxes let table regions be excluded from the prose stream so their contents are
not extracted twice.
"""

from __future__ import annotations

import asyncio
import logging
from itertools import pairwise
from pathlib import Path

import fitz  # PyMuPDF

from app.core.models import (
    BlockType,
    DocumentKind,
    ExtractedImage,
    ParsedBlock,
    ParsedDocument,
)
from app.core.text import content_hash, normalize
from app.parsing.structure import LineRecord, detect_heading_levels

logger = logging.getLogger(__name__)

# PyMuPDF span flag bit for bold. Italic is bit 1, serif bit 2, bold bit 4.
_BOLD_FLAG = 1 << 4

# Fraction of a line area that must sit inside a table for the line to be
# treated as part of that table rather than as prose.
_TABLE_OVERLAP = 0.5

# How far above this document's measured baseline gap a line must sit, as a
# fraction of font size, to count as starting a new paragraph rather than
# continuing the current one.
_PARAGRAPH_GAP_RATIO = 0.35

# A page yielding less than this much text is treated as scanned rather than
# sparse, and rendered for the vision model. A genuine text page with a single
# heading still clears it comfortably.
_SCANNED_PAGE_CHARS = 60

# Rendering resolution for a scanned page. 150 DPI is legible to a vision model
# without producing an image too large to send.
_RENDER_DPI = 150


def _extract_page_images(
    page: fitz.Page, doc: fitz.Document, page_number: int, order: int
) -> list[ExtractedImage]:
    """Embedded raster images on one page.

    Vector drawings are skipped: a chart drawn with line primitives is already
    text-searchable for its labels, and rasterising every diagram would spend a
    vision call on decoration.
    """
    found: list[ExtractedImage] = []
    for info in page.get_images(full=True):
        xref = info[0]
        try:
            extracted = doc.extract_image(xref)
        except Exception as exc:
            logger.debug(
                "could not extract image %s on page %s: %s", xref, page_number, exc
            )
            continue

        blob = extracted.get("image")
        if not blob:
            continue
        found.append(
            ExtractedImage(
                data=blob,
                mime_type=f"image/{extracted.get('ext', 'png')}",
                order=order,
                page_number=page_number,
                width=int(extracted.get("width") or 0),
                height=int(extracted.get("height") or 0),
                content_hash=content_hash(blob.hex()),
            )
        )
        order += 1
    return found


def _render_page(
    page: fitz.Page, page_number: int, order: int
) -> ExtractedImage | None:
    """Renders a whole page as an image.

    Used for scanned pages, where the content is not embedded images but the
    page itself. Without this a scanned PDF is simply unusable, which is the
    single most common complaint about document RAG.
    """
    try:
        pixmap = page.get_pixmap(dpi=_RENDER_DPI)
        blob = pixmap.tobytes("png")
    except Exception as exc:
        logger.debug("could not render page %s: %s", page_number, exc)
        return None

    return ExtractedImage(
        data=blob,
        mime_type="image/png",
        order=order,
        page_number=page_number,
        width=pixmap.width,
        height=pixmap.height,
        content_hash=content_hash(blob.hex()),
    )


def _rect_overlap_ratio(
    line: tuple[float, float, float, float],
    table: tuple[float, float, float, float],
) -> float:
    lx0, ly0, lx1, ly1 = line
    tx0, ty0, tx1, ty1 = table
    ix0, iy0 = max(lx0, tx0), max(ly0, ty0)
    ix1, iy1 = min(lx1, tx1), min(ly1, ty1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    intersection = (ix1 - ix0) * (iy1 - iy0)
    line_area = max((lx1 - lx0) * (ly1 - ly0), 1e-6)
    return intersection / line_area


def _table_to_text(rows: list[list[str | None]]) -> str:
    """Renders a table as pipe-delimited rows.

    Markdown-ish layout keeps the row and column relationship legible to both
    the embedding model and the student reading the citation, without pulling
    in a formatting dependency.
    """
    lines: list[str] = []
    for row in rows:
        cells = [(cell or "").strip().replace("\n", " ") for cell in row]
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def _extract_page(
    page: fitz.Page, page_number: int, order_start: int
) -> tuple[list[LineRecord], list[ParsedBlock], int]:
    """Returns prose lines and table blocks for one page.

    Tables are pulled first so their regions can be masked out of the prose
    stream; otherwise every cell appears twice in the chunked output.
    """
    table_blocks: list[ParsedBlock] = []
    table_rects: list[tuple[float, float, float, float]] = []

    try:
        for table in page.find_tables().tables:
            rows = table.extract()
            text = normalize(_table_to_text(rows))
            rect = (
                float(table.bbox[0]),
                float(table.bbox[1]),
                float(table.bbox[2]),
                float(table.bbox[3]),
            )
            table_rects.append(rect)
            if text:
                table_blocks.append(
                    ParsedBlock(
                        text=text,
                        type=BlockType.TABLE,
                        page_number=page_number,
                        bbox=rect,
                        order=0,  # assigned below, once reading order is known
                    )
                )
    except Exception as exc:
        logger.debug("table extraction failed on page %s: %s", page_number, exc)

    lines: list[LineRecord] = []
    text_page = page.get_text("dict")
    for block in text_page.get("blocks", []):
        if block.get("type") != 0:  # 0 is text; 1 is an image
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            text = normalize("".join(span.get("text", "") for span in spans))
            if not text:
                continue

            raw_bbox = line.get("bbox", (0.0, 0.0, 0.0, 0.0))
            bbox = (
                float(raw_bbox[0]),
                float(raw_bbox[1]),
                float(raw_bbox[2]),
                float(raw_bbox[3]),
            )
            if any(
                _rect_overlap_ratio(bbox, rect) >= _TABLE_OVERLAP
                for rect in table_rects
            ):
                continue

            # Attribute the line to its largest span: a heading with a trailing
            # footnote marker should still be sized as a heading.
            dominant = max(spans, key=lambda s: float(s.get("size", 0.0)))
            lines.append(
                LineRecord(
                    text=text,
                    font_size=float(dominant.get("size", 0.0)),
                    is_bold=bool(int(dominant.get("flags", 0)) & _BOLD_FLAG),
                    page_number=page_number,
                    bbox=bbox,
                    order=0,  # assigned below
                )
            )

    # Order is assigned across lines and tables together, by position on the
    # page. Numbering tables first would place a table at the foot of a page
    # ahead of the prose that introduces it, and the chunker trusts order as
    # reading order.
    positioned: list[tuple[tuple[float, float], LineRecord | ParsedBlock]] = [
        *(((line.bbox[1], line.bbox[0]), line) for line in lines),
        *(
            ((block.bbox[1], block.bbox[0]), block)
            for block in table_blocks
            if block.bbox is not None
        ),
    ]
    positioned.sort(key=lambda item: item[0])

    order = order_start
    for _, item in positioned:
        item.order = order
        order += 1

    return lines, table_blocks, order


def _median_line_gap(lines: list[LineRecord]) -> float:
    """The document's normal inter-line gap.

    Measured rather than assumed, because it depends on the leading the author
    chose and on how the font's glyph boxes sit relative to the baseline --
    tightly-set text routinely yields slightly negative gaps.
    """
    gaps: list[float] = []
    for previous, current in pairwise(lines):
        if previous.page_number != current.page_number:
            continue
        gaps.append(current.bbox[1] - previous.bbox[3])

    if not gaps:
        return 0.0
    gaps.sort()
    return gaps[len(gaps) // 2]


def _starts_new_paragraph(
    previous: LineRecord, line: LineRecord, baseline_gap: float
) -> bool:
    """Whether this line begins a new paragraph rather than continuing one."""
    # A page break always ends the paragraph: continuing across it would
    # attach the wrong page number to the merged text.
    if previous.page_number != line.page_number:
        return True

    # A vertical gap materially wider than this document's normal leading is a
    # paragraph break. PDFs record no paragraph markers, so spacing is the only
    # signal available -- and paragraph edges make good chunk edges.
    threshold = baseline_gap + max(2.0, _PARAGRAPH_GAP_RATIO * previous.font_size)
    return line.bbox[1] - previous.bbox[3] > threshold


def _merge_paragraph_lines(
    lines: list[LineRecord], levels: dict[int, int]
) -> list[ParsedBlock]:
    """Joins consecutive body lines into paragraphs.

    PDF text arrives one visual line at a time. Left as-is, every line becomes
    its own block and the chunker loses any sense of a paragraph, so
    consecutive non-heading lines on the same page are merged.
    """
    blocks: list[ParsedBlock] = []
    buffer: list[LineRecord] = []
    baseline_gap = _median_line_gap(lines)

    def flush() -> None:
        if not buffer:
            return
        text = normalize(" ".join(line.text for line in buffer))
        if text:
            first = buffer[0]
            last = buffer[-1]
            blocks.append(
                ParsedBlock(
                    text=text,
                    type=BlockType.PARAGRAPH,
                    page_number=first.page_number,
                    font_size=first.font_size,
                    bbox=(
                        min(b.bbox[0] for b in buffer),
                        first.bbox[1],
                        max(b.bbox[2] for b in buffer),
                        last.bbox[3],
                    ),
                    order=first.order,
                )
            )
        buffer.clear()

    for line in lines:
        level = levels.get(line.order)
        if level is not None:
            flush()
            blocks.append(
                ParsedBlock(
                    text=line.text,
                    type=BlockType.HEADING,
                    page_number=line.page_number,
                    level=level,
                    font_size=line.font_size,
                    bbox=line.bbox,
                    order=line.order,
                )
            )
            continue

        if buffer and _starts_new_paragraph(buffer[-1], line, baseline_gap):
            flush()

        buffer.append(line)

    flush()
    return blocks


class PyMuPDFParser:
    name = "pymupdf"

    def supports(self, kind: DocumentKind) -> bool:
        return kind == DocumentKind.PDF

    def _parse_sync(
        self, path: Path, document_id: str, filename: str
    ) -> ParsedDocument:
        all_lines: list[LineRecord] = []
        table_blocks: list[ParsedBlock] = []
        images: list[ExtractedImage] = []
        order = 0

        with fitz.open(path) as doc:
            page_count = doc.page_count
            metadata = dict(doc.metadata or {})
            for index in range(page_count):
                page = doc[index]
                page_number = index + 1
                lines, tables, order = _extract_page(page, page_number, order)
                all_lines.extend(lines)
                table_blocks.extend(tables)

                page_text = sum(len(line.text) for line in lines)
                if page_text < _SCANNED_PAGE_CHARS:
                    # Nothing readable here. Render the page itself rather than
                    # hunting for embedded images that may not exist.
                    rendered = _render_page(page, page_number, order)
                    if rendered is not None:
                        images.append(rendered)
                        order += 1
                else:
                    found = _extract_page_images(page, doc, page_number, order)
                    images.extend(found)
                    order += len(found)

        # Heading levels are decided across the whole document, not per page,
        # so a given size means the same thing on page 1 and on page 90.
        levels = detect_heading_levels(all_lines)
        blocks = _merge_paragraph_lines(all_lines, levels)
        blocks.extend(table_blocks)
        blocks.sort(key=lambda b: b.order)

        return ParsedDocument(
            document_id=document_id,
            filename=filename,
            kind=DocumentKind.PDF,
            blocks=blocks,
            images=images,
            page_count=page_count,
            parser=self.name,
            metadata={
                "title": metadata.get("title") or None,
                "author": metadata.get("author") or None,
                "heading_count": sum(1 for b in blocks if b.type == BlockType.HEADING),
                "table_count": len(table_blocks),
                "image_count": len(images),
            },
        )

    async def parse(
        self, path: Path, *, document_id: str, filename: str
    ) -> ParsedDocument:
        # PyMuPDF is synchronous and CPU-bound; keep it off the event loop.
        return await asyncio.to_thread(self._parse_sync, path, document_id, filename)
