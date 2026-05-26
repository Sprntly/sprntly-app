"""Build the DATA_SUMMARY object from a list of CanonicalUserRow.

The Express tier reads only the resulting ``DataSummary`` — never the
raw rows.  Per spec, ``goal_metric_rate`` is the share of users whose
``goal_metric`` is "achieved" (binary 1) when goal is binary;
otherwise we fall back to the mean (clipped to [0,1]).

For each feature column we report:

  avg_in_goal_1 / avg_in_goal_0  — mean among users with goal==1 / 0
  lift                            — avg_in_goal_1 / avg_in_goal_0
                                    (inf-safe: 0 → 1e9 sentinel)
  null_pct                        — fraction of users with this feature null
  n_users_with_data               — count of users with non-null value
"""
from __future__ import annotations

from typing import Iterable

import pandas as pd

from app.data_format.quality import assess_quality
from app.data_format.schema import (
    CanonicalUserRow,
    DataSummary,
    FeatureSummary,
)


_LIFT_INF_SENTINEL = 1e9  # what we return when denominator is 0 but numerator isn't


def _rows_to_df(rows: Iterable[CanonicalUserRow]) -> pd.DataFrame:
    records = []
    for r in rows:
        rec: dict = {
            "user_id": r.user_id,
            "signup_date": r.signup_date,
            "goal_metric": r.goal_metric,
        }
        rec.update(r.features or {})
        records.append(rec)
    return pd.DataFrame.from_records(records)


def _goal_rate(goal: pd.Series) -> float:
    """Binary → share with goal==1; else clipped mean."""
    g = goal.dropna()
    if g.empty:
        return 0.0
    distinct = set(g.unique().tolist())
    if distinct <= {0, 0.0, 1, 1.0}:
        return float((g > 0).sum()) / float(len(g))
    rate = float(g.mean())
    return max(0.0, min(1.0, rate))


def _binary_goal_mask(goal: pd.Series) -> pd.Series:
    """Boolean Series: True where goal counts as "achieved".

    Binary goal → goal > 0.  Continuous → goal >= median.
    """
    g = goal.dropna()
    if g.empty:
        return goal.fillna(False).astype(bool)
    distinct = set(g.unique().tolist())
    if distinct <= {0, 0.0, 1, 1.0}:
        return (goal > 0).fillna(False)
    return (goal >= g.median()).fillna(False)


def _safe_lift(num: float, den: float) -> float:
    if den == 0 and num == 0:
        return 1.0
    if den == 0:
        return _LIFT_INF_SENTINEL
    return float(num / den)


def build_data_summary(
    rows: list[CanonicalUserRow],
    goal_metric: str,
    connector: str,
    company_name: str,
    product_type: str,
) -> DataSummary:
    """Assemble a DATA_SUMMARY from canonical rows.

    ``goal_metric`` here is the *label* (e.g. "Day-30 retention") — the
    numeric values come from ``CanonicalUserRow.goal_metric``.
    """
    if not rows:
        return DataSummary(
            goal_metric=goal_metric,
            n_users=0,
            goal_metric_rate=0.0,
            features={},
            data_quality=assess_quality([]),
            connector=connector,
            company_name=company_name,
            product_type=product_type,
        )

    df = _rows_to_df(rows)
    n = len(df)

    goal = df["goal_metric"]
    goal_mask = _binary_goal_mask(goal)
    rate = _goal_rate(goal)

    feature_cols = [
        c for c in df.columns if c not in ("user_id", "signup_date", "goal_metric")
    ]

    features: dict[str, FeatureSummary] = {}
    for col in feature_cols:
        series = pd.to_numeric(df[col], errors="coerce")
        n_with = int(series.notna().sum())
        null_pct = 1.0 - (n_with / n) if n else 0.0
        in_1 = series[goal_mask].dropna()
        in_0 = series[~goal_mask].dropna()
        avg_1 = float(in_1.mean()) if not in_1.empty else 0.0
        avg_0 = float(in_0.mean()) if not in_0.empty else 0.0
        features[col] = FeatureSummary(
            avg_in_goal_1=round(avg_1, 6),
            avg_in_goal_0=round(avg_0, 6),
            lift=round(_safe_lift(avg_1, avg_0), 6),
            null_pct=round(null_pct, 4),
            n_users_with_data=n_with,
        )

    data_quality = assess_quality(rows)

    return DataSummary(
        goal_metric=goal_metric,
        n_users=n,
        goal_metric_rate=round(rate, 4),
        features=features,
        data_quality=data_quality,
        connector=connector,
        company_name=company_name,
        product_type=product_type,
    )
