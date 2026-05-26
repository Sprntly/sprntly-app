"""Mixpanel → CanonicalUserRow.

Spec field map:
  distinct_id (post-merge) → user_id
  time                     → signup_date
  event                    → binary cols
  identity merge required  → callers can supply ``alias_map`` mapping
                             pre-merge ids to canonical distinct_ids.

If ``alias_map`` is omitted we treat ``distinct_id`` as already merged
(noop).  All other behavior mirrors the Amplitude normalizer so the
shape coming out is identical.
"""
from __future__ import annotations

from typing import Any, Optional

from app.data_format.normalizers.amplitude import normalize_amplitude
from app.data_format.schema import CanonicalUserRow


def normalize_mixpanel(
    events: list[dict[str, Any]],
    goal_metric_window_days: int = 30,
    *,
    alias_map: Optional[dict[str, str]] = None,
) -> list[CanonicalUserRow]:
    """Identity-merge + remap to Amplitude shape, then reuse that pipeline.

    Mixpanel events arrive as ``{"event": ..., "properties": {"distinct_id": ...,
    "time": ..., ...}}``.  We flatten to the Amplitude shape so we don't
    duplicate the pivot logic.
    """
    alias_map = alias_map or {}

    flattened: list[dict[str, Any]] = []
    for ev in events:
        props = ev.get("properties") or {}
        if not isinstance(props, dict):
            props = {}
        raw_id = props.get("distinct_id") or ev.get("distinct_id")
        if raw_id is None:
            continue
        uid = alias_map.get(str(raw_id), str(raw_id))

        # Mixpanel "time" is usually epoch seconds (int) or ISO string.
        t = props.get("time") or ev.get("time")
        if isinstance(t, (int, float)) and t < 1e12:
            # epoch seconds → convert to ms so amplitude parser handles it.
            t = float(t) * 1000.0

        # Pull through a small set of useful top-level props.
        country = props.get("$country_code") or props.get("country")
        platform = props.get("$os") or props.get("platform") or props.get("device")

        # Build event_properties from the remaining numeric props.
        SKIP = {
            "distinct_id",
            "time",
            "$country_code",
            "country",
            "$os",
            "platform",
            "device",
            "token",
            "$insert_id",
        }
        event_properties: dict[str, Any] = {
            k: v
            for k, v in props.items()
            if k not in SKIP and isinstance(v, (int, float)) and not isinstance(v, bool)
        }

        flattened.append(
            {
                "user_id": uid,
                "event_type": ev.get("event"),
                "event_time": t,
                "event_properties": event_properties,
                "country": country,
                "platform": platform,
            }
        )

    return normalize_amplitude(
        flattened, goal_metric_window_days=goal_metric_window_days
    )


__all__ = ["normalize_mixpanel"]
