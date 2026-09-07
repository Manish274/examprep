"""Retrieval against a real Qdrant instance.

Uses a throwaway collection and the mock embedder, so these cost no API quota
and leave nothing behind. Skipped when Qdrant is unreachable, rather than
encoding "infrastructure is running" into the suite.

Mocked out, these tests would pass while the real thing was broken: named
vectors, the IDF modifier and payload filtering are all Qdrant behaviours, not
behaviours of our code.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from qdrant_client import AsyncQdrantClient

from app.core.models import Chunk, ChunkMetadata, RetrievalStrategy
from app.embedding.mock import MockEmbeddingProvider
from app.retrieval.bm25 import Bm25Encoder
from app.retrieval.fusion import ReciprocalRankFusion
from app.retrieval.qdrant_store import QdrantStore
from app.retrieval.retrievers import (
    DenseRetriever,
    HybridRetriever,
    RetrievalService,
    SparseRetriever,
)

QDRANT_URL = "http://localhost:6333"
DIMENSIONS = 256
ALICE = "user-alice"
BOB = "user-bob"


def _qdrant_available() -> bool:
    try:
        return httpx.get(f"{QDRANT_URL}/readyz", timeout=2.0).status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _qdrant_available(), reason="Qdrant not reachable at localhost:6333"
)


CORPUS = [
    # (document_id, heading, text)
    ("doc-db", "First Normal Form",
     "A relation is in first normal form 1NF if every attribute contains only "
     "atomic values and repeating groups are not permitted."),
    ("doc-db", "Second Normal Form",
     "A relation is in second normal form 2NF if it is in 1NF and every "
     "non-prime attribute is fully functionally dependent on every candidate key."),
    ("doc-db", "Third Normal Form",
     "A relation is in third normal form 3NF if it is in 2NF and no non-prime "
     "attribute is transitively dependent on any candidate key."),
    ("doc-db", "Denormalization",
     "Denormalization deliberately introduces redundancy to improve read "
     "performance, trading write cost for query speed."),
    ("doc-idx", "B-Tree Indexes",
     "A balanced tree structure with logarithmic lookup that supports both "
     "equality and range queries."),
    ("doc-idx", "Hash Indexes",
     "A hash index supports equality lookups only and cannot answer range "
     "queries at all."),
]


def _chunk(index: int, document_id: str, heading: str, text: str) -> Chunk:
    return Chunk(
        id=str(uuid.uuid4()),
        text=text,
        token_count=len(text.split()),
        metadata=ChunkMetadata(
            document_id=document_id,
            document_name=f"{document_id}.pdf",
            chunk_index=index,
            page_number=index + 1,
            section=heading,
            heading=heading,
            heading_path=["Study Notes", heading],
            content_hash=f"{index:064d}",
        ),
    )


@pytest.fixture
async def service():
    """An isolated collection populated with the corpus above."""
    client = AsyncQdrantClient(url=QDRANT_URL, timeout=30)
    collection = f"test_{uuid.uuid4().hex[:12]}"
    store = QdrantStore(client, collection, DIMENSIONS)
    await store.ensure_collection()

    embedder = MockEmbeddingProvider(dimensions=DIMENSIONS)
    encoder = Bm25Encoder()

    chunks = [
        _chunk(i, doc, heading, text)
        for i, (doc, heading, text) in enumerate(CORPUS)
    ]
    texts = [c.embedding_text() for c in chunks]
    vectors = await embedder.embed_documents(texts)

    await store.upsert_chunks(
        chunks,
        [v.values for v in vectors],
        [encoder.encode_document(t) for t in texts],
        user_id=ALICE,
    )

    dense = DenseRetriever(store, embedder)
    sparse = SparseRetriever(store, encoder)
    try:
        yield RetrievalService(
            dense=dense,
            sparse=sparse,
            hybrid=HybridRetriever(dense, sparse, ReciprocalRankFusion()),
        ), store, chunks
    finally:
        await client.delete_collection(collection)
        await client.close()


class TestSparseRetrieval:
    async def test_finds_an_exact_technical_term(self, service) -> None:
        # The case dense retrieval handles worst: a bare acronym.
        retrieval, _, _ = service
        results = await retrieval.search(
            "3NF", user_id=ALICE, strategy=RetrievalStrategy.BM25, top_k=3
        )
        assert results
        assert results[0].chunk.metadata.heading == "Third Normal Form"

    async def test_scores_come_back_on_the_sparse_field(self, service) -> None:
        retrieval, _, _ = service
        results = await retrieval.search(
            "transitive dependency",
            user_id=ALICE,
            strategy=RetrievalStrategy.BM25,
            top_k=3,
        )
        assert results[0].sparse_score is not None
        assert results[0].dense_score is None

    async def test_a_stopword_only_query_returns_nothing(self, service) -> None:
        # Must not raise: an empty sparse vector is rejected by the server, so
        # the retriever short-circuits instead of sending one.
        retrieval, _, _ = service
        assert (
            await retrieval.search(
                "the and of", user_id=ALICE, strategy=RetrievalStrategy.BM25
            )
            == []
        )


class TestDenseRetrieval:
    async def test_returns_ranked_results(self, service) -> None:
        retrieval, _, _ = service
        results = await retrieval.search(
            "normal form candidate key",
            user_id=ALICE,
            strategy=RetrievalStrategy.DENSE,
            top_k=4,
        )
        assert len(results) == 4
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    async def test_scores_come_back_on_the_dense_field(self, service) -> None:
        retrieval, _, _ = service
        results = await retrieval.search(
            "normal form", user_id=ALICE, strategy=RetrievalStrategy.DENSE, top_k=2
        )
        assert results[0].dense_score is not None
        assert results[0].sparse_score is None


class TestHybridRetrieval:
    async def test_merges_both_retrievers(self, service) -> None:
        retrieval, _, _ = service
        results = await retrieval.search(
            "transitive dependency on a candidate key",
            user_id=ALICE,
            strategy=RetrievalStrategy.HYBRID,
            top_k=5,
        )

        assert results
        # At least one result should have been seen by both sides, which is
        # the evidence fusion exists to exploit.
        assert any(
            r.dense_rank is not None and r.sparse_rank is not None for r in results
        )

    async def test_records_rrf_scores_and_per_stage_ranks(self, service) -> None:
        retrieval, _, _ = service
        results = await retrieval.search(
            "3NF transitive dependency",
            user_id=ALICE,
            strategy=RetrievalStrategy.HYBRID,
            top_k=5,
        )
        top = results[0]
        assert top.rrf_score is not None
        assert top.score == top.rrf_score

    async def test_returns_no_duplicates(self, service) -> None:
        retrieval, _, _ = service
        results = await retrieval.search(
            "normal form", user_id=ALICE, strategy=RetrievalStrategy.HYBRID, top_k=10
        )
        ids = [r.chunk.id for r in results]
        assert len(ids) == len(set(ids))

    async def test_hybrid_rerank_falls_back_to_hybrid_for_now(self, service) -> None:
        # The reranker lands in Milestone 3; until then this must still return
        # candidates rather than failing.
        retrieval, _, _ = service
        results = await retrieval.search(
            "normal form",
            user_id=ALICE,
            strategy=RetrievalStrategy.HYBRID_RERANK,
            top_k=3,
        )
        assert results


class TestMetadataRoundTrip:
    async def test_citation_metadata_survives_the_vector_store(
        self, service
    ) -> None:
        # A grounded answer is only as good as the source it points at, so this
        # is the property the whole pipeline exists to preserve.
        retrieval, _, _ = service
        results = await retrieval.search(
            "3NF", user_id=ALICE, strategy=RetrievalStrategy.BM25, top_k=1
        )
        meta = results[0].chunk.metadata

        assert meta.document_id == "doc-db"
        assert meta.document_name == "doc-db.pdf"
        assert meta.heading == "Third Normal Form"
        assert meta.heading_path == ["Study Notes", "Third Normal Form"]
        assert meta.page_number == 3
        assert results[0].chunk.text.startswith("A relation is in third normal form")


class TestIsolation:
    async def test_another_user_sees_nothing(self, service) -> None:
        # The payload filter is the isolation boundary. This service knows
        # nothing about identity beyond that.
        retrieval, _, _ = service
        for strategy in RetrievalStrategy:
            assert (
                await retrieval.search("3NF", user_id=BOB, strategy=strategy) == []
            )

    async def test_document_filter_scopes_the_search(self, service) -> None:
        retrieval, _, _ = service
        results = await retrieval.search(
            "index range query",
            user_id=ALICE,
            strategy=RetrievalStrategy.HYBRID,
            document_ids=["doc-db"],
            top_k=10,
        )
        assert results
        assert {r.chunk.metadata.document_id for r in results} == {"doc-db"}


class TestStoreMaintenance:
    async def test_counts_reflect_ownership(self, service) -> None:
        _, store, chunks = service
        assert await store.count() == len(chunks)
        assert await store.count(user_id=ALICE) == len(chunks)
        assert await store.count(user_id=BOB) == 0

    async def test_deleting_a_document_leaves_the_others(self, service) -> None:
        retrieval, store, _ = service
        await store.delete_document("doc-idx", user_id=ALICE)

        assert await store.count(user_id=ALICE) == 4
        results = await retrieval.search(
            "b-tree range queries",
            user_id=ALICE,
            strategy=RetrievalStrategy.BM25,
            top_k=10,
        )
        assert all(r.chunk.metadata.document_id != "doc-idx" for r in results)

    async def test_reindexing_replaces_rather_than_duplicates(
        self, service
    ) -> None:
        # A re-ingest must not leave the previous generation of chunks in the
        # index beside the new one.
        _, store, chunks = service
        embedder = MockEmbeddingProvider(dimensions=DIMENSIONS)
        encoder = Bm25Encoder()

        subset = [c for c in chunks if c.metadata.document_id == "doc-idx"]
        texts = [c.embedding_text() for c in subset]
        vectors = await embedder.embed_documents(texts)

        await store.delete_document("doc-idx", user_id=ALICE)
        await store.upsert_chunks(
            subset,
            [v.values for v in vectors],
            [encoder.encode_document(t) for t in texts],
            user_id=ALICE,
        )
        assert await store.count(user_id=ALICE) == len(chunks)

    async def test_rejects_misaligned_vector_input(self, service) -> None:
        # Misalignment attaches one chunk's text to another's vector, and
        # nothing downstream would notice.
        _, store, chunks = service
        with pytest.raises(ValueError, match="length mismatch"):
            await store.upsert_chunks(
                chunks, [[0.0] * DIMENSIONS], [], user_id=ALICE
            )


class TestCollectionSchema:
    async def test_dimension_mismatch_is_refused(self, service) -> None:
        # Indexing 3072-wide vectors into a 256-wide collection would fail per
        # point; failing at startup names the real cause instead.
        _, store, _ = service
        wrong = QdrantStore(store._client, store.collection, DIMENSIONS * 2)

        with pytest.raises(RuntimeError, match="Re-index"):
            await wrong.ensure_collection()

    async def test_ensure_is_idempotent(self, service) -> None:
        _, store, _ = service
        assert await store.ensure_collection() is False
