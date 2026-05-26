"""Stage 4 — Causal Validation.

Spec §2.2 Stage 4: For each top finding's behaviour we treat it as a
quasi-experiment.

  - PSM (Propensity Score Matching):
      - Fit LogisticRegression to predict treatment from covariates
      - Match each treated user to the nearest control by propensity,
        within caliper = 0.05 (in propensity-score units)
      - t-test on goal_metric across matched groups
  - DiD (Difference-in-Differences):
      - statsmodels OLS:  goal ~ time + treatment + time:treatment
      - the time:treatment coefficient is the DiD estimate

Triangulation: a finding is "causally validated" when both PSM and DiD
point in the same direction with p < 0.10 (loose threshold — Stage 4
is signal-amplification, not gospel-truth).
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

import statsmodels.formula.api as smf

from ..types import DiDResult, Finding, PSMResult, StageOutput


# ─────────────────────────── PSM ───────────────────────────


def run_psm(
    user_table: pd.DataFrame,
    treatment_col: str,
    goal_metric: str,
    *,
    covariates: list[str] | None = None,
    caliper: float = 0.05,
) -> PSMResult:
    df = user_table.copy()
    if treatment_col not in df.columns:
        raise ValueError(f"treatment_col '{treatment_col}' not in dataframe")

    # Coerce treatment to {0,1}. For numeric features we threshold at the
    # median so PSM remains applicable to non-binary "behaviours".
    if not df[treatment_col].isin([0, 1]).all():
        threshold = df[treatment_col].median()
        df["_T"] = (df[treatment_col] > threshold).astype(int)
    else:
        df["_T"] = df[treatment_col].astype(int)

    if covariates is None:
        covariates = [
            c
            for c in df.columns
            if c not in {treatment_col, goal_metric, "_T", "user_id"}
            and pd.api.types.is_numeric_dtype(df[c])
        ]

    if not covariates:
        return PSMResult(treatment_col, 0.0, 1.0, 0, 0, caliper)

    X = df[covariates].fillna(0.0).to_numpy()
    X = StandardScaler().fit_transform(X)
    T = df["_T"].to_numpy()

    if len(np.unique(T)) < 2:
        return PSMResult(treatment_col, 0.0, 1.0, 0, 0, caliper)

    # Propensity: P(T=1 | X)
    lr = LogisticRegression(max_iter=1000, solver="lbfgs")
    lr.fit(X, T)
    p = lr.predict_proba(X)[:, 1]

    treated_idx = np.where(T == 1)[0]
    control_idx = np.where(T == 0)[0]
    if len(treated_idx) == 0 or len(control_idx) == 0:
        return PSMResult(treatment_col, 0.0, 1.0, 0, 0, caliper)

    # Greedy nearest-neighbour match WITHOUT replacement, within caliper.
    control_p = p[control_idx]
    matched_treated: list[int] = []
    matched_control: list[int] = []
    used: set[int] = set()
    # Order treated by descending propensity so we match the hardest cases first.
    for ti in sorted(treated_idx, key=lambda i: -p[i]):
        # Find nearest unused control by propensity distance
        diffs = np.abs(control_p - p[ti])
        # Mask used
        for u in used:
            # find position in control_idx of u
            pos = np.where(control_idx == u)[0]
            if len(pos):
                diffs[pos[0]] = np.inf
        best = int(np.argmin(diffs))
        if diffs[best] > caliper:
            continue
        matched_treated.append(ti)
        matched_control.append(int(control_idx[best]))
        used.add(int(control_idx[best]))

    if not matched_treated:
        return PSMResult(treatment_col, 0.0, 1.0, 0, 0, caliper)

    y = df[goal_metric].astype(float).fillna(0.0).to_numpy()
    y_t = y[matched_treated]
    y_c = y[matched_control]

    estimate = float(np.mean(y_t) - np.mean(y_c))
    if len(y_t) > 1 and len(y_c) > 1 and (np.std(y_t) > 0 or np.std(y_c) > 0):
        t_stat, p_value = stats.ttest_ind(y_t, y_c, equal_var=False)
        p_value = float(p_value)
    else:
        p_value = 1.0

    return PSMResult(
        treatment=treatment_col,
        estimate=estimate,
        p_value=p_value,
        n_treated_matched=len(matched_treated),
        n_control_matched=len(matched_control),
        caliper=caliper,
    )


# ─────────────────────────── DiD ───────────────────────────


def run_did(
    user_table: pd.DataFrame,
    treatment_col: str,
    time_col: str,
    goal_metric: str,
) -> DiDResult:
    df = user_table.copy()
    if treatment_col not in df.columns or time_col not in df.columns:
        return DiDResult(treatment_col, 0.0, 1.0, 0)

    if pd.api.types.is_datetime64_any_dtype(df[time_col]):
        median_ts = df[time_col].median()
        df["_post"] = (df[time_col] > median_ts).astype(int)
    elif df[time_col].isin([0, 1]).all():
        df["_post"] = df[time_col].astype(int)
    else:
        threshold = df[time_col].median()
        df["_post"] = (df[time_col] > threshold).astype(int)

    if not df[treatment_col].isin([0, 1]).all():
        thr = df[treatment_col].median()
        df["_T"] = (df[treatment_col] > thr).astype(int)
    else:
        df["_T"] = df[treatment_col].astype(int)

    df["_y"] = df[goal_metric].astype(float).fillna(0.0)

    if df["_T"].nunique() < 2 or df["_post"].nunique() < 2:
        return DiDResult(treatment_col, 0.0, 1.0, len(df))

    try:
        model = smf.ols("_y ~ _post + _T + _post:_T", data=df).fit()
    except Exception:  # noqa: BLE001 — OLS is rarely flaky but stay defensive
        return DiDResult(treatment_col, 0.0, 1.0, len(df))

    interaction_name = None
    for name in model.params.index:
        if ":" in name and "_post" in name and "_T" in name:
            interaction_name = name
            break
    if interaction_name is None:
        return DiDResult(treatment_col, 0.0, 1.0, len(df))

    return DiDResult(
        treatment=treatment_col,
        estimate=float(model.params[interaction_name]),
        p_value=float(model.pvalues[interaction_name]),
        n_obs=int(model.nobs),
    )


# ─────────────────────────── Entry ───────────────────────────


def run_causal_validation(
    user_table: pd.DataFrame,
    goal_metric: str,
    candidates: list[str],
    *,
    time_col: str | None = None,
    p_threshold: float = 0.10,
) -> StageOutput:
    """Run PSM + DiD per candidate; triangulate them into a single finding."""
    started = time.perf_counter()

    if time_col is None:
        for c in ("signup_date", "event_date", "date", "timestamp"):
            if c in user_table.columns:
                time_col = c
                break

    findings: list[Finding] = []
    for cand in candidates:
        if cand not in user_table.columns:
            continue
        psm = run_psm(user_table, cand, goal_metric)
        did = (
            run_did(user_table, cand, time_col, goal_metric)
            if time_col is not None
            else DiDResult(cand, 0.0, 1.0, 0)
        )

        psm_sig = psm.p_value < p_threshold and psm.n_treated_matched >= 20
        did_sig = did.p_value < p_threshold and did.n_obs > 0
        same_direction = np.sign(psm.estimate) == np.sign(did.estimate) and psm.estimate != 0

        if psm_sig and did_sig and same_direction:
            confidence = "HIGH"
        elif psm_sig or did_sig:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        # Use PSM estimate as canonical (DiD is sometimes degenerate when
        # the time split doesn't isolate pre/post)
        estimate = psm.estimate if psm.n_treated_matched > 0 else did.estimate
        direction = "positive" if estimate >= 0 else "negative"

        findings.append(
            Finding(
                feature=cand,
                importance=float(abs(estimate)),
                direction=direction,
                confidence=confidence,
                metadata={
                    "psm_estimate": round(psm.estimate, 4),
                    "psm_p_value": round(psm.p_value, 4),
                    "psm_n_matched": psm.n_treated_matched,
                    "did_estimate": round(did.estimate, 4),
                    "did_p_value": round(did.p_value, 4),
                    "did_n_obs": did.n_obs,
                    "triangulated": bool(psm_sig and did_sig and same_direction),
                },
            )
        )

    return StageOutput(
        stage=4,
        findings=findings,
        elapsed_seconds=time.perf_counter() - started,
        cost_estimate_usd=0.0,
        metadata={"candidates": list(candidates), "time_col": time_col},
    )
