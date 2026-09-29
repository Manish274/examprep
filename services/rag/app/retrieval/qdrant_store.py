"""Qdrant vector store.

One collection, one point per chunk, two named vectors on each point: a dense
embedding and a BM25 sparse vector. Keeping both on the same point is what
makes hybrid search a property of the index rather than a join performed in
application code -- the two retrievers see exactly the same corpus, always.

IDF is configured as a server-side modifier on the sparse vector. Qdrant then
computes it from the collection's current statistics at query time, so it stays
correct as documents are added. Precomputing IDF at ingest would freeze it at
whatever the corpus looked like that afternoon.

Every query is filtered by user id. That filter is the isolation boundary: this
service knows nothing about who a user is, only that vectors carrying a
different id must never be returned.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence

from qdrant_client import AsyncQdrantClient, models

from app.core.models import (
    Chunk,
    ChunkMetadata,
    ContentSource,
    ScoredChunk,
    SparseVector,
)

logger = logging.getLogger(__name__)

DENSE = "dense"
SPARSE = "sparse"


class QdrantStore:
    def __init__(
        self,
        client: AsyncQdrantClient,
        collection: str,
        dimensions: int,
    ) -> None:
        self._client = client
        self._collection = collection
        self._dimensions = dimensions

    @property
    def collection(self) -> str:
        return self._collection

    # ── schema ──────────────────────────────────────────────

    async def ensure_collection(self) -> bool:
        """Creates the collection if absent. Returns True if it created one.

        Also verifies the dimensions of an existing collection. A mismatch means
        the embedding model or its output width changed, which silently
        invalidates every stored vector -- worth failing loudly rather than
        indexing incomparable vectors alongside each other.
        """
        if await self._client.collection_exists(self._collection):
            info = await self._client.get_collection(self._collection)
            vectors = info.config.params.vectors
            dense = vectors.get(DENSE) if isinstance(vectors, dict) else None
            existing = dense.size if dense is not None else None
            if existing is not None and existing != self._dimensions:
                raise RuntimeError(
                    f"Collection '{self._collection}' holds {existing}-dimensional "
                    f"vectors but the configured model produces {self._dimensions}. "
                    "Re-index the corpus or point at a different collection."
                )
            return False

        await self._client.create_collection(
            collection_name=self._collection,
            vectors_config={
                DENSE: models.VectorParams(
                    size=self._dimensions, distance=models.Distance.COSINE
                )
            },
            sparse_vectors_config={
                SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)
            },
        )

        # Payload indexes on the fields every query filters by. Without them
        # Qdrant scans the payload of each candidate instead of narrowing first.
        for field in ("user_id", "document_id"):
            await self._client.create_payload_index(
                collection_name=self._collection,
                field_name=field,
                field_schema=models.PayloadSchemaType.KEYWORD,
            )

        logger.info(
            "created collection %s (dense=%s, sparse=IDF)",
            self._collection,
            self._dimensions,
        )
        return True

    # ── writing ─────────────────────────────────────────────

    @staticmethod
    def _payload(chunk: Chunk, user_id: str) -> dict[str, object]:
        meta = chunk.metadata
        return {
            "user_id": user_id,
            "document_id": meta.document_id,
            "document_name": meta.document_name,
            "chunk_index": meta.chunk_index,
            "text": chunk.text,
            "token_count": chunk.token_count,
            "page_number": meta.page_number,
            "slide_number": meta.slide_number,
            "section": meta.section,
            "heading": meta.heading,
            "heading_path": meta.heading_path,
            "char_start": meta.char_start,
            "char_end": meta.char_end,
            "content_hash": meta.content_hash,
            "source": meta.source.value,
        }

    async def upsert_chunks(
        self,
        chunks: Sequence[Chunk],
        dense: Sequence[Sequence[float]],
        sparse: Sequence[SparseVector],
        *,
        user_id: str,
        batch_size: int = 128,
    ) -> int:
        if not chunks:
            return 0
        if not (len(chunks) == len(dense) == len(sparse)):
            # Misalignment here would attach one chunk's text to another's
            # vector, which no downstream check would catch.
            raise ValueError(
                "chunk/vector length mismatch: "
                f"{len(chunks)}, {len(dense)}, {len(sparse)}"
            )

        written = 0
        for start in range(0, len(chunks), batch_size):
            window = slice(start, start + batch_size)
            points = [
                models.PointStruct(
                    id=chunk.id,
                    vector={
                        DENSE: list(dense_vector),
                        SPARSE: models.SparseVector(
                            indices=sparse_vector.indices,
                            values=sparse_vector.values,
                        ),
                    },
                    payload=self._payload(chunk, user_id),
                )
                for chunk, dense_vector, sparse_vector in zip(
                    chunks[window], dense[window], sparse[window], strict=True
                )
            ]
            await self._client.upsert(
                collection_name=self._collection, points=points, wait=True
            )
            written += len(points)

        return written

    async def delete_document(
        self, document_id: str, *, user_id: str, keep: Sequence[str] = ()
    ) -> None:
        """Removes a document's points, except those listed in `keep` -- which
        is how a re-ingest prunes the chunks its new version no longer has."""
        selector = self._filter(user_id, [document_id])
        if keep:
            selector.must_not = [models.HasIdCondition(has_id=list(keep))]
        await self._client.delete(
            collection_name=self._collection,
            points_selector=models.FilterSelector(filter=selector),
            wait=True,
        )

    # ── reading ─────────────────────────────────────────────

    @staticmethod
    def _filter(user_id: str, document_ids: Sequence[str] | None) -> models.Filter:
        must: list[models.Condition] = [
            models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id))
        ]
        if document_ids:
            must.append(
                models.FieldCondition(
                    key="document_id", match=models.MatchAny(any=list(document_ids))
                )
            )
        return models.Filter(must=must)

    @staticmethod
    def _to_scored(point: object, *, dense_score: bool) -> ScoredChunk:
        payload = getattr(point, "payload", None) or {}
        score = float(getattr(point, "score", 0.0))
        chunk = Chunk(
            id=str(getattr(point, "id", "")),
            text=str(payload.get("text", "")),
            token_count=int(payload.get("token_count") or 0),
            metadata=ChunkMetadata(
                document_id=str(payload.get("document_id", "")),
                document_name=str(payload.get("document_name", "")),
                chunk_index=int(payload.get("chunk_index") or 0),
                page_number=payload.get("page_number"),
                slide_number=payload.get("slide_number"),
                section=payload.get("section"),
                heading=payload.get("heading"),
                heading_path=list(payload.get("heading_path") or []),
                char_start=payload.get("char_start"),
                char_end=payload.get("char_end"),
                content_hash=str(payload.get("content_hash", "")),
                source=ContentSource(payload.get("source") or "text"),
            ),
        )
        return ScoredChunk(
            chunk=chunk,
            score=score,
            dense_score=score if dense_score else None,
            sparse_score=None if dense_score else score,
        )

    async def search_dense(
        self,
        vector: Sequence[float],
        *,
        user_id: str,
        document_ids: Sequence[str] | None = None,
        top_k: int = 50,
    ) -> list[ScoredChunk]:
        response = await self._client.query_points(
            collection_name=self._collection,
            query=list(vector),
            using=DENSE,
            limit=top_k,
            query_filter=self._filter(user_id, document_ids),
            with_payload=True,
        )
        return [self._to_scored(p, dense_score=True) for p in response.points]

    async def search_sparse(
        self,
        vector: SparseVector,
        *,
        user_id: str,
        document_ids: Sequence[str] | None = None,
        top_k: int = 50,
    ) -> list[ScoredChunk]:
        if not vector.indices:
            # Every query term was a stopword. BM25 has nothing to match, and
            # an empty query would otherwise be rejected by the server.
            return []

        response = await self._client.query_points(
            collection_name=self._collection,
            query=models.SparseVector(indices=vector.indices, values=vector.values),
            using=SPARSE,
            limit=top_k,
            query_filter=self._filter(user_id, document_ids),
            with_payload=True,
        )
        return [self._to_scored(p, dense_score=False) for p in response.points]

    async def sample_chunks(
        self,
        *,
        user_id: str,
        document_ids: Sequence[str] | None = None,
        limit: int = 25,
    ) -> list[Chunk]:
        """Reads indexed chunks back out, for gold-set generation.

        Scrolls rather than searches: this wants a representative sample of the
        corpus, not the answer to any particular question.
        """
        points, _ = await self._client.scroll(
            collection_name=self._collection,
            scroll_filter=self._filter(user_id, document_ids),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return [self._to_scored(p, dense_score=True).chunk for p in points]

    async def known_chunk_ids(
        self, chunk_ids: Sequence[str], *, user_id: str
    ) -> set[str]:
        """Which of these ids are actually in the index, for this user.

        Chunk ids are derived from the document id and the content hash, so
        re-ingesting a document after any change to parsing or chunking gives
        every chunk a new id. A gold set written before that change then names
        nothing that exists -- and an evaluation run against it reports
        Recall@5 of 0.000 for every strategy, which reads as a catastrophic
        regression rather than as a stale file.
        """
        wanted = [c for c in chunk_ids if _is_uuid(c)]
        if not wanted:
            return set()

        points = await self._client.retrieve(
            collection_name=self._collection,
            ids=list(wanted),
            with_payload=True,
            with_vectors=False,
        )
        return {
            str(point.id)
            for point in points
            # Scoped by owner, like every other read: existence in another
            # student's corpus is not existence here.
            if (point.payload or {}).get("user_id") == user_id
        }

    async def count(self, *, user_id: str | None = None) -> int:
        result = await self._client.count(
            collection_name=self._collection,
            count_filter=self._filter(user_id, None) if user_id else None,
            exact=True,
        )
        return int(result.count)


def _is_uuid(value: str) -> bool:
    """Qdrant rejects a malformed point id outright, which would turn a typo in
    a gold set into a failed request rather than a reported one."""
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError):
        return False
    return True
