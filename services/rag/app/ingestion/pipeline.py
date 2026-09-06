"""Ingestion pipeline: file in, chunks out.

Selects a parser by document kind and a chunker by name, both through the
registry, so a different combination can be evaluated by changing a string
rather than this code.

Embedding and indexing arrive in Milestone 2. Until then the pipeline stops at
chunking and hands the chunks back to the Node worker, which owns Postgres.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from app.chunking.fixed import FixedWindowChunker
from app.chunking.structural import StructuralChunker
from app.config import Settings
from app.core.models import Chunk, DocumentKind, ParsedDocument
from app.parsing.pdf import PyMuPDFParser
from app.parsing.pptx import PythonPptxParser

logger = logging.getLogger(__name__)


class UnsupportedDocumentError(ValueError):
    """Raised when no registered parser handles the document kind."""


class EmptyDocumentError(ValueError):
    """Raised when a document yields no extractable text.

    Almost always a scanned PDF with no text layer. Worth a distinct error so
    the student can be told to supply a searchable copy rather than being shown
    a generic failure.
    """


def select_parser(kind: DocumentKind):
    for parser in (PyMuPDFParser(), PythonPptxParser()):
        if parser.supports(kind):
            return parser
    raise UnsupportedDocumentError(f"No parser handles {kind.value}")


def build_chunker(name: str, settings: Settings):
    if name == "fixed_window":
        return FixedWindowChunker(
            target_tokens=settings.CHUNK_TARGET_TOKENS,
            overlap_tokens=settings.CHUNK_OVERLAP_TOKENS,
        )
    if name == "structural":
        return StructuralChunker(
            target_tokens=settings.CHUNK_TARGET_TOKENS,
            overlap_tokens=settings.CHUNK_OVERLAP_TOKENS,
            min_tokens=settings.CHUNK_MIN_TOKENS,
        )
    raise ValueError(
        f"Unknown chunker '{name}'. Available: structural, fixed_window"
    )


class IngestionResult:
    def __init__(
        self,
        document: ParsedDocument,
        chunks: list[Chunk],
        chunker_name: str,
        timings: dict[str, int],
    ) -> None:
        self.document = document
        self.chunks = chunks
        self.chunker_name = chunker_name
        self.timings = timings


async def ingest_document(
    path: Path,
    *,
    document_id: str,
    filename: str,
    kind: DocumentKind,
    settings: Settings,
    chunker_name: str = "structural",
) -> IngestionResult:
    if not path.is_file():
        raise FileNotFoundError(f"Document not found at {path}")

    parser = select_parser(kind)

    started = time.perf_counter()
    document = await parser.parse(path, document_id=document_id, filename=filename)
    parse_ms = int((time.perf_counter() - started) * 1000)

    if not document.blocks:
        raise EmptyDocumentError(
            "No extractable text found. If this is a scanned document, it "
            "needs OCR or a searchable copy before it can be studied."
        )

    started = time.perf_counter()
    chunks = build_chunker(chunker_name, settings).chunk(document)
    chunk_ms = int((time.perf_counter() - started) * 1000)

    if not chunks:
        raise EmptyDocumentError(
            "The document was read but produced no chunks. It may contain only "
            "headings or images."
        )

    logger.info(
        "ingested %s: %s pages, %s blocks, %s chunks (parse %sms, chunk %sms)",
        filename,
        document.page_count,
        len(document.blocks),
        len(chunks),
        parse_ms,
        chunk_ms,
    )

    return IngestionResult(
        document=document,
        chunks=chunks,
        chunker_name=chunker_name,
        timings={"parse_ms": parse_ms, "chunk_ms": chunk_ms},
    )
