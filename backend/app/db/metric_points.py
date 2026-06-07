"""metric_points store — tiny rolling aggregates (the ONLY DS persistence).

One row = one number for one metric, one weekly period, one source. The unique
key (enterprise_id, metric, period_start, source) makes a re-run of the same
week idempotent: an upsert overwrites the value in place rather than appending
a duplicate. Provider rows themselves are never persisted here (or anywhere) —
they are pulled + computed transiently (data-minimization rule).
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from app.db.client import require_client, utc_now


def _period_key(period_start: str | date) -> str:
    """Normalize a period to an ISO date string (YYYY-MM-DD)."""
    if isinstance(period_start, date):
        return period_start.isoformat()
    return str(period_start)


def upsert_metric_point(
    enterprise_id: str,
    metric: str,
    period_start: str | date,
    value: float,
    source: str,
    *,
    client=None,
) -> None:
    """Insert-or-overwrite one weekly aggregate. Idempotent on the unique key."""
    cli = client or require_client()
    cli.table("metric_points").upsert(
        {
            "enterprise_id": enterprise_id,
            "metric": metric,
            "period_start": _period_key(period_start),
            "value": float(value),
            "source": source,
            "computed_at": utc_now(),
        },
        on_conflict="enterprise_id,metric,period_start,source",
    ).execute()


def list_metric_points(
    enterprise_id: str,
    *,
    metric: Optional[str] = None,
    since: Optional[str | date] = None,
    client=None,
) -> list[dict]:
    """All points for an enterprise (optionally one metric / since a date),
    sorted ascending by period_start. `since` is an inclusive ISO date string.

    Range filtering is done in Python so it works identically against real
    Supabase and the in-memory test fake; per-enterprise volumes are tiny
    (one number per metric per week), so this is cheap."""
    cli = client or require_client()
    q = cli.table("metric_points").select("*").eq("enterprise_id", enterprise_id)
    if metric:
        q = q.eq("metric", metric)
    rows = q.execute().data or []
    since_key = _period_key(since) if since is not None else None
    out = [r for r in rows if since_key is None or _period_key(r["period_start"]) >= since_key]
    out.sort(key=lambda r: _period_key(r["period_start"]))
    return out


def distinct_metrics(enterprise_id: str, *, client=None) -> list[str]:
    """Distinct metric names that have at least one point, sorted."""
    cli = client or require_client()
    rows = (
        cli.table("metric_points").select("metric")
        .eq("enterprise_id", enterprise_id).execute().data or []
    )
    return sorted({r["metric"] for r in rows})
