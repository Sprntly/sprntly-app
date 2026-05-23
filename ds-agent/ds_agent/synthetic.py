"""Synthetic SaaS dataset with implanted ground-truth effects.

The dataset is shaped to test that the agent's Stage-1 pipeline finds known
behavioral drivers of `retention_30d`. Truths planted (in order of effect size):

  T1  posts_first_week >= 1                 -> +30pp retention   (strong, common)
  T2  mobile_only_user == 1                 -> -20pp retention   (strong, common)
  T3  invites_sent >= 3 AND comments >= 5   -> +45pp retention   (rare, ~2% of users)
  T4  plan_tier=enterprise AND tickets >= 3 -> +25pp retention   (stratified)

The pipeline's reported top findings should overlap with these truths;
that overlap is the smoke test in tests/test_pipeline_e2e.py.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def generate(n_users: int = 15_000, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)

    # Categorical dimensions
    region = rng.choice(["US", "EU", "APAC", "Other"], size=n_users, p=[0.55, 0.25, 0.15, 0.05])
    plan_tier = rng.choice(["free", "pro", "enterprise"], size=n_users, p=[0.70, 0.25, 0.05])
    device = rng.choice(["web", "mobile", "both"], size=n_users, p=[0.45, 0.30, 0.25])
    signup_source = rng.choice(
        ["organic", "paid", "referral", "direct"],
        size=n_users,
        p=[0.45, 0.25, 0.15, 0.15],
    )

    # Behavioral features (most users are low-engagement; a long tail uses the product heavily)
    posts_first_week = rng.poisson(0.4, size=n_users)
    comments_first_week = rng.poisson(1.2, size=n_users)
    shares_first_week = rng.poisson(0.25, size=n_users)
    sessions_per_week = rng.gamma(shape=2.0, scale=1.5, size=n_users).round(1)
    profile_completeness = rng.beta(2.0, 5.0, size=n_users).round(2)
    invites_sent = rng.poisson(0.5, size=n_users)
    session_duration_avg = rng.lognormal(mean=2.0, sigma=0.8, size=n_users).round(1)
    support_tickets = rng.poisson(0.2, size=n_users)

    mobile_only_user = (device == "mobile").astype(int)

    # Signup date spread over the last 365 days
    signup_day_offset = rng.integers(0, 365, size=n_users)
    signup_date = pd.Timestamp.now("UTC").normalize() - pd.to_timedelta(signup_day_offset, unit="D")

    # Compose the retention probability from implanted truths + noise
    p_retain = np.full(n_users, 0.35)  # base rate

    # T1: posts_first_week >= 1
    p_retain += 0.30 * (posts_first_week >= 1)

    # T2: mobile_only_user
    p_retain -= 0.20 * mobile_only_user

    # T3: rare power-user segment (invites_sent >= 3 AND comments >= 5)
    rare_segment = (invites_sent >= 3) & (comments_first_week >= 5)
    p_retain += 0.45 * rare_segment

    # T4: enterprise + tickets stratified effect
    enterprise_engaged = (plan_tier == "enterprise") & (support_tickets >= 3)
    p_retain += 0.25 * enterprise_engaged

    # Add some independent noise so the agent isn't trivially recovering the formula
    p_retain += rng.normal(0, 0.05, size=n_users)
    p_retain = np.clip(p_retain, 0.02, 0.98)

    retention_30d = (rng.random(n_users) < p_retain).astype(int)

    df = pd.DataFrame(
        {
            "user_id": [f"u_{i:06d}" for i in range(n_users)],
            "signup_date": signup_date,
            "region": region,
            "plan_tier": plan_tier,
            "device": device,
            "signup_source": signup_source,
            "mobile_only_user": mobile_only_user,
            "posts_first_week": posts_first_week,
            "comments_first_week": comments_first_week,
            "shares_first_week": shares_first_week,
            "sessions_per_week": sessions_per_week,
            "profile_completeness": profile_completeness,
            "invites_sent": invites_sent,
            "session_duration_avg": session_duration_avg,
            "support_tickets": support_tickets,
            "retention_30d": retention_30d,
        }
    )
    return df


GROUND_TRUTH_BEHAVIORS = {
    "posts_first_week",
    "mobile_only_user",
    "invites_sent",
    "comments_first_week",
}
