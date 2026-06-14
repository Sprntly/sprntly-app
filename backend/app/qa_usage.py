"""Per-enterprise Q&A usage/cost surfacing.

Every gateway call already writes cost_usd + token counts into
`agent_decision_log.factors` (§4d / sprntly-ai-infra §8). This reads the QA-
agent rows back and aggregates them so the product can show a tenant their
ask spend. Aggregation is a pure function over rows (unit-tested); the fetch is
best-effort and returns zeros if the table/client is unavailable.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# Agents that make up the Q&A path (router + skill/direct answer + verify + the
# legacy ask compose call).
QA_AGENTS = ("qa", "qa-router", "qa-verify", "ask")


def _zero() -> dict:
    return {"calls": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0}


def aggregate_usage(rows: Iterable[dict]) -> dict:
    """Sum calls / cost / tokens across decision-log rows, with a per-agent
    breakdown. `factors` holds cost_usd/input_tokens/output_tokens for llm_call
    rows; missing fields count as zero."""
    total = _zero()
    by_agent: dict[str, dict] = {}
    for row in rows:
        agent = row.get("agent") or "unknown"
        f = row.get("factors") or {}
        bucket = by_agent.setdefault(agent, _zero())
        for acc in (total, bucket):
            acc["calls"] += 1
            acc["cost_usd"] += float(f.get("cost_usd") or 0.0)
            acc["input_tokens"] += int(f.get("input_tokens") or 0)
            acc["output_tokens"] += int(f.get("output_tokens") or 0)
    total["cost_usd"] = round(total["cost_usd"], 6)
    for b in by_agent.values():
        b["cost_usd"] = round(b["cost_usd"], 6)
    total["by_agent"] = by_agent
    return total


def fetch_qa_usage(enterprise_id: str, *, limit: int = 1000) -> dict:
    """Read recent QA decision-log rows for a tenant and aggregate. Best-effort:
    any read failure (no client, missing table) returns the zero shape."""
    try:
        from app.db.client import require_client

        c = require_client()
        resp = (
            c.table("agent_decision_log")
            .select("agent,factors")
            .eq("enterprise_id", enterprise_id)
            .in_("agent", list(QA_AGENTS))
            .order("timestamp", desc=True)
            .limit(limit)
            .execute()
        )
        return aggregate_usage(resp.data or [])
    except Exception:  # noqa: BLE001 — usage read must never break the UI
        logger.exception("qa usage fetch failed for enterprise=%s", enterprise_id)
        agg = _zero()
        agg["by_agent"] = {}
        return agg
