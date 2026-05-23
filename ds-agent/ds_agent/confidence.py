"""Confidence scoring (spec §2.3).

Each finding gets a HIGH | MEDIUM | LOW score derived from a weighted
average of five factors. Stages 2–5 are not implemented yet, so factors
that depend on them (causal signal, temporal stability) are skipped and
the remaining weights renormalized.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


_WEIGHTS = {
    "statistical_significance": 0.25,
    "sample_size": 0.20,
    "effect_size": 0.25,
    "data_quality": 0.15,
    "cross_method_agreement": 0.15,
}


def _factor_statistical_significance(finding: dict[str, Any]) -> float:
    # Stage 1 doesn't compute p-values directly. As a proxy we use the
    # SHAP effect-size standard deviation across seeds: a tight std means
    # the finding is stable, which we treat as a significance proxy.
    if "effect_size_std" in finding and finding["effect_size_std"] > 0:
        ratio = finding["effect_size"] / (finding["effect_size_std"] + 1e-9)
        return float(min(1.0, ratio / 5.0))
    # PCA findings: use absolute correlation as the proxy.
    return float(min(1.0, abs(finding.get("effect_size", 0.0)) * 2))


def _factor_sample_size(finding: dict[str, Any], n_total: int) -> float:
    n = finding.get("sample_size", n_total)
    return float(min(1.0, n / 1000.0))


def _factor_effect_size(finding: dict[str, Any]) -> float:
    es = abs(finding.get("effect_size", 0.0))
    # SHAP magnitudes for retention-style problems are usually well under 0.5,
    # so normalize against 0.20 for "strong" rather than 2.0.
    return float(min(1.0, es / 0.20))


def _factor_data_quality(finding: dict[str, Any], df: pd.DataFrame) -> float:
    behavior = finding.get("behavior")
    if behavior and behavior in df.columns:
        return float(1.0 - df[behavior].isna().mean())
    return 0.85  # PCA / aggregate findings can't be tied to a single column


def _factor_cross_method_agreement(finding: dict[str, Any]) -> float:
    n_methods = finding.get("num_methods_supporting", 1)
    # Boost SHAP findings that survived multiple random seeds.
    seed_agreement = finding.get("seed_agreement", 1.0)
    base = min(1.0, n_methods / 3.0)
    return float(0.5 * base + 0.5 * seed_agreement)


def score(finding: dict[str, Any], df: pd.DataFrame) -> dict[str, Any]:
    factors = {
        "statistical_significance": _factor_statistical_significance(finding),
        "sample_size": _factor_sample_size(finding, len(df)),
        "effect_size": _factor_effect_size(finding),
        "data_quality": _factor_data_quality(finding, df),
        "cross_method_agreement": _factor_cross_method_agreement(finding),
    }
    weighted = sum(factors[k] * _WEIGHTS[k] for k in _WEIGHTS)
    if weighted >= 0.70:
        label = "HIGH"
    elif weighted >= 0.45:
        label = "MEDIUM"
    else:
        label = "LOW"
    return {"label": label, "weighted_score": round(weighted, 3), "factors": {k: round(v, 3) for k, v in factors.items()}}


def rank_by_impact(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Spec §2.3: rank by effect_size × confidence."""
    label_weight = {"HIGH": 1.0, "MEDIUM": 0.6, "LOW": 0.3}
    return sorted(
        findings,
        key=lambda f: abs(f.get("effect_size", 0.0)) * label_weight[f["confidence_score"]["label"]],
        reverse=True,
    )
