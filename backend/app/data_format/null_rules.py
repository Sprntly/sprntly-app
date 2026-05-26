"""Null / missing-value rules from Data_Format_Spec.docx.

Pure-Python (no pandas) so this stays composable — callers can feed it
list-of-dicts produced by any normalizer.  The rules are applied in the
exact order the spec lists them:

1.  ``user_id`` null/duplicate → drop duplicates, keep first by
    ``signup_date``.
2.  ``signup_date`` null → infer from first event; otherwise drop row.
    (Inference happens in the normalizer; here we just drop nulls.)
3.  ``goal_metric`` null → DROP that user row.
4.  feature with <50 unique users having data → drop column.
5.  feature with null > 30% → DROP column.
6.  feature with null 10-30% → impute median, reduce confidence by 0.1.
7.  feature with null < 10% → impute median, flag caveat.
8.  feature all zeros → keep but caveat "no variation".

Returns ``(cleaned_rows, caveats)``.  ``caveats`` is a list of
human-readable strings the Synthesis Agent surfaces in the Brief.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any


# Top-level required keys that aren't features.
_REQUIRED = ("user_id", "signup_date", "goal_metric")
# Optional canonical keys (treated as non-features).
_OPTIONAL_CANON = ("tenure_bucket", "region", "device", "tier")


def _feature_keys(rows: list[dict[str, Any]]) -> list[str]:
    """Return every key in any row that isn't a canonical column."""
    out: set[str] = set()
    for r in rows:
        for k in r.keys():
            if k in _REQUIRED or k in _OPTIONAL_CANON:
                continue
            out.add(k)
    return sorted(out)


def _is_null(v: Any) -> bool:
    if v is None:
        return True
    # NaN check without importing math (handles float('nan'))
    try:
        return v != v  # type: ignore[no-any-return]
    except Exception:
        return False


def apply_null_rules(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Apply the 8 null-handling rules in order.

    Input is a list of dicts (one per user).  Each dict has the canonical
    keys plus any number of feature columns.  Output is the cleaned list
    of dicts plus the list of caveat strings.
    """
    caveats: list[str] = []
    if not rows:
        return [], caveats

    # ---- Rule 1: user_id null/duplicate → drop dupes, keep first by signup_date.
    by_id: dict[str, dict[str, Any]] = {}
    dropped_null_uid = 0
    dropped_dupes = 0
    for r in rows:
        uid = r.get("user_id")
        if _is_null(uid) or uid == "":
            dropped_null_uid += 1
            continue
        existing = by_id.get(uid)
        if existing is None:
            by_id[uid] = r
            continue
        # Keep whichever has earlier signup_date; nulls lose.
        e_sd = existing.get("signup_date")
        r_sd = r.get("signup_date")
        if _is_null(e_sd) and not _is_null(r_sd):
            by_id[uid] = r
        elif not _is_null(r_sd) and not _is_null(e_sd) and r_sd < e_sd:
            by_id[uid] = r
        dropped_dupes += 1
    rows = list(by_id.values())
    if dropped_null_uid:
        caveats.append(
            f"dropped {dropped_null_uid} row(s) with null/empty user_id"
        )
    if dropped_dupes:
        caveats.append(
            f"deduplicated {dropped_dupes} duplicate user_id row(s); kept earliest signup_date"
        )

    # ---- Rule 2: signup_date null → drop row (inference is the normalizer's job).
    before = len(rows)
    rows = [r for r in rows if not _is_null(r.get("signup_date"))]
    dropped_sd = before - len(rows)
    if dropped_sd:
        caveats.append(
            f"dropped {dropped_sd} row(s) with null signup_date (normalizer could not infer)"
        )

    # ---- Rule 3: goal_metric null → drop row.
    before = len(rows)
    rows = [r for r in rows if not _is_null(r.get("goal_metric"))]
    dropped_gm = before - len(rows)
    if dropped_gm:
        caveats.append(
            f"dropped {dropped_gm} row(s) with null goal_metric (goal is never imputed)"
        )

    if not rows:
        return [], caveats

    n = len(rows)
    features = _feature_keys(rows)

    # Track per-feature stats.
    null_count: dict[str, int] = defaultdict(int)
    unique_users_with_data: dict[str, int] = defaultdict(int)
    non_null_values: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        for f in features:
            v = r.get(f)
            if _is_null(v):
                null_count[f] += 1
            else:
                unique_users_with_data[f] += 1
                try:
                    non_null_values[f].append(float(v))
                except (TypeError, ValueError):
                    # Non-numeric in a feature col — treat as null.
                    null_count[f] += 1
                    unique_users_with_data[f] -= 1

    # ---- Rule 4: feature with <50 unique users with data → drop column.
    drop_low_coverage = [
        f for f in features if unique_users_with_data[f] < 50
    ]
    # ---- Rule 5: feature with null > 30% → drop column.
    drop_high_null = [
        f
        for f in features
        if f not in drop_low_coverage and (null_count[f] / n) > 0.30
    ]

    dropped_cols = set(drop_low_coverage) | set(drop_high_null)
    if drop_low_coverage:
        caveats.append(
            "dropped feature(s) with <50 unique users with data: "
            + ", ".join(sorted(drop_low_coverage))
        )
    if drop_high_null:
        caveats.append(
            "dropped feature(s) with >30% null: "
            + ", ".join(sorted(drop_high_null))
        )

    # ---- Rules 6, 7, 8 — for the surviving columns.
    surviving = [f for f in features if f not in dropped_cols]
    impute_value: dict[str, float] = {}
    for f in surviving:
        vals = non_null_values[f]
        med = median(vals) if vals else 0.0
        impute_value[f] = med
        null_pct = null_count[f] / n
        if 0.10 <= null_pct <= 0.30:
            caveats.append(
                f"feature {f!r}: {null_pct:.0%} null — imputed median, "
                "confidence reduced by 0.10"
            )
        elif 0 < null_pct < 0.10:
            caveats.append(
                f"feature {f!r}: {null_pct:.0%} null — imputed median"
            )
        # Rule 8: all-zeros caveat.
        if vals and all(v == 0 for v in vals):
            caveats.append(f"feature {f!r}: no variation (all zeros)")

    # Apply: drop columns + impute remaining nulls.
    cleaned: list[dict[str, Any]] = []
    for r in rows:
        new_r: dict[str, Any] = {}
        for k, v in r.items():
            if k in dropped_cols:
                continue
            if k in surviving and _is_null(v):
                new_r[k] = impute_value[k]
            else:
                new_r[k] = v
        # Make sure every surviving feature is present (imputed if missing).
        for f in surviving:
            if f not in new_r:
                new_r[f] = impute_value[f]
        cleaned.append(new_r)

    return cleaned, caveats
