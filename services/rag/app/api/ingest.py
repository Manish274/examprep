"""Ingestion endpoint.

Called by the Node worker, never by a browser. The worker supplies a storage
key; this service reads the file, parses it, chunks it, and returns the chunks
with their metadata intact. Persisting them is the worker's job -- Postgres
belongs to the Node side.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import CorrelationId, InternalAuth, SettingsDep
from app.container import get_container
from app.core.models import Chunk, DocumentKind
from app.ingestion.pipeline import (
    EmptyDocumentError,
    UnsupportedDocumentError,
    ingest_document,
)
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
    slide_number: int | None
    section: str | None
    heading: str | None
    heading_path: list[str]
    char_start: int | None
    char_end: int | None
    content_hash: str

    @classmethod
    def from_chunk(cls, chunk: Chunk) -> ChunkResponse:
        meta = chunk.metadata
        return cls(
            id=chunk.id,
            text=chunk.text,
            token_count=chunk.token_count,
            chunk_index=meta.chunk_index,
            page_number=meta.page_number,
            slide_number=meta.slide_number,
            section=meta.section,
            heading=meta.heading,
            heading_path=meta.heading_path,
            char_start=meta.char_start,
            char_end=meta.char_end,
            content_hash=meta.content_hash,
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
    try:
        result = await ingest_document(
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
        )
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
        timings=result.timings,
        metadata=result.document.metadata,
        chunks=[ChunkResponse.from_chunk(c) for c in result.chunks],
    )


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
    )

    return {
        "chunker": result.chunker_name,
        "chunk_count": len(result.chunks),
        "timings": result.timings,
        "chunks": [
            {
                "index": c.metadata.chunk_index,
                "tokens": c.token_count,
                "page": c.metadata.page_number,
                "slide": c.metadata.slide_number,
                "heading_path": c.metadata.heading_path,
                "text": c.text,
            }
            for c in result.chunks[: request.limit]
        ],
    }
