"""Stage 1: Core Pattern Discovery (PCA + SHAP + Stratified).

Spec §2.2 Stage 1. Returns a flat list of finding dicts; consolidation,
ranking, and confidence scoring happen downstream in the pipeline.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.preprocessing import StandardScaler

import shap


# ─────────────────────────── helpers ───────────────────────────


def _safe_corr(a: np.ndarray, b: np.ndarray) -> float:
    if np.std(a) == 0 or np.std(b) == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _train_model(X: pd.DataFrame, y: pd.Series, is_binary: bool, seed: int = 0):
    if is_binary:
        m = RandomForestClassifier(
            n_estimators=200, max_depth=8, n_jobs=-1, random_state=seed
        )
    else:
        m = RandomForestRegressor(
            n_estimators=200, max_depth=8, n_jobs=-1, random_state=seed
        )
    m.fit(X, y)
    return m


def _shap_importances(model, X: pd.DataFrame, is_binary: bool) -> tuple[np.ndarray, np.ndarray]:
    """Returns (mean |shap|, direction_sign) per feature.

    `direction_sign[j]` is +1 / -1 / 0 based on the correlation between the
    feature's value and its SHAP value across rows. Correlation is robust to
    skewed feature distributions; mean signed SHAP is not.
    """
    explainer = shap.TreeExplainer(model)
    shap_vals = explainer.shap_values(X, check_additivity=False)

    # For classifiers, recent SHAP returns a 3-D array (n_samples, n_features, n_classes)
    # or a list of arrays — pick the positive class.
    if isinstance(shap_vals, list):
        sv = shap_vals[1] if len(shap_vals) >= 2 else shap_vals[0]
    elif isinstance(shap_vals, np.ndarray) and shap_vals.ndim == 3:
        sv = shap_vals[:, :, -1]
    else:
        sv = shap_vals

    abs_mean = np.abs(sv).mean(axis=0)

    direction_sign = np.zeros(X.shape[1])
    X_arr = X.to_numpy()
    for j in range(X.shape[1]):
        col = X_arr[:, j]
        s = sv[:, j]
        if np.std(col) == 0 or np.std(s) == 0:
            continue
        corr = np.corrcoef(col, s)[0, 1]
        if corr > 0.05:
            direction_sign[j] = 1
        elif corr < -0.05:
            direction_sign[j] = -1
    return abs_mean, direction_sign


# ─────────────────────────── PCA ───────────────────────────


def _pca_findings(
    df: pd.DataFrame, numeric_features: list[str], goal_metric: str, seed: int
) -> list[dict[str, Any]]:
    if len(numeric_features) < 2:
        return []

    X = df[numeric_features].fillna(0.0)
    y = df[goal_metric].astype(float).fillna(0.0).values

    X_scaled = StandardScaler().fit_transform(X)
    n_comp = min(5, X.shape[1])
    pca = PCA(n_components=n_comp, random_state=seed)
    components = pca.fit_transform(X_scaled)

    findings: list[dict[str, Any]] = []
    for i in range(n_comp):
        comp_vec = components[:, i]
        corr = _safe_corr(comp_vec, y)
        if abs(corr) < 0.10:
            continue
        loadings = pca.components_[i]
        top_idx = np.argsort(np.abs(loadings))[-3:][::-1]
        top_behaviors = [numeric_features[j] for j in top_idx]
        findings.append(
            {
                "method": "PCA",
                "factor": f"latent_factor_{i + 1}",
                "behaviors": top_behaviors,
                "effect_size": float(abs(corr)),
                "directionality": "positive" if corr > 0 else "negative",
                "variance_explained": float(pca.explained_variance_ratio_[i]),
            }
        )
    return findings


# ─────────────────────────── SHAP ───────────────────────────


def _shap_findings(
    df: pd.DataFrame,
    numeric_features: list[str],
    goal_metric: str,
    is_binary: bool,
    top_k: int = 10,
    seed: int = 0,
) -> list[dict[str, Any]]:
    if not numeric_features:
        return []

    X = df[numeric_features].fillna(0.0)
    y = df[goal_metric].fillna(0.0)
    if is_binary:
        y = y.astype(int)

    model = _train_model(X, y, is_binary, seed=seed)
    abs_mean, direction_sign = _shap_importances(model, X, is_binary)

    order = np.argsort(abs_mean)[::-1][:top_k]
    findings: list[dict[str, Any]] = []
    for idx in order:
        name = numeric_features[idx]
        findings.append(
            {
                "method": "SHAP",
                "behavior": name,
                "effect_size": float(abs_mean[idx]),
                "directionality": "positive" if direction_sign[idx] > 0 else "negative",
                "sample_size": int(len(df)),
            }
        )
    return findings


# ─────────────────────────── Stratified ───────────────────────────


def _stratified_findings(
    df: pd.DataFrame,
    numeric_features: list[str],
    categorical_features: list[str],
    goal_metric: str,
    is_binary: bool,
    min_stratum_size: int = 200,
    importance_threshold: float = 0.02,
    seed: int = 0,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for dim in categorical_features:
        # Skip very high-cardinality dimensions (likely IDs)
        n_unique = df[dim].nunique(dropna=True)
        if n_unique > 10:
            continue
        for value in df[dim].dropna().unique():
            stratum = df[df[dim] == value]
            if len(stratum) < min_stratum_size:
                continue

            X = stratum[numeric_features].fillna(0.0)
            y = stratum[goal_metric].fillna(0.0)
            if is_binary:
                y = y.astype(int)
                if y.nunique() < 2:
                    continue  # constant outcome inside stratum

            model = _train_model(X, y, is_binary, seed=seed)
            abs_mean, direction_sign = _shap_importances(model, X, is_binary)
            for j, importance in enumerate(abs_mean):
                if importance < importance_threshold:
                    continue
                findings.append(
                    {
                        "method": "Stratified_SHAP",
                        "behavior": numeric_features[j],
                        "stratum": f"{dim}={value}",
                        "stratum_dimension": dim,
                        "stratum_value": str(value),
                        "effect_size": float(importance),
                        "directionality": "positive" if direction_sign[j] > 0 else "negative",
                        "sample_size": int(len(stratum)),
                    }
                )
    return findings


# ─────────────────────────── entry ───────────────────────────


def run(
    df: pd.DataFrame,
    numeric_features: list[str],
    categorical_features: list[str],
    goal_metric: str,
    is_binary: bool,
    seeds: tuple[int, ...] = (0, 1, 2),
) -> dict[str, Any]:
    """Run Stage 1 with multiple seeds (per spec §2.2 Stage 1).

    SHAP and PCA findings are aggregated across seeds; the count of seeds in
    which a behavior appeared in the top-K is reported so downstream confidence
    scoring can use cross-method/cross-seed agreement.
    """
    pca_all: list[dict[str, Any]] = []
    shap_all: list[dict[str, Any]] = []
    for s in seeds:
        pca_all.extend(_pca_findings(df, numeric_features, goal_metric, seed=s))
        shap_all.extend(_shap_findings(df, numeric_features, goal_metric, is_binary, seed=s))

    # Aggregate SHAP across seeds: average effect size; count seed agreement
    shap_aggregated: dict[str, dict[str, Any]] = {}
    for f in shap_all:
        key = f["behavior"]
        bucket = shap_aggregated.setdefault(
            key,
            {
                "method": "SHAP",
                "behavior": key,
                "effect_sizes": [],
                "directionalities": [],
                "sample_size": f["sample_size"],
            },
        )
        bucket["effect_sizes"].append(f["effect_size"])
        bucket["directionalities"].append(f["directionality"])

    shap_consolidated = []
    for key, b in shap_aggregated.items():
        sizes = b["effect_sizes"]
        directions = b["directionalities"]
        majority_dir = max(set(directions), key=directions.count)
        shap_consolidated.append(
            {
                "method": "SHAP",
                "behavior": key,
                "effect_size": float(np.mean(sizes)),
                "effect_size_std": float(np.std(sizes)),
                "directionality": majority_dir,
                "seed_agreement": len(sizes) / len(seeds),
                "sample_size": b["sample_size"],
            }
        )

    # Stratified runs once (cost grows with strata × features)
    stratified = _stratified_findings(
        df, numeric_features, categorical_features, goal_metric, is_binary, seed=seeds[0]
    )

    return {
        "pca": pca_all,
        "shap": shap_consolidated,
        "stratified": stratified,
    }
