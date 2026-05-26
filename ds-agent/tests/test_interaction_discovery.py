"""Stage 5 — Interaction Discovery tests."""

from __future__ import annotations

from ds_agent.stages.interaction_discovery import run_interaction_discovery

from tests.synth_fixtures import interaction_dataset


def test_decision_tree_recovers_planted_interaction() -> None:
    df = interaction_dataset(n=4000, seed=42)
    out = run_interaction_discovery(
        df, goal_metric="retention_30d", max_depth=4, min_samples_leaf=100
    )

    assert out.stage == 5
    assert out.findings, "expected at least one interaction finding"

    # Rules involving usage_hours AND is_mobile should appear in at least one finding
    found_interaction = any(
        ("usage_hours" in f.feature and "is_mobile" in f.feature) for f in out.findings
    )
    assert found_interaction, f"planted interaction not found in: {[f.feature for f in out.findings]}"


def test_interaction_skips_when_too_few_rows() -> None:
    import pandas as pd

    df = pd.DataFrame({"a": [1.0] * 100, "b": [2.0] * 100, "retention_30d": [0, 1] * 50})
    out = run_interaction_discovery(df, goal_metric="retention_30d")
    assert out.metadata.get("skipped") is True
