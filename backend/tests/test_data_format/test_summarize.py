"""DATA_SUMMARY round-trip from canonical rows."""
from __future__ import annotations

from datetime import date, timedelta

from app.data_format.schema import CanonicalUserRow, DataSummary, QualityTier
from app.data_format.summarize import build_data_summary


def _rows(n=200):
    out = []
    for i in range(n):
        # Goal: half the users have goal=1.
        goal = float(i % 2)
        # posts_in_week_1: users who hit goal post more.
        posts = 5.0 if goal == 1.0 else 1.0
        out.append(
            CanonicalUserRow(
                user_id=f"u{i}",
                signup_date=date(2026, 1, 1) + timedelta(days=i % 30),
                goal_metric=goal,
                features={"posts_in_week_1": posts, "sessions": float(i % 5)},
            )
        )
    return out


def test_summary_basic_shape() -> None:
    s = build_data_summary(
        _rows(200),
        goal_metric="Day-30 retention",
        connector="amplitude",
        company_name="Acme Corp",
        product_type="B2B SaaS",
    )
    assert s.n_users == 200
    assert s.goal_metric == "Day-30 retention"
    assert s.connector == "amplitude"
    assert s.company_name == "Acme Corp"
    assert s.product_type == "B2B SaaS"
    assert "posts_in_week_1" in s.features
    assert "sessions" in s.features


def test_summary_lift_calculation() -> None:
    s = build_data_summary(
        _rows(200),
        goal_metric="Day-30 retention",
        connector="amplitude",
        company_name="Acme",
        product_type="B2B SaaS",
    )
    fs = s.features["posts_in_week_1"]
    assert fs.avg_in_goal_1 == 5.0
    assert fs.avg_in_goal_0 == 1.0
    assert fs.lift == 5.0
    assert fs.null_pct == 0.0
    assert fs.n_users_with_data == 200


def test_summary_goal_rate_binary() -> None:
    s = build_data_summary(
        _rows(200),
        goal_metric="g",
        connector="csv",
        company_name="a",
        product_type="b",
    )
    # Half are 1.
    assert s.goal_metric_rate == 0.5


def test_summary_includes_quality() -> None:
    s = build_data_summary(
        _rows(200),
        goal_metric="g",
        connector="csv",
        company_name="a",
        product_type="b",
    )
    # 200 users, full completeness → LOW (between 100 and 1000).
    assert s.data_quality.quality_tier == QualityTier.LOW
    assert s.data_quality.goal_completeness == 1.0


def test_summary_empty() -> None:
    s = build_data_summary(
        [],
        goal_metric="g",
        connector="csv",
        company_name="a",
        product_type="b",
    )
    assert s.n_users == 0
    assert s.features == {}
    assert s.data_quality.quality_tier == QualityTier.INSUFFICIENT


def test_summary_roundtrip_json() -> None:
    s = build_data_summary(
        _rows(150),
        goal_metric="Day-30 retention",
        connector="amplitude",
        company_name="Acme Corp",
        product_type="B2B SaaS",
    )
    blob = s.model_dump(mode="json")
    rebuilt = DataSummary.model_validate(blob)
    assert rebuilt == s


def test_summary_safe_lift_zero_denominator() -> None:
    # All users with goal==0 have the feature at 0; users with goal==1 have it at 5.
    rows = []
    for i in range(200):
        goal = float(i % 2)
        feat = 5.0 if goal == 1.0 else 0.0
        rows.append(
            CanonicalUserRow(
                user_id=f"u{i}",
                signup_date=date(2026, 1, 1),
                goal_metric=goal,
                features={"f": feat},
            )
        )
    s = build_data_summary(
        rows, goal_metric="g", connector="csv", company_name="a", product_type="b"
    )
    fs = s.features["f"]
    assert fs.avg_in_goal_1 == 5.0
    assert fs.avg_in_goal_0 == 0.0
    # Lift uses sentinel since denominator is 0 but numerator isn't.
    assert fs.lift > 1e6
