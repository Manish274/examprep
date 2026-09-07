from __future__ import annotations

from app.core.models import Chunk, ChunkMetadata, ScoredChunk
from app.core.tokenizer import HeuristicTokenCounter
from app.generation.context import (
    ContextBuilder,
    cited_markers,
    verify_citations,
)

COUNTER = HeuristicTokenCounter()


def _scored(
    text: str,
    *,
    chunk_id: str = "",
    page: int | None = 1,
    slide: int | None = None,
    heading_path: list[str] | None = None,
    name: str = "notes.pdf",
    index: int = 0,
) -> ScoredChunk:
    return ScoredChunk(
        chunk=Chunk(
            id=chunk_id or f"chunk-{index}",
            text=text,
            token_count=COUNTER.count(text),
            metadata=ChunkMetadata(
                document_id="doc-1",
                document_name=name,
                chunk_index=index,
                page_number=page,
                slide_number=slide,
                heading_path=heading_path or ["Normalization"],
                content_hash="h" * 64,
            ),
        ),
        score=1.0,
    )


def _builder(**kwargs: object) -> ContextBuilder:
    options: dict[str, object] = {
        "max_tokens": 500,
        "max_chunks": 8,
        "counter": COUNTER,
    }
    options.update(kwargs)
    return ContextBuilder(**options)  # type: ignore[arg-type]


class TestMarkersAndSources:
    def test_markers_are_sequential_and_match_the_sources(self) -> None:
        # A marker with no matching Source is a citation the student cannot
        # click; a Source with no marker never influenced the answer.
        context = _builder().build(
            [_scored("First passage.", index=0), _scored("Second passage.", index=1)]
        )

        assert "[S1]" in context.text
        assert "[S2]" in context.text
        assert [s.marker for s in context.sources] == ["S1", "S2"]

    def test_each_block_names_its_document_page_and_heading(self) -> None:
        context = _builder().build(
            [_scored("Content.", page=42, heading_path=["Unit 3", "Third Normal Form"])]
        )

        assert "notes.pdf" in context.text
        assert "p.42" in context.text
        assert "Third Normal Form" in context.text

    def test_slide_numbers_are_used_for_decks(self) -> None:
        context = _builder().build([_scored("Content.", page=None, slide=7)])
        assert "slide 7" in context.text

    def test_sources_carry_what_the_frontend_renders(self) -> None:
        [source] = _builder().build(
            [_scored("A relation is in 3NF.", chunk_id="c-9", page=42)]
        ).sources

        assert source.chunk_id == "c-9"
        assert source.page_number == 42
        assert source.document_name == "notes.pdf"
        assert "3NF" in source.snippet

    def test_long_snippets_are_cut_at_a_word_boundary(self) -> None:
        context = _builder(max_tokens=100_000).build([_scored("word " * 400)])
        snippet = context.sources[0].snippet

        assert snippet.endswith("…")
        assert "  " not in snippet


class TestDeduplication:
    def test_near_duplicates_are_dropped(self) -> None:
        # Chunk overlap means the same sentences arrive twice; spending budget
        # on a repeat is bad, showing the citation twice is worse.
        text = (
            "A relation is in third normal form if no non-prime "
            "attribute is transitive."
        )
        context = _builder().build([_scored(text, index=0), _scored(text, index=1)])

        assert len(context.sources) == 1
        assert context.dropped == 1

    def test_distinct_passages_on_one_topic_both_survive(self) -> None:
        context = _builder().build(
            [
                _scored(
                    "A relation is in second normal form if every non-prime "
                    "attribute depends on the whole key.",
                    index=0,
                ),
                _scored(
                    "A relation is in third normal form if no non-prime "
                    "attribute is transitively dependent.",
                    index=1,
                ),
            ]
        )
        assert len(context.sources) == 2

    def test_empty_chunks_are_skipped(self) -> None:
        context = _builder().build([_scored("   ", index=0), _scored("Real.", index=1)])
        assert len(context.sources) == 1


class TestBudget:
    def test_stops_admitting_once_the_token_budget_is_spent(self) -> None:
        chunks = [_scored(f"Passage {i}. " + "word " * 200, index=i) for i in range(10)]
        context = _builder(max_tokens=300).build(chunks)

        assert context.token_count <= 300
        assert context.dropped > 0

    def test_respects_the_chunk_ceiling(self) -> None:
        chunks = [_scored(f"Short passage {i}.", index=i) for i in range(20)]
        context = _builder(max_chunks=3).build(chunks)

        assert len(context.sources) == 3
        assert context.dropped == 17

    def test_preserves_rank_order_among_admitted_chunks(self) -> None:
        chunks = [_scored(f"Passage {i}.", index=i) for i in range(5)]
        context = _builder(max_chunks=3).build(chunks)

        assert [s.chunk_id for s in context.sources] == [
            "chunk-0",
            "chunk-1",
            "chunk-2",
        ]

    def test_the_header_counts_against_the_budget(self) -> None:
        # Measuring the raw chunk rather than the rendered block would overrun
        # the budget by the size of every header.
        context = _builder(max_tokens=100_000).build([_scored("Tiny.")])
        assert context.token_count > COUNTER.count("Tiny.")

    def test_a_later_shorter_chunk_can_still_fit(self) -> None:
        # Stopping at the first chunk that does not fit would waste the
        # remaining budget.
        context = _builder(max_tokens=120).build(
            [_scored("word " * 300, index=0), _scored("Short one.", index=1)]
        )
        assert [s.chunk_id for s in context.sources] == ["chunk-1"]


class TestEmptyContext:
    def test_no_chunks_produces_an_empty_context(self) -> None:
        context = _builder().build([])

        assert context.is_empty
        assert context.text == ""
        assert context.sources == []


class TestCitationVerification:
    def test_extracts_the_markers_a_model_used(self) -> None:
        assert cited_markers("Per [S1] and [S3], a relation is in 3NF.") == {"S1", "S3"}

    def test_finds_no_markers_in_an_uncited_answer(self) -> None:
        assert cited_markers("A relation is in 3NF.") == set()

    def test_resolves_citations_against_the_context(self) -> None:
        context = _builder().build(
            [_scored("First.", index=0), _scored("Second.", index=1)]
        )
        resolved, dangling = verify_citations("As [S2] says.", context.sources)

        assert [s.marker for s in resolved] == ["S2"]
        assert dangling == set()

    def test_flags_a_citation_that_does_not_exist(self) -> None:
        # A model can emit [S9] when three sources exist. Rendering that would
        # show a source that was never in the context.
        context = _builder().build([_scored("Only one.", index=0)])
        resolved, dangling = verify_citations("Per [S9].", context.sources)

        assert resolved == []
        assert dangling == {"S9"}

    def test_returns_resolved_sources_in_marker_order(self) -> None:
        context = _builder().build(
            [_scored(f"Passage {i}.", index=i) for i in range(3)]
        )
        resolved, _ = verify_citations("See [S3] and [S1].", context.sources)

        assert [s.marker for s in resolved] == ["S1", "S3"]
