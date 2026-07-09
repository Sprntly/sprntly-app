"""Pilot-1 structured analyses — weekly aggregates + anomaly Findings.

The math is pure code (no LLM): given RawRecords pulled TRANSIENTLY from the
connected providers (same token plumbing as routes/ingest.py), we compute a
small set of weekly aggregates per provider, upsert them as tiny rolling
aggregates (`metric_points`, one number per metric per week per source), then
detect anomalies against each metric's own trailing history. Anomalies become
Findings: kg_signal rows (source_type=agent_inferred, kind=metric_anomaly) +
one decision-log row summarizing the run.

Data-minimization: provider rows are consumed here and discarded. Nothing but
the distilled aggregates and Findings is persisted.

Aggregate definitions (kept deliberately simple + documented):
  hubspot   open_deal_value_usd  — sum of `amount_usd` over deals NOT in a
                                    won/lost/closed stage (open pipeline value).
            deals_open_count     — count of those open deals.
            deals_lost_count     — count of deals whose stage looks lost.
  clickup   tasks_open           — tasks whose status is not a closed/done state.
            tasks_closed_7d      — tasks closed within the trailing 7 days of
                                    the week (status closed-ish + updated in week).
            bugs_open            — open tasks tagged/typed as a bug
                                    (properties.tags or status/title heuristic).
  fireflies meetings_7d          — meetings in the week.

Each record is bucketed into the ISO-week (Monday) of its timestamp; a metric's
weekly value is the aggregate over that week's records. Status/stage heuristics
are intentionally lexical (substring on lowercased text) — pilot-scale, and
documented so they can be tightened per-enterprise later.
"""
from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

from app.db.metric_points import list_metric_points, upsert_metric_point
from app.graph.config_layers import config_get
from app.graph.decision_log import log_agent_decision
from app.graph.facade import GraphFacade
from app.graph.types import Signal
from app.kg_ingest.types import RawRecord

logger = logging.getLogger(__name__)

AGENT = "ds"

# Which signal source_type a provider's Findings carry — the anomaly Finding is
# always agent_inferred (it's the agent's inference, not raw provider evidence).
_FINDING_SOURCE_TYPE = "agent_inferred"
_FINDING_KIND = "metric_anomaly"

# Status/stage lexical heuristics (lowercased substring match). Documented above.
_CLOSED_STATUS_HINTS = ("closed", "done", "complete", "resolved", "shipped")
_LOST_STAGE_HINTS = ("lost", "closedlost", "closed_lost", "closed lost", "abandon")
_WON_STAGE_HINTS = ("won", "closedwon", "closed_won", "closed won")
_BUG_HINTS = ("bug", "defect", "incident")

# Metrics each provider contributes (used by the series endpoint to know the set
# even before any points exist, and to drive per-provider aggregation).
PROVIDER_METRICS: dict[str, tuple[str, ...]] = {
    "hubspot":   ("open_deal_value_usd", "deals_open_count", "deals_lost_count"),
    "clickup":   ("tasks_open", "tasks_closed_7d", "bugs_open"),
    # Jira issues are work items too — same metric names as ClickUp so both
    # trackers feed the identical DS series/views.
    "jira":      ("tasks_open", "tasks_closed_7d", "bugs_open"),
    "fireflies": ("meetings_7d",),
}


# ─────────────────────────── time bucketing ───────────────────────────

