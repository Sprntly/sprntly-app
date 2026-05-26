"""Post-processing for Stages 2-5 outputs.

Spec §2.3:
  - merge_and_rank: dedupe findings across stages, score them
  - compute_impact_concentration: how concentrated is the effect?
  - run_robustness_check: re-run a finding detector across seeds
  - compute_estimated_lift: scenario-based pp uplift, capped at 90%
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Callable

import numpy as np
import pandas as pd

from .types import (
    Finding,
    ImpactConcentration,
    LiftScenarios,
    RobustnessResult,
    StageOutput,
)


# ─────────────────────────── merge & rank ───────────────────────────

_CONFIDENCE_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
_STAGE_WEIGHT = {
    1: 1.00,  # Stage 1 pattern discovery
    2: 0.90,  # Temporal
    3: 0.85,  # Tail
    4: 1.10,  # Causal — bonus because it's the most rigorous
    5: 0.95,  # Interactions
}


def _finding_key(f: Finding) -> str:
    """Dedup key — interactions keep full rule, single features use name."""
    if " AND " in f.feature or " > " in f.feature or " <= " in f.feature:
        return f"interaction::{f.feature}"
    return f"feature::{f.feature}"


def merge_and_rank(stage_outputs: list[StageOutput]) -> list[Finding]:
    by_key: dict[str, list[tuple[int, Finding]]] = defaultdict(list)
    for so in stage_outputs:
        for f in so.findings:
            by_key[_finding_key(f)].append((so.stage, f))

    merged: list[Finding] = []
    for key, items in by_key.items():
        # Pick the highest-confidence representative; aggregate stages supporting it
        items.sort(
            key=lambda kv: (
                _CONFIDENCE_RANK[kv[1].confidence],
                kv[1].importance * _STAGE_WEIGHT.get(kv[0], 1.0),
            ),
            reverse=True,
        )
        winning_stage, rep = items[0]
        supporting_stages = sorted({s for s, _ in items})
        # Weighted score for ranking
        score = max(
            _STAGE_WEIGHT.get(s, 1.0) * f.importance * _CONFIDENCE_RANK[f.confidence]
            for s, f in items
        )
        new_meta = dict(rep.metadata)
        new_meta["supporting_stages"] = supporting_stages
        new_meta["primary_stage"] = winning_stage
        new_meta["rank_score"] = round(float(score), 4)
        # Confidence boost when multiple stages agree
        confidence = rep.confidence
        if len(supporting_stages) >= 2 and confidence == "MEDIUM":
            confidence = "HIGH"
        merged.append(
            Finding(
                feature=rep.feature,
                importance=rep.importance,
                direction=rep.direction,
                confidence=confidence,
                metadata=new_meta,
            )
        )

    merged.sort(key=lambda f: f.metadata.get("rank_score", f.importance), reverse=True)
    return merged


# ─────────────────────────── impact concentration ───────────────────────────


def compute_impact_concentration(
    finding: Finding,
    user_table: pd.DataFrame,
    goal_metric: str,
) -> ImpactConcentration:
    """Ratio of contribution-to-goal share over segment-size share.

    For a single-feature finding we use median split (above-median = segment).
    For an interaction we use the embedded rule when available in metadata.
    """
    n = len(user_table)
    if n == 0:
        return ImpactConcentration(0.0, 0.0, 0.0)

    y = user_table[goal_metric].astype(float).fillna(0.0).to_numpy()
    total = float(np.sum(y))

    feat = finding.feature
    mask: np.ndarray
    if feat in user_table.columns:
        col = user_table[feat]
        if col.isin([0, 1]).all():
            mask = (col == 1).to_numpy()
        else:
            thr = col.median()
            mask = (col > thr).to_numpy()
    elif "leaf_id" in finding.metadata:
        # Interaction finding — we don't re-run the tree here; use leaf size
        n_samples = int(finding.metadata.get("n_samples", 0))
        leaf_mean = float(finding.metadata.get("leaf_mean", 0.0))
        seg_pct = n_samples / n if n else 0.0
        contribution = leaf_mean * n_samples
        contrib_pct = contribution / total if total > 0 else 0.0
        ratio = (contrib_pct / seg_pct) if seg_pct > 0 else 0.0
        return ImpactConcentration(
            segment_size_pct=round(seg_pct, 4),
            contribution_pct=round(contrib_pct, 4),
            ratio=round(ratio, 3),
        )
    else:
        return ImpactConcentration(0.0, 0.0, 0.0)

    seg_size = int(mask.sum())
    seg_pct = seg_size / n if n else 0.0
    seg_contrib = float(np.sum(y[mask]))
    contrib_pct = seg_contrib / total if total > 0 else 0.0
    ratio = (contrib_pct / seg_pct) if seg_pct > 0 else 0.0
    return ImpactConcentration(
        segment_size_pct=round(seg_pct, 4),
        contribution_pct=round(contrib_pct, 4),
        ratio=round(ratio, 3),
    )


# ─────────────────────────── robustness ───────────────────────────


def run_robustness_check(
    user_table: pd.DataFrame,
    goal_metric: str,
    *,
    detector: Callable[[pd.DataFrame, str, int], list[str]] | None = None,
    n_runs: int = 3,
    seeds: tuple[int, ...] = (42, 43, 44),
) -> RobustnessResult:
    """Re-run a finding detector across seeds; report consistency.

    ``detector`` is a function(df, goal_metric, seed) -> list[feature_name].
    The default detector uses a RandomForest's top-5 importances. Tests
    can inject their own.
    """
    from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor

    if detector is None:

        def _default(df: pd.DataFrame, goal: str, seed: int) -> list[str]:
            feats = [
                c
                for c in df.columns
                if c not in {goal, "user_id"} and pd.api.types.is_numeric_dtype(df[c])
            ]
            X = df[feats].fillna(0.0)
            y = df[goal].fillna(0.0)
            is_binary = y.isin([0, 1]).all() and y.nunique() <= 2
            if is_binary:
                y = y.astype(int)
                m = RandomForestClassifier(
                    n_estimators=100, max_depth=10, random_state=seed, n_jobs=-1
                )
            else:
                m = RandomForestRegressor(
                    n_estimators=100, max_depth=10, random_state=seed, n_jobs=-1
                )
            m.fit(X, y)
            order = np.argsort(m.feature_importances_)[::-1][:5]
            return [feats[i] for i in order]

        detector = _default

    seeds = tuple(seeds[:n_runs]) if len(seeds) >= n_runs else tuple(range(42, 42 + n_runs))
    runs: list[set[str]] = []
    for s in seeds:
        runs.append(set(detector(user_table, goal_metric, s)))

    # Consistency = mean pairwise Jaccard
    if len(runs) < 2:
        consistency = 1.0
    else:
        scores = []
        for i in range(len(runs)):
            for j in range(i + 1, len(runs)):
                u = runs[i] | runs[j]
                if not u:
                    continue
                scores.append(len(runs[i] & runs[j]) / len(u))
        consistency = float(np.mean(scores)) if scores else 0.0

    return RobustnessResult(
        consistency_score=round(consistency, 3),
        n_runs=len(runs),
        boost_applied=bool(consistency >= 0.80),
    )


def apply_robustness_boost(findings: list[Finding], robustness: RobustnessResult) -> list[Finding]:
    """If consistency ≥ 0.80, bump MEDIUM → HIGH and boost importance by 15%."""
    if not robustness.boost_applied:
        return findings
    out: list[Finding] = []
    for f in findings:
        new_meta = dict(f.metadata)
        new_meta["robustness_consistency"] = robustness.consistency_score
        new_meta["robustness_boost_applied"] = True
        confidence = f.confidence
        if confidence == "MEDIUM":
            confidence = "HIGH"
        out.append(
            Finding(
                feature=f.feature,
                importance=round(f.importance * 1.15, 4),
                direction=f.direction,
                confidence=confidence,
                metadata=new_meta,
            )
        )
    return out


# ─────────────────────────── lift estimation ───────────────────────────


def compute_estimated_lift(finding: Finding, *, cap: float = 0.90) -> LiftScenarios:
    """Conservative/Realistic/Optimistic scenarios in percentage points.

    The spec gives three scenarios anchored at +5pp, +15pp, +30pp lifts.
    We scale each by the finding's importance (clamped) and cap at 90pp.
    """
    base_strength = max(0.1, min(1.0, float(finding.importance)))
    sign = 1.0 if finding.direction == "positive" else -1.0

    conservative = sign * min(cap, 0.05 * base_strength)
    realistic = sign * min(cap, 0.15 * base_strength)
    optimistic = sign * min(cap, 0.30 * base_strength)

    return LiftScenarios(
        conservative_pp=round(conservative * 100, 2),
        realistic_pp=round(realistic * 100, 2),
        optimistic_pp=round(optimistic * 100, 2),
        capped_at=cap * 100,
    )
