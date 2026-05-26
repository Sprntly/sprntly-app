"""Comprehensive tier — all 5 stages + robustness 3-runs + impact concentration + lift.

Spec §3.2 Comprehensive: ~4hr, ~$8. Used by Monday Brief + STRATEGIC questions
+ when a guardrail bumps a Deep run up.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import pandas as pd

from ..post_processing import (
    apply_robustness_boost,
    compute_estimated_lift,
    compute_impact_concentration,
    merge_and_rank,
    run_robustness_check,
)
from ..stages.causal_validation import run_causal_validation
from ..stages.interaction_discovery import run_interaction_discovery
from ..stages.tail_analysis import run_tail_analysis
from ..stages.temporal_dynamics import run_temporal_dynamics
from ..tiers.deep import _run_shap_3x
from ..types import Finding, ImpactConcentration, LiftScenarios, StageOutput


@dataclass
class ComprehensiveResult:
    findings: list[Finding] = field(default_factory=list)
    stage_outputs: list[StageOutput] = field(default_factory=list)
    impact_concentrations: dict[str, ImpactConcentration] = field(default_factory=dict)
    estimated_lifts: dict[str, LiftScenarios] = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    cost_estimate_usd: float = 8.00
    robustness_consistency: float = 0.0


def run_comprehensive(
    user_table: pd.DataFrame,
    goal_metric: str,
    *,
    top_n_findings: int = 5,
) -> ComprehensiveResult:
    started = time.perf_counter()

    # Stage 1 — SHAP/PCA/Stratified (3 seeds)
    so1 = _run_shap_3x(user_table, goal_metric, top_k=10)

    # Stage 2 — temporal
    so2 = run_temporal_dynamics(user_table, goal_metric, time_buckets=4)

    # Stage 3 — tail
    so3 = run_tail_analysis(user_table, goal_metric, contamination=0.05)

    # Stage 4 — causal validation on top SHAP findings
    top_candidates = [f.feature for f in so1.findings[:top_n_findings] if f.feature in user_table.columns]
    so4 = run_causal_validation(user_table, goal_metric, candidates=top_candidates)

    # Stage 5 — interactions
    so5 = run_interaction_discovery(user_table, goal_metric, max_depth=4, min_samples_leaf=100)

    stage_outputs = [so1, so2, so3, so4, so5]
    merged = merge_and_rank(stage_outputs)

    # Robustness 3-runs
    robustness = run_robustness_check(user_table, goal_metric, n_runs=3, seeds=(42, 43, 44))
    merged = apply_robustness_boost(merged, robustness)

    concentrations: dict[str, ImpactConcentration] = {}
    lifts: dict[str, LiftScenarios] = {}
    for f in merged[:top_n_findings]:
        concentrations[f.feature] = compute_impact_concentration(f, user_table, goal_metric)
        lifts[f.feature] = compute_estimated_lift(f)

    return ComprehensiveResult(
        findings=merged,
        stage_outputs=stage_outputs,
        impact_concentrations=concentrations,
        estimated_lifts=lifts,
        elapsed_seconds=time.perf_counter() - started,
        cost_estimate_usd=8.00,
        robustness_consistency=robustness.consistency_score,
    )
