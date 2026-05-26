"""Deep tier — integration test with synthetic data."""

from __future__ import annotations

from ds_agent.synthetic import generate
from ds_agent.tiers.deep import run_deep


def test_deep_tier_end_to_end_on_synthetic_saas_data() -> None:
    df = generate(n_users=3000, seed=42)
    result = run_deep(df, goal_metric="retention_30d", top_n_findings=3)

    assert result.findings, "expected at least one finding"
    assert result.cost_estimate_usd == 1.20

    # Stage outputs: SHAP 3x, temporal lite, PSM
    stage_ids = {so.stage for so in result.stage_outputs}
    assert 1 in stage_ids
    assert 4 in stage_ids

    # Impact concentration computed for top findings
    assert len(result.impact_concentrations) > 0
    assert len(result.estimated_lifts) > 0

    # Robustness consistency is in [0,1]
    assert 0.0 <= result.robustness_consistency <= 1.0
