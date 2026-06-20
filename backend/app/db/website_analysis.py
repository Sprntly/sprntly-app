"""Fire-and-forget website-analysis jobs for onboarding.

The onboarding "Gathering information about your business" interstitial used to
call `analyze_website(...)` inline inside the POST. A backgrounded / remounted
tab would orphan that request even though the analysis finishes cheaply
server-side. Mirroring `ask_jobs`, the POST now persists a `generating` row
here, kicks the same analysis in a background task, and returns a `job_id`; the
client polls GET /v1/onboarding/analyze-website/{job_id}.

Status walks generating → ready (or error). `result` holds the FULL analysis
dict `analyze_website` returns (ok / reason / industry / business_type /
business_context / suggested_metrics / ...), so the onboarding form consumes an
unchanged shape via setWebsiteAnalysis(result). Per-request, per-tenant.
"""
import json

from app.db.client import require_client, retry_on_disconnect


@retry_on_disconnect
def start_analysis_job(company_id: str, url: str) -> int:
    """Persist a `generating` website-analysis job row and return its id. The
    POST returns this id immediately; the background worker fills `result` and
    flips the status to `ready` (or `error`)."""
    c = require_client()
    resp = c.table("website_analysis_jobs").insert({
        "company_id": company_id,
        "url": url,
        "status": "generating",
    }).execute()
    return resp.data[0]["id"]


def complete_analysis_job(job_id: int, result: dict) -> None:
    """Store the full analysis dict and mark the job `ready`."""
    c = require_client()
    c.table("website_analysis_jobs").update({
        "result": result or {},
        "status": "ready",
        "error": None,
        "updated_at": _now(),
    }).eq("id", job_id).execute()


def fail_analysis_job(job_id: int, error: str) -> None:
    """Mark the job `error` (best-effort — the worker never crashes on this)."""
    c = require_client()
    c.table("website_analysis_jobs").update({
        "status": "error",
        "error": (error or "")[:500],
        "updated_at": _now(),
    }).eq("id", job_id).execute()


@retry_on_disconnect
def get_analysis_job(job_id: int) -> dict | None:
    """Fetch a website-analysis job row by id, or None. `result` is decoded to a
    dict (jsonb in prod / JSON-string in the SQLite test fake); None when the
    job hasn't finished (still generating) is normalized to None."""
    c = require_client()
    resp = (
        c.table("website_analysis_jobs")
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
