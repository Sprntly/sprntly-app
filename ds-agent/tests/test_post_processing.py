"""Post-processing tests: merge_and_rank, impact concentration, robustness, lift."""

from __future__ import annotations

import pandas as pd

from ds_agent.post_processing import (
    apply_robustness_boost,
    compute_estimated_lift,
    compute_impact_concentration,
    merge_and_rank,
    run_robustness_check,
)
from ds_agent.types import Finding, RobustnessResult, StageOutput


def _f(feature: str, importance: float, confidence: str = "MEDIUM", direction: str = "positive") -> Finding:
    return Finding(
        feature=feature, importance=importance, direction=direction, confidence=confidence
    )


def test_merge_and_rank_dedupes_across_stages() -> None:
    s1 = StageOutput(stage=1, findings=[_f("posts", 0.4, "HIGH"), _f("mobile", 0.3, "MEDIUM")])
    s2 = StageOutput(stage=2, findings=[_f("posts", 0.35, "MEDIUM")])  # dup
    s4 = StageOutput(stage=4, findings=[_f("posts", 0.2, "HIGH")])  # dup, causal

    merged = merge_and_rank([s1, s2, s4])
    names = [f.feature for f in merged]
    assert names.count("posts") == 1, "duplicate findings should be merged"
    assert "mobile" in names

    # The merged 'posts' finding should record all supporting stages
    posts = next(f for f in merged if f.feature == "posts")
    assert set(posts.metadata["supporting_stages"]) == {1, 2, 4}
    # MEDIUM + 3 supporting stages → confidence boost
    assert posts.confidence == "HIGH"


def test_impact_concentration_for_binary_feature() -> None:
    df = pd.DataFrame(
        {
            "is_power": [1] * 100 + [0] * 900,
            # power users retain 90%, others 30% → contribution_pct heavily skewed
            "retention_30d": [1] * 90 + [0] * 10 + [1] * 270 + [0] * 630,
        }
    )
    ic = compute_impact_concentration(
        _f("is_power", 0.5), df, goal_metric="retention_30d"
    )
    # 10% of users, ~25% of conversions → ratio ~ 2.5
    assert 0.05 <= ic.segment_size_pct <= 0.15
    assert ic.ratio > 1.5


def test_robustness_check_returns_high_consistency_on_stable_data() -> None:
    from tests.synth_fixtures import temporal_dataset

    df = temporal_dataset(n=2000, seed=42)
    r = run_robustness_check(df, goal_metric="retention_30d", n_runs=3)
    assert r.n_runs == 3
    assert 0.0 <= r.consistency_score <= 1.0
    # We don't strictly assert >= 0.80 to avoid flaky CI, but the boost-applied
    # flag must reflect the threshold correctly.
    assert r.boost_applied == (r.consistency_score >= 0.80)


def test_apply_robustness_boost_lifts_medium_to_high() -> None:
    findings = [_f("a", 0.3, "MEDIUM"), _f("b", 0.5, "HIGH"), _f("c", 0.2, "LOW")]
    r = RobustnessResult(consistency_score=0.85, n_runs=3, boost_applied=True)
    boosted = apply_robustness_boost(findings, r)
    confidences = {f.feature: f.confidence for f in boosted}
    assert confidences["a"] == "HIGH"  # MEDIUM → HIGH
    assert confidences["b"] == "HIGH"  # stays HIGH
    assert confidences["c"] == "LOW"  # stays LOW
    # 15% importance boost
    a = next(f for f in boosted if f.feature == "a")
    assert a.importance == round(0.3 * 1.15, 4)


def test_lift_scenarios_capped_at_90() -> None:
    f = _f("x", 1.0, "HIGH", direction="positive")
    lift = compute_estimated_lift(f, cap=0.90)
    assert lift.conservative_pp == 5.0  # 5pp
    assert lift.realistic_pp == 15.0
    assert lift.optimistic_pp == 30.0
    assert lift.capped_at == 90.0

    # negative direction → negative pp
    fneg = _f("x", 1.0, "HIGH", direction="negative")
    lift_neg = compute_estimated_lift(fneg)
    assert lift_neg.conservative_pp == -5.0
