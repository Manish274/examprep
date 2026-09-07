from __future__ import annotations

import pytest

from app.eval.metrics import (
    aggregate,
    evaluate_query,
    hit_rate,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)

RANKED = ["a", "b", "c", "d", "e"]


class TestRecall:
    def test_counts_relevant_chunks_found_in_the_cutoff(self) -> None:
        assert recall_at_k(RANKED, {"a", "c"}, k=3) == 1.0
        assert recall_at_k(RANKED, {"a", "e"}, k=3) == 0.5

    def test_is_zero_when_nothing_relevant_is_retrieved(self) -> None:
        assert recall_at_k(RANKED, {"z"}, k=5) == 0.0

    def test_ignores_position_within_the_cutoff(self) -> None:
        # Recall asks whether the material made it into the candidate set at
        # all; where it landed is NDCG's question.
        assert recall_at_k(["a", "z"], {"a"}, k=2) == recall_at_k(
            ["z", "a"], {"a"}, k=2
        )

    def test_an_empty_gold_set_scores_zero_rather_than_dividing_by_zero(self) -> None:
        assert recall_at_k(RANKED, set(), k=5) == 0.0


class TestPrecision:
    def test_measures_noise_in_the_returned_set(self) -> None:
        assert precision_at_k(RANKED, {"a", "b"}, k=2) == 1.0
        assert precision_at_k(RANKED, {"a"}, k=4) == 0.25

    def test_divides_by_k_not_by_results_returned(self) -> None:
        # A system returning one result when asked for five should not score
        # 1.0 for its reticence.
        assert precision_at_k(["a"], {"a"}, k=5) == pytest.approx(0.2)

    def test_zero_k_is_safe(self) -> None:
        assert precision_at_k(RANKED, {"a"}, k=0) == 0.0


class TestReciprocalRank:
    def test_rewards_an_early_first_hit(self) -> None:
        assert reciprocal_rank(RANKED, {"a"}) == 1.0
        assert reciprocal_rank(RANKED, {"b"}) == 0.5
        assert reciprocal_rank(RANKED, {"d"}) == 0.25

    def test_only_the_first_hit_counts(self) -> None:
        assert reciprocal_rank(RANKED, {"b", "c"}) == 0.5

    def test_is_zero_when_nothing_relevant_appears(self) -> None:
        assert reciprocal_rank(RANKED, {"z"}) == 0.0


class TestNdcg:
    def test_is_one_for_a_perfect_ranking(self) -> None:
        assert ndcg_at_k(["a", "b", "c"], {"a", "b"}, k=3) == pytest.approx(1.0)

    def test_penalises_a_relevant_result_ranked_late(self) -> None:
        # The distinction recall cannot make.
        early = ndcg_at_k(["a", "z", "y"], {"a"}, k=3)
        late = ndcg_at_k(["z", "y", "a"], {"a"}, k=3)
        assert early > late

    def test_supports_graded_relevance(self) -> None:
        # A chunk that fully answers should outrank one that merely mentions
        # the topic, and the metric should say so.
        graded = {"a": 1.0, "b": 0.3}
        good = ndcg_at_k(["a", "b"], {"a", "b"}, k=2, graded=graded)
        poor = ndcg_at_k(["b", "a"], {"a", "b"}, k=2, graded=graded)
        assert good > poor

    def test_is_zero_with_no_ground_truth(self) -> None:
        assert ndcg_at_k(RANKED, set(), k=5) == 0.0


class TestHitRate:
    def test_is_binary(self) -> None:
        assert hit_rate(RANKED, {"c"}, k=3) == 1.0
        assert hit_rate(RANKED, {"c"}, k=2) == 0.0


class TestEvaluateQuery:
    def test_reports_every_metric_at_every_cutoff(self) -> None:
        scores = evaluate_query(RANKED, {"a"}, k_values=(1, 3))

        assert set(scores) == {
            "mrr",
            "recall@1",
            "precision@1",
            "ndcg@1",
            "hit@1",
            "recall@3",
            "precision@3",
            "ndcg@3",
            "hit@3",
        }

    def test_a_perfect_result_scores_one_across_the_board(self) -> None:
        scores = evaluate_query(["a"], {"a"}, k_values=(1,))
        assert scores["recall@1"] == scores["precision@1"] == scores["mrr"] == 1.0


class TestAggregate:
    def test_averages_across_queries(self) -> None:
        assert aggregate([{"recall@5": 1.0}, {"recall@5": 0.0}])["recall@5"] == 0.5

    def test_weights_every_question_equally(self) -> None:
        # Macro-averaging: a question with many relevant chunks must not
        # dominate one with a single answer.
        combined = aggregate([{"mrr": 1.0}, {"mrr": 1.0}, {"mrr": 0.0}])
        assert combined["mrr"] == pytest.approx(2 / 3)

    def test_empty_input_yields_no_metrics(self) -> None:
        assert aggregate([]) == {}
