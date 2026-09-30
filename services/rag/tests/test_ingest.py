"""End-to-end ingestion: a real file on disk through to indexed vectors.

Runs against a throwaway Qdrant collection with the mock embedder, so the whole
path -- parse, chunk, embed, encode, index -- is exercised without spending API
quota or touching the real collection.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import httpx
import pytest

from app.config import Settings, get_settings
from app.container import build_test_container, set_container
from app.core.models import Chunk, ChunkMetadata
from app.core.text import chunk_id_for, content_hash
from app.ingestion.pipeline import deduplicate
from app.main import create_app

FIXTURES = Path(__file__).parent / "fixtures"
TOKEN = "test_internal_token"
USER = "user-under-test"
DIMENSIONS = 256

pytestmark = pytest.mark.skipif(
    not (FIXTURES / "normalization.pdf").exists(),
    reason="fixtures not generated; run tests/fixtures/generate.py",
)


@pytest.fixture
def uploads(tmp_path: Path) -> Path:
    """An isolated storage root holding copies of the fixtures."""
    root = tmp_path / "uploads"
    (root / "user-1").mkdir(parents=True)
    for name in ("normalization.pdf", "indexing.pptx", "empty.pdf"):
        shutil.copy(FIXTURES / name, root / "user-1" / name)
    return root


@pytest.fixture
async def client(uploads: Path):
    settings = Settings(
        INTERNAL_SERVICE_TOKEN=TOKEN,
        STORAGE_LOCAL_PATH=uploads,
        CHUNK_TARGET_TOKENS=120,
        CHUNK_OVERLAP_TOKENS=24,
        CHUNK_MIN_TOKENS=20,
        EMBEDDING_PROVIDER="mock",
        EMBEDDING_DIMENSIONS=DIMENSIONS,
        QDRANT_COLLECTION=f"test_ingest_{uuid.uuid4().hex[:12]}",
    )

    container = build_test_container(settings)
    set_container(container)
    await container.startup()

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"x-internal-token": TOKEN},
        ) as client:
            yield client
    finally:
        await container.qdrant.delete_collection(settings.QDRANT_COLLECTION)
        await container.shutdown()
        set_container(None)


def _pdf_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "document_id": "doc-1",
        "user_id": USER,
        "filename": "normalization.pdf",
        "kind": "pdf",
        "storage_key": "user-1/normalization.pdf",
    }
    body.update(overrides)
    return body


class TestAuthentication:
    async def test_rejects_a_request_with_no_token(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(
            "/ingest", json=_pdf_body(), headers={"x-internal-token": ""}
        )
        assert response.status_code == 401

    async def test_rejects_a_wrong_token(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/ingest", json=_pdf_body(), headers={"x-internal-token": "wrong"}
        )
        assert response.status_code == 401


class TestIngestPdf:
    async def test_returns_chunks_with_metadata(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post("/ingest", json=_pdf_body())
        assert response.status_code == 200

        body = response.json()
        assert body["document_id"] == "doc-1"
        assert body["page_count"] == 2
        assert body["parser"] == "pymupdf"
        assert body["chunker"] == "structural"
        assert body["chunk_count"] == len(body["chunks"]) > 0

    async def test_every_chunk_carries_citation_metadata(
        self, client: httpx.AsyncClient
    ) -> None:
        chunks = (await client.post("/ingest", json=_pdf_body())).json()["chunks"]

        for chunk in chunks:
            assert chunk["text"].strip()
            assert chunk["token_count"] > 0
            assert len(chunk["content_hash"]) == 64
            # A chunk with no location cannot be cited.
            assert chunk["page_number"] is not None

    async def test_chunk_indexes_are_dense_and_ordered(
        self, client: httpx.AsyncClient
    ) -> None:
        chunks = (await client.post("/ingest", json=_pdf_body())).json()["chunks"]
        indexes = [c["chunk_index"] for c in chunks]
        assert indexes == list(range(len(chunks)))

    async def test_heading_paths_survive_the_boundary(
        self, client: httpx.AsyncClient
    ) -> None:
        chunks = (await client.post("/ingest", json=_pdf_body())).json()["chunks"]
        paths = [tuple(c["heading_path"]) for c in chunks]

        assert ("Database Normalization", "Third Normal Form") in paths
        assert ("Database Normalization", "Denormalization") in paths

    async def test_reports_timings(self, client: httpx.AsyncClient) -> None:
        body = (await client.post("/ingest", json=_pdf_body())).json()
        assert body["timings"]["parse_ms"] >= 0
        assert body["timings"]["chunk_ms"] >= 0


class TestIngestPptx:
    async def test_produces_slide_numbered_chunks(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(
            "/ingest",
            json=_pdf_body(
                document_id="doc-2",
                filename="indexing.pptx",
                kind="pptx",
                storage_key="user-1/indexing.pptx",
            ),
        )
        assert response.status_code == 200

        body = response.json()
        assert body["parser"] == "python-pptx"
        for chunk in body["chunks"]:
            assert chunk["slide_number"] is not None
            assert chunk["page_number"] is None

    async def test_speaker_notes_reach_the_chunks(
        self, client: httpx.AsyncClient
    ) -> None:
        body = (
            await client.post(
                "/ingest",
                json=_pdf_body(
                    document_id="doc-2",
                    filename="indexing.pptx",
                    kind="pptx",
                    storage_key="user-1/indexing.pptx",
                ),
            )
        ).json()

        combined = " ".join(c["text"] for c in body["chunks"])
        assert "buying read speed" in combined


class TestChunkerSelection:
    async def test_the_baseline_chunker_can_be_requested(
        self, client: httpx.AsyncClient
    ) -> None:
        body = (
            await client.post("/ingest", json=_pdf_body(chunker="fixed_window"))
        ).json()

        assert body["chunker"] == "fixed_window"
        # The baseline discards structure, which is exactly what makes it a
        # useful comparison point.
        assert all(c["heading_path"] == [] for c in body["chunks"])

    async def test_an_unknown_chunker_is_a_client_error(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(
            "/ingest", json=_pdf_body(chunker="does-not-exist")
        )
        assert response.status_code == 400
        assert "structural" in response.json()["detail"]


class TestFailureModes:
    async def test_missing_file_is_a_404(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/ingest", json=_pdf_body(storage_key="user-1/absent.pdf")
        )
        assert response.status_code == 404

    async def test_path_traversal_is_rejected(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/ingest", json=_pdf_body(storage_key="../../../etc/passwd")
        )
        assert response.status_code in {400, 404}

    async def test_a_document_with_no_text_layer_is_422(
        self, client: httpx.AsyncClient
    ) -> None:
        # The scanned-PDF case. It must be distinguishable from a server fault
        # so the student can be told what to do about it.
        response = await client.post(
            "/ingest",
            json=_pdf_body(filename="empty.pdf", storage_key="user-1/empty.pdf"),
        )
        assert response.status_code == 422
        assert "scanned" in response.json()["detail"].lower()

    async def test_an_unparseable_kind_is_rejected(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post("/ingest", json=_pdf_body(kind="docx"))
        assert response.status_code == 422  # pydantic rejects the enum value


class TestPreview:
    async def test_returns_a_capped_sample_for_inspection(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(
            "/ingest/preview",
            json={
                "storage_key": "user-1/normalization.pdf",
                "filename": "normalization.pdf",
                "kind": "pdf",
                "limit": 2,
            },
        )
        assert response.status_code == 200

        body = response.json()
        assert len(body["chunks"]) == 2
        assert body["chunk_count"] >= 2


class TestIndexing:
    """Ingestion now writes vectors, not just chunks. These assert against the
    store itself rather than the response, since the response could report a
    count the index never received."""

    async def test_chunks_reach_the_vector_store(
        self, client: httpx.AsyncClient
    ) -> None:
        from app.container import get_container

        body = (await client.post("/ingest", json=_pdf_body())).json()
        store = get_container().store

        assert body["indexed"] == body["chunk_count"]
        assert await store.count(user_id=USER) == body["chunk_count"]

    async def test_indexed_chunks_are_retrievable(
        self, client: httpx.AsyncClient
    ) -> None:
        from app.container import get_container
        from app.core.models import RetrievalStrategy

        await client.post("/ingest", json=_pdf_body())
        results = await get_container().retrieval.search(
            "3NF transitive dependency",
            user_id=USER,
            strategy=RetrievalStrategy.HYBRID,
            top_k=3,
        )

        assert results
        assert "normal form" in results[0].chunk.text.lower()

    async def test_vectors_are_scoped_to_the_ingesting_user(
        self, client: httpx.AsyncClient
    ) -> None:
        from app.container import get_container
        from app.core.models import RetrievalStrategy

        await client.post("/ingest", json=_pdf_body())
        container = get_container()

        assert await container.store.count(user_id="someone-else") == 0
        assert (
            await container.retrieval.search(
                "3NF", user_id="someone-else", strategy=RetrievalStrategy.BM25
            )
            == []
        )

    async def test_reingesting_replaces_rather_than_duplicates(
        self, client: httpx.AsyncClient
    ) -> None:
        # A retried job, or a re-upload after a chunker change, must not leave
        # two generations of the same document in the index.
        from app.container import get_container

        first = (await client.post("/ingest", json=_pdf_body())).json()
        second = (await client.post("/ingest", json=_pdf_body())).json()

        assert first["chunk_count"] == second["chunk_count"]
        assert await get_container().store.count(user_id=USER) == second["chunk_count"]

    async def test_reports_embedding_and_index_timings(
        self, client: httpx.AsyncClient
    ) -> None:
        timings = (await client.post("/ingest", json=_pdf_body())).json()["timings"]

        for stage in ("parse_ms", "chunk_ms", "embed_ms", "sparse_ms", "index_ms"):
            assert stage in timings

    async def test_preview_does_not_index(self, client: httpx.AsyncClient) -> None:
        # Preview exists for inspecting chunk boundaries while tuning; it must
        # not leave anything behind in the store.
        from app.container import get_container

        await client.post(
            "/ingest/preview",
            json={
                "storage_key": "user-1/normalization.pdf",
                "filename": "normalization.pdf",
                "kind": "pdf",
            },
        )
        assert await get_container().store.count() == 0


class TestVectorDeletion:
    """Deleting a document must remove its vectors. Without this the Postgres
    rows go away while the index keeps answering questions from material the
    student deliberately deleted."""

    async def test_removes_the_documents_vectors(
        self, client: httpx.AsyncClient
    ) -> None:
        from app.container import get_container

        body = (await client.post("/ingest", json=_pdf_body())).json()
        store = get_container().store
        assert await store.count(user_id=USER) == body["chunk_count"]

        response = await client.post(
            "/documents/delete", json={"document_id": "doc-1", "user_id": USER}
        )

        assert response.status_code == 200
        assert response.json()["removed"] == body["chunk_count"]
        assert await store.count(user_id=USER) == 0

    async def test_leaves_other_documents_intact(
        self, client: httpx.AsyncClient
    ) -> None:
        from app.container import get_container

        await client.post("/ingest", json=_pdf_body())
        deck = await client.post(
            "/ingest",
            json=_pdf_body(
                document_id="doc-2",
                filename="indexing.pptx",
                kind="pptx",
                storage_key="user-1/indexing.pptx",
            ),
        )
        await client.post(
            "/documents/delete", json={"document_id": "doc-1", "user_id": USER}
        )

        store = get_container().store
        assert await store.count(user_id=USER) == deck.json()["chunk_count"]

    async def test_cannot_delete_another_users_vectors(
        self, client: httpx.AsyncClient
    ) -> None:
        # A document id alone must not be enough; the filter is scoped by user.
        from app.container import get_container

        body = (await client.post("/ingest", json=_pdf_body())).json()
        response = await client.post(
            "/documents/delete",
            json={"document_id": "doc-1", "user_id": "someone-else"},
        )

        assert response.json()["removed"] == 0
        assert await get_container().store.count(user_id=USER) == body["chunk_count"]

    async def test_deleting_an_unknown_document_is_harmless(
        self, client: httpx.AsyncClient
    ) -> None:
        # A retried cleanup must not fail on work that already succeeded.
        response = await client.post(
            "/documents/delete", json={"document_id": "never-existed", "user_id": USER}
        )
        assert response.status_code == 200
        assert response.json()["removed"] == 0


def _figures_pdf(path: Path) -> None:
    """Two pages of prose with a picture on the second, big enough to read."""
    import fitz

    picture = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 400, 300), 0)
    picture.clear_with(180)
    with fitz.open() as pdf:
        for number, body in enumerate(
            (
                "B-trees keep keys sorted and balanced. Every node holds between "
                "t-1 and 2t-1 keys, so the height stays logarithmic in the number "
                "of keys stored. Searching descends from the root one node at a "
                "time, comparing against the keys held in each node.",
                "Inserting into a full node splits it around the median key, and "
                "the median moves up into the parent. The diagram below shows a "
                "node of a B-tree of minimum degree three before and after a split.",
            ),
            start=1,
        ):
            page = pdf.new_page()
            page.insert_textbox(fitz.Rect(72, 72, 520, 300), body, fontsize=11)
            if number == 2:
                page.insert_image(fitz.Rect(72, 320, 472, 620), pixmap=picture)
        pdf.save(str(path))


@pytest.fixture
async def vision_client(uploads: Path):
    """A service reading images with the stand-in model."""
    _figures_pdf(uploads / "user-1" / "figures.pdf")
    settings = Settings(
        INTERNAL_SERVICE_TOKEN=TOKEN,
        STORAGE_LOCAL_PATH=uploads,
        EMBEDDING_PROVIDER="mock",
        EMBEDDING_DIMENSIONS=DIMENSIONS,
        VISION_PROVIDER="mock",
        QDRANT_COLLECTION=f"test_ingest_{uuid.uuid4().hex[:12]}",
    )

    container = build_test_container(settings)
    set_container(container)
    await container.startup()

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"x-internal-token": TOKEN},
        ) as client:
            yield client
    finally:
        await container.qdrant.delete_collection(settings.QDRANT_COLLECTION)
        await container.shutdown()
        set_container(None)


def _figures_body(figures: str, **overrides: object) -> dict[str, object]:
    return _pdf_body(
        filename="figures.pdf",
        storage_key="user-1/figures.pdf",
        figures=figures,
        **overrides,
    )


def _sources(body: dict[str, object]) -> set[str]:
    return {c["source"] for c in body["chunks"]}  # type: ignore[attr-defined]


class TestFigures:
    """The text is indexed first; the pictures follow in a second pass."""

    async def test_a_deferred_pass_indexes_the_text_and_counts_the_pictures(
        self, vision_client: httpx.AsyncClient
    ) -> None:
        from app.container import get_container

        body = (await vision_client.post("/ingest", json=_figures_body("defer"))).json()

        assert body["figures_pending"] == 1
        assert _sources(body) == {"text"}
        assert get_container().vision.calls == 0  # type: ignore[union-attr]
        assert await get_container().store.count(user_id=USER) == body["chunk_count"]

    async def test_the_full_pass_adds_them_and_leaves_no_stale_chunks(
        self, vision_client: httpx.AsyncClient
    ) -> None:
        from app.container import get_container

        await vision_client.post("/ingest", json=_figures_body("defer"))
        body = (await vision_client.post("/ingest", json=_figures_body("read"))).json()

        assert body["figures_pending"] == 0
        combined = " ".join(c["text"] for c in body["chunks"])
        assert "A diagram showing a worked example." in combined
        # Replaced in place: the chunks the figure changed are gone.
        assert await get_container().store.count(user_id=USER) == body["chunk_count"]

    async def test_a_picture_read_once_is_never_read_again(
        self, vision_client: httpx.AsyncClient
    ) -> None:
        # A retried upload, or the same slides uploaded by someone else.
        from app.container import get_container

        await vision_client.post("/ingest", json=_figures_body("read"))
        again = (
            await vision_client.post(
                "/ingest", json=_figures_body("defer", document_id="doc-copy")
            )
        ).json()

        assert again["figures_pending"] == 0
        assert again["vision"]["cached"] == 1
        assert get_container().vision.calls == 1  # type: ignore[union-attr]

    async def test_pictures_the_model_could_not_read_are_still_pending(
        self, vision_client: httpx.AsyncClient
    ) -> None:
        # An overloaded model must not cost the document its pictures for
        # good: what failed is reported as unread, for a later pass to retry.
        from app.container import get_container
        from app.vision.providers import VisionUnavailableError

        async def busy(images: object) -> dict[str, str | None]:
            raise VisionUnavailableError("vision upstream 503")

        get_container().vision.describe = busy  # type: ignore[union-attr]
        body = (await vision_client.post("/ingest", json=_figures_body("read"))).json()

        assert body["vision"]["failed"] == 1
        assert body["figures_pending"] == 1
        assert _sources(body) == {"text"}

    @pytest.mark.parametrize(
        ("error", "advice"),
        [
            ("busy", "in a few minutes"),
            ("quota", "tomorrow"),
        ],
    )
    async def test_a_scan_no_page_of_which_could_be_read_says_why(
        self, vision_client: httpx.AsyncClient, error: str, advice: str
    ) -> None:
        # A busy model and a spent quota need different advice: one clears in
        # minutes, the other at the end of the day.
        from app.container import get_container
        from app.vision.providers import (
            VisionQuotaExhaustedError,
            VisionUnavailableError,
        )

        async def refuse(images: object) -> dict[str, str | None]:
            if error == "quota":
                raise VisionQuotaExhaustedError("vision: daily quota used up")
            raise VisionUnavailableError("vision upstream 503")

        get_container().vision.describe = refuse  # type: ignore[union-attr]
        response = await vision_client.post(
            "/ingest",
            json=_pdf_body(filename="empty.pdf", storage_key="user-1/empty.pdf"),
        )

        assert response.status_code == 422
        assert advice in response.json()["detail"]

    async def test_a_scanned_document_is_read_straight_away(
        self, vision_client: httpx.AsyncClient
    ) -> None:
        # With no text there is nothing to search in the meantime, so a
        # deferred pass reads the pages rather than leaving the document empty.
        body = (
            await vision_client.post(
                "/ingest",
                json=_pdf_body(
                    filename="empty.pdf",
                    storage_key="user-1/empty.pdf",
                    figures="defer",
                ),
            )
        ).json()

        assert body["figures_pending"] == 0
        assert _sources(body) == {"vision"}

    async def test_progress_is_idle_when_nothing_runs(
        self, vision_client: httpx.AsyncClient
    ) -> None:
        response = await vision_client.get("/documents/doc-1/progress")
        assert response.json() == {"running": False, "done": 0, "total": 0}


class TestDeterministicChunkIds:
    async def test_reingesting_produces_identical_chunk_ids(
        self, client: httpx.AsyncClient
    ) -> None:
        # Random ids would be regenerated on every ingest, silently
        # invalidating every gold set that references them.
        first = (await client.post("/ingest", json=_pdf_body())).json()
        second = (await client.post("/ingest", json=_pdf_body())).json()

        assert [c["id"] for c in first["chunks"]] == [c["id"] for c in second["chunks"]]


class TestDuplicateChunks:
    """A document containing the same text twice must still ingest.

    Chunk ids are uuid5(document_id, sha256(text)), so two byte-identical
    chunks in one document produce one id -- and the insert dies on the primary
    key, taking the whole upload with it. Lecture decks trigger this constantly:
    a title slide repeated between sections, or an animation built up over five
    slides where each adds a picture and the text never changes.
    """

    @staticmethod
    def _chunk(text: str, index: int, slide: int) -> Chunk:
        return Chunk(
            id=chunk_id_for("doc-1", text),
            text=text,
            token_count=len(text.split()),
            metadata=ChunkMetadata(
                document_id="doc-1",
                document_name="lecture.pptx",
                chunk_index=index,
                slide_number=slide,
                content_hash=content_hash(text),
            ),
        )

    def test_keeps_one_copy_of_a_repeated_slide(self) -> None:
        chunks = [
            self._chunk("Intro to AI, Paolo Turrini", 0, 5),
            self._chunk("Intro to AI, Paolo Turrini", 1, 6),
            self._chunk("Intro to AI, Paolo Turrini", 2, 7),
            self._chunk("Markov decision processes", 3, 8),
        ]

        kept, dropped = deduplicate(chunks)

        assert [c.text for c in kept] == [
            "Intro to AI, Paolo Turrini",
            "Markov decision processes",
        ]
        assert len(dropped) == 2
        assert len({c.id for c in kept}) == len(kept)

    def test_keeps_the_first_occurrence(self) -> None:
        # The retained chunk should carry the earliest position in the
        # document -- where a reader would actually meet it.
        chunks = [
            self._chunk("Expected utility of a policy", 37, 44),
            self._chunk("Expected utility of a policy", 38, 45),
        ]

        kept, _ = deduplicate(chunks)

        assert kept[0].metadata.chunk_index == 37
        assert kept[0].metadata.slide_number == 44

    def test_near_duplicates_are_left_alone(self) -> None:
        # Only byte-identical text is redundant. Two passages that merely
        # overlap are different material and both must survive.
        chunks = [
            self._chunk("A policy maps states to actions", 0, 1),
            self._chunk("A policy maps states to actions.", 1, 2),
        ]

        kept, dropped = deduplicate(chunks)

        assert len(kept) == 2
        assert dropped == []

    def test_an_empty_document_does_not_explode(self) -> None:
        assert deduplicate([]) == ([], [])
