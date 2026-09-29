"""Ingestion pipeline: file in, indexed chunks out.

    parse -> read images -> chunk -> embed (dense) -> encode (sparse) -> index

Selects a parser by document kind and a chunker by name, so an evaluation can
compare chunking strategies by changing a string rather than this code.

Reading images is by far the slowest stage and the only one that can run out
of quota, so it can be deferred: a pass with `read_figures=False` indexes the
text (and any figures already in the vision cache) in seconds, and reports how
many images are still unread. A second, full pass later adds them. Because
chunk ids are derived from chunk text and embeddings are cached, that second
pass re-embeds only the chunks the figures actually changed.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from app.chunking.fixed import FixedWindowChunker
from app.chunking.structural import StructuralChunker
from app.config import Settings
from app.core.interfaces import EmbeddingProvider, SparseEncoder
from app.core.models import Chunk, DocumentKind, ParsedDocument
from app.ingestion.vision_enrichment import ProgressCallback, enrich_with_vision
from app.observability.trace import span
from app.parsing.pdf import PyMuPDFParser
from app.parsing.pptx import PythonPptxParser
from app.retrieval.qdrant_store import QdrantStore
from app.vision.cache import VisionCache
from app.vision.providers import VisionProvider

logger = logging.getLogger(__name__)


class UnsupportedDocumentError(ValueError):
    """Raised when no parser handles the document kind."""


class EmptyDocumentError(ValueError):
    """Raised when a document yields no extractable text.

    Almost always a scanned PDF with no text layer. Worth a distinct error so
    the student can be told to supply a searchable copy rather than being shown
    a generic failure.
    """


def select_parser(kind: DocumentKind) -> PyMuPDFParser | PythonPptxParser:
    for parser in (PyMuPDFParser(), PythonPptxParser()):
        if parser.supports(kind):
            return parser
    raise UnsupportedDocumentError(f"No parser handles {kind.value}")


def build_chunker(
    name: str, settings: Settings
) -> FixedWindowChunker | StructuralChunker:
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
    raise ValueError(f"Unknown chunker '{name}'. Available: structural, fixed_window")


def deduplicate(chunks: list[Chunk]) -> tuple[list[Chunk], list[str]]:
    """Drops chunks whose text is byte-identical to an earlier one.

    Chunk ids are `uuid5(document_id, sha256(text))`, which is what makes them
    reproducible across re-ingests -- and what makes two identical chunks in one
    document collide on the primary key, failing the whole upload.

    Lecture decks produce this constantly: a title slide repeated between
    sections, or an animation built up over five slides where each adds a
    picture and the extracted text never changes. Those five chunks embed
    identically and score identically, so all four extra copies can do is crowd
    real material out of the top K. Keeping the first occurrence is both the fix
    for the collision and the better index.

    The first is kept rather than the last, so the retained chunk carries the
    earliest position in the document -- where a reader would meet it.
    """
    seen: dict[str, Chunk] = {}
    kept: list[Chunk] = []
    dropped: list[str] = []

    for chunk in chunks:
        first = seen.get(chunk.id)
        if first is None:
            seen[chunk.id] = chunk
            kept.append(chunk)
            continue
        # Recorded rather than discarded silently: a document losing a fifth of
        # its chunks is worth being able to see.
        dropped.append(
            f"chunk {chunk.metadata.chunk_index} duplicates "
            f"{first.metadata.chunk_index}"
        )

    return kept, dropped


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
        duplicates: list[str] | None = None,
    ) -> None:
        self.document = document
        self.chunks = chunks
        self.chunker_name = chunker_name
        self.timings = timings
        self.indexed = indexed
        self.cache_hits = cache_hits
        self.vision = vision or {}
        self.duplicates = duplicates or []

    @property
    def figures_pending(self) -> int:
        """Images left unread by a pass that deferred them."""
        return self.vision.get("pending", 0)


async def parse_and_chunk(
    path: Path,
    *,
    document_id: str,
    filename: str,
    kind: DocumentKind,
    settings: Settings,
    chunker_name: str = "structural",
    vision: VisionProvider | None = None,
    vision_cache: VisionCache | None = None,
    read_figures: bool = True,
    on_progress: ProgressCallback | None = None,
) -> IngestionResult:
    """Parse, read the images (or only the cached ones), then chunk."""
    if not path.is_file():
        raise FileNotFoundError(f"Document not found at {path}")

    parser = select_parser(kind)

    started = time.perf_counter()
    async with span("parse", filename=filename, kind=kind.value) as observed:
        document = await parser.parse(path, document_id=document_id, filename=filename)
        observed.output(
            pages=document.page_count,
            blocks=len(document.blocks),
            images=len(document.images),
        )
        observed.meta(parser=parser.name)
    parse_ms = int((time.perf_counter() - started) * 1000)

    # Images are read before the empty-document check, because a scanned PDF
    # has no text at all and the vision pass is exactly what rescues it -- and
    # for the same reason a document with no text is never deferred: there
    # would be nothing to search in the meantime.
    vision_stats: dict[str, int] = {}
    vision_ms = 0
    if vision is not None and vision_cache is not None and document.images:
        read = read_figures or not document.blocks
        started = time.perf_counter()
        async with span(
            "vision", candidates=len(document.images), read=read
        ) as observed:
            result = await enrich_with_vision(
                document,
                vision,
                vision_cache,
                min_pixels=settings.VISION_MIN_PIXELS,
                max_images=settings.VISION_MAX_IMAGES,
                batch_size=settings.VISION_BATCH_SIZE,
                concurrency=settings.VISION_CONCURRENCY,
                read=read,
                on_progress=on_progress,
            )
            vision_stats = result.as_dict()
            observed.output(**vision_stats)
        vision_ms = int((time.perf_counter() - started) * 1000)

    if not document.blocks:
        if vision_stats.get("skipped_quota") or vision_stats.get("failed"):
            raise EmptyDocumentError(
                "This document's pages are images, and they could not be read "
                "right now -- the free daily quota for reading images may be "
                "used up. Try uploading it again later."
            )
        raise EmptyDocumentError(
            "No extractable text found. If this is a scanned document, enable "
            "the vision provider so its pages can be read as images."
        )

    started = time.perf_counter()
    async with span(
        "chunk", chunker=chunker_name, blocks=len(document.blocks)
    ) as observed:
        chunks = build_chunker(chunker_name, settings).chunk(document)
        chunks, duplicates = deduplicate(chunks)
        observed.output(
            chunks=len(chunks),
            duplicates_dropped=len(duplicates),
            tokens=sum(c.token_count for c in chunks),
            median_tokens=(
                sorted(c.token_count for c in chunks)[len(chunks) // 2] if chunks else 0
            ),
        )
    chunk_ms = int((time.perf_counter() - started) * 1000)

    if duplicates:
        logger.info(
            "%s: dropped %s duplicate chunks (%s)",
            filename,
            len(duplicates),
            "; ".join(duplicates[:5]),
        )

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
        duplicates=duplicates,
    )


async def embed_and_index(
    result: IngestionResult,
    *,
    user_id: str,
    embedder: EmbeddingProvider,
    sparse_encoder: SparseEncoder,
    store: QdrantStore,
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
        vectors = await embedder.embed_documents(texts)
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
    sparse = [sparse_encoder.encode_document(text) for text in texts]
    sparse_ms = int((time.perf_counter() - started) * 1000)

    started = time.perf_counter()
    async with span("index", chunks=len(chunks)) as observed:
        # Replace rather than append: a re-ingest must not leave the previous
        # generation of chunks in the index alongside the new one. Written
        # first and pruned after, because a document is searchable while its
        # figures are added -- deleting first would leave it empty for the
        # length of the upsert.
        indexed = await store.upsert_chunks(
            chunks,
            [v.values for v in vectors],
            sparse,
            user_id=user_id,
        )
        await store.delete_document(
            result.document.document_id,
            user_id=user_id,
            keep=[chunk.id for chunk in chunks],
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
    embedder: EmbeddingProvider | None = None,
    sparse_encoder: SparseEncoder | None = None,
    store: QdrantStore | None = None,
    vision: VisionProvider | None = None,
    vision_cache: VisionCache | None = None,
    read_figures: bool = True,
    on_progress: ProgressCallback | None = None,
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
        vision_cache=vision_cache,
        read_figures=read_figures,
        on_progress=on_progress,
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
