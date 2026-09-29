from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.models import Chunk, ChunkMetadata, RetrievalStrategy, ScoredChunk
from app.eval.harness import (
    EvaluationHarness,
    GoldQuery,
    load_gold_set,
    save_gold_set,
)


def _scored(chunk_id: str) -> ScoredChunk:
    return ScoredChunk(
        chunk=Chunk(
            id=chunk_id,
            text=f"passage {chunk_id}",
            token_count=5,
            metadata=ChunkMetadata(
                document_id="doc-1",
                document_name="notes.pdf",
                chunk_index=0,
                content_hash="h" * 64,
            ),
        ),
        score=1.0,
    )


class FakeRetrieval:
    """Returns a fixed ranking per strategy, so the harness can be tested
    without any infrastructure and with a known correct answer."""

    def __init__(self, rankings: dict[RetrievalStrategy, list[str]]) -> None:
        self.rankings = rankings
        self.calls: list[tuple[str, RetrievalStrategy]] = []

    async def search(self, query, *, user_id, strategy, document_ids=None, top_k=10):
        self.calls.append((query, strategy))
        return [_scored(c) for c in self.rankings.get(strategy, [])][:top_k]


class ExplodingRetrieval:
    def __init__(self, fail_on: str) -> None:
        self.fail_on = fail_on

    async def search(self, query, *, user_id, strategy, document_ids=None, top_k=10):
        if query == self.fail_on:
            raise RuntimeError("retrieval exploded")
        return [_scored("right")]


GOLD = [
    GoldQuery(question="what is 3NF", relevant_chunk_ids={"right"}),
    GoldQuery(question="what is an index", relevant_chunk_ids={"right"}),
]


class TestGoldSetIO:
    def test_round_trips_through_jsonl(self, tmp_path: Path) -> None:
        path = tmp_path / "gold.jsonl"
        save_gold_set(GOLD, path)
        loaded = load_gold_set(path)

        assert [g.question for g in loaded] == [g.question for g in GOLD]
        assert loaded[0].relevant_chunk_ids == {"right"}

    def test_one_question_per_line_stays_diffable(self, tmp_path: Path) -> None:
        path = tmp_path / "gold.jsonl"
        save_gold_set(GOLD, path)

        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln]
        assert len(lines) == 2
        assert all(json.loads(ln)["question"] for ln in lines)

    def test_preserves_graded_relevance(self, tmp_path: Path) -> None:
        path = tmp_path / "gold.jsonl"
        save_gold_set(
            [
                GoldQuery(
                    question="q",
                    relevant_chunk_ids={"a", "b"},
                    graded={"a": 1.0, "b": 0.4},
                )
            ],
            path,
        )
        assert load_gold_set(path)[0].graded == {"a": 1.0, "b": 0.4}

    def test_skips_blank_lines_and_comments(self, tmp_path: Path) -> None:
        path = tmp_path / "gold.jsonl"
        path.write_text(
            '// a comment\n\n{"question":"q","relevant_chunk_ids":["a"]}\n',
            encoding="utf-8",
        )
        assert len(load_gold_set(path)) == 1

    def test_a_malformed_line_names_its_line_number(self, tmp_path: Path) -> None:
        # A gold set is edited by hand; the error has to be findable.
        path = tmp_path / "gold.jsonl"
        path.write_text(
            '{"question":"ok","relevant_chunk_ids":["a"]}\nnot json\n', encoding="utf-8"
        )
        with pytest.raises(ValueError, match=":2"):
            load_gold_set(path)

    def test_an_empty_file_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "gold.jsonl"
        path.write_text("\n\n", encoding="utf-8")
        with pytest.raises(ValueError, match="no gold queries"):
            load_gold_set(path)


