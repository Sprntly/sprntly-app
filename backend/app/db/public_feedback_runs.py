"""public_feedback_runs — captured record sets + rendered public-feedback reports.

One row per completed run of the public-feedback pipeline (app/public_feedback.py).
The row is what the skill's query mode answers follow-ups from, so a failed save
degrades those follow-ups but must never break the chat answer — callers treat
`save_public_feedback_run` as best-effort.
"""
from __future__ import annotations

import logging

from app.db.client import require_client, retry_on_disconnect

logger = logging.getLogger(__name__)


@retry_on_disconnect
def get_public_feedback_run(company_id: str, run_id: int) -> dict | None:
    """One run WITH its stored html, company-scoped (None when the id doesn't
    exist or belongs to another tenant — the route 404s either way, so
    existence is never disclosed cross-tenant)."""
    c = require_client()
    resp = (
        c.table("public_feedback_runs")
        .select("id, question, window_label, html, created_at")
        .eq("company_id", company_id)
        .eq("id", run_id)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


@retry_on_disconnect
def latest_public_feedback_run(company_id: str) -> dict | None:
    """The most recent run for a company (records + metadata + created_at), or
    None. What the skill's query mode answers follow-up questions from."""
    c = require_client()
    resp = (
        c.table("public_feedback_runs")
        .select("id, question, window_label, records, metadata, created_at")
        .eq("company_id", company_id)
        .order("id", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


@retry_on_disconnect
def save_public_feedback_run(
    company_id: str,
    *,
    question: str,
    window_label: str,
    records: list[dict],
    metadata: dict,
    html: str,
) -> int | None:
    """Persist a completed run and return its id (None on failure — the chat
    answer already rendered; only follow-up querying is degraded)."""
    c = require_client()
    resp = c.table("public_feedback_runs").insert({
        "company_id": company_id,
        "question": question,
        "window_label": window_label or "",
        "records": records or [],
        "metadata": metadata or {},
        "html": html or "",
    }).execute()
    return resp.data[0]["id"] if resp.data else None
