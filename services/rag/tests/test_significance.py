"""Tests for the significance analysis.

The value of this module is entirely in not overstating a result, so the tests
are mostly about restraint: a small gap on few queries must come back
insignificant, and a p-value must not move between runs.
"""

from __future__ import annotations

from app.eval.significance import (
    _binomial_two_sided,
    compare,
    compare_all,
    paired_bootstrap,
    render,
)


def _rows(values: list[float], metric: str = "mrr") -> list[dict[str, float]]:
    return [{metric: v} for v in values]


class TestPairedBootstrap:
    def test_a_large_consistent_gap_is_significant(self) -> None:
        p, low, high = paired_bootstrap([0.5] * 8 + [0.4] * 8, resamples=2000)
        assert p < 0.05
        assert low > 0

    def test_a_gap_that_could_be_noise_is_not(self) -> None:
        # Four questions up, three down, by small amounts. A table of means
        # would show a difference; there is no evidence behind it.
        p, low, high = paired_bootstrap(
            [0.1, -0.1, 0.05, -0.08, 0.02, -0.03, 0.01], resamples=2000
        )
        assert p > 0.05
        assert low < 0 < high

    def test_no_difference_at_all_is_never_significant(self) -> None:
        p, low, high = paired_bootstrap([0.0] * 10, resamples=500)
        assert p == 1.0
        assert (low, high) == (0.0, 0.0)

    def test_the_p_value_is_reproducible(self) -> None:
        # A significance test that moves between runs is not evidence.
        differences = [0.3, -0.1, 0.2, 0.4, -0.2, 0.1, 0.25]
        first = paired_bootstrap(differences, resamples=1000)
        assert first == paired_bootstrap(differences, resamples=1000)

    def test_an_empty_set_reports_nothing_rather_than_dividing_by_zero(self) -> None:
        assert paired_bootstrap([]) == (1.0, 0.0, 0.0)


class TestSignTest:
    def test_an_even_split_is_not_evidence(self) -> None:
        assert _binomial_two_sided(5, 5) == 1.0

    def test_a_clean_sweep_of_ten_is(self) -> None:
        assert _binomial_two_sided(10, 0) < 0.01

    def test_seven_to_two_is_not_quite(self) -> None:
        # Roughly p = 0.18. Worth knowing, not worth acting on.
        assert 0.05 < _binomial_two_sided(7, 2) < 0.30

    def test_no_decisive_queries_means_no_result(self) -> None:
        assert _binomial_two_sided(0, 0) == 1.0


class TestCompare:
    def test_reports_the_means_the_difference_and_the_record(self) -> None:
        result = compare(
            "mrr",
            "dense",
            _rows([1.0, 1.0, 0.5, 1.0]),
            "bm25",
            _rows([0.5, 0.25, 0.5, 1.0]),
        )

        assert result is not None
        assert result.treatment_mean == 0.875
        assert result.baseline_mean == 0.5625
        assert result.difference == 0.3125
        assert (result.wins, result.losses, result.ties) == (2, 0, 2)

    def test_refuses_to_pair_runs_of_different_lengths(self) -> None:
        # A shorter run means one strategy errored on some queries. Pairing by
        # position would silently compare different questions.
        assert compare("mrr", "a", _rows([1.0, 1.0]), "b", _rows([1.0])) is None

    def test_a_missing_metric_is_not_invented(self) -> None:
        assert compare("ndcg@5", "a", _rows([1.0]), "b", _rows([1.0])) is None

    def test_an_empty_run_yields_nothing(self) -> None:
        assert compare("mrr", "a", [], "b", []) is None


class TestCompareAll:
    def test_every_strategy_is_measured_against_the_baseline(self) -> None:
        per_query = {
            "dense": _rows([1.0, 1.0, 0.5]),
            "bm25": _rows([0.5, 0.5, 0.25]),
            "hybrid_rerank": _rows([1.0, 0.5, 0.5]),
        }
        results = compare_all(per_query, "hybrid_rerank", metrics=("mrr",))

        assert {r.treatment for r in results} == {"dense", "bm25"}
        assert all(r.baseline == "hybrid_rerank" for r in results)

    def test_an_unknown_baseline_yields_nothing_rather_than_guessing(self) -> None:
        assert compare_all({"dense": _rows([1.0])}, "does-not-exist") == []


class TestRender:
    def test_says_so_when_there_is_nothing_to_compare(self) -> None:
        assert "no comparable" in render([])

    def test_the_table_carries_the_interval_not_only_the_verdict(self) -> None:
        results = compare_all(
            {"dense": _rows([1.0, 1.0, 0.5]), "bm25": _rows([0.5, 0.5, 0.25])},
            "bm25",
            metrics=("mrr",),
        )
        table = render(results)

        assert "dense" in table and "bm25" in table
        # With n this small the interval is the honest part of the output.
        assert "95% CI" in table
