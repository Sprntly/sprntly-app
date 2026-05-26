"""Validation: each FAIL/WARN scenario from the spec."""
from __future__ import annotations

from datetime import date, timedelta

from app.data_format.quality import validate_user_table


def _row(uid="u1", sd=date(2026, 1, 1), gm=1.0, features=None):
    return {
        "user_id": uid,
        "signup_date": sd,
        "goal_metric": gm,
        "features": features or {},
    }


def _many(n: int, **kw):
    """n rows with varying goal_metric so distinct >= 2."""
    feats = kw.pop("features_per_row", lambda i: {"a": 1.0, "b": 2.0, "c": 3.0})
    return [
        _row(
            uid=f"u{i}",
            sd=date(2026, 1, 1) + timedelta(days=i % 30),
            gm=float(i % 2),
            features=feats(i),
        )
        for i in range(n)
    ]


def test_passes_clean_table() -> None:
    res = validate_user_table(_many(200))
    assert res.passed, res.failures


def test_fail_empty() -> None:
    res = validate_user_table([])
    assert not res.passed
    assert any("empty" in f for f in res.failures)


def test_fail_user_id_null() -> None:
    rows = _many(150)
    rows[0]["user_id"] = None
    res = validate_user_table(rows)
    assert not res.passed
    assert any("user_id" in f for f in res.failures)


def test_fail_signup_date_missing() -> None:
    rows = _many(150)
    del rows[0]["signup_date"]
    res = validate_user_table(rows)
    assert not res.passed
    assert any("signup_date" in f for f in res.failures)


def test_fail_goal_metric_missing_col() -> None:
    rows = _many(150)
    for r in rows:
        del r["goal_metric"]
    res = validate_user_table(rows)
    assert not res.passed
    assert any("goal_metric" in f for f in res.failures)


def test_fail_goal_completeness_under_50() -> None:
    rows = _many(200)
    for r in rows[:120]:  # 60% null
        r["goal_metric"] = None
    res = validate_user_table(rows)
    assert not res.passed
    assert any("INSUFFICIENT" in f or "completeness" in f for f in res.failures)


def test_fail_n_users_under_100() -> None:
    res = validate_user_table(_many(50))
    assert not res.passed
    assert any("n_users=50" in f for f in res.failures)


def test_warn_n_users_under_1000() -> None:
    res = validate_user_table(_many(500))
    assert res.passed
    assert any("LOW tier" in w for w in res.warnings)


def test_fail_goal_single_distinct() -> None:
    rows = _many(200)
    for r in rows:
        r["goal_metric"] = 1.0
    res = validate_user_table(rows)
    assert not res.passed
    assert any("distinct" in f for f in res.failures)


def test_warn_too_few_features() -> None:
    # Two features → should WARN (need ≥3).
    rows = _many(200, features_per_row=lambda i: {"a": 1.0, "b": 2.0})
    res = validate_user_table(rows)
    assert res.passed
    assert any("feature col" in w for w in res.warnings)


def test_warn_feature_leakage() -> None:
    # Make feature 'leak' = goal exactly.
    rows = []
    for i in range(200):
        rows.append(
            {
                "user_id": f"u{i}",
                "signup_date": date(2026, 1, 1),
                "goal_metric": float(i % 2),
                "features": {
                    "leak": float(i % 2),  # r==1.0 with goal
                    "a": float(i),
                    "b": float(i * 2),
                },
            }
        )
    res = validate_user_table(rows)
    assert res.passed
    assert any("leakage" in w for w in res.warnings)
