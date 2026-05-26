"""Deep tier — SHAP 3x + stratification on top-3 + PSM only + temporal lite + robustness 2-runs.

Spec §3.2 Deep: ~50min, ~$1.20. Sits between Express and Comprehensive.
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
from ..stages.causal_validation import run_psm
from ..stages.temporal_dynamics import run_temporal_dynamics
from ..types import Finding, ImpactConcentration, LiftScenarios, StageOutput


@dataclass
class DeepResult:
    findings: list[Finding] = field(default_factory=list)
    stage_outputs: list[StageOutput] = field(default_factory=list)
    impact_concentrations: dict[str, ImpactConcentration] = field(default_factory=dict)
    estimated_lifts: dict[str, LiftScenarios] = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    cost_estimate_usd: float = 1.20
    robustness_consistency: float = 0.0


def _run_shap_3x(
    user_table: pd.DataFrame, goal_metric: str, top_k: int = 10
) -> StageOutput:
    """Lightweight Stage-1-like SHAP across 3 seeds. Reuses legacy stage."""
    from ..legacy.stages.pattern_discovery import run as run_stage1

    numeric_features = [
        c
        for c in user_table.columns
        if c not in {goal_metric, "user_id", "signup_date"}
        and pd.api.types.is_numeric_dtype(user_table[c])
    ]
    categorical_features = [
        c for c in user_table.columns if pd.api.types.is_object_dtype(user_table[c])
    ]
    gv = user_table[goal_metric].dropna()
    is_binary = bool(gv.isin([0, 1]).all() and gv.nunique() <= 2)

    started = time.perf_counter()
    stage1 = run_stage1(
        df=user_table,
        numeric_features=numeric_features,
        categorical_features=categorical_features,
        goal_metric=goal_metric,
        is_binary=is_binary,
        seeds=(42, 43, 44),
    )

    findings: list[Finding] = []
    for sf in stage1.get("shap", [])[:top_k]:
        confidence = (
            "HIGH"
            if sf.get("seed_agreement", 0) >= 1.0
            else ("MEDIUM" if sf.get("seed_agreement", 0) >= 0.66 else "LOW")
        )
        findings.append(
            Finding(
                feature=sf["behavior"],
                importance=float(sf["effect_size"]),
                direction=sf["directionality"],
                confidence=confidence,
                metadata={
                    "method": "SHAP_3x",
                    "seed_agreement": sf.get("seed_agreement"),
                    "effect_size_std": sf.get("effect_size_std"),
                    "sample_size": sf.get("sample_size"),
                },
            )
        )

    return StageOutput(
        stage=1,
        findings=findings,
        elapsed_seconds=time.perf_counter() - started,
        cost_estimate_usd=0.0,
        metadata={"stratified_findings": stage1.get("stratified", [])[:3]},
    )


def run_deep(
    user_table: pd.DataFrame,
    goal_metric: str,
    top_n_findings: int = 3,
) -> DeepResult:
    started = time.perf_counter()

    # SHAP 3x (with built-in stratification on top categoricals)
    so_shap = _run_shap_3x(user_table, goal_metric, top_k=10)

    # Temporal lite — n_buckets=2 instead of 4
    so_temp = run_temporal_dynamics(user_table, goal_metric, time_buckets=2)

    stage_outputs = [so_shap, so_temp]

    # PSM only — on top-N SHAP findings
    causal_findings: list[Finding] = []
    for f in so_shap.findings[:top_n_findings]:
        if f.feature not in user_table.columns:
            continue
        psm = run_psm(user_table, f.feature, goal_metric)
        if psm.n_treated_matched == 0:
            continue
        confidence = "HIGH" if psm.p_value < 0.05 else ("MEDIUM" if psm.p_value < 0.10 else "LOW")
        causal_findings.append(
            Finding(
                feature=f.feature,
                importance=float(abs(psm.estimate)),
                direction="positive" if psm.estimate >= 0 else "negative",
                confidence=confidence,
                metadata={
                    "psm_estimate": round(psm.estimate, 4),
                    "psm_p_value": round(psm.p_value, 4),
                    "psm_n_matched": psm.n_treated_matched,
                },
            )
        )
    stage_outputs.append(
        StageOutput(
            stage=4,
            findings=causal_findings,
            elapsed_seconds=0.0,
            cost_estimate_usd=0.0,
            metadata={"variant": "psm_only"},
        )
    )

    merged = merge_and_rank(stage_outputs)

    # Robustness 2-runs
    robustness = run_robustness_check(user_table, goal_metric, n_runs=2, seeds=(42, 43))
    merged = apply_robustness_boost(merged, robustness)

    # Impact concentration + lift for top findings
    concentrations: dict[str, ImpactConcentration] = {}
    lifts: dict[str, LiftScenarios] = {}
    for f in merged[:top_n_findings]:
        concentrations[f.feature] = compute_impact_concentration(f, user_table, goal_metric)
        lifts[f.feature] = compute_estimated_lift(f)

    return DeepResult(
        findings=merged,
        stage_outputs=stage_outputs,
        impact_concentrations=concentrations,
        estimated_lifts=lifts,
        elapsed_seconds=time.perf_counter() - started,
        cost_estimate_usd=1.20,
        robustness_consistency=robustness.consistency_score,
    )
