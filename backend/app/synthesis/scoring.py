"""Deterministic prioritization math, ported from the `prioritize` skill.

Source: vendored skill `prioritize` @a9f28fc5c273
        (skills/prioritize/scripts/score.py).

The skill's CLI `score.py` ships four ranking frameworks (RICE / WSJF / VoC /
North-Star). The one that fits Sprntly's Synthesis pipeline is **VoC Volume &
Severity** — it ranks *problems* (themes) by converging, evidence-weighted
signals, which is exactly the quantitative half of §4c base scoring computed in
`synthesis/convergence.py`. We port that formula here as a pure function and
wire it into the convergence base score (additively — no pipeline
restructuring).

The remaining frameworks (RICE / WSJF / North-Star) rank *solution* backlogs
and don't map onto the current theme-convergence pipeline; `goal_factor` (the
goal-alignment multiplier) is ported alongside as a standalone tested function
so it's ready to wire into a future goal-aware ranking pass (e.g. weighting
themes by KPI-tree fit). It is not yet called in the live path.
"""
from __future__ import annotations


def norm_conf(confidence: float | None) -> float:
    """Normalize a confidence to 0..1. Percent inputs (>1) are divided by 100;
    None means 'no discount' (1.0). Ported verbatim from score.py:norm_conf."""
    if confidence is None:
        return 1.0
    return confidence / 100.0 if confidence > 1 else confidence


def voc_score(
    *,
    impact: float,
    severity: float,
    strategic_fit: float = 1.0,
    confidence: float = 1.0,
    trend: float = 1.0,
) -> float:
    """VoC Volume & Severity score (prioritize skill, method="voc").

    score = impact * severity * strategic_fit * confidence * trend

    All factors are 0..1 except `trend`, a modifier (default 1.0). In the
    skill's terms: `impact` is converged reach (accounts affected + analytics +
    churn + sales signal), `confidence` is a data-quality multiplier, and
    `strategic_fit` ties the problem to the goal. Ported from
    score.py:score_item (method == "voc").
    """
    return impact * severity * strategic_fit * confidence * trend


# Mapping a coarse fit label to a 0..1 weight (score.py:GOAL_FIT).
GOAL_FIT = {
    "high": 1.0, "med": 0.6, "medium": 0.6, "low": 0.25, "none": 0.1, "off": 0.1,
}


def fit_value(v) -> float | None:
    """Coerce a fit ('high'/'med'/'low' or a 0..1 number) to a 0..1 float, or
    None if unrecognized. Ported from score.py:fit_value."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        if v < 0:
            return 0.0
        return float(v) if v <= 1 else 1.0
    return GOAL_FIT.get(str(v).lower())


def goal_factor(fit, *, goal_weight: float = 1.0) -> float:
    """Goal-alignment multiplier in 0..1 for a single fit (score.py goal mode,
    single-goal `goal_fit` branch).

    `goal_weight` blends the factor toward 1.0: 1 = full effect, 0 = goal
    ignored (factor always 1.0). Unknown/None fit is neutral (1.0).

    NOTE: ported for a future goal-aware ranking pass; not yet wired into the
    live convergence path.
    """
    if goal_weight <= 0:
        return 1.0
    base = fit_value(fit)
    if base is None:
        return 1.0
    return base * goal_weight + (1 - goal_weight)