def _parse_ts(raw: Optional[str]) -> Optional[datetime]:
    """Parse a provider timestamp. Accepts ISO-8601 or epoch millis/seconds
    (ClickUp/Fireflies hand back epoch-ms strings). None on anything unusable."""
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s.isdigit():
        v = int(s)
        if v > 10_000_000_000:  # epoch milliseconds
            v //= 1000
        try:
            return datetime.fromtimestamp(v, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _week_start(dt: datetime) -> date:
    """ISO week anchor — the Monday (UTC) of the record's week."""
    d = dt.astimezone(timezone.utc).date()
    return d - timedelta(days=d.weekday())


def _is_closed_status(status: Optional[str]) -> bool:
    s = (status or "").lower()
    return any(h in s for h in _CLOSED_STATUS_HINTS)


def _looks_like_bug(rec: RawRecord) -> bool:
    tags = [str(t).lower() for t in (rec.properties.get("tags") or [])]
    if any(any(h in t for h in _BUG_HINTS) for t in tags):
        return True
    text = f"{rec.title} {rec.properties.get('list') or ''}".lower()
    return any(h in text for h in _BUG_HINTS)


# ─────────────────────────── per-provider aggregation ───────────────────────────

def _aggregate_hubspot(records: Iterable[RawRecord]) -> dict[date, dict[str, float]]:
    weeks: dict[date, dict[str, float]] = defaultdict(
        lambda: {"open_deal_value_usd": 0.0, "deals_open_count": 0.0, "deals_lost_count": 0.0})
    for rec in records:
        dt = _parse_ts(rec.timestamp)
        if dt is None:
            continue
        wk = _week_start(dt)
        stage = (rec.properties.get("stage") or "").lower()
        is_lost = any(h in stage for h in _LOST_STAGE_HINTS)
        is_won = any(h in stage for h in _WON_STAGE_HINTS)
        is_closed = is_lost or is_won or "closed" in stage
        if is_lost:
            weeks[wk]["deals_lost_count"] += 1
        if not is_closed:
            weeks[wk]["deals_open_count"] += 1
            try:
                amount = float(rec.properties.get("amount_usd") or 0)
            except (TypeError, ValueError):
                amount = 0.0
            weeks[wk]["open_deal_value_usd"] += amount
    return weeks


def _aggregate_clickup(records: Iterable[RawRecord]) -> dict[date, dict[str, float]]:
    weeks: dict[date, dict[str, float]] = defaultdict(
        lambda: {"tasks_open": 0.0, "tasks_closed_7d": 0.0, "bugs_open": 0.0})
    for rec in records:
        dt = _parse_ts(rec.timestamp)
        if dt is None:
            continue
        wk = _week_start(dt)
        closed = _is_closed_status(rec.properties.get("status"))
        if closed:
            # The record's activity (status flip) landed in this week — count it
            # as a closure attributable to the trailing 7 days of that week.
            weeks[wk]["tasks_closed_7d"] += 1
        else:
            weeks[wk]["tasks_open"] += 1
            if _looks_like_bug(rec):
                weeks[wk]["bugs_open"] += 1
    return weeks


def _looks_like_bug_jira(rec: RawRecord) -> bool:
    """Jira carries a native issue type (Bug/Story/Task/Epic), so prefer that;
    fall back to labels + title heuristics (labels is Jira's field, not tags)."""
    if any(h in str(rec.properties.get("type") or "").lower() for h in _BUG_HINTS):
        return True
    labels = [str(t).lower() for t in (rec.properties.get("labels") or [])]
    if any(any(h in lbl for h in _BUG_HINTS) for lbl in labels):
        return True
    return any(h in (rec.title or "").lower() for h in _BUG_HINTS)


def _aggregate_jira(records: Iterable[RawRecord]) -> dict[date, dict[str, float]]:
    weeks: dict[date, dict[str, float]] = defaultdict(
        lambda: {"tasks_open": 0.0, "tasks_closed_7d": 0.0, "bugs_open": 0.0})
    for rec in records:
        dt = _parse_ts(rec.timestamp)
        if dt is None:
            continue
        wk = _week_start(dt)
        if _is_closed_status(rec.properties.get("status")):
            weeks[wk]["tasks_closed_7d"] += 1
        else:
            weeks[wk]["tasks_open"] += 1
            if _looks_like_bug_jira(rec):
                weeks[wk]["bugs_open"] += 1
    return weeks


def _aggregate_fireflies(records: Iterable[RawRecord]) -> dict[date, dict[str, float]]:
    weeks: dict[date, dict[str, float]] = defaultdict(lambda: {"meetings_7d": 0.0})
    for rec in records:
        dt = _parse_ts(rec.timestamp)
        if dt is None:
            continue
        weeks[_week_start(dt)]["meetings_7d"] += 1
    return weeks


_AGGREGATORS = {
    "hubspot":   _aggregate_hubspot,
    "clickup":   _aggregate_clickup,
    "jira":      _aggregate_jira,
    "fireflies": _aggregate_fireflies,
}


def compute_weekly_aggregates(
    provider: str, records: Iterable[RawRecord]
) -> dict[date, dict[str, float]]:
    """Pure function: RawRecords → {week_start: {metric: value}} for a provider."""
    if provider not in _AGGREGATORS:
        raise ValueError(f"No DS aggregator for provider {provider!r}")
    return _AGGREGATORS[provider](records)


# ─────────────────────────── anomaly detection ───────────────────────────

def detect_anomaly(
    points: list[tuple[str, float]],
    *,
    min_points: int,
    z_threshold: float,
    pct_threshold: float,
) -> Optional[dict]:
    """Given a metric's full weekly series (sorted ascending by period), judge
    the LATEST point against its trailing history. Returns a Finding dict
    {metric-agnostic: z, pct_change, period, value} or None.

    z      = (latest - trailing_mean) / trailing_std (0 std ⇒ z=0)
    pct    = (latest - trailing_mean) / |trailing_mean| (0 mean ⇒ pct=0)
    Anomaly iff |z| ≥ z_threshold OR |pct| ≥ pct_threshold, and we have enough
    points (latest + ≥ (min_points-1) trailing)."""
    if len(points) < min_points:
        return None
    period, latest = points[-1]
    trailing = [v for _, v in points[:-1]]
    mean = statistics.fmean(trailing)
    std = statistics.pstdev(trailing) if len(trailing) > 1 else 0.0
    z = (latest - mean) / std if std > 0 else 0.0
    pct = (latest - mean) / abs(mean) if mean != 0 else 0.0
    if abs(z) >= z_threshold or abs(pct) >= pct_threshold:
        return {
            "period": period,
            "value": latest,
            "z": round(z, 4),
            "pct_change": round(pct, 4),
            "trailing_mean": round(mean, 4),
        }
    return None


# ─────────────────────────── orchestration ───────────────────────────

def analyze_provider(
    facade: GraphFacade,
    enterprise_id: str,
    provider: str,
    *,
    records: list[RawRecord],
) -> dict:
    """Aggregate one provider's transient RawRecords → upsert metric_points →
    detect anomalies vs each metric's trailing history → write Findings.

    Returns counts: {provider, weeks, points_written, findings, anomalies:[...]}.
    Does NOT decision-log on its own — the caller (run_analyses) logs the whole
    run once so the reasoning summarizes across providers."""
    weekly = compute_weekly_aggregates(provider, records)

    # Upsert every (week, metric) — idempotent on the unique key.
    points_written = 0
    metrics_touched: set[str] = set()
    for wk in sorted(weekly):
        for metric, value in weekly[wk].items():
            upsert_metric_point(enterprise_id, metric, wk, value, provider)
            points_written += 1
            metrics_touched.add(metric)

    # Thresholds from config (platform defaults, enterprise-overridable).
    min_points = int(config_get("ds.anomaly.min_points", enterprise_id, default=4))
    z_threshold = float(config_get("ds.anomaly.z_threshold", enterprise_id, default=2.0))
    pct_threshold = float(config_get("ds.anomaly.pct_threshold", enterprise_id, default=0.3))

    anomalies: list[dict] = []
    for metric in sorted(metrics_touched):
        # Read the metric's FULL history for this source (newly-upserted weeks
        # included) so detection sees the trailing context, not just this batch.
        rows = list_metric_points(enterprise_id, metric=metric)
        series = [
            (str(r["period_start"]), float(r["value"]))
            for r in rows if r["source"] == provider
        ]
        finding = detect_anomaly(
            series, min_points=min_points,
            z_threshold=z_threshold, pct_threshold=pct_threshold)
        if not finding:
            continue
        anomaly = {"metric": metric, "source": provider, **finding}
        anomalies.append(anomaly)
        _write_finding(facade, enterprise_id, anomaly)

    return {
        "provider": provider,
        "weeks": len(weekly),
        "points_written": points_written,
        "findings": len(anomalies),
        "anomalies": anomalies,
    }


def _write_finding(facade: GraphFacade, enterprise_id: str, anomaly: dict) -> str:
    """Persist one anomaly as a kg_signal Finding. Returns the signal id."""
    metric = anomaly["metric"]
    direction = "up" if anomaly["z"] >= 0 and anomaly["pct_change"] >= 0 else "down"
    content = (
        f"Metric anomaly: {metric} moved {direction} to {anomaly['value']:g} in week "
        f"{anomaly['period']} (z={anomaly['z']:g}, pct_change={anomaly['pct_change']:+.0%} "
        f"vs trailing mean {anomaly['trailing_mean']:g})."
    )
    signal = Signal(
        enterprise_id=enterprise_id,
        source_type=_FINDING_SOURCE_TYPE,
        kind=_FINDING_KIND,
        content=content,
        properties={
            "metric": metric,
            "z": anomaly["z"],
            "pct_change": anomaly["pct_change"],
            "period": anomaly["period"],
            "value": anomaly["value"],
            "trailing_mean": anomaly["trailing_mean"],
            "provider": anomaly["source"],
        },
        provenance={"agent": AGENT, "analysis": "pilot1_weekly_anomaly"},
    )
    facade.write_signal(enterprise_id, signal)
    return signal.id


def run_analyses(
    facade: GraphFacade,
    enterprise_id: str,
    *,
    records_by_provider: dict[str, list[RawRecord]],
) -> dict:
    """Run pilot-1 analyses across the supplied providers (error-isolated per
    provider), then decision-log the whole run once.

    `records_by_provider` is the TRANSIENT pull — already fetched by the caller
    (the route reuses the kg_ingest token/puller plumbing). Returns per-provider
    results + an aggregate summary."""
    per_provider: dict[str, dict] = {}
    errors: list[str] = []
    all_anomalies: list[dict] = []
    totals = {"points_written": 0, "findings": 0}

    for provider, records in records_by_provider.items():
        if provider not in _AGGREGATORS:
            errors.append(f"{provider}: no DS aggregator")
            continue
        try:
            res = analyze_provider(facade, enterprise_id, provider, records=records)
        except Exception as e:  # noqa: BLE001 — error-isolation per provider
            logger.exception("ds analysis failed for %s", provider)
            errors.append(f"{provider}: {e}")
            continue
        per_provider[provider] = res
        all_anomalies.extend(res["anomalies"])
        totals["points_written"] += res["points_written"]
        totals["findings"] += res["findings"]

    # One decision-log row for the run — reasoning summarizes the anomalies.
    reasoning = _summarize(all_anomalies, errors)
    log_agent_decision(
        enterprise_id=enterprise_id, agent=AGENT, decision_type="analyze",
        factors={
            "providers": sorted(per_provider.keys()),
            "points_written": totals["points_written"],
            "findings": totals["findings"],
            "errors": errors,
        },
        reasoning=reasoning,
        output={"anomalies": all_anomalies},
        prompt_version="ds-pilot1-v1",
    )

    return {
        "ok": not errors or bool(per_provider),
        "providers": per_provider,
        "points_written": totals["points_written"],
        "findings": totals["findings"],
        "anomalies": all_anomalies,
        "errors": errors,
    }


def _summarize(anomalies: list[dict], errors: list[str]) -> str:
    if not anomalies:
        base = "No metric anomalies detected this run."
    else:
        parts = [
            f"{a['metric']} ({a['source']}) {a['pct_change']:+.0%} z={a['z']:g} in {a['period']}"
            for a in anomalies
        ]
        base = f"Detected {len(anomalies)} metric anomal{'y' if len(anomalies) == 1 else 'ies'}: " \
               + "; ".join(parts) + "."
    if errors:
        base += f" {len(errors)} provider(s) errored: {'; '.join(errors)}."
    return base
