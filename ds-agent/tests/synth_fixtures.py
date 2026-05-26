"""Synthetic dataframes with planted ground-truth signals for Stage 2-5 tests.

Kept separate from ds_agent/synthetic.py so the test data doesn't get
bundled with the package distribution.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def temporal_dataset(n: int = 4000, seed: int = 42) -> pd.DataFrame:
    """Planted truth:
      - feature_A is STABLE: always high importance for retention
      - feature_B is EMERGING: only matters in the latest time bucket
      - feature_C is DEGRADING: mattered early, doesn't anymore
      - feature_D is noise
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    bucket = np.repeat(np.arange(4), n // 4)
    if len(bucket) < n:
        bucket = np.concatenate([bucket, np.full(n - len(bucket), 3)])

    feature_A = rng.normal(0, 1, n)
    feature_B = rng.normal(0, 1, n)
    feature_C = rng.normal(0, 1, n)
    feature_D = rng.normal(0, 1, n)

    base = 0.35
    p = np.full(n, base)
    p += 0.25 * feature_A  # always strong
    p += 0.30 * feature_B * (bucket == 3)  # only the last bucket
    p += 0.30 * feature_C * (bucket == 0)  # only the first bucket
    p = np.clip(p + rng.normal(0, 0.1, n), 0.02, 0.98)
    retention = (rng.random(n) < p).astype(int)

    return pd.DataFrame(
        {
            "user_id": [f"u_{i:06d}" for i in range(n)],
            "signup_date": dates,
            "feature_A": feature_A,
            "feature_B": feature_B,
            "feature_C": feature_C,
            "feature_D": feature_D,
            "retention_30d": retention,
        }
    )


def tail_dataset(n: int = 4000, seed: int = 42) -> pd.DataFrame:
    """Planted truth: a rare 3% segment of users have feature_X >> population.
    Their retention is much higher than the rest."""
    rng = np.random.default_rng(seed)
    is_power = rng.random(n) < 0.03
    feature_X = np.where(is_power, rng.normal(8, 1, n), rng.normal(0, 1, n))
    feature_Y = rng.normal(0, 1, n)
    feature_Z = rng.normal(0, 1, n)

    base_p = 0.30
    p = np.full(n, base_p) + 0.50 * is_power + 0.05 * feature_Y
    p = np.clip(p + rng.normal(0, 0.05, n), 0.02, 0.98)
    retention = (rng.random(n) < p).astype(int)
    return pd.DataFrame(
        {
            "user_id": [f"u_{i:06d}" for i in range(n)],
            "feature_X": feature_X,
            "feature_Y": feature_Y,
            "feature_Z": feature_Z,
            "retention_30d": retention,
        }
    )


def did_dataset(n: int = 4000, true_did: float = 0.20, seed: int = 42) -> pd.DataFrame:
    """Synthetic DiD ground truth.
    Treatment doubles in post period; we plant a known DiD coefficient."""
    rng = np.random.default_rng(seed)
    # Each user observed once but at a specific timestamp pre/post
    is_post = (rng.random(n) < 0.5).astype(int)
    is_treated = (rng.random(n) < 0.5).astype(int)

    base = 0.30
    time_eff = 0.05
    treat_eff = 0.10

    y = (
        base
        + time_eff * is_post
        + treat_eff * is_treated
        + true_did * is_post * is_treated
        + rng.normal(0, 0.05, n)
    )
    # also add some covariates so PSM has something to balance
    cov_1 = rng.normal(0, 1, n)
    cov_2 = rng.normal(0, 1, n)
    return pd.DataFrame(
        {
            "user_id": [f"u_{i:06d}" for i in range(n)],
            "signup_date": pd.to_datetime("2026-01-01", utc=True)
            + pd.to_timedelta(is_post * 30 + rng.integers(0, 5, n), unit="D"),
            "treatment": is_treated,
            "cov_1": cov_1,
            "cov_2": cov_2,
            "goal": y,
        }
    )


def interaction_dataset(n: int = 4000, seed: int = 42) -> pd.DataFrame:
    """Planted interaction: high_usage AND mobile_user → +30pp retention."""
    rng = np.random.default_rng(seed)
    usage_hours = rng.gamma(2.0, 2.0, n)
    is_mobile = (rng.random(n) < 0.5).astype(int)
    high_usage = (usage_hours > usage_hours.mean()).astype(int)
    other_feat = rng.normal(0, 1, n)

    base = 0.30
    p = np.full(n, base)
    p += 0.05 * high_usage  # weak alone
    p += 0.05 * is_mobile  # weak alone
    p += 0.30 * (high_usage & is_mobile)  # interaction is the punch
    p = np.clip(p + rng.normal(0, 0.05, n), 0.02, 0.98)
    retention = (rng.random(n) < p).astype(int)
    return pd.DataFrame(
        {
            "user_id": [f"u_{i:06d}" for i in range(n)],
            "usage_hours": usage_hours,
            "is_mobile": is_mobile,
            "other_feat": other_feat,
            "retention_30d": retention,
        }
    )
