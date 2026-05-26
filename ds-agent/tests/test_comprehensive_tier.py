"""Comprehensive tier — integration test running all 5 stages."""

from __future__ import annotations

from ds_agent.synthetic import generate
from ds_agent.tiers.comprehensive import run_comprehensive


def test_comprehensive_runs_all_five_stages() -> None:
    df = generate(n_users=3000, seed=42)
    result = run_comprehensive(df, goal_metric="retention_30d", top_n_findings=5)

    assert result.findings, "expected findings"
    assert result.cost_estimate_usd == 8.00

    stage_ids = {so.stage for so in result.stage_outputs}
    assert stage_ids >= {1, 2, 3, 4, 5}, f"missing stages: {stage_ids}"

    # Should produce impact concentrations + lifts for top findings
    assert len(result.impact_concentrations) > 0
    assert len(result.estimated_lifts) > 0

    # All findings should have a confidence field
    assert all(f.confidence in {"LOW", "MEDIUM", "HIGH"} for f in result.findings)
