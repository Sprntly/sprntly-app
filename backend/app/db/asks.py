"""Ask logging + cached Ask responses + fire-and-forget Ask jobs.

ask_log:    append-only history of every /v1/ask call.
cached_asks: pre-computed answers keyed by (dataset, question), feeds
             the warmer + answers cache hits in O(1).
ask_jobs:   per-request, per-tenant status row for the blur-safe chat Ask
             flow — POST persists a `generating` row + kicks the answer in a
             background task; the client polls GET /v1/ask/{id}.
"""
import json

from app.db.client import require_client, retry_on_disconnect


# ─────────────────────── ask_log (append-only) ───────────────────────


def log_ask(question: str, answer: str, citations: list) -> None:
    c = require_client()
    c.table("ask_log").insert({
        "question": question,
        "answer": answer,
        "citations": citations,
    }).execute()


# ─────────────────────── cached_asks ───────────────────────


def _normalize_q(q: str) -> str:
    """Normalize a question for cache keying: strip + collapse whitespace.

    Exact-text match keyed on this normalized form. The predefined
    prompts list is constant, so this hits cleanly without any fuzzy
    matching.
    """
    return " ".join((q or "").strip().split())


def start_cached_ask(
    dataset: str, question: str, cache_version: int | None = None
) -> int:
    c = require_client()
    resp = c.table("cached_asks").insert({
        "dataset": dataset,
        "question": _normalize_q(question),
        "response": {},
        "status": "generating",
        "cache_version": cache_version,
    }).execute()
    return resp.data[0]["id"]


def complete_cached_ask(cache_id: int, response_json: str) -> None:
    """response_json is a JSON-string from the caller (legacy contract).
    We decode and store as jsonb in Supabase.
    """
    try:
        decoded = json.loads(response_json) if response_json else {}
    except (TypeError, ValueError):
        decoded = {}
    c = require_client()
    c.table("cached_asks").update({
        "response": decoded,
        "status": "ready",
        "error": None,
    }).eq("id", cache_id).execute()


def fail_cached_ask(cache_id: int, error: str) -> None:
    c = require_client()
    c.table("cached_asks").update({
        "status": "failed",
        "error": (error or "")[:500],
    }).eq("id", cache_id).execute()


def find_cached_ask(dataset: str, question: str) -> dict | None:
    """Most recent ready/generating cached Ask for a question.

    Returns the SQLite-shaped dict — `response_json` (string), not
    `response` (jsonb) — so callers don't change.
    """
    c = require_client()
    resp = (
        c.table("cached_asks")
        .select("*")
        .eq("dataset", dataset)
        .eq("question", _normalize_q(question))
        .in_("status", ["ready", "generating"])
        .order("id", desc=True)
        .limit(1)
        .execute()
    )
    if not resp.data:
        return None
    row = resp.data[0]
    # Translate jsonb back to JSON string for back-compat.
    row["response_json"] = json.dumps(row.get("response") or {})
    return row


def invalidate_stale_cached_asks(current_version: int) -> int:
    c = require_client()
    rows = (
        c.table("cached_asks")
        .select("id, cache_version")
        .in_("status", ["ready", "generating"])
        .execute()
        .data
    )
    stale_ids = [
        r["id"] for r in rows
        if r.get("cache_version") is None or r["cache_version"] != current_version
    ]
    if stale_ids:
        c.table("cached_asks").update({"status": "invalidated"}).in_("id", stale_ids).execute()
    return len(stale_ids)


def invalidate_orphan_generating_cached_asks() -> int:
    c = require_client()
    rows = c.table("cached_asks").select("id").eq("status", "generating").execute().data
    ids = [r["id"] for r in rows]
    if ids:
        c.table("cached_asks").update({"status": "invalidated"}).in_("id", ids).execute()
    return len(ids)


# ─────────────────────── ask_jobs (fire-and-forget) ───────────────────────


@retry_on_disconnect
def start_ask_job(
    company_id: str,
    dataset: str,
    question: str,
    conversation_id: int | None = None,
    pinned_skill: str | None = None,
    prd_id: int | None = None,
) -> int:
    """Persist a `generating` Ask job row and return its id. The POST returns
    this id immediately; the background worker fills `response` and flips the
    status to `ready` (or `error`)."""
    c = require_client()
    resp = c.table("ask_jobs").insert({
        "company_id": company_id,
        "dataset": dataset,
        "question": question,
        "conversation_id": conversation_id,
        "pinned_skill": pinned_skill,
        "prd_id": prd_id,
        "status": "generating",
        "response": {},
    }).execute()
    return resp.data[0]["id"]


def complete_ask_job(ask_id: int, payload: dict) -> None:
    """Store the citation-stripped answer payload and mark the job `ready`."""
    c = require_client()
    c.table("ask_jobs").update({
        "response": payload or {},
        "status": "ready",
        "error": None,
        "updated_at": _now(),
    }).eq("id", ask_id).execute()


def fail_ask_job(ask_id: int, error: str) -> None:
    """Mark the job `error` (best-effort — the worker never crashes on this)."""
    c = require_client()
    c.table("ask_jobs").update({
        "status": "error",
        "error": (error or "")[:500],
        "updated_at": _now(),
    }).eq("id", ask_id).execute()


@retry_on_disconnect
def get_ask_job(ask_id: int) -> dict | None:
    """Fetch an Ask job row by id, or None. `response` is decoded to a dict
    (jsonb in prod / JSON-string in the SQLite test fake)."""
    c = require_client()
    resp = c.table("ask_jobs").select("*").eq("id", ask_id).limit(1).execute()
    if not resp.data:
        return None
    row = resp.data[0]
    raw = row.get("response")
    if isinstance(raw, str):
        try:
            row["response"] = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            row["response"] = {}
    elif raw is None:
        row["response"] = {}
    return row


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
