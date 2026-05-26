"""Stage 3 — Tail Analysis tests."""

from __future__ import annotations

from ds_agent.stages.tail_analysis import run_tail_analysis

from tests.synth_fixtures import tail_dataset


def test_tail_finds_planted_rare_segment() -> None:
    df = tail_dataset(n=4000, seed=42)
    out = run_tail_analysis(df, goal_metric="retention_30d", contamination=0.05)

    assert out.stage == 3
    assert out.findings, "expected at least one tail finding"
    assert out.metadata["n_outliers"] > 0

    # feature_X is the planted differentiator for the rare segment
    features_found = {f.feature for f in out.findings}
    assert "feature_X" in features_found

    # The outlier segment should have noticeably different goal mean from population
    assert abs(out.metadata["lift_vs_population"]) > 0.05


def test_tail_skips_when_insufficient_data() -> None:
    import pandas as pd

    df = pd.DataFrame({"a": [1.0] * 50, "retention_30d": [0, 1] * 25})
    out = run_tail_analysis(df, goal_metric="retention_30d")
    assert out.metadata.get("skipped") is True
