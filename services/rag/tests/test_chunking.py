from __future__ import annotations

import pytest

from app.chunking.fixed import FixedWindowChunker
from app.chunking.structural import StructuralChunker
from app.core.models import (
    BlockType,
    DocumentKind,
    ParsedBlock,
    ParsedDocument,
)
from app.core.tokenizer import HeuristicTokenCounter


def _doc(*blocks: ParsedBlock, filename: str = "notes.pdf") -> ParsedDocument:
    return ParsedDocument(
        document_id="doc-1",
        filename=filename,
        kind=DocumentKind.PDF,
        blocks=list(blocks),
        page_count=1,
        parser="test",
    )


def _heading(text: str, level: int, order: int, page: int = 1) -> ParsedBlock:
    return ParsedBlock(
        text=text,
        type=BlockType.HEADING,
        page_number=page,
        level=level,
        order=order,
    )


def _para(text: str, order: int, page: int = 1) -> ParsedBlock:
    return ParsedBlock(
        text=text, type=BlockType.PARAGRAPH, page_number=page, order=order
    )


# A deterministic counter keeps these tests independent of whether the tiktoken
# vocabulary is downloadable in the environment they run in.
COUNTER = HeuristicTokenCounter()


def _chunker(**kwargs: int) -> StructuralChunker:
    options: dict[str, object] = {
        "target_tokens": 100,
        "overlap_tokens": 20,
        "min_tokens": 10,
        "counter": COUNTER,
    }
    options.update(kwargs)
    return StructuralChunker(**options)  # type: ignore[arg-type]


class TestHeadingBoundaries:
    def test_never_merges_two_sections_into_one_chunk(self) -> None:
        # The rule that matters most: two topics in one chunk means every query
        # matching either drags in the other as noise.
        document = _doc(
            _heading("First Normal Form", 2, 0),
            _para("Atomic values only.", 1),
            _heading("Second Normal Form", 2, 2),
            _para("No partial dependency.", 3),
        )
        chunks = _chunker().chunk(document)

        assert len(chunks) == 2
        assert chunks[0].metadata.heading == "First Normal Form"
        assert chunks[1].metadata.heading == "Second Normal Form"
        assert "partial" not in chunks[0].text

    def test_records_the_full_heading_ancestry(self) -> None:
        document = _doc(
            _heading("Unit 3", 1, 0),
            _heading("Normalization", 2, 1),
            _heading("Third Normal Form", 3, 2),
            _para("A relation is in 3NF when it is in 2NF.", 3),
        )
        chunk = _chunker().chunk(document)[0]

        assert chunk.metadata.heading_path == [
            "Unit 3",
            "Normalization",
            "Third Normal Form",
        ]
        assert chunk.metadata.section == "Unit 3"
        assert chunk.metadata.heading == "Third Normal Form"

    def test_heading_text_is_metadata_not_chunk_body(self) -> None:
        # Duplicating the heading into the text would double-count its terms in
        # BM25. It reaches the embedding through embedding_text() instead.
        document = _doc(
            _heading("Third Normal Form", 2, 0),
            _para("A relation is in 3NF when it is in 2NF.", 1),
        )
        chunk = _chunker().chunk(document)[0]

        assert not chunk.text.startswith("Third Normal Form")
        assert "Third Normal Form" in chunk.embedding_text()

    def test_content_before_any_heading_is_still_captured(self) -> None:
        document = _doc(_para("Preface text with no heading above it.", 0))
        chunks = _chunker().chunk(document)

        assert len(chunks) == 1
        assert chunks[0].metadata.heading is None
        assert chunks[0].metadata.heading_path == []


class TestSentenceBoundaries:
    def test_splits_long_sections_without_cutting_a_sentence(self) -> None:
        sentences = [f"This is sentence number {i} in the section." for i in range(40)]
        document = _doc(
            _heading("Long Section", 1, 0),
            _para(" ".join(sentences), 1),
        )
        chunks = _chunker(target_tokens=60, overlap_tokens=0).chunk(document)

        assert len(chunks) > 1
        for chunk in chunks:
            assert chunk.text.rstrip().endswith(".")

    def test_respects_the_token_target(self) -> None:
        sentences = [f"Sentence {i} has a few words in it." for i in range(60)]
        document = _doc(_para(" ".join(sentences), 0))
        chunks = _chunker(target_tokens=80, overlap_tokens=0).chunk(document)

        for chunk in chunks:
            assert COUNTER.count(chunk.text) <= 80 * 1.2


