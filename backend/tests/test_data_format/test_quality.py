"""Quality-tier classification — all four tiers."""
from __future__ import annotations

from datetime import date, timedelta

from app.data_format.quality import assess_quality
from app.data_format.schema import CanonicalUserRow, QualityTier


def _mk_rows(n: int, feature_completeness: float, goal_present: float = 1.0):
    rows = []
    feats_keys = ["f1", "f2", "f3"]
    threshold = int(feature_completeness * n)
    goal_threshold = int(goal_present * n)
    for i in range(n):
        feats = {k: float(i) for k in feats_keys} if i < threshold else {k: None for k in feats_keys}
        rows.append(
            {
                "user_id": f"u{i}",
                "signup_date": date(2026, 1, 1) + timedelta(days=i % 10),
                "goal_metric": float(i % 2) if i < goal_threshold else None,
                "features": feats,
            }
        )
    return rows


def test_quality_tier_high() -> None:
    rows = _mk_rows(n=12_000, feature_completeness=0.95)
    q = assess_quality(rows)
    assert q.quality_tier == QualityTier.HIGH
    assert q.completeness_pct >= 0.90


def test_quality_tier_medium() -> None:
    rows = _mk_rows(n=2_000, feature_completeness=0.80)
    q = assess_quality(rows)
    assert q.quality_tier == QualityTier.MEDIUM


def test_quality_tier_low() -> None:
    rows = _mk_rows(n=500, feature_completeness=0.60)
    q = assess_quality(rows)
    assert q.quality_tier == QualityTier.LOW


def test_quality_tier_insufficient_few_users() -> None:
    rows = _mk_rows(n=50, feature_completeness=0.95)
    q = assess_quality(rows)
    assert q.quality_tier == QualityTier.INSUFFICIENT


def test_quality_tier_insufficient_low_completeness() -> None:
    rows = _mk_rows(n=20_000, feature_completeness=0.40)
    q = assess_quality(rows)
    assert q.quality_tier == QualityTier.INSUFFICIENT


def test_quality_empty_table() -> None:
    q = assess_quality([])
    assert q.quality_tier == QualityTier.INSUFFICIENT
    assert q.completeness_pct == 0.0
    assert q.goal_completeness == 0.0


def test_quality_with_canonical_rows() -> None:
    rows = [
        CanonicalUserRow(
            user_id=f"u{i}",
            signup_date=date(2026, 1, 1),
            goal_metric=1.0,
            features={"a": 1.0, "b": 2.0},
        )
        for i in range(150)
    ]
    q = assess_quality(rows)
    # 150 users, full completeness → LOW (between 100 and 1000).
    assert q.quality_tier == QualityTier.LOW
    assert q.completeness_pct == 1.0
    assert q.goal_completeness == 1.0
