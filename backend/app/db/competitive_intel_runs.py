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


# A run is claimed at START and completed at the end, so a row can exist for a
# run that is still going (or one that died mid-flight). `metadata.status`
# carries that, rather than a column, because the table's migration is already
# on main and schema changes are gated — see `claim_competitive_intel_run`.
STATUS_RUNNING = "running"
STATUS_COMPLETE = "complete"
# How many rows back to look for the newest COMPLETED run. A company would need
# this many consecutive abandoned runs to hide a good one, which is already a
# louder problem than a stale diff.
_LATEST_SCAN_DEPTH = 5


def _is_complete(row: dict) -> bool:
    """A row is usable as a prior run unless it is explicitly still running.
    Rows written before this status existed carry no key and count as complete."""
    meta = row.get("metadata")
    status = (meta or {}).get("status") if isinstance(meta, dict) else None
    return status != STATUS_RUNNING


@retry_on_disconnect
def latest_competitive_intel_run(company_id: str) -> dict | None:
    """The most recent COMPLETED run for a company (state + records + metadata +
    the set it covered), or None. Both the Scan/Review mode decision and the
    skill's query mode read this.

    In-flight rows are skipped: a claimed run has no state and no records yet,
    so treating one as the prior run would diff against nothing and silently
    turn a Scan into a comparison with an empty picture."""
    c = require_client()
    resp = (
        c.table("competitive_intel_runs")
        .select(
            "id, mode, question, window_label, competitor_set, records, "
            "state, metadata, created_at"
        )
        .eq("company_id", company_id)
        .order("id", desc=True)
        .limit(_LATEST_SCAN_DEPTH)
        .execute()
    )
    for row in resp.data or []:
        if _is_complete(row):
            return row
    return None


@retry_on_disconnect
def claim_competitive_intel_run(
    company_id: str, *, question: str, mode: str, competitor_set: list[str],
) -> int | None:
    """Write the run row at START and return its id.

    The pipeline used to persist only on success, after a sweep that can run
    twenty minutes. Every staging attempt that timed out therefore left ZERO
    trace: no row, no records, no way to tell a run had happened at all — while
    the money had already been spent. Claiming up front means an abandoned run
    is visible and attributable, and the completion path becomes an UPDATE.

    Status lives in `metadata.status` rather than a column: the table's
    migration is already merged, and adding a column is an Apurva-gated change
    that would block this fix. `_is_complete` treats a missing key as complete,
    so rows written before this existed keep working.
    """
    c = require_client()
    resp = c.table("competitive_intel_runs").insert({
        "company_id": company_id,
        "question": question,
        "mode": mode if mode in ("scan", "review") else "review",
        "competitor_set": competitor_set or [],
        "metadata": {"status": STATUS_RUNNING},
    }).execute()
    return resp.data[0]["id"] if resp.data else None


@retry_on_disconnect
def complete_competitive_intel_run(
    run_id: int,
    *,
    mode: str,
    window_label: str,
    competitor_set: list[str],
    records: list[dict],
    state: dict,
    metadata: dict,
    html: str,
) -> None:
    """Fill in a claimed run and mark it complete."""
    c = require_client()
    c.table("competitive_intel_runs").update({
        "mode": mode if mode in ("scan", "review") else "review",
        "window_label": window_label or "",
        "competitor_set": competitor_set or [],
        "records": records or [],
        "state": state or {},
        "metadata": {**(metadata or {}), "status": STATUS_COMPLETE},
        "html": html or "",
    }).eq("id", run_id).execute()


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
