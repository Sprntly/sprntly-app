"""Stage 2 — Temporal Dynamics.

Spec §2.2 Stage 2: Run SHAP per time-bucket and surface
  - stable drivers (consistent rank across buckets),
  - emerging drivers (rank improves toward the most recent bucket),
  - degrading drivers (rank falls).

Then a concept-drift check: if the union of top-K feature sets between
the first and last buckets has a Jaccard overlap < 0.5, we flag drift.

Hyperparameters per spec:
  - RandomForestRegressor/Classifier: n_estimators=100, max_depth=10
  - n_buckets default 4 (equal-frequency by time column)
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor

import shap

from ..types import Finding, StageOutput


_TIME_CANDIDATES = ("signup_date", "event_date", "date", "timestamp", "ts")


def _detect_time_col(df: pd.DataFrame) -> str | None:
    for c in _TIME_CANDIDATES:
        if c in df.columns:
            return c
    # any datetime-ish column
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            return c
    return None


def _bucketise(df: pd.DataFrame, time_col: str, n_buckets: int) -> list[pd.DataFrame]:
    s = pd.to_datetime(df[time_col], errors="coerce", utc=True)
    df = df.assign(_t=s).dropna(subset=["_t"]).sort_values("_t")
    if df.empty:
        return []
    # qcut may collapse buckets when ties dominate; fall back to range slices.
    try:
        df["_bucket"] = pd.qcut(df["_t"].rank(method="first"), n_buckets, labels=False)
    except ValueError:
        df["_bucket"] = (np.linspace(0, n_buckets, len(df), endpoint=False)).astype(int)
    return [df[df["_bucket"] == i].drop(columns=["_t", "_bucket"]) for i in range(n_buckets)]


def _bucket_shap(
    bucket_df: pd.DataFrame,
    numeric_features: list[str],
    goal_metric: str,
    is_binary: bool,
    seed: int = 42,
) -> dict[str, float]:
    if len(bucket_df) < 50 or not numeric_features:
        return {}
    X = bucket_df[numeric_features].fillna(0.0)
    y = bucket_df[goal_metric].fillna(0.0)
    if is_binary:
        y = y.astype(int)
        if y.nunique() < 2:
            return {}
        model = RandomForestClassifier(
            n_estimators=100, max_depth=10, n_jobs=-1, random_state=seed
        )
    else:
        if float(np.std(y)) == 0.0:
            return {}
        model = RandomForestRegressor(
            n_estimators=100, max_depth=10, n_jobs=-1, random_state=seed
        )
    model.fit(X, y)
    explainer = shap.TreeExplainer(model)
    sv = explainer.shap_values(X, check_additivity=False)
    if isinstance(sv, list):
        sv = sv[1] if len(sv) >= 2 else sv[0]
    elif isinstance(sv, np.ndarray) and sv.ndim == 3:
        sv = sv[:, :, -1]
    abs_mean = np.abs(sv).mean(axis=0)
    return {f: float(abs_mean[i]) for i, f in enumerate(numeric_features)}


def _direction_corr(df: pd.DataFrame, feat: str, goal: str) -> float:
    if feat not in df.columns or goal not in df.columns:
        return 0.0
    a = df[feat].astype(float).fillna(0.0).to_numpy()
    b = df[goal].astype(float).fillna(0.0).to_numpy()
    if np.std(a) == 0 or np.std(b) == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _classify_pattern(ranks: list[int], n_features: int) -> str:
    """Given per-bucket ranks (0=top, lower=better), call stable/emerging/degrading."""
    if not ranks:
        return "absent"
    # stable: small variance, all in top half
    half = max(1, n_features // 2)
    if max(ranks) - min(ranks) <= 1 and all(r < half for r in ranks):
        return "stable"
    # emerging: monotonically improving (rank drops over time)
    if all(ranks[i] >= ranks[i + 1] for i in range(len(ranks) - 1)) and ranks[0] > ranks[-1]:
        return "emerging"
    # degrading: monotonically worsening
    if all(ranks[i] <= ranks[i + 1] for i in range(len(ranks) - 1)) and ranks[0] < ranks[-1]:
        return "degrading"
    return "volatile"


def run_temporal_dynamics(
    user_table: pd.DataFrame,
    goal_metric: str,
    time_buckets: int = 4,
    *,
    time_col: str | None = None,
    is_binary: bool | None = None,
    numeric_features: list[str] | None = None,
    top_k: int = 5,
) -> StageOutput:
    """Stage 2 entry point. See module docstring for spec mapping."""
    started = time.perf_counter()

    if time_col is None:
        time_col = _detect_time_col(user_table)
    if time_col is None:
        return StageOutput(
            stage=2,
            findings=[],
            elapsed_seconds=time.perf_counter() - started,
            cost_estimate_usd=0.0,
            metadata={"skipped": True, "reason": "no_time_column"},
        )

    if numeric_features is None:
        numeric_features = [
            c
            for c in user_table.columns
            if c not in {goal_metric, time_col, "user_id"}
            and pd.api.types.is_numeric_dtype(user_table[c])
        ]
    if is_binary is None:
        gv = user_table[goal_metric].dropna()
        is_binary = bool(gv.isin([0, 1]).all() and gv.nunique() <= 2)

    buckets = _bucketise(user_table, time_col, time_buckets)
    if len(buckets) < 2:
        return StageOutput(
            stage=2,
            findings=[],
            elapsed_seconds=time.perf_counter() - started,
            cost_estimate_usd=0.0,
            metadata={"skipped": True, "reason": "not_enough_temporal_data"},
        )

    per_bucket_imp: list[dict[str, float]] = []
    for b in buckets:
        per_bucket_imp.append(_bucket_shap(b, numeric_features, goal_metric, is_binary))

    # Compute rank per bucket per feature
    feature_ranks: dict[str, list[int]] = {f: [] for f in numeric_features}
    for imp in per_bucket_imp:
        if not imp:
            continue
        ordered = sorted(imp.items(), key=lambda kv: kv[1], reverse=True)
        ranks = {f: r for r, (f, _) in enumerate(ordered)}
        for f in numeric_features:
            feature_ranks[f].append(ranks.get(f, len(numeric_features) - 1))

    # Concept drift between first and last non-empty bucket
    first_top = set()
    last_top = set()
    nonempty = [imp for imp in per_bucket_imp if imp]
    if len(nonempty) >= 2:
        first_top = {
            f for f, _ in sorted(nonempty[0].items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        }
        last_top = {
            f for f, _ in sorted(nonempty[-1].items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        }
    union = first_top | last_top
    jaccard = len(first_top & last_top) / max(1, len(union))
    drift_detected = bool(union) and jaccard < 0.5

    findings: list[Finding] = []
    for feat, ranks in feature_ranks.items():
        if not ranks:
            continue
        pattern = _classify_pattern(ranks, len(numeric_features))
        if pattern in ("absent", "volatile"):
            continue
        # importance = mean abs shap across buckets
        imp_vals = [bd.get(feat, 0.0) for bd in per_bucket_imp if bd]
        importance = float(np.mean(imp_vals)) if imp_vals else 0.0
        if importance == 0.0:
            continue
        direction = "positive" if _direction_corr(user_table, feat, goal_metric) >= 0 else "negative"
        # Confidence: stable + high importance → HIGH; emerging recent only → MEDIUM
        if pattern == "stable" and importance > 0.0:
            confidence = "HIGH"
        elif pattern in ("emerging", "degrading"):
            confidence = "MEDIUM"
        else:
            confidence = "LOW"
        findings.append(
            Finding(
                feature=feat,
                importance=importance,
                direction=direction,
                confidence=confidence,
                metadata={
                    "pattern": pattern,
                    "bucket_ranks": ranks,
                    "n_buckets": len(buckets),
                },
            )
        )

    findings.sort(key=lambda f: f.importance, reverse=True)

    return StageOutput(
        stage=2,
        findings=findings,
        elapsed_seconds=time.perf_counter() - started,
        cost_estimate_usd=0.0,  # local compute
        metadata={
            "time_col": time_col,
            "n_buckets": len(buckets),
            "bucket_sizes": [len(b) for b in buckets],
            "concept_drift_detected": drift_detected,
            "concept_drift_jaccard": round(jaccard, 3),
        },
    )
