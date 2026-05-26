"""Stage 5 — Interaction Discovery.

Spec §2.2 Stage 5: Fit a shallow DecisionTreeClassifier(max_depth=4,
min_samples_leaf=100) and extract leaf paths with high lift. The path
description becomes a finding whose "feature" is the rule string.

A "high lift" leaf is one where the leaf's goal-mean differs from the
population mean by ≥ 0.05 (5pp for binary outcomes, 5% relative for
continuous — we use absolute since callers usually pass [0,1]-bounded
metrics).
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pandas as pd
from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor

from ..types import Finding, StageOutput


def _leaf_paths(tree, feature_names: list[str]) -> list[dict[str, Any]]:
    """Walk a fitted sklearn tree and return one dict per leaf with its rule
    path. Output shape: {"rule": "...", "samples": N, "leaf_id": id}."""
    t = tree.tree_
    paths: list[dict[str, Any]] = []

    def recurse(node_id: int, conditions: list[str]) -> None:
        left = t.children_left[node_id]
        right = t.children_right[node_id]
        if left == right:  # leaf
            paths.append(
                {
                    "rule": " AND ".join(conditions) if conditions else "<root>",
                    "leaf_id": int(node_id),
                    "samples": int(t.n_node_samples[node_id]),
                }
            )
            return
        feat = feature_names[t.feature[node_id]]
        thr = float(t.threshold[node_id])
        recurse(left, conditions + [f"{feat} <= {thr:.3f}"])
        recurse(right, conditions + [f"{feat} > {thr:.3f}"])

    recurse(0, [])
    return paths


def run_interaction_discovery(
    user_table: pd.DataFrame,
    goal_metric: str,
    max_depth: int = 4,
    *,
    min_samples_leaf: int = 100,
    numeric_features: list[str] | None = None,
    lift_threshold: float = 0.05,
    is_binary: bool | None = None,
) -> StageOutput:
    started = time.perf_counter()

    if numeric_features is None:
        numeric_features = [
            c
            for c in user_table.columns
            if c not in {goal_metric, "user_id"}
            and pd.api.types.is_numeric_dtype(user_table[c])
        ]
    if not numeric_features or len(user_table) < min_samples_leaf * 2:
        return StageOutput(
            stage=5,
            findings=[],
            elapsed_seconds=time.perf_counter() - started,
            cost_estimate_usd=0.0,
            metadata={"skipped": True, "reason": "insufficient_data"},
        )

    if is_binary is None:
        gv = user_table[goal_metric].dropna()
        is_binary = bool(gv.isin([0, 1]).all() and gv.nunique() <= 2)

    X = user_table[numeric_features].fillna(0.0)
    y = user_table[goal_metric].fillna(0.0)
    if is_binary:
        y = y.astype(int)
        if y.nunique() < 2:
            return StageOutput(
                stage=5,
                findings=[],
                elapsed_seconds=time.perf_counter() - started,
                cost_estimate_usd=0.0,
                metadata={"skipped": True, "reason": "constant_outcome"},
            )
        clf = DecisionTreeClassifier(
            max_depth=max_depth, min_samples_leaf=min_samples_leaf, random_state=42
        )
        clf.fit(X, y)
        pop_mean = float(y.mean())
        leaf_assign = clf.apply(X)
    else:
        clf = DecisionTreeRegressor(
            max_depth=max_depth, min_samples_leaf=min_samples_leaf, random_state=42
        )
        clf.fit(X, y)
        pop_mean = float(y.mean())
        leaf_assign = clf.apply(X)

    paths = _leaf_paths(clf, numeric_features)

    findings: list[Finding] = []
    for p in paths:
        mask = leaf_assign == p["leaf_id"]
        n = int(mask.sum())
        if n < min_samples_leaf:
            continue
        leaf_mean = float(y[mask].mean())
        lift = leaf_mean - pop_mean
        if abs(lift) < lift_threshold:
            continue
        # Only multi-feature interactions are interesting at Stage 5
        if " AND " not in p["rule"]:
            continue
        direction = "positive" if lift > 0 else "negative"
        # Confidence: large lift + large sample → HIGH
        if abs(lift) >= 0.15 and n >= min_samples_leaf * 2:
            confidence = "HIGH"
        elif abs(lift) >= 0.10:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"
        findings.append(
            Finding(
                feature=p["rule"],
                importance=float(abs(lift)),
                direction=direction,
                confidence=confidence,
                metadata={
                    "leaf_id": p["leaf_id"],
                    "n_samples": n,
                    "leaf_mean": round(leaf_mean, 4),
                    "population_mean": round(pop_mean, 4),
                    "lift": round(lift, 4),
                    "depth": p["rule"].count(" AND ") + 1,
                },
            )
        )

    findings.sort(key=lambda f: f.importance, reverse=True)

    return StageOutput(
        stage=5,
        findings=findings,
        elapsed_seconds=time.perf_counter() - started,
        cost_estimate_usd=0.0,
        metadata={
            "max_depth": max_depth,
            "min_samples_leaf": min_samples_leaf,
            "n_leaves": len(paths),
        },
    )
