"""End-to-end ingestion: a real file on disk through to chunks over HTTP."""

from __future__ import annotations

import shutil
from pathlib import Path

import httpx
import pytest

from app.config import Settings, get_settings
from app.main import create_app

FIXTURES = Path(__file__).parent / "fixtures"
TOKEN = "test_internal_token"

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
    def _settings() -> Settings:
        return Settings(
            INTERNAL_SERVICE_TOKEN=TOKEN,
            STORAGE_LOCAL_PATH=uploads,
            CHUNK_TARGET_TOKENS=120,
            CHUNK_OVERLAP_TOKENS=24,
            CHUNK_MIN_TOKENS=20,
        )

    app = create_app()
    app.dependency_overrides[get_settings] = _settings
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"x-internal-token": TOKEN},
    ) as client:
        yield client


def _pdf_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "document_id": "doc-1",
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
            await client.post(
                "/ingest", json=_pdf_body(chunker="fixed_window")
            )
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

    async def test_path_traversal_is_rejected(
        self, client: httpx.AsyncClient
    ) -> None:
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
            json=_pdf_body(
                filename="empty.pdf", storage_key="user-1/empty.pdf"
            ),
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
