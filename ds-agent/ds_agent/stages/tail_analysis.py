"""Stage 3 — Tail Analysis.

Spec §2.2 Stage 3: Identify rare-but-high-impact user segments using
IsolationForest. Take the outlier cluster, compare their goal-metric
mean to the population mean. If meaningfully different, characterise
the segment by the features that diverge most from the population.

Hyperparameters per spec:
  - IsolationForest: contamination=0.05, n_estimators=100, random_state=42
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from ..types import Finding, StageOutput


def run_tail_analysis(
    user_table: pd.DataFrame,
    goal_metric: str,
    contamination: float = 0.05,
    *,
    numeric_features: list[str] | None = None,
    min_segment_size: int = 30,
    importance_threshold: float = 0.5,
) -> StageOutput:
    started = time.perf_counter()

    if numeric_features is None:
        numeric_features = [
            c
            for c in user_table.columns
            if c not in {goal_metric, "user_id"}
            and pd.api.types.is_numeric_dtype(user_table[c])
        ]
    if not numeric_features or len(user_table) < max(min_segment_size, 100):
        return StageOutput(
            stage=3,
            findings=[],
            elapsed_seconds=time.perf_counter() - started,
            cost_estimate_usd=0.0,
            metadata={"skipped": True, "reason": "insufficient_data"},
        )

    X = user_table[numeric_features].fillna(0.0).to_numpy()
    X_scaled = StandardScaler().fit_transform(X)

    iso = IsolationForest(
        contamination=contamination, n_estimators=100, random_state=42, n_jobs=-1
    )
    labels = iso.fit_predict(X_scaled)  # -1 = outlier, 1 = inlier
    outlier_mask = labels == -1

    n_outliers = int(outlier_mask.sum())
    if n_outliers < min_segment_size:
        return StageOutput(
            stage=3,
            findings=[],
            elapsed_seconds=time.perf_counter() - started,
            cost_estimate_usd=0.0,
            metadata={"skipped": True, "reason": "no_outlier_segment", "n_outliers": n_outliers},
        )

    y = user_table[goal_metric].astype(float).fillna(0.0).to_numpy()
    pop_mean = float(np.mean(y))
    seg_mean = float(np.mean(y[outlier_mask]))
    lift = seg_mean - pop_mean

    # Characterise the outlier segment by standardised feature deviations
    pop_mu = X.mean(axis=0)
    pop_sd = X.std(axis=0)
    pop_sd[pop_sd == 0] = 1.0  # avoid /0 for constant columns
    seg_mu = X[outlier_mask].mean(axis=0)
    z = (seg_mu - pop_mu) / pop_sd  # z-score of segment mean vs population

    findings: list[Finding] = []
    order = np.argsort(np.abs(z))[::-1]
    for idx in order:
        if abs(z[idx]) < importance_threshold:
            continue
        feat = numeric_features[idx]
        direction = "positive" if (z[idx] > 0) == (lift >= 0) else "negative"
        # Confidence: large |z| + sizeable lift → HIGH
        if abs(z[idx]) >= 1.5 and abs(lift) >= 0.05:
            confidence = "HIGH"
        elif abs(z[idx]) >= 1.0:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"
        findings.append(
            Finding(
                feature=feat,
                importance=float(abs(z[idx])),
                direction=direction,
                confidence=confidence,
                metadata={
                    "segment": "tail_isolation_forest",
                    "segment_size": n_outliers,
                    "segment_size_pct": round(n_outliers / len(user_table), 4),
                    "segment_goal_mean": round(seg_mean, 4),
                    "population_goal_mean": round(pop_mean, 4),
                    "lift_vs_population": round(lift, 4),
                    "feature_zscore": round(float(z[idx]), 3),
                    "feature_segment_mean": round(float(seg_mu[idx]), 4),
                    "feature_population_mean": round(float(pop_mu[idx]), 4),
                },
            )
        )

    return StageOutput(
        stage=3,
        findings=findings,
        elapsed_seconds=time.perf_counter() - started,
        cost_estimate_usd=0.0,
        metadata={
            "n_outliers": n_outliers,
            "contamination": contamination,
            "segment_goal_mean": round(seg_mean, 4),
            "population_goal_mean": round(pop_mean, 4),
            "lift_vs_population": round(lift, 4),
        },
    )
