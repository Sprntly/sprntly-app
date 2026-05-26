"""CSV / Google Sheets → CanonicalUserRow.

Spec: PM picks the columns; non-numeric columns are dropped with a
warning.  We require the caller to point at the ``goal_metric`` column;
``user_id`` and ``signup_date`` columns are required and looked up by
those exact names (or common aliases).

Any other numeric column becomes a feature.  Non-numeric features are
dropped and their names returned in the second slot of the tuple so the
caller can surface a warning to the PM.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional


from app.data_format.schema import CanonicalUserRow


_USER_ID_ALIASES = ("user_id", "userid", "uid", "user", "id")
_SIGNUP_ALIASES = ("signup_date", "signup", "created_at", "first_seen", "joined")
_CANON_COLS = {"user_id", "signup_date", "goal_metric", "region", "device", "tier"}


def _pick(row: dict[str, Any], names: tuple[str, ...]) -> Any:
    for n in names:
        if n in row:
            return row[n]
        # Case-insensitive fallback.
        for k in row.keys():
            if k.lower() == n.lower():
                return row[k]
    return None


def _parse_date(v: Any) -> Optional[date]:
    if v is None or v == "":
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, str):
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(v, fmt).date()
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def _to_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def normalize_csv(
    rows: list[dict[str, Any]],
    goal_metric_col: str,
) -> tuple[list[CanonicalUserRow], list[str]]:
    """Convert CSV-like dicts into canonical rows.

    Returns ``(canonical_rows, warnings)``.  ``warnings`` lists any
    columns that were dropped because they were not numeric.
    """
    warnings: list[str] = []
    if not rows:
        return [], warnings

    # First pass: figure out which extra columns are numeric across the file.
    # We sniff up to the first 50 rows for type detection.
    extra_cols: dict[str, dict[str, int]] = {}
    for r in rows[:50]:
        for k, v in r.items():
            if k in _CANON_COLS or k == goal_metric_col:
                continue
            if k in _USER_ID_ALIASES or k in _SIGNUP_ALIASES:
                continue
            extra_cols.setdefault(k, {"num": 0, "nonnull": 0})
            if v is None or v == "":
                continue
            extra_cols[k]["nonnull"] += 1
            if _to_float(v) is not None:
                extra_cols[k]["num"] += 1

    keep_features: set[str] = set()
    for k, stats in extra_cols.items():
        if stats["nonnull"] == 0:
            # All null in the sniff window — keep it; null_rules will drop later if needed.
            keep_features.add(k)
            continue
        if stats["num"] / stats["nonnull"] >= 0.8:
            keep_features.add(k)
        else:
            warnings.append(
                f"dropped non-numeric column {k!r} (CSV connector requires numeric features)"
            )

    out: list[CanonicalUserRow] = []
    for r in rows:
        uid = _pick(r, _USER_ID_ALIASES)
        if uid is None or uid == "":
            continue
        signup = _parse_date(_pick(r, _SIGNUP_ALIASES))
        if signup is None:
            continue
        goal_raw = r.get(goal_metric_col)
        goal_val = _to_float(goal_raw)
        if goal_val is None:
            # Drop this row — goal_metric is never imputed.
            continue

        features: dict[str, Optional[float]] = {}
        for k in keep_features:
            features[k] = _to_float(r.get(k))

        region = r.get("region")
        if isinstance(region, str):
            region = region.strip().upper() or None
            if region and (len(region) != 2 or not region.isalpha()):
                region = None

        device_raw = r.get("device")
        device = None
        if isinstance(device_raw, str):
            low = device_raw.strip().lower()
            if low in ("mobile", "web", "desktop", "unknown"):
                device = low

        tier_raw = r.get("tier")
        tier = None
        if isinstance(tier_raw, str):
            low = tier_raw.strip().lower()
            if low in ("free", "pro", "enterprise"):
                tier = low

        try:
            row = CanonicalUserRow(
                user_id=str(uid),
                signup_date=signup,
                goal_metric=goal_val,
                region=region,
                device=device,  # type: ignore[arg-type]
                tier=tier,  # type: ignore[arg-type]
                features=features,
            )
        except Exception as e:  # noqa: BLE001 — surface as warning, skip row
            warnings.append(f"skipped row user_id={uid!r}: {e}")
            continue
        out.append(row)

    return out, warnings


__all__ = ["normalize_csv"]
