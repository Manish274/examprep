from __future__ import annotations

from app.core.models import Chunk, ChunkMetadata, RetrievalStrategy


def _chunk(**overrides: object) -> Chunk:
    fields: dict[str, object] = {
        "document_id": "doc-1",
        "document_name": "notes.pdf",
        "chunk_index": 0,
        "page_number": 42,
        "heading_path": ["Unit 3", "Normalization", "Third Normal Form"],
        "content_hash": "abc123",
    }
    fields.update(overrides)
    metadata = ChunkMetadata(**fields)  # type: ignore[arg-type]
    return Chunk(
        id="chunk-1",
        text="It must also satisfy 2NF.",
        token_count=8,
        metadata=metadata,
    )


class TestEmbeddingText:
    def test_prepends_document_and_heading_trail(self) -> None:
        # An isolated chunk loses its subject; the trail restores it.
        assert _chunk().embedding_text() == (
            "notes.pdf > Unit 3 > Normalization > Third Normal Form\n\n"
            "It must also satisfy 2NF."
        )

    def test_document_name_alone_when_no_headings(self) -> None:
        chunk = _chunk(heading_path=[])
        assert chunk.embedding_text().startswith("notes.pdf\n\n")

    def test_original_text_is_left_untouched(self) -> None:
        chunk = _chunk()
        assert chunk.text == "It must also satisfy 2NF."


class TestRetrievalStrategy:
    def test_covers_exactly_the_four_evaluated_strategies(self) -> None:
        assert {s.value for s in RetrievalStrategy} == {
            "bm25",
            "dense",
            "hybrid",
            "hybrid_rerank",
        }
