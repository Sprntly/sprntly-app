"""Fire-and-forget LLM-context extraction jobs for onboarding.

The "Import your context" step parses the uploaded Markdown twice. The
deterministic heading walk runs inline and returns with the POST — it is
instant and exact, but only understands files our own prompt produced. The LLM
pass reads context documents of ANY shape, costs a real round-trip, and so runs
here as a background job the client polls.

Status walks generating → ready (or error). `result` holds the same
{fields, unmapped, format_version, note} dict the POST returns, so the frontend
consumes one contract from both endpoints. Mirrors `website_analysis_jobs`.
"""
import json

from app.db.client import require_client, retry_on_disconnect


@retry_on_disconnect
def start_context_job(company_id: str) -> int:
    """Persist a `generating` extraction job row and return its id. The POST
    returns this id immediately; the background worker fills `result` and flips
    the status to `ready` (or `error`)."""
    c = require_client()
    resp = c.table("llm_context_jobs").insert({
        "company_id": company_id,
        "status": "generating",
    }).execute()
    return resp.data[0]["id"]


def complete_context_job(job_id: int, result: dict) -> None:
    """Store the extraction result and mark the job `ready`."""
    c = require_client()
    c.table("llm_context_jobs").update({
        "result": result or {},
        "status": "ready",
        "error": None,
        "updated_at": _now(),
    }).eq("id", job_id).execute()


def fail_context_job(job_id: int, error: str) -> None:
    """Mark the job `error` (best-effort — the worker never crashes on this)."""
    c = require_client()
    c.table("llm_context_jobs").update({
        "status": "error",
        "error": (error or "")[:500],
        "updated_at": _now(),
    }).eq("id", job_id).execute()


@retry_on_disconnect
def get_context_job(job_id: int) -> dict | None:
    """Fetch an extraction job row by id, or None. `result` is decoded to a dict
    (jsonb in prod / JSON-string in the SQLite test fake)."""
    c = require_client()
    resp = (
        c.table("llm_context_jobs")
        .select("*")
        .eq("id", job_id)
        .limit(1)
        .execute()
    )
    if not resp.data:
        return None
    row = resp.data[0]
    raw = row.get("result")
    if isinstance(raw, str):
        try:
            row["result"] = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            row["result"] = None
    return row


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
