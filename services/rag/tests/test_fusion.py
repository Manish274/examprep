from __future__ import annotations

import pytest

from app.core.models import Chunk, ChunkMetadata, ScoredChunk
from app.retrieval.fusion import ReciprocalRankFusion


def _scored(
    chunk_id: str,
    score: float,
    *,
    dense: float | None = None,
    sparse: float | None = None,
    index: int = 0,
) -> ScoredChunk:
    return ScoredChunk(
        chunk=Chunk(
            id=chunk_id,
            text=f"text for {chunk_id}",
            token_count=10,
            metadata=ChunkMetadata(
                document_id="doc-1",
                document_name="notes.pdf",
                chunk_index=index,
                content_hash="h" * 64,
            ),
        ),
        score=score,
        dense_score=dense,
        sparse_score=sparse,
    )


class TestReciprocalRankFusion:
    def test_computes_the_documented_formula(self) -> None:
        fused = ReciprocalRankFusion(k=60).fuse([[_scored("a", 0.9)]], top_k=10)
        assert fused[0].rrf_score == pytest.approx(1 / 61)

    def test_a_document_found_by_both_outranks_one_found_by_either(self) -> None:
        # The entire reason to fuse: agreement between two independent
        # retrievers is stronger evidence than one confident retriever.
        dense = [_scored("a", 0.9, dense=0.9), _scored("b", 0.8, dense=0.8)]
        sparse = [_scored("c", 9.0, sparse=9.0), _scored("a", 4.0, sparse=4.0)]

        fused = ReciprocalRankFusion().fuse([dense, sparse], top_k=10)
        assert fused[0].chunk.id == "a"

    def test_ignores_the_magnitude_of_incoming_scores(self) -> None:
        # Cosine sits in [-1,1] while BM25 is unbounded. Rank is all that is
        # used, so a huge BM25 score cannot swamp the dense side.
        dense = [_scored("a", 0.9, dense=0.9)]
        sparse = [_scored("b", 9999.0, sparse=9999.0)]

        fused = ReciprocalRankFusion().fuse([dense, sparse], top_k=10)
        assert fused[0].rrf_score == pytest.approx(fused[1].rrf_score)

    def test_preserves_per_retriever_ranks_and_scores(self) -> None:
        # The eval harness attributes wins to a stage using these; collapsing
        # them into one number would make that impossible.
        dense = [_scored("x", 0.1, dense=0.1), _scored("a", 0.9, dense=0.9)]
        sparse = [_scored("a", 4.0, sparse=4.0)]

        fused = ReciprocalRankFusion().fuse([dense, sparse], top_k=10)
        merged = next(f for f in fused if f.chunk.id == "a")

        assert merged.dense_rank == 2
        assert merged.sparse_rank == 1
        assert merged.dense_score == 0.9
        assert merged.sparse_score == 4.0

    def test_larger_k_flattens_the_advantage_of_rank_one(self) -> None:
        rankings = [[_scored("a", 1.0), _scored("b", 0.9)]]
        tight = ReciprocalRankFusion(k=1).fuse(rankings, top_k=10)
        loose = ReciprocalRankFusion(k=1000).fuse(rankings, top_k=10)

        tight_gap = tight[0].rrf_score - tight[1].rrf_score
        loose_gap = loose[0].rrf_score - loose[1].rrf_score
        assert tight_gap > loose_gap

    def test_respects_top_k(self) -> None:
        ranking = [_scored(f"c{i}", 1.0 - i / 100, index=i) for i in range(20)]
        assert len(ReciprocalRankFusion().fuse([ranking], top_k=5)) == 5

    def test_deduplicates_across_rankings(self) -> None:
        one = [_scored("a", 0.9, dense=0.9)]
        two = [_scored("a", 4.0, sparse=4.0)]
        assert len(ReciprocalRankFusion().fuse([one, two], top_k=10)) == 1

    def test_ties_break_deterministically(self) -> None:
        # Identical fused scores must not reorder between runs, or evaluation
        # stops being reproducible.
        a = [_scored("z-id", 1.0, index=5)]
        b = [_scored("a-id", 1.0, index=5)]

        first = ReciprocalRankFusion().fuse([a, b], top_k=10)
        second = ReciprocalRankFusion().fuse([b, a], top_k=10)
        assert [c.chunk.id for c in first] == [c.chunk.id for c in second]

    def test_does_not_mutate_the_input_rankings(self) -> None:
        # The eval harness inspects the per-retriever lists after fusing.
        dense = [_scored("a", 0.9, dense=0.9)]
        fused = ReciprocalRankFusion().fuse([dense, []], top_k=10)

        assert fused[0].score != 0.9
        assert dense[0].score == 0.9
        assert dense[0].rrf_score is None

    def test_handles_empty_input(self) -> None:
        assert ReciprocalRankFusion().fuse([], top_k=10) == []
        assert ReciprocalRankFusion().fuse([[], []], top_k=10) == []

    def test_rejects_a_nonsensical_k(self) -> None:
        with pytest.raises(ValueError, match="k must be positive"):
            ReciprocalRankFusion(k=0)
