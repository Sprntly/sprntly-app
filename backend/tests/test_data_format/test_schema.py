"""Pydantic invariants on CanonicalUserRow + DataSummary."""
from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.data_format.schema import (
    CanonicalUserRow,
    DataQuality,
    DataSummary,
    FeatureSummary,
    QualityTier,
    ValidationResult,
)


def _ok_row(**over):
    base = dict(
        user_id="u1",
        signup_date=date(2026, 1, 1),
        goal_metric=1.0,
    )
    base.update(over)
    return CanonicalUserRow(**base)


def test_canonical_row_minimal_ok() -> None:
    r = _ok_row()
    assert r.user_id == "u1"
    assert r.goal_metric == 1.0
    assert r.features == {}


def test_canonical_row_missing_required() -> None:
    with pytest.raises(ValidationError):
        CanonicalUserRow(user_id="u1", signup_date=date(2026, 1, 1))  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        CanonicalUserRow(signup_date=date(2026, 1, 1), goal_metric=1.0)  # type: ignore[call-arg]


def test_canonical_row_empty_user_id_rejected() -> None:
    with pytest.raises(ValidationError):
        _ok_row(user_id="")


def test_canonical_row_region_must_be_iso2() -> None:
    assert _ok_row(region="us").region == "US"
    with pytest.raises(ValidationError):
        _ok_row(region="USA")
    with pytest.raises(ValidationError):
        _ok_row(region="1!")


def test_canonical_row_feature_must_be_numeric() -> None:
    _ok_row(features={"clicks_wk1": 5.0})
    _ok_row(features={"clicks_wk1": 0})
    _ok_row(features={"clicks_wk1": None})  # null OK pre-null-rule pass
    with pytest.raises(ValidationError):
        _ok_row(features={"clicks_wk1": "five"})
    with pytest.raises(ValidationError):
        _ok_row(features={"clicks_wk1": True})  # bools rejected — spec says binary 0/1
    with pytest.raises(ValidationError):
        _ok_row(features={"": 1.0})


def test_canonical_row_extra_forbidden() -> None:
    with pytest.raises(ValidationError):
        CanonicalUserRow(
            user_id="u1",
            signup_date=date(2026, 1, 1),
            goal_metric=1.0,
            extraneous=42,  # type: ignore[call-arg]
        )


def test_data_summary_roundtrip() -> None:
    s = DataSummary(
        goal_metric="Day-30 retention",
        n_users=45000,
        goal_metric_rate=0.42,
        features={
            "posts_in_week_1": FeatureSummary(
                avg_in_goal_1=2.3,
                avg_in_goal_0=0.4,
                lift=5.75,
                null_pct=0.02,
                n_users_with_data=44100,
            )
        },
        data_quality=DataQuality(
            completeness_pct=0.91,
            quality_tier=QualityTier.HIGH,
            goal_completeness=0.99,
        ),
        connector="amplitude",
        company_name="Acme Corp",
        product_type="B2B SaaS",
    )
    dumped = s.model_dump(mode="json")
    rebuilt = DataSummary.model_validate(dumped)
    assert rebuilt == s


def test_data_summary_rate_bounds() -> None:
    with pytest.raises(ValidationError):
        DataSummary(
            goal_metric="x",
            n_users=10,
            goal_metric_rate=1.5,  # >1
            features={},
            data_quality=DataQuality(
                completeness_pct=0.5,
                quality_tier=QualityTier.LOW,
                goal_completeness=1.0,
            ),
            connector="csv",
            company_name="A",
            product_type="B",
        )


def test_validation_result_default_warnings_empty() -> None:
    vr = ValidationResult(passed=True)
    assert vr.warnings == []
    assert vr.failures == []
