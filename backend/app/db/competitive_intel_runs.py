"""competitive_intel_runs — stored competitive-intelligence runs (state + records).

One row per completed run of the competitive-intelligence pipeline
(app/competitive_intel.py). The row carries three things the next interaction
needs:

  * `state` — the skill's `state/ci-state.json` (references/state-spec.md):
    competitors{} / our_state / decisions[], every field with observed_on +
    source + tier. Diffing this run's state against the prior row's IS what
    makes a Scan possible; `run_id` / `previous_run` in the skill's schema map
    to row ids.
  * `competitor_set` — so a materially changed set can force a Review.
  * `records` + `metadata` — what follow-up questions are answered from,
    without re-running the multi-minute web sweep.

Every call here is BEST-EFFORT at the call site: with the table absent (the
migration lands in its own Apurva-gated PR) `latest_competitive_intel_run`
raises and the pipeline simply runs a Review, and a failed save degrades
follow-ups only — it must never break the answer that already rendered.
"""
from __future__ import annotations

import logging

from app.db.client import require_client, retry_on_disconnect

logger = logging.getLogger(__name__)


@retry_on_disconnect
def latest_competitive_intel_run(company_id: str) -> dict | None:
    """The most recent run for a company (state + records + metadata + the set
    it covered), or None. Both the Scan/Review mode decision and the skill's
    query mode read this."""
    c = require_client()
    resp = (
        c.table("competitive_intel_runs")
        .select(
            "id, mode, question, window_label, competitor_set, records, "
            "state, metadata, created_at"
        )
        .eq("company_id", company_id)
        .order("id", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


@retry_on_disconnect
def save_competitive_intel_run(
    company_id: str,
    *,
    question: str,
    mode: str,
    window_label: str,
    competitor_set: list[str],
    records: list[dict],
    state: dict,
    metadata: dict,
    html: str,
) -> int | None:
    """Persist a completed run and return its id (None on failure — the chat
    answer already rendered; only the next run's diff and follow-up querying
    are degraded)."""
    c = require_client()
    resp = c.table("competitive_intel_runs").insert({
        "company_id": company_id,
        "question": question,
        "mode": mode if mode in ("scan", "review") else "review",
        "window_label": window_label or "",
        "competitor_set": competitor_set or [],
        "records": records or [],
        "state": state or {},
        "metadata": metadata or {},
        "html": html or "",
    }).execute()
    return resp.data[0]["id"] if resp.data else None