class TestOverlap:
    def test_carries_context_between_chunks_of_one_section(self) -> None:
        sentences = [f"Alpha {i} beta gamma delta epsilon zeta." for i in range(40)]
        document = _doc(
            _heading("Section", 1, 0), _para(" ".join(sentences), 1)
        )
        chunks = _chunker(target_tokens=60, overlap_tokens=20).chunk(document)

        assert len(chunks) > 1
        tail = chunks[0].text.split(".")[-2].strip()
        assert tail and tail in chunks[1].text

    def test_no_overlap_across_a_heading(self) -> None:
        # Overlap exists for continuity within a topic. Carrying it across a
        # heading would reintroduce the contamination heading splits prevent.
        document = _doc(
            _heading("Alpha Section", 1, 0),
            _para("Unique alpha content here that is distinctive.", 1),
            _heading("Beta Section", 1, 2),
            _para("Unique beta content here that is distinctive.", 3),
        )
        chunks = _chunker(overlap_tokens=50).chunk(document)
        assert "alpha" not in chunks[1].text.lower()

    def test_overlap_is_capped_below_the_target(self) -> None:
        # An overlap at or above the target would make every chunk pure repeat.
        chunker = StructuralChunker(
            target_tokens=100, overlap_tokens=500, counter=COUNTER
        )
        assert chunker.overlap_tokens <= 50


class TestAtomicBlocks:
    def test_tables_are_never_split(self) -> None:
        table = ParsedBlock(
            text="\n".join(f"Row {i} | value {i} | detail {i}" for i in range(6)),
            type=BlockType.TABLE,
            page_number=1,
            order=1,
        )
        document = _doc(_heading("Comparison", 1, 0), table)
        chunks = _chunker(target_tokens=40).chunk(document)

        table_chunks = [c for c in chunks if "Row 0" in c.text]
        assert len(table_chunks) == 1
        assert "Row 5" in table_chunks[0].text

    def test_an_enormous_table_is_split_on_row_boundaries(self) -> None:
        # Overflow is tolerated up to a point; beyond it, splitting on rows at
        # least keeps each piece internally coherent.
        table = ParsedBlock(
            text="\n".join(
                f"Row {i} | a fairly long value {i} | more detail {i}"
                for i in range(200)
            ),
            type=BlockType.TABLE,
            page_number=1,
            order=0,
        )
        chunks = _chunker(target_tokens=100).chunk(_doc(table))

        assert len(chunks) > 1
        for chunk in chunks:
            assert "Row" in chunk.text

    def test_speaker_notes_stay_whole(self) -> None:
        note = ParsedBlock(
            text="An index is a trade. You buy read speed with write speed.",
            type=BlockType.SPEAKER_NOTE,
            slide_number=1,
            order=1,
        )
        document = _doc(_heading("Indexing", 1, 0), note)
        chunks = _chunker(target_tokens=15).chunk(document)

        note_chunks = [c for c in chunks if "trade" in c.text]
        assert len(note_chunks) == 1
        assert "write speed" in note_chunks[0].text


class TestMetadata:
    def test_chunk_indexes_are_sequential_from_zero(self) -> None:
        document = _doc(
            _heading("A", 1, 0),
            _para("Alpha content.", 1),
            _heading("B", 1, 2),
            _para("Beta content.", 3),
            _heading("C", 1, 4),
            _para("Gamma content.", 5),
        )
        chunks = _chunker().chunk(document)
        assert [c.metadata.chunk_index for c in chunks] == list(range(len(chunks)))

    def test_page_number_comes_from_the_first_block_in_the_chunk(self) -> None:
        document = _doc(
            _heading("Section", 1, 0, page=4),
            _para("Content on page four.", 1, page=4),
        )
        chunk = _chunker().chunk(document)[0]
        assert chunk.metadata.page_number == 4
        assert chunk.metadata.slide_number is None

    def test_slide_number_is_preserved_for_decks(self) -> None:
        block = ParsedBlock(
            text="An index improves lookup speed.",
            type=BlockType.PARAGRAPH,
            slide_number=7,
            order=0,
        )
        chunk = _chunker().chunk(_doc(block))[0]
        assert chunk.metadata.slide_number == 7
        assert chunk.metadata.page_number is None

    def test_character_offsets_advance_through_the_document(self) -> None:
        document = _doc(
            _heading("A", 1, 0),
            _para("First paragraph of content.", 1),
            _heading("B", 1, 2),
            _para("Second paragraph of content.", 3),
        )
        chunks = _chunker().chunk(document)

        for chunk in chunks:
            assert chunk.metadata.char_start is not None
            assert chunk.metadata.char_end is not None
            assert chunk.metadata.char_end > chunk.metadata.char_start
        assert chunks[1].metadata.char_start > chunks[0].metadata.char_start

    def test_document_name_rides_along_for_citations(self) -> None:
        document = _doc(_para("Some content.", 0), filename="lecture-3.pdf")
        assert _chunker().chunk(document)[0].metadata.document_name == "lecture-3.pdf"

    def test_content_hash_is_set_for_cache_lookups(self) -> None:
        chunk = _chunker().chunk(_doc(_para("Some content.", 0)))[0]
        assert len(chunk.metadata.content_hash) == 64


