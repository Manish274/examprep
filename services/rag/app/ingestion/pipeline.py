"""Ingestion pipeline: file in, indexed chunks out.

    parse -> chunk -> embed (dense) -> encode (sparse) -> index

Selects a parser by document kind and a chunker by name, both through the
registry, so a different combination can be evaluated by changing a string
rather than this code.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

from app.chunking.fixed import FixedWindowChunker
from app.chunking.structural import StructuralChunker
from app.config import Settings
from app.core.models import Chunk, DocumentKind, ParsedDocument
from app.ingestion.vision_enrichment import enrich_with_vision
from app.observability.trace import span
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
        indexed: int = 0,
        cache_hits: int = 0,
        vision: dict[str, int] | None = None,
    ) -> None:
        self.document = document
        self.chunks = chunks
        self.chunker_name = chunker_name
        self.timings = timings
        self.indexed = indexed
        self.cache_hits = cache_hits
        self.vision = vision or {}


async def parse_and_chunk(
    path: Path,
    *,
    document_id: str,
    filename: str,
    kind: DocumentKind,
    settings: Settings,
    chunker_name: str = "structural",
    vision: object | None = None,
) -> IngestionResult:
    """Parse, optionally read the images, then chunk."""
    if not path.is_file():
        raise FileNotFoundError(f"Document not found at {path}")

    parser = select_parser(kind)

    started = time.perf_counter()
    async with span("parse", filename=filename, kind=kind.value) as observed:
        document = await parser.parse(
            path, document_id=document_id, filename=filename
        )
        observed.output(
            pages=document.page_count,
            blocks=len(document.blocks),
            images=len(document.images),
        )
        observed.meta(parser=parser.name)
    parse_ms = int((time.perf_counter() - started) * 1000)

    # Images are read before the empty-document check, because a scanned PDF
    # has no text at all and the vision pass is exactly what rescues it.
    vision_stats: dict[str, int] = {}
    vision_ms = 0
    if vision is not None and document.images:
        started = time.perf_counter()
        async with span("vision", candidates=len(document.images)) as observed:
            result = await enrich_with_vision(
                document,
                vision,
                min_pixels=settings.VISION_MIN_PIXELS,
                max_images=settings.VISION_MAX_IMAGES,
            )
            vision_stats = result.as_dict()
            observed.output(**vision_stats)
        vision_ms = int((time.perf_counter() - started) * 1000)

    if not document.blocks:
        raise EmptyDocumentError(
            "No extractable text found. If this is a scanned document, enable "
            "the vision provider so its pages can be read as images."
        )

    started = time.perf_counter()
    async with span(
        "chunk", chunker=chunker_name, blocks=len(document.blocks)
    ) as observed:
        chunks = build_chunker(chunker_name, settings).chunk(document)
        observed.output(
            chunks=len(chunks),
            tokens=sum(c.token_count for c in chunks),
            median_tokens=(
                sorted(c.token_count for c in chunks)[len(chunks) // 2]
                if chunks
                else 0
            ),
        )
    chunk_ms = int((time.perf_counter() - started) * 1000)

    if not chunks:
        raise EmptyDocumentError(
            "The document was read but produced no chunks. It may contain only "
            "headings or images."
        )

    logger.info(
        "parsed %s: %s pages, %s blocks, %s chunks (parse %sms, chunk %sms)",
        filename,
        document.page_count,
        len(document.blocks),
        len(chunks),
        parse_ms,
        chunk_ms,
    )

    timings = {"parse_ms": parse_ms, "chunk_ms": chunk_ms}
    if vision_ms:
        timings["vision_ms"] = vision_ms

    return IngestionResult(
        document=document,
        chunks=chunks,
        chunker_name=chunker_name,
        timings=timings,
        vision=vision_stats,
    )


async def embed_and_index(
    result: IngestionResult,
    *,
    user_id: str,
    embedder: object,
    sparse_encoder: object,
    store: object,
    on_progress: Callable[[int, int], None] | None = None,
) -> IngestionResult:
    """Embeds the chunks and writes them to the vector store.

    Dense and sparse vectors are produced from `embedding_text()`, not from the
    raw chunk body. That prefixes the document name and heading trail, so a
    chunk reading "It must also satisfy 2NF" still carries its subject -- and
    on the sparse side it means a search for "Third Normal Form" matches the
    section actually titled that.
    """
    chunks = result.chunks
    texts = [chunk.embedding_text() for chunk in chunks]

    started = time.perf_counter()
    async with span("embed", chunks=len(texts)) as observed:
        vectors = await embedder.embed_documents(texts)  # type: ignore[attr-defined]
        observed.output(vectors=len(vectors))
        # Cache hits are the difference between a re-ingest costing a quota
        # day and costing nothing, so they belong in the trace.
        observed.meta(
            cache_hits=getattr(embedder, "hits", 0),
            cache_misses=getattr(embedder, "misses", 0),
        )
    embed_ms = int((time.perf_counter() - started) * 1000)

    if len(vectors) != len(chunks):
        raise RuntimeError(
            f"embedder returned {len(vectors)} vectors for {len(chunks)} chunks"
        )

    started = time.perf_counter()
    sparse = [sparse_encoder.encode_document(text) for text in texts]  # type: ignore[attr-defined]
    sparse_ms = int((time.perf_counter() - started) * 1000)

    if on_progress:
        on_progress(len(chunks), len(chunks))

    started = time.perf_counter()
    async with span("index", chunks=len(chunks)) as observed:
        # Replace rather than append: a re-ingest must not leave the previous
        # generation of chunks in the index alongside the new one.
        await store.delete_document(  # type: ignore[attr-defined]
            result.document.document_id, user_id=user_id
        )
        indexed = await store.upsert_chunks(  # type: ignore[attr-defined]
            chunks,
            [v.values for v in vectors],
            sparse,
            user_id=user_id,
        )
        observed.output(indexed=indexed)
    index_ms = int((time.perf_counter() - started) * 1000)

    result.timings.update(
        {"embed_ms": embed_ms, "sparse_ms": sparse_ms, "index_ms": index_ms}
    )
    result.indexed = indexed
    result.cache_hits = int(getattr(embedder, "hits", 0))

    logger.info(
        "indexed %s chunks for %s (embed %sms, sparse %sms, index %sms)",
        indexed,
        result.document.filename,
        embed_ms,
        sparse_ms,
        index_ms,
    )
    return result


async def ingest_document(
    path: Path,
    *,
    document_id: str,
    filename: str,
    kind: DocumentKind,
    settings: Settings,
    chunker_name: str = "structural",
    user_id: str | None = None,
    embedder: object | None = None,
    sparse_encoder: object | None = None,
    store: object | None = None,
    vision: object | None = None,
) -> IngestionResult:
    """Full pipeline. Stops after chunking when no index target is supplied,
    which is what the preview endpoint and the chunking tests want."""
    result = await parse_and_chunk(
        path,
        document_id=document_id,
        filename=filename,
        kind=kind,
        settings=settings,
        chunker_name=chunker_name,
        vision=vision,
    )

    if user_id and embedder and sparse_encoder and store:
        result = await embed_and_index(
            result,
            user_id=user_id,
            embedder=embedder,
            sparse_encoder=sparse_encoder,
            store=store,
        )

    return result
