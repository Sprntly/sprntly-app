"""Amplitude → CanonicalUserRow.

Spec field map:
  user_id            → user_id
  min(event_time)    → signup_date
  event_type         → one binary col per type (drop if <50 unique users)
  event_properties.* numeric → mean per user

``goal_metric`` is computed as Day-N retention (default 30): 1 if the
user has any event in [signup_date + 1d, signup_date + (N+1)d], else 0.
Callers who want a different goal can post-process or pass a custom
``goal_metric_fn``.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional

from app.data_format.schema import CanonicalUserRow


_GoalFn = Callable[[str, list[dict[str, Any]], date], float]


def _parse_event_time(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    if isinstance(v, (int, float)):
        # Amplitude exports timestamps as ms since epoch.
        try:
            return datetime.fromtimestamp(float(v) / 1000.0, tz=timezone.utc).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(v, str):
        # Try ISO 8601 first, then a couple of common Amplitude formats.
        for fmt in (None, "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                if fmt is None:
                    return datetime.fromisoformat(v.replace("Z", "+00:00"))
                return datetime.strptime(v, fmt)
            except ValueError:
                continue
    return None


def _retention_goal(
    user_id: str, events: list[dict[str, Any]], signup_date: date, *, window_days: int = 30
) -> float:
    """1 if the user has any event in (signup, signup + window_days], else 0."""
    start = signup_date + timedelta(days=1)
    end = signup_date + timedelta(days=window_days + 1)
    for ev in events:
        t = _parse_event_time(ev.get("event_time") or ev.get("time"))
        if t is None:
            continue
        d = t.date()
        if start <= d < end:
            return 1.0
    return 0.0


def normalize_amplitude(
    events: list[dict[str, Any]],
    goal_metric_window_days: int = 30,
    *,
    goal_metric_fn: Optional[_GoalFn] = None,
) -> list[CanonicalUserRow]:
    """Pivot a flat list of Amplitude events into per-user canonical rows.

    Each input event is a dict with at minimum ``user_id``, ``event_type``,
    and ``event_time`` (ms since epoch, datetime, or ISO string).  Optional
    ``event_properties`` (dict) contribute numeric per-user means; optional
    top-level ``country`` / ``platform`` contribute region / device.
    """
    # 1) Group events by user.
    by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ev in events:
        uid = ev.get("user_id")
        if uid is None or uid == "":
            continue
        by_user[str(uid)].append(ev)

    # 2) First pass: count unique users per event_type so we can drop low-coverage types.
    users_per_event: dict[str, set[str]] = defaultdict(set)
    for uid, evs in by_user.items():
        for ev in evs:
            et = ev.get("event_type")
            if et:
                users_per_event[str(et)].add(uid)
    kept_event_types = {
        et for et, users in users_per_event.items() if len(users) >= 50
    }

    rows: list[CanonicalUserRow] = []
    for uid, evs in by_user.items():
        # signup_date = min(event_time).
        times = [
            t
            for t in (_parse_event_time(e.get("event_time") or e.get("time")) for e in evs)
            if t is not None
        ]
        if not times:
            continue
        signup = min(times).date()

        # Binary cols for kept event types.
        features: dict[str, Optional[float]] = {}
        seen_types = {str(e.get("event_type")) for e in evs if e.get("event_type")}
        for et in kept_event_types:
            features[f"event__{et}"] = 1.0 if et in seen_types else 0.0

        # Numeric event_properties → mean per user.
        prop_sums: dict[str, float] = defaultdict(float)
        prop_counts: dict[str, int] = defaultdict(int)
        for ev in evs:
            props = ev.get("event_properties") or {}
            if not isinstance(props, dict):
                continue
            for k, v in props.items():
                if isinstance(v, bool):
                    continue
                if isinstance(v, (int, float)):
                    prop_sums[k] += float(v)
                    prop_counts[k] += 1
        for k, total in prop_sums.items():
            if prop_counts[k]:
                features[f"prop__{k}_mean"] = total / prop_counts[k]

        # Region / device from any event that has them (last write wins).
        region = None
        device = None
        for ev in evs:
            c = ev.get("country") or ev.get("region")
            if c and isinstance(c, str) and len(c) == 2:
                region = c.upper()
            p = ev.get("platform") or ev.get("device")
            if isinstance(p, str):
                low = p.lower()
                if low in ("ios", "android", "mobile"):
                    device = "mobile"
                elif low in ("web", "browser"):
                    device = "web"
                elif low in ("desktop", "macos", "windows", "linux"):
                    device = "desktop"

        # goal_metric.
        if goal_metric_fn is not None:
            goal_value = float(goal_metric_fn(uid, evs, signup))
        else:
            goal_value = _retention_goal(
                uid, evs, signup, window_days=goal_metric_window_days
            )

        row = CanonicalUserRow(
            user_id=uid,
            signup_date=signup,
            goal_metric=goal_value,
            region=region,
            device=device,  # type: ignore[arg-type]
            features=features,
        )
        rows.append(row)

    return rows


__all__ = ["normalize_amplitude"]
