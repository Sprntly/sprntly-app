"""Quality-tier classification + table-level validation.

Two public functions:

* ``assess_quality(rows)`` → ``DataQuality``
  Walks the canonical rows, computes completeness, classifies the tier
  (HIGH / MEDIUM / LOW / INSUFFICIENT) per spec.

* ``validate_user_table(rows)`` → ``ValidationResult``
  Runs every FAIL/WARN rule from Data_Format_Spec.docx.  Callers must
  check ``result.passed`` before invoking any DS algorithm.
"""
from __future__ import annotations

from typing import Iterable

from app.data_format.schema import (
    CanonicalUserRow,
    DataQuality,
    QualityTier,
    ValidationResult,
)


def _to_dicts(
    rows: Iterable[CanonicalUserRow | dict],
) -> list[dict]:
    out: list[dict] = []
    for r in rows:
        if isinstance(r, CanonicalUserRow):
            out.append(r.model_dump(mode="python"))
        else:
            out.append(dict(r))
    return out


def assess_quality(
    rows: list[CanonicalUserRow] | list[dict],
) -> DataQuality:
    """Compute completeness + assign quality tier.

    Completeness is the fraction of (user, feature) cells that are
    non-null across all feature columns.  ``goal_completeness`` is the
    share of users with a non-null ``goal_metric``.
    """
    dicts = _to_dicts(rows)
    n = len(dicts)
    if n == 0:
        return DataQuality(
            completeness_pct=0.0,
            quality_tier=QualityTier.INSUFFICIENT,
            goal_completeness=0.0,
        )

    # goal completeness.
    goal_present = sum(
        1 for r in dicts if r.get("goal_metric") is not None
    )
    goal_completeness = goal_present / n

    # Feature completeness — across the union of feature keys.
    feature_keys: set[str] = set()
    for r in dicts:
        feats = r.get("features") or {}
        feature_keys.update(feats.keys())

    if feature_keys:
        total_cells = n * len(feature_keys)
        filled = 0
        for r in dicts:
            feats = r.get("features") or {}
            for k in feature_keys:
                if feats.get(k) is not None:
                    filled += 1
        completeness = filled / total_cells if total_cells else 0.0
    else:
        # No features at all — completeness = goal_completeness so we
        # don't pretend coverage is perfect when there's nothing to cover.
        completeness = goal_completeness

    # Tier classification.
    if completeness >= 0.90 and n >= 10_000:
        tier = QualityTier.HIGH
    elif completeness >= 0.70 and n >= 1_000:
        tier = QualityTier.MEDIUM
    elif completeness >= 0.50 and n >= 100:
        tier = QualityTier.LOW
    else:
        tier = QualityTier.INSUFFICIENT

    return DataQuality(
        completeness_pct=round(completeness, 4),
        quality_tier=tier,
        goal_completeness=round(goal_completeness, 4),
    )


def _distinct_count(values: list) -> int:
    return len({v for v in values if v is not None})


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson correlation — None if undefined (zero variance / <2 points)."""
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = sum((x - mx) ** 2 for x in xs)
    dy = sum((y - my) ** 2 for y in ys)
    if dx == 0 or dy == 0:
        return None
    return num / ((dx * dy) ** 0.5)


def validate_user_table(
    rows: list[CanonicalUserRow] | list[dict],
) -> ValidationResult:
    """Run every FAIL/WARN rule from the spec."""
    failures: list[str] = []
    warnings: list[str] = []

    dicts = _to_dicts(rows)
    n = len(dicts)

    # Rule: user_id exists + no nulls.
    if not dicts:
        failures.append("FAIL: table is empty")
        return ValidationResult(passed=False, failures=failures, warnings=warnings)

    if any("user_id" not in r for r in dicts):
        failures.append("FAIL: user_id column missing from one or more rows")
    elif any(r.get("user_id") in (None, "") for r in dicts):
        failures.append("FAIL: user_id has null/empty values")

    # Rule: signup_date exists.
    if any("signup_date" not in r for r in dicts):
        failures.append("FAIL: signup_date column missing")
    elif any(r.get("signup_date") is None for r in dicts):
        failures.append("FAIL: signup_date has null values")

    # Rule: goal_metric exists.
    if any("goal_metric" not in r for r in dicts):
        failures.append("FAIL: goal_metric column missing")

    # Rule: goal_metric completeness ≥ 50%.
    gm_vals = [r.get("goal_metric") for r in dicts if "goal_metric" in r]
    gm_present = [v for v in gm_vals if v is not None]
    if dicts:
        completeness = len(gm_present) / n
        if completeness < 0.50:
            failures.append(
                f"FAIL: goal_metric completeness {completeness:.0%} < 50% (INSUFFICIENT)"
            )

    # Rule: n_users ≥ 100.
    if n < 100:
        failures.append(f"FAIL: n_users={n} < 100")
    elif n < 1_000:
        warnings.append(f"WARN: n_users={n} < 1000 (LOW tier)")

    # Rule: goal_metric has ≥ 2 distinct values.
    if _distinct_count(gm_present) < 2:
        failures.append(
            "FAIL: goal_metric has <2 distinct values (cannot distinguish goal vs. no-goal)"
        )

    # Rule: ≥3 feature cols after null dropping.
    feature_keys: set[str] = set()
    for r in dicts:
        feats = r.get("features") or {}
        feature_keys.update(feats.keys())
    if len(feature_keys) < 3:
        warnings.append(
            f"WARN: only {len(feature_keys)} feature col(s); <3 limits DS signal"
        )

    # Rule: no feature correlates r>0.99 with goal (possible leakage).
    if gm_present and len(gm_present) == n:
        for f in feature_keys:
            xs: list[float] = []
            ys: list[float] = []
            for r in dicts:
                v = (r.get("features") or {}).get(f)
                g = r.get("goal_metric")
                if v is None or g is None:
                    continue
                xs.append(float(v))
                ys.append(float(g))
            r_val = _pearson(xs, ys)
            if r_val is not None and abs(r_val) > 0.99:
                warnings.append(
                    f"WARN: feature {f!r} correlates r={r_val:.3f} with goal — possible leakage"
                )

    return ValidationResult(
        passed=not failures,
        failures=failures,
        warnings=warnings,
    )
