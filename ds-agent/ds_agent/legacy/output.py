"""Assemble the final JSON output (spec §3.3).

Fields tied to stages we haven't built yet (temporal_stability, causal_*,
impact_concentration, estimated_business_impact) are emitted as None so
the schema shape matches the spec and downstream consumers don't crash.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd


def assemble(
    *,
    run_id: str,
    analytics_tool: str,
    goal_metric: str,
    business_model: str,
    data_quality: dict[str, Any],
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for f in findings:
        counts[f["confidence_score"]["label"]] += 1

    return {
        "agent_run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input": {
            "analytics_tool": analytics_tool,
            "goal_metric": goal_metric,
            "business_model": business_model,
            "data_quality": {
                "sample_size": data_quality.get("sample_size"),
                "completeness": data_quality.get("completeness"),
                "time_period": data_quality.get("time_period"),
                "goal_metric_is_binary": data_quality.get("goal_metric_is_binary"),
                "unreliable_columns": data_quality.get("unreliable_columns", []),
                "low_power_segments": data_quality.get("low_power_segments", []),
                "known_issues": data_quality.get("known_issues", []),
            },
        },
        "findings": [_finding_payload(rank=i + 1, f=f) for i, f in enumerate(findings)],
        "summary": {
            "total_findings": len(findings),
            "high_confidence_findings": counts["HIGH"],
            "medium_confidence_findings": counts["MEDIUM"],
            "low_confidence_findings": counts["LOW"],
            "stages_run": ["pattern_discovery"],
            "stages_pending": [
                "temporal_dynamics",
                "tail_analysis",
                "causal_inference",
                "interaction_discovery",
            ],
        },
        "quality_flags": _quality_flags(data_quality),
    }


def _finding_payload(*, rank: int, f: dict[str, Any]) -> dict[str, Any]:
    return {
        "rank": rank,
        "behavior": f.get("behavior"),
        "scope": f.get("scope"),
        "factor_loadings": f.get("factor_loadings"),  # PCA-only
        "impact_type": f.get("directionality"),
        "effect_size": f.get("effect_size"),
        "effect_size_std": f.get("effect_size_std"),
        "confidence_score": f["confidence_score"]["label"],
        "confidence_breakdown": f["confidence_score"]["factors"],
        "weighted_confidence": f["confidence_score"]["weighted_score"],
        "supporting_analyses": f.get("supporting_analyses", []),
        "seed_agreement": f.get("seed_agreement"),
        "stratum": f.get("stratum"),
        "segment_variation": f.get("segment_variation", []),
        "sample_size": f.get("sample_size"),
        # Not yet implemented (Stages 2–5)
        "temporal_stability": None,
        "causal_signal": None,
        "causal_estimate": None,
        "interactions": None,
        "impact_concentration": None,
        "estimated_business_impact": None,
        # Filled by the synthesis layer
        "narrative": f.get("narrative"),
        "recommended_action": f.get("recommended_action"),
    }


def _quality_flags(data_quality: dict[str, Any]) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    for col in data_quality.get("unreliable_columns", []):
        flags.append(
            {
                "flag": "unreliable_column",
                "description": f"Column '{col}' is < 70% complete; findings touching it have reduced confidence.",
            }
        )
    low_power = data_quality.get("low_power_segments", [])
    if low_power:
        worst = sorted(low_power, key=lambda s: s["size"])[:3]
        for seg in worst:
            flags.append(
                {
                    "flag": "low_power_segment",
                    "description": (
                        f"{seg['dimension']}={seg['value']} has only {seg['size']} users; "
                        "stratified findings in this segment are underpowered."
                    ),
                }
            )
    return flags
