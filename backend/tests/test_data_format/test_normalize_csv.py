"""CSV / Google Sheets normalizer."""
from __future__ import annotations

from datetime import date

from app.data_format.normalizers.csv import normalize_csv


def test_normalize_csv_minimal_happy_path() -> None:
    rows = [
        {"user_id": "u1", "signup_date": "2026-01-01", "retained_30d": "1", "posts": "5"},
        {"user_id": "u2", "signup_date": "2026-01-02", "retained_30d": "0", "posts": "0"},
    ]
    out, warnings = normalize_csv(rows, goal_metric_col="retained_30d")
    assert warnings == []
    assert len(out) == 2
    assert out[0].user_id == "u1"
    assert out[0].signup_date == date(2026, 1, 1)
    assert out[0].goal_metric == 1.0
    assert out[0].features == {"posts": 5.0}


def test_normalize_csv_drops_non_numeric_features_with_warning() -> None:
    rows = [
        {
            "user_id": f"u{i}",
            "signup_date": "2026-01-01",
            "retained_30d": "1",
            "company_name": "Acme",  # non-numeric — drop
            "posts": str(i),
        }
        for i in range(10)
    ]
    out, warnings = normalize_csv(rows, goal_metric_col="retained_30d")
    assert any("company_name" in w for w in warnings)
    assert "company_name" not in out[0].features
    assert "posts" in out[0].features


def test_normalize_csv_drops_rows_with_null_goal() -> None:
    rows = [
        {"user_id": "u1", "signup_date": "2026-01-01", "retained_30d": "1"},
        {"user_id": "u2", "signup_date": "2026-01-02", "retained_30d": ""},
        {"user_id": "u3", "signup_date": "2026-01-03", "retained_30d": "not_a_number"},
        {"user_id": "u4", "signup_date": "2026-01-04", "retained_30d": "0"},
    ]
    out, _ = normalize_csv(rows, goal_metric_col="retained_30d")
    assert {r.user_id for r in out} == {"u1", "u4"}


def test_normalize_csv_drops_rows_missing_signup() -> None:
    rows = [
        {"user_id": "u1", "signup_date": "", "retained_30d": "1"},
        {"user_id": "u2", "signup_date": "2026-01-01", "retained_30d": "1"},
    ]
    out, _ = normalize_csv(rows, goal_metric_col="retained_30d")
    assert {r.user_id for r in out} == {"u2"}


def test_normalize_csv_accepts_region_device_tier() -> None:
    rows = [
        {
            "user_id": "u1",
            "signup_date": "2026-01-01",
            "retained_30d": "1",
            "region": "us",
            "device": "Mobile",
            "tier": "PRO",
        }
    ]
    out, _ = normalize_csv(rows, goal_metric_col="retained_30d")
    assert out[0].region == "US"
    assert out[0].device.value == "mobile"
    assert out[0].tier.value == "pro"


def test_normalize_csv_aliases() -> None:
    rows = [
        {"uid": "u1", "created_at": "2026-01-01", "goal": "1", "x": "1"},
    ]
    out, _ = normalize_csv(rows, goal_metric_col="goal")
    assert out[0].user_id == "u1"
    assert out[0].signup_date == date(2026, 1, 1)
    assert out[0].features == {"x": 1.0}


def test_normalize_csv_empty_input() -> None:
    out, warnings = normalize_csv([], goal_metric_col="g")
    assert out == []
    assert warnings == []
