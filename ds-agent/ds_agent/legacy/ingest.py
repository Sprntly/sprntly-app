"""CSV ingest + data-quality assessment.

Spec §3.1 (CSV connector) + §2.4 (Data quality at intake).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd


# Columns we never treat as candidate behavioral features even when numeric.
_RESERVED_COLUMNS = {"user_id", "signup_date"}


@dataclass
class DatasetMeta:
    df: pd.DataFrame
    goal_metric: str
    numeric_features: list[str]
    categorical_features: list[str]
    data_quality: dict[str, Any] = field(default_factory=dict)


def load(csv_path: str | Path, goal_metric: str) -> DatasetMeta:
    path = Path(csv_path)
    df = pd.read_csv(path)

    if goal_metric not in df.columns:
        raise ValueError(
            f"goal_metric '{goal_metric}' not found in CSV. Columns: {list(df.columns)}"
        )

    if "signup_date" in df.columns:
        df["signup_date"] = pd.to_datetime(df["signup_date"], errors="coerce", utc=True)

    numeric_features: list[str] = []
    categorical_features: list[str] = []
    for col in df.columns:
        if col == goal_metric or col in _RESERVED_COLUMNS:
            continue
        if pd.api.types.is_numeric_dtype(df[col]):
            numeric_features.append(col)
        elif pd.api.types.is_string_dtype(df[col]) or pd.api.types.is_categorical_dtype(df[col]):
            categorical_features.append(col)

    quality = _assess_quality(df, goal_metric, numeric_features, categorical_features)

    return DatasetMeta(
        df=df,
        goal_metric=goal_metric,
        numeric_features=numeric_features,
        categorical_features=categorical_features,
        data_quality=quality,
    )


def _assess_quality(
    df: pd.DataFrame,
    goal_metric: str,
    numeric_features: list[str],
    categorical_features: list[str],
) -> dict[str, Any]:
    n = len(df)
    completeness = {col: float(1.0 - df[col].isna().mean()) for col in df.columns}
    overall_completeness = float(sum(completeness.values()) / max(1, len(completeness)))

    unreliable_columns = [col for col, c in completeness.items() if c < 0.70]
    low_power_segments: list[dict[str, Any]] = []
    for col in categorical_features:
        counts = df[col].value_counts(dropna=False)
        for value, count in counts.items():
            if count < 100:
                low_power_segments.append(
                    {"dimension": col, "value": str(value), "size": int(count)}
                )

    goal_values = df[goal_metric].dropna()
    is_binary = goal_values.isin([0, 1]).all() and goal_values.nunique() <= 2

    return {
        "sample_size": n,
        "completeness": round(overall_completeness, 3),
        "completeness_per_column": completeness,
        "unreliable_columns": unreliable_columns,
        "low_power_segments": low_power_segments,
        "goal_metric_is_binary": bool(is_binary),
        "time_period": _time_period(df),
        "known_issues": [],
    }


def _time_period(df: pd.DataFrame) -> str | None:
    if "signup_date" not in df.columns:
        return None
    dates = pd.to_datetime(df["signup_date"], errors="coerce", utc=True).dropna()
    if dates.empty:
        return None
    return f"{dates.min().date()} to {dates.max().date()}"
