"""Ingestion endpoint.

Called by the Node worker, never by a browser. The worker supplies a storage
key; this service reads the file, parses it, chunks it, and returns the chunks
with their metadata intact. Persisting them is the worker's job -- Postgres
belongs to the Node side.

A document is usually ingested twice: once with `figures="defer"`, which makes
its text searchable in seconds, and then, if that pass left images unread, with
`figures="read"` to add them.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import CorrelationId, InternalAuth, SettingsDep
from app.container import get_container
from app.core.models import Chunk, DocumentKind
from app.embedding.rate_limit import QuotaExhaustedError
from app.ingestion import inflight
from app.ingestion.pipeline import (
    EmptyDocumentError,
    UnsupportedDocumentError,
    ingest_document,
)
from app.observability.trace import identify
from app.storage.local import LocalFilesystemStorage

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ingestion"], dependencies=[InternalAuth])


class IngestRequest(BaseModel):
    document_id: str
    # Opaque here: this service never learns who a user is, only that vectors
    # carrying a different id must never be returned to them.
    user_id: str
    filename: str
    kind: DocumentKind
    # Opaque to this service; resolved by the configured StorageProvider.
    storage_key: str
    # Named rather than inlined so an evaluation run can compare strategies
    # without a deploy.
    chunker: str = Field(default="structural")
    # "defer" indexes the text now and leaves unread images for a later "read"
    # pass, reporting how many in `figures_pending`. A document with no text
    # at all -- a scanned one -- is read in full either way.
    figures: Literal["defer", "read"] = "read"


class ChunkResponse(BaseModel):
    """A chunk as it crosses the service boundary.

    Flattened deliberately: the Node worker writes these straight into the
    document_chunks table, and a nested metadata object would mean unwrapping
    on every insert.
    """

    id: str
    text: str
    token_count: int
    chunk_index: int
    page_number: int | None
    page_end: int | None
    slide_number: int | None
    section: str | None
    heading: str | None
    heading_path: list[str]
    char_start: int | None
    char_end: int | None
    content_hash: str
    # "text" or "vision" -- generated content is marked all the way to
    # the citation the student sees.
    source: str

    @classmethod
    def from_chunk(cls, chunk: Chunk) -> ChunkResponse:
        meta = chunk.metadata
        return cls(
            id=chunk.id,
            text=chunk.text,
            token_count=chunk.token_count,
            chunk_index=meta.chunk_index,
            page_number=meta.page_number,
            page_end=meta.page_end,
            slide_number=meta.slide_number,
            section=meta.section,
            heading=meta.heading,
            heading_path=meta.heading_path,
            char_start=meta.char_start,
            char_end=meta.char_end,
            content_hash=meta.content_hash,
            source=meta.source.value,
        )


class IngestResponse(BaseModel):
    document_id: str
    page_count: int
    block_count: int
    chunk_count: int
    parser: str
    chunker: str
    indexed: int
    cache_hits: int
    vision: dict[str, int]
    # Images still unread: left by a "defer" pass, or tried and not read (an
    # overloaded model, a spent quota). Zero means the document is complete.
    figures_pending: int
    # Chunks whose text was byte-identical to an earlier one and were dropped.
    # A high count on a deck usually means repeated title or build-up slides.
    duplicates_dropped: int
    timings: dict[str, int]
    metadata: dict[str, Any]
    chunks: list[ChunkResponse]


@router.post("/ingest", response_model=IngestResponse)
async def ingest(
    request: IngestRequest,
    settings: SettingsDep,
    correlation_id: CorrelationId = None,
) -> IngestResponse:
    storage = LocalFilesystemStorage(settings.STORAGE_LOCAL_PATH)

    try:
        path = storage.local_path(request.storage_key)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    if path is None or not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No stored document for key {request.storage_key}",
        )

    container = get_container()
    identify(user_id=request.user_id)
    try:
        result = await inflight.run_once(
            request.document_id,
            f"{request.figures}:{request.chunker}:{request.user_id}:{request.storage_key}",
            lambda on_progress: ingest_document(
                path,
                document_id=request.document_id,
                filename=request.filename,
                kind=request.kind,
                settings=settings,
                chunker_name=request.chunker,
                user_id=request.user_id,
                embedder=container.embedder,
                sparse_encoder=container.sparse_encoder,
                store=container.store,
                vision=container.vision,
                vision_cache=container.vision_cache,
                read_figures=request.figures == "read",
                on_progress=on_progress,
            ),
        )
    except QuotaExhaustedError as exc:
        # A 4xx, so the worker fails the document at once rather than
        # retrying into the same wall; the message is what the student sees.
        logger.warning("ingest of %s stopped: %s", request.document_id, exc)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Today's free quota for indexing documents is used up. "
                "Try uploading this again tomorrow."
            ),
        ) from exc
    except UnsupportedDocumentError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)
        ) from exc
    except EmptyDocumentError as exc:
        # 422 rather than 500: the request was well formed, the document simply
        # holds nothing this pipeline can study.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    logger.info(
        "ingest complete for %s (correlation_id=%s)",
        request.document_id,
        correlation_id,
    )

    return IngestResponse(
        document_id=request.document_id,
        page_count=result.document.page_count,
        block_count=len(result.document.blocks),
        chunk_count=len(result.chunks),
        parser=result.document.parser,
        chunker=result.chunker_name,
        indexed=result.indexed,
        cache_hits=result.cache_hits,
        vision=result.vision,
        figures_pending=result.figures_pending,
        duplicates_dropped=len(result.duplicates),
        timings=result.timings,
        metadata=result.document.metadata,
        chunks=[ChunkResponse.from_chunk(c) for c in result.chunks],
    )


class DeleteRequest(BaseModel):
    document_id: str
    user_id: str


@router.post("/documents/delete")
async def delete_document(request: DeleteRequest) -> dict[str, Any]:
    """Removes a document's vectors from the index.

    Called when a student deletes a document. Without it the Postgres rows and
    the stored file go away while the vectors remain, and the deleted material
    keeps answering that student's questions -- which is both wrong and, for
    someone who deleted a document deliberately, a breach of the expectation
    that deleting means deleted.

    Scoped by user id so a document id alone cannot remove another user's
    vectors. Any ingest still running for the document is stopped first;
    otherwise it would write the vectors straight back.
    """
    container = get_container()
    if await inflight.cancel(request.document_id):
        logger.info("stopped the running ingest of %s", request.document_id)
    before = await container.store.count(user_id=request.user_id)
    await container.store.delete_document(request.document_id, user_id=request.user_id)
    after = await container.store.count(user_id=request.user_id)

    logger.info(
        "removed %s vectors for document %s", before - after, request.document_id
    )
    return {"document_id": request.document_id, "removed": before - after}


@router.get("/documents/{document_id}/progress")
async def ingest_progress(document_id: str) -> dict[str, Any]:
    """How far a running ingest is through its images.

    Polled by the worker while it waits on /ingest, because reading images is
    the one stage long enough to be worth a progress bar -- and a single
    request has no other way to report part way through.
    """
    run = inflight.progress(document_id)
    if run is None:
        return {"running": False, "done": 0, "total": 0}
    return {"running": True, "done": run.done, "total": run.total}


class PreviewRequest(BaseModel):
    storage_key: str
    filename: str
    kind: DocumentKind
    chunker: str = "structural"
    limit: Annotated[int, Field(ge=1, le=50)] = 5


@router.post("/ingest/preview")
async def preview(
    request: PreviewRequest,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Parses and chunks without persisting anything.

    Exists to make chunking decisions inspectable while tuning: run the same
    file through two chunkers and read the boundaries side by side, rather than
    inferring quality from retrieval scores alone.
    """
    storage = LocalFilesystemStorage(settings.STORAGE_LOCAL_PATH)
    path = storage.local_path(request.storage_key)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Document not found")

    result = await ingest_document(
        path,
        document_id="preview",
        filename=request.filename,
        kind=request.kind,
        settings=settings,
        chunker_name=request.chunker,
        vision=get_container().vision,
        vision_cache=get_container().vision_cache,
    )

    return {
        "chunker": result.chunker_name,
        "vision": result.vision,
        "chunk_count": len(result.chunks),
        "duplicates_dropped": len(result.duplicates),
        "timings": result.timings,
        "chunks": [
            {
                "index": c.metadata.chunk_index,
                "tokens": c.token_count,
                "page": c.metadata.page_number,
                "page_end": c.metadata.page_end,
                "slide": c.metadata.slide_number,
                "heading_path": c.metadata.heading_path,
                "source": c.metadata.source.value,
                "text": c.text,
            }
            for c in result.chunks[: request.limit]
        ],
    }
