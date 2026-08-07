"""Customer-key LLM usage + estimated cost — behind Settings → Admin → Usage.

Reports ONLY calls billed to the company's own API key, whichever provider it
runs on (see `_CUSTOMER_KEY_MODE`). A company with no key configured runs on
Sprntly's platform key and correctly sees nothing here — it owes nothing.

Provider-aware since a workspace can run on Anthropic or OpenAI: `by_provider`
splits spend between them, which matters most for a workspace that has switched
mid-period and needs to reconcile against two separate invoices.

Grain is the COMPANY (tenant), not a `workspaces` row: `require_company` supplies
the id and every row is keyed on it, so all workspaces under one company share a
single usage view. (The UI calls the company "Workspace" in Settings — that is
the product's user-facing wording, not this module's grain.)

  GET /v1/admin/usage/summary?days=30&tz=UTC   → totals, daily series, breakdowns
  GET /v1/admin/usage/export.csv?days=30&tz=UTC → the same rollup as a CSV

Both are gated on require_company + owner/admin, matching the Claude-key page:
spend is commercially sensitive, so it stays with the people who administer the
company.

One `llm_usage_summary` RPC backs both endpoints. Postgres does the GROUP BY and
returns pre-aggregated (day, feature, operation, model, key_mode) rows; this
module only re-slices that small result into the views the dashboard needs, so
adding a breakdown never costs another query.

On "estimated": the provider APIs return token COUNTS, never dollars. Every
`est_cost_usd` here is tokens x `app.llm_telemetry.MODEL_PRICING`, so it tracks
the published rate card rather than an invoice. The response says so explicitly
(`cost_basis`) so the UI can label it and nobody mistakes it for billing.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.auth import CompanyContext, require_company
from app.db.llm_usage import fetch_usage_summary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/admin/usage", tags=["admin"])

_MAX_DAYS = 365

# This surface reports ONLY what the customer's own provider key paid for.
#
# Calls made on Sprntly's platform key are spend WE absorb — a workspace that
# has not configured a key owes nothing, so surfacing that spend here would
# inflate a figure the reader reasonably takes as their bill.
#
# `unknown` is excluded for the same reason and is not a judgement call: it is
# the value carried by rows written before key attribution existed (the
# backfilled history), and by definition we cannot claim those were the
# customer's. Excluding beats guessing.
#
# The rollup itself stays key-mode-agnostic so platform spend remains queryable
# for internal/staff analysis — the filter belongs to this customer-facing view,
# not to the data.
_CUSTOMER_KEY_MODE = "customer"

# Numeric columns that roll up by simple addition. Kept in one place so a new
# metric is summed everywhere (totals + every breakdown) by adding one entry.
_SUM_FIELDS = (
    "calls",
    "failed_calls",
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "est_cost_usd",
)


def _require_admin(company: CompanyContext) -> None:
    if company.role not in ("owner", "admin"):
        raise HTTPException(403, "Usage is restricted to owners and admins")


def _empty() -> dict[str, Any]:
    return {f: 0 for f in _SUM_FIELDS}


def _accumulate(bucket: dict[str, Any], row: dict[str, Any]) -> None:
    for f in _SUM_FIELDS:
        bucket[f] = bucket.get(f, 0) + (row.get(f) or 0)


def _group_by(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """Roll rows up by one dimension, largest estimated spend first."""
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        k = row.get(key) or "unknown"
        bucket = out.setdefault(k, {key: k, **_empty()})
        _accumulate(bucket, row)
    return sorted(out.values(), key=lambda b: b["est_cost_usd"], reverse=True)


def _daily_series(
    rows: list[dict[str, Any]], start: datetime, days: int
) -> list[dict[str, Any]]:
    """One entry per calendar day in the range, including days with no usage.

    Gap-filling happens here rather than in the chart: a line/area chart that
    silently skips empty days compresses the x-axis and misrepresents a quiet
    week as continuous activity.
    """
    by_day: dict[str, dict[str, Any]] = {}
    for row in rows:
        day = str(row.get("day") or "")[:10]
        if not day:
            continue
        bucket = by_day.setdefault(day, {"day": day, **_empty()})
        _accumulate(bucket, row)

    series: list[dict[str, Any]] = []
    for offset in range(days):
        day = (start + timedelta(days=offset)).date().isoformat()
        series.append(by_day.get(day) or {"day": day, **_empty()})
    return series


def _load(
    company: CompanyContext, days: int, tz: str
) -> tuple[list[dict[str, Any]], datetime, datetime]:
    now = datetime.now(timezone.utc)
    # End on tomorrow's boundary so today's partial day is included; the RPC
    # range is half-open [start, end).
    end = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)
    rows = fetch_usage_summary(
        company_id=company.company_id,
        start=start.isoformat(),
        end=end.isoformat(),
        tz=tz,
    )
    # The single point where platform/unknown spend is dropped. Done here rather
    # than in each view so no breakdown can ever forget it and leak spend the
    # customer did not pay for.
    rows = [r for r in rows if r.get("key_mode") == _CUSTOMER_KEY_MODE]
    return rows, start, end


@router.get("/summary")
def usage_summary(
    days: int = Query(30, ge=1, le=_MAX_DAYS),
    tz: str = Query("UTC", max_length=64),
    company: CompanyContext = Depends(require_company),
) -> dict[str, Any]:
    """Totals, a gap-filled daily series, and per-feature/model/provider splits.

    Every breakdown ships in this one payload — the client switches views
    without refetching, so a new dimension costs nothing at read time.
    """
    _require_admin(company)
    rows, start, end = _load(company, days, tz)

    totals = _empty()
    for row in rows:
        _accumulate(totals, row)

    return {
        "range": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "days": days,
            "tz": tz,
        },
        # Machine-readable provenance of the money column, so the UI never
        # presents this as a billed figure.
        "cost_basis": "estimated_from_tokens",
        # States what is counted, so a client can never mistake this for the
        # company's total LLM usage.
        "scope": "customer_key",
        "totals": totals,
        "daily": _daily_series(rows, start, days),
        "by_feature": _group_by(rows, "feature"),
        "by_model": _group_by(rows, "model"),
        # Unconditional since 20260807120100 added `provider` to the rollup.
        # It used to be guarded on the column being present, which — because
        # the RPC never selected it — made this a permanently empty list.
        "by_provider": _group_by(rows, "provider"),
        "by_operation": _group_by(rows, "operation"),
    }


@router.get("/export.csv")
def usage_export_csv(
    days: int = Query(30, ge=1, le=_MAX_DAYS),
    tz: str = Query("UTC", max_length=64),
    company: CompanyContext = Depends(require_company),
) -> StreamingResponse:
    """The rollup as a CSV — one row per (day, feature, operation, model, key)."""
    _require_admin(company)
    rows, _start, _end = _load(company, days, tz)

    columns = [
        "day",
        "feature",
        "operation",
        "provider",
        "model",
        "key_mode",
        *_SUM_FIELDS,
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({c: row.get(c, "") for c in columns})
    buf.seek(0)

    stamp = datetime.now(timezone.utc).date().isoformat()
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="sprntly-usage-{stamp}-{days}d.csv"'
            )
        },
    )
