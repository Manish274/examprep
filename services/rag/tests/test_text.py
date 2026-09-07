from __future__ import annotations

import pytest

from app.core.text import (
    content_hash,
    looks_like_heading,
    normalize,
    split_sentences,
)
from app.core.tokenizer import HeuristicTokenCounter, get_token_counter


class TestNormalize:
    def test_rejoins_hyphenated_line_breaks(self) -> None:
        # PDF extraction splits words at the right margin. Left alone, "normal-
        # ization" is two tokens that match nothing.
        assert normalize("normal-\nization") == "normalization"

    def test_collapses_runs_of_spaces(self) -> None:
        assert normalize("a    b\tc") == "a b c"

    def test_strips_control_characters(self) -> None:
        assert normalize("clean\x00text\x07") == "cleantext"

    def test_collapses_excess_blank_lines(self) -> None:
        assert normalize("a\n\n\n\n\nb") == "a\n\nb"

    def test_normalises_unicode_so_hashes_are_stable(self) -> None:
        # Composed vs decomposed forms look identical but hash differently.
        assert normalize("café") == normalize("café")

    def test_empty_input_is_safe(self) -> None:
        assert normalize("") == ""


class TestSplitSentences:
    def test_splits_on_terminal_punctuation(self) -> None:
        assert split_sentences("One. Two! Three?") == ["One.", "Two!", "Three?"]

    def test_does_not_split_on_common_abbreviations(self) -> None:
        result = split_sentences("Use an index, e.g. a B-tree, for lookups.")
        assert len(result) == 1

    def test_does_not_split_on_initials(self) -> None:
        # "Edgar F. Codd" must survive as one sentence.
        result = split_sentences("It was introduced by Edgar F. Codd in 1970.")
        assert len(result) == 1

    def test_treats_newlines_as_hard_boundaries(self) -> None:
        # List items and headings carry no terminal punctuation but are still
        # separate units.
        assert split_sentences("First bullet\nSecond bullet") == [
            "First bullet",
            "Second bullet",
        ]

    def test_keeps_decimal_numbers_intact(self) -> None:
        assert len(split_sentences("Section 3.2 covers this.")) == 1

    def test_blank_input_yields_nothing(self) -> None:
        assert split_sentences("   \n  ") == []


class TestContentHash:
    def test_is_stable_across_whitespace_noise(self) -> None:
        # The embedding cache is keyed on this, so cosmetic differences must
        # not cause a re-embed.
        assert content_hash("a  b") == content_hash("a b")

    def test_differs_for_different_content(self) -> None:
        assert content_hash("1NF") != content_hash("2NF")

    def test_is_a_full_sha256(self) -> None:
        assert len(content_hash("x")) == 64


class TestLooksLikeHeading:
    @pytest.mark.parametrize(
        "text",
        [
            "Third Normal Form",
            "3.2 Normalization",
            "Chapter 4",
            "UNIT III",
            "Key Concepts:",
        ],
    )
    def test_accepts_heading_shaped_text(self, text: str) -> None:
        assert looks_like_heading(text)

    @pytest.mark.parametrize(
        "text",
        [
            "A relation is in third normal form if it is in 2NF.",
            "",
            "the quick brown fox jumps over the lazy dog and keeps running",
        ],
    )
    def test_rejects_prose(self, text: str) -> None:
        assert not looks_like_heading(text)

    def test_rejects_very_long_lines(self) -> None:
        assert not looks_like_heading("Word " * 60)


class TestTokenCounter:
    def test_counts_are_positive_and_scale_with_length(self) -> None:
        counter = get_token_counter()
        short = counter.count("hello")
        long = counter.count("hello " * 100)
        assert 0 < short < long

    def test_empty_string_is_zero(self) -> None:
        assert get_token_counter().count("") == 0

    def test_truncate_respects_the_budget(self) -> None:
        counter = get_token_counter()
        text = "word " * 500
        truncated = counter.truncate(text, 50)
        assert counter.count(truncated) <= 50

    def test_heuristic_fallback_is_deterministic(self) -> None:
        # The fallback keeps chunking working with no network access, so it
        # must behave consistently rather than merely not crash.
        counter = HeuristicTokenCounter()
        assert counter.count("a" * 400) == counter.count("b" * 400) == 100


class TestChunkId:
    def test_is_deterministic_for_the_same_content(self) -> None:
        # Random ids would be regenerated on every ingest, silently
        # invalidating any gold set that references them.
        from app.core.text import chunk_id_for

        assert chunk_id_for("doc-1", "A relation is in 3NF.") == chunk_id_for(
            "doc-1", "A relation is in 3NF."
        )

    def test_differs_across_documents(self) -> None:
        from app.core.text import chunk_id_for

        assert chunk_id_for("doc-1", "same text") != chunk_id_for("doc-2", "same text")

    def test_differs_for_different_content(self) -> None:
        from app.core.text import chunk_id_for

        assert chunk_id_for("doc-1", "2NF") != chunk_id_for("doc-1", "3NF")

    def test_survives_cosmetic_whitespace_changes(self) -> None:
        # Re-parsing can shift whitespace without changing meaning; a gold set
        # should survive that.
        from app.core.text import chunk_id_for

        assert chunk_id_for("d", "a  b") == chunk_id_for("d", "a b")

    def test_is_a_valid_uuid(self) -> None:
        # Qdrant point ids must be a UUID or an unsigned integer.
        import uuid as uuid_module

        from app.core.text import chunk_id_for

        uuid_module.UUID(chunk_id_for("doc-1", "text"))
