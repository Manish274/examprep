from __future__ import annotations

from app.retrieval.bm25 import Bm25Encoder, term_index, tokenize


class TestTokenize:
    def test_lowercases_and_stems(self) -> None:
        # Stemming is what lets a question about "normalization" reach source
        # material that says "normalized".
        assert tokenize("Normalization") == tokenize("normalized")

    def test_drops_stopwords(self) -> None:
        assert "the" not in tokenize("the relation")
        assert tokenize("the relation") == tokenize("relation")

    def test_keeps_negations_that_carry_meaning(self) -> None:
        # "no partial dependency" and "partial dependency" mean opposite
        # things; an over-eager stopword list would collapse them.
        assert tokenize("no partial dependency") != tokenize("partial dependency")

    def test_preserves_acronyms(self) -> None:
        assert "3nf" in tokenize("What is 3NF?")
        assert "bcnf" in tokenize("BCNF is stricter")

    def test_keeps_hyphenated_technical_terms_whole(self) -> None:
        # "B-tree" splitting into "b" and "tree" would match every mention of
        # a tree in the corpus.
        assert "b-tree" in tokenize("A B-tree index")

    def test_keeps_decimal_numbers_whole(self) -> None:
        assert "3.5" in tokenize("version 3.5 released")

    def test_drops_single_characters(self) -> None:
        assert tokenize("a b c relation") == tokenize("relation")

    def test_empty_and_symbol_only_input(self) -> None:
        assert tokenize("") == []
        assert tokenize("!!! ???") == []


class TestTermIndex:
    def test_is_deterministic_across_calls(self) -> None:
        assert term_index("normalization") == term_index("normalization")

    def test_differs_between_terms(self) -> None:
        assert term_index("2nf") != term_index("3nf")

    def test_fits_in_32_bits(self) -> None:
        # Qdrant sparse vector indices are u32.
        for term in ("relation", "3nf", "b-tree", "x" * 200):
            assert 0 <= term_index(term) <= 0xFFFFFFFF

    def test_is_stable_against_a_known_value(self) -> None:
        # Pins the hash. If this changes, every indexed sparse vector becomes
        # unmatchable by new queries and the corpus needs re-indexing.
        assert term_index("normal") == 3867909202


class TestEncoding:
    def test_document_weights_saturate_with_repetition(self) -> None:
        encoder = Bm25Encoder()
        once = encoder.encode_document("relation")
        many = encoder.encode_document("relation relation relation relation")

        # More occurrences weigh more, but far less than linearly -- the
        # fourth mention says little the first did not.
        assert many.values[0] > once.values[0]
        assert many.values[0] < 4 * once.values[0]

    def test_query_weights_are_binary(self) -> None:
        # A user typing a word twice should not double its influence.
        encoder = Bm25Encoder()
        vector = encoder.encode_query("relation relation relation")
        assert vector.values == [1.0]

    def test_indices_and_values_stay_aligned(self) -> None:
        vector = Bm25Encoder().encode_document("first normal form relation")
        assert len(vector.indices) == len(vector.values)
        assert len(set(vector.indices)) == len(vector.indices)

    def test_a_stopword_only_query_encodes_to_nothing(self) -> None:
        # Must be representable: the retriever checks for this and skips the
        # search rather than sending an empty vector Qdrant would reject.
        vector = Bm25Encoder().encode_query("the and of")
        assert vector.indices == []

    def test_document_and_query_agree_on_indices(self) -> None:
        # The whole mechanism depends on both sides hashing a term the same
        # way; if they diverge nothing ever matches.
        encoder = Bm25Encoder()
        doc = encoder.encode_document("third normal form")
        query = encoder.encode_query("normal form")

        assert set(query.indices).issubset(set(doc.indices))
