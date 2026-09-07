"""Is the difference real, or is it nineteen questions of noise?

The baseline run put dense at 0.939 MRR and hybrid at 0.800 and concluded dense
was better. On nineteen questions that conclusion deserves a number attached to
it: two or three questions falling the other way would have reversed the
ordering, and nothing in a table of means would have shown it.

Two tests, deliberately both:

**Paired bootstrap.** Resamples the per-query differences and asks how often
the mean lands on the other side of zero. Uses the pairing -- the same
questions went through both strategies, so comparing them query by query
removes the variance that comes from some questions simply being harder.

**Sign test.** Counts wins and losses and ignores their size. It answers a
different question -- "does this strategy win more often?" rather than "does it
win by more?" -- and it is the one to trust when a single dramatic query is
doing all the work.

They can disagree. When they do, that disagreement is the finding.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

DEFAULT_RESAMPLES = 10_000
# Fixed, so re-running the analysis on the same report reproduces the same
# p-value. A significance test that moves between runs is not evidence.
DEFAULT_SEED = 20260907


@dataclass
class Comparison:
    """One metric, two strategies, the same queries."""

    metric: str
    treatment: str
    baseline: str
    n: int
    treatment_mean: float
    baseline_mean: float
    difference: float
    ci_low: float
    ci_high: float
    p_bootstrap: float
    wins: int
    losses: int
    ties: int
    p_sign: float

    @property
    def significant(self) -> bool:
        """At the conventional 5%, on the bootstrap.

        Reported as a flag but never as a verdict on its own: with n below
        about thirty, "not significant" mostly means "not enough questions".
        """
        return self.p_bootstrap < 0.05

    def summary(self) -> str:
        verdict = "significant" if self.significant else "not significant"
        return (
            f"{self.metric}: {self.treatment} {self.treatment_mean:.3f} vs "
            f"{self.baseline} {self.baseline_mean:.3f} "
            f"(diff {self.difference:+.3f}, 95% CI "
            f"[{self.ci_low:+.3f}, {self.ci_high:+.3f}], "
            f"p={self.p_bootstrap:.3f} {verdict}; "
            f"{self.wins}W-{self.losses}L-{self.ties}T, sign p={self.p_sign:.3f})"
        )


def paired_bootstrap(
    differences: Sequence[float],
    *,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> tuple[float, float, float]:
    """Returns (p_value, ci_low, ci_high) for the mean difference."""
    n = len(differences)
    if n == 0:
        return 1.0, 0.0, 0.0
    if all(d == differences[0] for d in differences):
        # Zero variance: the bootstrap has nothing to resample. A constant
        # non-zero difference is as certain as this method can report.
        return (1.0 if differences[0] == 0 else 0.0), differences[0], differences[0]

    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(resamples):
        total = 0.0
        for _ in range(n):
            total += differences[rng.randrange(n)]
        means.append(total / n)
    means.sort()

    # Two-sided: how much of the resampled distribution sits on the far side
    # of zero from the observed effect.
    below = sum(1 for m in means if m <= 0.0)
    above = sum(1 for m in means if m >= 0.0)
    p = 2.0 * min(below, above) / resamples
    p = min(1.0, p)

    low = means[int(0.025 * resamples)]
    high = means[min(resamples - 1, int(0.975 * resamples))]
    return p, low, high


def _binomial_two_sided(wins: int, losses: int) -> float:
    """Exact two-sided sign test under p=0.5.

    Written out rather than pulled from scipy: it is eight lines, and adding a
    hundred megabytes of dependency to a service that must stay deployable on a
    free tier is a poor trade.
    """
    trials = wins + losses
    if trials == 0:
        return 1.0

    def pmf(k: int) -> float:
        return math.comb(trials, k) * 0.5**trials

    observed = pmf(wins)
    # Sum every outcome at least as extreme as the one seen, which is the
    # definition of a two-sided exact test.
    total = sum(pmf(k) for k in range(trials + 1) if pmf(k) <= observed + 1e-12)
    return min(1.0, total)


def compare(
    metric: str,
    treatment_name: str,
    treatment: Sequence[dict[str, float]],
    baseline_name: str,
    baseline: Sequence[dict[str, float]],
    *,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> Comparison | None:
    """Compares two strategies on one metric, query by query.

    Returns None when the two runs are not comparable -- a different number of
    queries means one strategy errored on some, and pairing them by position
    would silently compare different questions.
    """
    if not treatment or len(treatment) != len(baseline):
        return None
    if metric not in treatment[0] or metric not in baseline[0]:
        return None

    a = [q[metric] for q in treatment]
    b = [q[metric] for q in baseline]
    differences = [x - y for x, y in zip(a, b, strict=True)]

    p, low, high = paired_bootstrap(differences, resamples=resamples, seed=seed)
    wins = sum(1 for d in differences if d > 0)
    losses = sum(1 for d in differences if d < 0)
    ties = len(differences) - wins - losses

    return Comparison(
        metric=metric,
        treatment=treatment_name,
        baseline=baseline_name,
        n=len(differences),
        treatment_mean=sum(a) / len(a),
        baseline_mean=sum(b) / len(b),
        difference=sum(differences) / len(differences),
        ci_low=low,
        ci_high=high,
        p_bootstrap=p,
        wins=wins,
        losses=losses,
        ties=ties,
        p_sign=_binomial_two_sided(wins, losses),
    )


def compare_all(
    per_query: dict[str, list[dict[str, float]]],
    baseline_name: str,
    *,
    metrics: Sequence[str] = ("mrr", "ndcg@5", "recall@5"),
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> list[Comparison]:
    """Every strategy against one baseline, across several metrics."""
    baseline = per_query.get(baseline_name)
    if not baseline:
        return []

    results: list[Comparison] = []
    for name, rows in per_query.items():
        if name == baseline_name:
            continue
        for metric in metrics:
            comparison = compare(
                metric,
                name,
                rows,
                baseline_name,
                baseline,
                resamples=resamples,
                seed=seed,
            )
            if comparison is not None:
                results.append(comparison)
    return results


def render(comparisons: Sequence[Comparison]) -> str:
    """A table, because the point is reading several comparisons together."""
    if not comparisons:
        return "no comparable strategy pairs"

    header = (
        f"{'metric':<12}{'strategy':<16}{'vs':<16}"
        f"{'diff':>9}{'95% CI':>20}{'p':>8}{'W-L-T':>10}{'sign p':>9}"
    )
    lines = [header, "-" * len(header)]
    for c in comparisons:
        ci = f"[{c.ci_low:+.3f},{c.ci_high:+.3f}]"
        mark = "*" if c.significant else " "
        lines.append(
            f"{c.metric:<12}{c.treatment:<16}{c.baseline:<16}"
            f"{c.difference:>+9.3f}{ci:>20}{c.p_bootstrap:>7.3f}{mark}"
            f"{f'{c.wins}-{c.losses}-{c.ties}':>10}{c.p_sign:>9.3f}"
        )
    lines.append("")
    lines.append("* p < 0.05 on the paired bootstrap. n is small; read the CI too.")
    return "\n".join(lines)