class TestUndersizedChunks:
    def test_a_thin_section_merges_into_its_nested_child(self) -> None:
        # "3.1 Overview" with one sentence under it would otherwise become a
        # chunk too thin to ever rank.
        document = _doc(
            _heading("Overview", 1, 0),
            _para("Brief.", 1),
            _heading("Overview Details", 2, 2),
            _para("Much more substantial content follows in this section.", 3),
        )
        chunks = _chunker(min_tokens=40).chunk(document)

        assert len(chunks) == 1
        assert "Brief." in chunks[0].text
        assert "substantial" in chunks[0].text

    def test_merging_keeps_the_more_specific_heading_path(self) -> None:
        # The deeper path contains the shallower one as a prefix, so keeping it
        # loses nothing. Keeping the parent instead would erase the child
        # heading from the index -- and that heading is often the exact phrase
        # a student searches for.
        document = _doc(
            _heading("Normalization", 1, 0),
            _para("Brief intro.", 1),
            _heading("First Normal Form", 2, 2),
            _para("A relation is in 1NF when every attribute is atomic.", 3),
        )
        chunks = _chunker(min_tokens=40).chunk(document)

        assert len(chunks) == 1
        assert chunks[0].metadata.heading_path == [
            "Normalization",
            "First Normal Form",
        ]
        assert chunks[0].metadata.heading == "First Normal Form"

    def test_unrelated_thin_sections_are_not_merged(self) -> None:
        document = _doc(
            _heading("Alpha", 1, 0),
            _para("Brief alpha.", 1),
            _heading("Beta", 1, 2),
            _para("Brief beta.", 3),
        )
        chunks = _chunker(min_tokens=40).chunk(document)

        assert len(chunks) == 2


class TestEdgeCases:
    def test_an_empty_document_yields_no_chunks(self) -> None:
        assert _chunker().chunk(_doc()) == []

    def test_a_document_of_only_headings_yields_no_chunks(self) -> None:
        # Headings become metadata, so a table of contents produces nothing to
        # retrieve -- which is correct.
        document = _doc(_heading("A", 1, 0), _heading("B", 1, 1))
        assert _chunker().chunk(document) == []

    def test_whitespace_only_blocks_are_dropped(self) -> None:
        document = _doc(_para("   ", 0), _para("Real content here.", 1))
        chunks = _chunker().chunk(document)
        assert len(chunks) == 1

    def test_rejects_a_nonsensical_target(self) -> None:
        with pytest.raises(ValueError, match="target_tokens"):
            StructuralChunker(target_tokens=0)


class TestFixedWindowBaseline:
    def test_produces_chunks_of_roughly_the_target_size(self) -> None:
        document = _doc(
            _para(" ".join(f"word{i}" for i in range(600)), 0)
        )
        chunks = FixedWindowChunker(
            target_tokens=100, overlap_tokens=10, counter=COUNTER
        ).chunk(document)

        assert len(chunks) > 1
        assert all(c.token_count <= 140 for c in chunks)

    def test_ignores_structure_which_is_the_point_of_the_baseline(self) -> None:
        # It exists to quantify what structure-aware chunking buys. If it
        # preserved heading paths there would be nothing to compare.
        document = _doc(
            _heading("Alpha", 1, 0),
            _para("Alpha content here.", 1),
            _heading("Beta", 1, 2),
            _para("Beta content here.", 3),
        )
        chunks = FixedWindowChunker(
            target_tokens=1000, overlap_tokens=0, counter=COUNTER
        ).chunk(document)

        assert len(chunks) == 1
        assert chunks[0].metadata.heading_path == []
        assert "Alpha content" in chunks[0].text
        assert "Beta content" in chunks[0].text

    def test_still_records_a_page_number_for_citations(self) -> None:
        document = _doc(_para("Content on a page.", 0, page=3))
        chunks = FixedWindowChunker(counter=COUNTER).chunk(document)
        assert chunks[0].metadata.page_number == 3

    def test_empty_document_yields_no_chunks(self) -> None:
        assert FixedWindowChunker(counter=COUNTER).chunk(_doc()) == []

    def test_rejects_overlap_larger_than_the_window(self) -> None:
        with pytest.raises(ValueError, match="overlap_tokens"):
            FixedWindowChunker(target_tokens=100, overlap_tokens=100)
