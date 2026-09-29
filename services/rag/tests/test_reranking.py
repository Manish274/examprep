from __future__ import annotations

import pytest

from app.core.models import Chunk, ChunkMetadata, ScoredChunk
from app.reranking.rerankers import (
    GeminiListwiseReranker,
    JinaReranker,
    NoOpReranker,
)


def _scored(chunk_id: str, score: float, text: str = "passage") -> ScoredChunk:
    return ScoredChunk(
        chunk=Chunk(
            id=chunk_id,
            text=text,
            token_count=10,
            metadata=ChunkMetadata(
                document_id="doc-1",
                document_name="notes.pdf",
                chunk_index=0,
                heading_path=["Normalization"],
                content_hash="h" * 64,
            ),
        ),
        score=score,
        rrf_score=score,
    )


CANDIDATES = [
    _scored("a", 0.9, "Denormalization introduces redundancy."),
    _scored("b", 0.8, "A relation is in 3NF if no transitive dependency exists."),
    _scored("c", 0.7, "A B-tree index supports range queries."),
]


class TestNoOpReranker:
    async def test_preserves_retrieval_order(self) -> None:
        result = await NoOpReranker().rerank("query", CANDIDATES, top_n=3)
        assert [r.chunk.id for r in result] == ["a", "b", "c"]

    async def test_truncates_to_top_n(self) -> None:
        result = await NoOpReranker().rerank("query", CANDIDATES, top_n=2)
        assert len(result) == 2

    async def test_handles_empty_candidates(self) -> None:
        assert await NoOpReranker().rerank("query", [], top_n=5) == []


class TestJinaReranker:
    def test_requires_an_api_key(self) -> None:
        with pytest.raises(ValueError, match="requires an API key"):
            JinaReranker("")

    async def test_reorders_by_relevance_score(self, monkeypatch) -> None:
        # The reranker's whole purpose: promoting the passage that answers the
        # question above ones merely about the topic.
        async def fake_call(self, query, documents, top_n):
            return [
                {"index": 1, "relevance_score": 0.95},
                {"index": 0, "relevance_score": 0.10},
                {"index": 2, "relevance_score": 0.02},
            ]

        monkeypatch.setattr(JinaReranker, "_call", fake_call)
        result = await JinaReranker("key").rerank("what is 3NF", CANDIDATES, top_n=3)

        assert [r.chunk.id for r in result] == ["b", "a", "c"]
        assert result[0].rerank_score == 0.95
        assert result[0].score == 0.95

    async def test_keeps_earlier_stage_scores_for_the_eval_harness(
        self, monkeypatch
    ) -> None:
        async def fake_call(self, query, documents, top_n):
            return [{"index": 1, "relevance_score": 0.95}]

        monkeypatch.setattr(JinaReranker, "_call", fake_call)
        [result] = await JinaReranker("key").rerank("q", CANDIDATES, top_n=1)

        # Overwriting rrf_score would make it impossible to attribute a win to
        # reranking rather than to fusion.
        assert result.rrf_score == 0.8
        assert result.rerank_score == 0.95

    async def test_falls_back_to_retrieval_order_when_the_api_fails(
        self, monkeypatch
    ) -> None:
        # A degraded answer from unreranked candidates beats no answer.
        async def boom(self, query, documents, top_n):
            raise RuntimeError("upstream down")

        monkeypatch.setattr(JinaReranker, "_call", boom)
        result = await JinaReranker("key").rerank("q", CANDIDATES, top_n=2)

        assert [r.chunk.id for r in result] == ["a", "b"]

    async def test_can_be_configured_to_fail_loudly_instead(self, monkeypatch) -> None:
        # An evaluation run wants the failure surfaced, not silently absorbed.
        async def boom(self, query, documents, top_n):
            raise RuntimeError("upstream down")

        monkeypatch.setattr(JinaReranker, "_call", boom)
        with pytest.raises(RuntimeError):
            await JinaReranker("key", fail_open=False).rerank("q", CANDIDATES, top_n=2)

    async def test_ignores_an_out_of_range_index(self, monkeypatch) -> None:
        # A bad index would attach one chunk's score to another's text.
        async def fake_call(self, query, documents, top_n):
            return [
                {"index": 99, "relevance_score": 0.9},
                {"index": 1, "relevance_score": 0.8},
            ]

        monkeypatch.setattr(JinaReranker, "_call", fake_call)
        result = await JinaReranker("key").rerank("q", CANDIDATES, top_n=3)

        assert [r.chunk.id for r in result] == ["b"]

    async def test_falls_back_when_nothing_usable_comes_back(self, monkeypatch) -> None:
        async def fake_call(self, query, documents, top_n):
            return []

        monkeypatch.setattr(JinaReranker, "_call", fake_call)
        result = await JinaReranker("key").rerank("q", CANDIDATES, top_n=2)
        assert len(result) == 2

    async def test_empty_candidates_short_circuit(self) -> None:
        assert await JinaReranker("key").rerank("q", [], top_n=5) == []

    def test_sends_the_heading_trail_with_the_passage(self) -> None:
        # A cross-encoder judging "It must also satisfy 2NF" needs to know what
        # section it came from, exactly as the embedder does.
        from app.reranking.rerankers import _document_text

        text = _document_text(CANDIDATES[1])
        assert "Normalization" in text
        assert "3NF" in text


class TestGeminiListwiseReranker:
    def test_requires_an_api_key(self) -> None:
        with pytest.raises(ValueError, match="requires an API key"):
            GeminiListwiseReranker("")

    def test_parses_a_clean_ranking(self) -> None:
        assert GeminiListwiseReranker._parse_order("[2, 1, 3]", 3) == [1, 0, 2]

    def test_parses_a_ranking_wrapped_in_prose(self) -> None:
        # Models add commentary despite instructions.
        parsed = GeminiListwiseReranker._parse_order(
            "Here is the ranking:\n[3, 1, 2]\nHope that helps!", 3
        )
        assert parsed == [2, 0, 1]

    def test_discards_duplicates_and_out_of_range_positions(self) -> None:
        # Either would corrupt the mapping back onto candidates.
        assert GeminiListwiseReranker._parse_order("[1, 1, 9, 2]", 3) == [0, 1]

    def test_rejects_a_response_with_no_ranking(self) -> None:
        with pytest.raises(ValueError, match="no ranking found"):
            GeminiListwiseReranker._parse_order("I cannot rank these.", 3)

    async def test_appends_candidates_the_model_omitted(self, monkeypatch) -> None:
        # A truncated response must not silently drop candidates entirely.
        async def fake_ask(self, prompt, count):
            return [1]

        monkeypatch.setattr(GeminiListwiseReranker, "_ask", fake_ask)
        result = await GeminiListwiseReranker("key").rerank("q", CANDIDATES, top_n=3)

        assert [r.chunk.id for r in result] == ["b", "a", "c"]
        assert result[0].rerank_score > result[1].rerank_score

    async def test_falls_back_when_the_model_fails(self, monkeypatch) -> None:
        async def boom(self, prompt, count):
            raise RuntimeError("quota exhausted")

        monkeypatch.setattr(GeminiListwiseReranker, "_ask", boom)
        result = await GeminiListwiseReranker("key").rerank("q", CANDIDATES, top_n=2)

        assert [r.chunk.id for r in result] == ["a", "b"]