class TestHarness:
    async def test_runs_every_strategy_over_every_query(self) -> None:
        retrieval = FakeRetrieval({s: ["right"] for s in RetrievalStrategy})
        report = await EvaluationHarness(retrieval).run(GOLD, user_id="u")

        assert report.query_count == 2
        assert len(report.strategies) == 4
        assert len(retrieval.calls) == 8

    async def test_scores_a_perfect_retriever_at_one(self) -> None:
        retrieval = FakeRetrieval({s: ["right"] for s in RetrievalStrategy})
        report = await EvaluationHarness(retrieval).run(GOLD, user_id="u")

        for result in report.strategies:
            assert result.metrics["recall@5"] == 1.0
            assert result.metrics["mrr"] == 1.0

    async def test_scores_a_useless_retriever_at_zero(self) -> None:
        retrieval = FakeRetrieval({s: ["wrong"] for s in RetrievalStrategy})
        report = await EvaluationHarness(retrieval).run(GOLD, user_id="u")

        assert report.strategies[0].metrics["recall@5"] == 0.0

    async def test_distinguishes_strategies(self) -> None:
        # The entire purpose: showing that one strategy beats another on this
        # material rather than assuming it does.
        retrieval = FakeRetrieval(
            {
                RetrievalStrategy.BM25: ["wrong", "wrong2", "right"],
                RetrievalStrategy.DENSE: ["wrong", "right"],
                RetrievalStrategy.HYBRID: ["right"],
                RetrievalStrategy.HYBRID_RERANK: ["right"],
            }
        )
        report = await EvaluationHarness(retrieval).run(GOLD, user_id="u")
        by_name = {s.strategy: s.metrics["mrr"] for s in report.strategies}

        assert by_name["hybrid"] > by_name["dense"] > by_name["bm25"]

    async def test_names_the_winner(self) -> None:
        retrieval = FakeRetrieval(
            {
                RetrievalStrategy.BM25: ["wrong"],
                RetrievalStrategy.DENSE: ["wrong"],
                RetrievalStrategy.HYBRID: ["wrong"],
                RetrievalStrategy.HYBRID_RERANK: ["right"],
            }
        )
        report = await EvaluationHarness(retrieval).run(GOLD, user_id="u")
        best = report.best_by("mrr")

        assert best is not None
        assert best.strategy == "hybrid_rerank"

    async def test_one_failing_query_does_not_abandon_the_run(self) -> None:
        report = await EvaluationHarness(ExplodingRetrieval("what is 3NF")).run(
            GOLD, user_id="u", strategies=[RetrievalStrategy.HYBRID]
        )
        result = report.strategies[0]

        assert result.failures == ["what is 3NF"]
        # The surviving query still scored, so the run is usable.
        assert result.metrics["recall@5"] == 1.0

    async def test_records_the_configuration_that_produced_the_result(self) -> None:
        # A metric without the config behind it cannot be compared to anything
        # later.
        retrieval = FakeRetrieval({s: ["right"] for s in RetrievalStrategy})
        report = await EvaluationHarness(retrieval).run(
            GOLD, user_id="u", config={"reranker": "jina", "dimensions": 3072}
        )
        assert report.config["reranker"] == "jina"

    async def test_honours_a_per_query_document_filter(self) -> None:
        captured: list[list[str] | None] = []

        class Capturing(FakeRetrieval):
            async def search(
                self, query, *, user_id, strategy, document_ids=None, top_k=10
            ):
                captured.append(document_ids)
                return await super().search(
                    query,
                    user_id=user_id,
                    strategy=strategy,
                    document_ids=document_ids,
                    top_k=top_k,
                )

        gold = [
            GoldQuery(
                question="q", relevant_chunk_ids={"right"}, document_ids=["doc-9"]
            )
        ]
        await EvaluationHarness(Capturing({RetrievalStrategy.HYBRID: ["right"]})).run(
            gold, user_id="u", strategies=[RetrievalStrategy.HYBRID]
        )
        assert captured == [["doc-9"]]


class TestReportTable:
    async def test_renders_one_row_per_strategy(self) -> None:
        retrieval = FakeRetrieval({s: ["right"] for s in RetrievalStrategy})
        report = await EvaluationHarness(retrieval).run(GOLD, user_id="u")
        table = report.to_table()

        for strategy in ("bm25", "dense", "hybrid", "hybrid_rerank"):
            assert strategy in table
        assert "recall@5" in table

    async def test_marks_the_best_value_in_each_column(self) -> None:
        retrieval = FakeRetrieval(
            {
                RetrievalStrategy.BM25: ["wrong"],
                RetrievalStrategy.DENSE: ["wrong"],
                RetrievalStrategy.HYBRID: ["wrong"],
                RetrievalStrategy.HYBRID_RERANK: ["right"],
            }
        )
        report = await EvaluationHarness(retrieval).run(GOLD, user_id="u")
        winner_row = next(
            line
            for line in report.to_table().splitlines()
            if line.startswith("hybrid_rerank")
        )
        assert "*" in winner_row

    async def test_serialises_to_json_safe_output(self) -> None:
        retrieval = FakeRetrieval({s: ["right"] for s in RetrievalStrategy})
        report = await EvaluationHarness(retrieval).run(GOLD, user_id="u")

        payload = json.dumps(report.to_dict())
        assert "hybrid_rerank" in payload
