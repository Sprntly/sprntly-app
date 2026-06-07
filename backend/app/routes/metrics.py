"""Dashboard metrics service — the backend for design-v4's Dashboard page.

Three routes (all tenant-scoped via require_company):

  POST /v1/metrics/refresh
      Transient pull + weekly aggregate + anomaly detect for every connected
      provider that has a DS aggregator. Error-isolated per provider (one bad
      connection never sinks the others). Reuses the kg_ingest token/puller
      plumbing (PULLERS / token_for) and db.get_connection — same shape as
      routes/ingest.py. Persists ONLY tiny aggregates + Findings.

  GET /v1/metrics/series?range=30d|qtd|ytd
      {metrics:[{metric, points:[{period_start, value}], current, pct_change}]}
      read from metric_points. The KPI tree's north star + primaries lead the
      list when their names match a metric we have points for.

  GET /v1/metrics/export?range=...
      The same series, flattened to CSV (text/csv).

The range toggle (30d / QTD / YTD) mirrors the Dashboard design's header.
"""
from __future__ import annotations

import csv
import io
import json
import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from app import db
from app.auth import CompanyContext, require_company
from app.connectors.tokens import TokenEncryptionError, decrypt_token_json
from app.db.metric_points import distinct_metrics, list_metric_points
from app.graph.facade import GraphFacade
from app.kg_ingest.runner import PULLERS, token_for
from app.kpi_tree import load_kpi_tree

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/metrics", tags=["metrics"])

# Providers we can run DS analyses for = the kg_ingest pullers that also have a
# DS aggregator. Imported lazily inside handlers to avoid an import cycle at
# module load (analyses imports db, which the route also touches).
_VALID_RANGES = ("30d", "qtd", "ytd")


def _range_start(range_key: str, *, today: date | None = None) -> date:
    """Inclusive lower bound (a period_start) for a range toggle value."""
    today = today or datetime.now(timezone.utc).date()
    if range_key == "30d":
        return today - timedelta(days=30)
    if range_key == "qtd":
        q_first_month = 3 * ((today.month - 1) // 3) + 1
        return date(today.year, q_first_month, 1)
    if range_key == "ytd":
        return date(today.year, 1, 1)
    raise HTTPException(400, f"Unknown range {range_key!r}; expected one of {_VALID_RANGES}")


def _ordered_metrics(enterprise_id: str, have: list[str]) -> list[str]:
    """KPI-tree north star + primaries first (when their names match a metric we
    actually have points for), then the rest alphabetically. Matching is exact
    on the metric name."""
    have_set = set(have)
    ordered: list[str] = []
    tree = load_kpi_tree(enterprise_id)
    if tree:
        priority = [tree.north_star.metric] + [m.metric for m in tree.primary_metrics]
        for name in priority:
            if name in have_set and name not in ordered:
                ordered.append(name)
    for name in sorted(have_set):
        if name not in ordered:
            ordered.append(name)
    return ordered


def _series_for_metric(enterprise_id: str, metric: str, since: date) -> dict:
    """One metric's series within range, collapsed across sources by summing
    same-period values (a metric like tasks_open comes from a single source in
    pilot-1, so this is a no-op there, but it keeps multi-source metrics sane).
    current = latest point; pct_change = latest vs previous point."""
    rows = list_metric_points(enterprise_id, metric=metric, since=since.isoformat())
    by_period: dict[str, float] = {}
    for r in rows:
        p = str(r["period_start"])
        by_period[p] = by_period.get(p, 0.0) + float(r["value"])
    points = [
        {"period_start": p, "value": by_period[p]} for p in sorted(by_period)
    ]
    current = points[-1]["value"] if points else None
    pct_change = None
    if len(points) >= 2:
        prev = points[-2]["value"]
        if prev != 0:
            pct_change = round((points[-1]["value"] - prev) / abs(prev), 4)
    return {
        "metric": metric,
        "points": points,
        "current": current,
        "pct_change": pct_change,
    }


def _build_series(enterprise_id: str, range_key: str) -> list[dict]:
    since = _range_start(range_key)
    have = distinct_metrics(enterprise_id)
    ordered = _ordered_metrics(enterprise_id, have)
    series = [_series_for_metric(enterprise_id, m, since) for m in ordered]
    # Drop metrics with no points inside the range (the toggle should hide empties).
    return [s for s in series if s["points"]]


# ─────────────────────────── routes ───────────────────────────

@router.post("/refresh")
def refresh(company: CompanyContext = Depends(require_company)):
    """Run pilot-1 analyses for every connected provider that has a DS aggregator.
    Transient pull → weekly aggregates → anomaly Findings. Error-isolated."""
    from app.ds.analyses import run_analyses

    facade = GraphFacade()
    records_by_provider: dict[str, list] = {}
    pull_errors: list[str] = []

    for provider, (puller, _key, _hint) in PULLERS.items():
        row = db.get_connection(company.company_id, provider)
        if not row:
            continue  # not connected — skip silently
        try:
            token_json = json.loads(decrypt_token_json(row["token_json_encrypted"]))
            token = token_for(provider, token_json)
            records_by_provider[provider] = list(puller(token))
        except (TokenEncryptionError, json.JSONDecodeError, ValueError) as e:
            pull_errors.append(f"{provider}: credential unusable: {e}")
        except Exception as e:  # noqa: BLE001 — puller failure (bad token, API down)
            logger.exception("metrics refresh: pull failed for %s", provider)
            pull_errors.append(f"{provider}: pull failed: {e}")

    if not records_by_provider and not pull_errors:
        raise HTTPException(404, "No connected providers with DS analyses available")

    result = run_analyses(facade, company.company_id, records_by_provider=records_by_provider)
    result["errors"] = pull_errors + result.get("errors", [])
    return {"ok": True, **result}


@router.get("/series")
def series(
    range: str = Query(default="30d"),
    company: CompanyContext = Depends(require_company),
):
    if range not in _VALID_RANGES:
        raise HTTPException(400, f"Unknown range {range!r}; expected one of {_VALID_RANGES}")
    return {"range": range, "metrics": _build_series(company.company_id, range)}


@router.get("/export")
def export(
    range: str = Query(default="30d"),
    company: CompanyContext = Depends(require_company),
):
    if range not in _VALID_RANGES:
        raise HTTPException(400, f"Unknown range {range!r}; expected one of {_VALID_RANGES}")
    metrics = _build_series(company.company_id, range)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["metric", "period_start", "value"])
    for m in metrics:
        for p in m["points"]:
            writer.writerow([m["metric"], p["period_start"], p["value"]])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="metrics_{range}.csv"'},
    )
