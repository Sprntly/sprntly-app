"""Ask logging + cached Ask responses + fire-and-forget Ask jobs.

ask_log:    append-only history of every /v1/ask call.
cached_asks: pre-computed answers keyed by (dataset, question), feeds
             the warmer + answers cache hits in O(1).
ask_jobs:   per-request, per-tenant status row for the blur-safe chat Ask
             flow — POST persists a `generating` row + kicks the answer in a
             background task; the client polls GET /v1/ask/{id}.
"""
import json
import logging

from postgrest.exceptions import APIError

from app.db.client import require_client, retry_on_disconnect

logger = logging.getLogger(__name__)

# The cache is keyed on the EXACT question text, which PostgREST sends as an
# `?question=eq.<value>` URL filter. The pre-warmed set is a handful of short
# starter prompts (see PREDEFINED_ASK_PROMPTS — the longest is ~130 chars), so a
# question longer than this ceiling can never be a cache hit. Looking it up
# anyway builds a URL that overflows PostgREST's request limit and 400s ("JSON
# could not be generated" / "Bad Request") — which is exactly what a chat ask
# carrying an inlined `[Attached files]` block (tens of KB) does. Skip the lookup
# for oversized questions so a multi-file ask is a clean cache miss, not a 500.
_MAX_CACHE_QUESTION_CHARS = 1000


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
    normalized = _normalize_q(question)
    # An oversized question (e.g. a chat ask with an inlined [Attached files]
    # block) can never match a pre-warmed prompt, and sending it as a URL filter
    # overflows PostgREST's request limit → a 400 that bubbles up as a 500 on the
    # whole ask. Treat it as an immediate cache miss.
    if len(normalized) > _MAX_CACHE_QUESTION_CHARS:
        return None
    c = require_client()
    try:
        resp = (
            c.table("cached_asks")
            .select("*")
            .eq("dataset", dataset)
            .eq("question", normalized)
            .in_("status", ["ready", "generating"])
            .order("id", desc=True)
            .limit(1)
            .execute()
        )
    except APIError:
        # Defence in depth: any malformed-query failure degrades to a cache miss
        # so the ask falls through to real generation instead of erroring out.
        logger.warning("cached_asks lookup failed; treating as miss", exc_info=True)
        return None
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
    """Store the citation-stripped answer payload and mark the job `ready`.

    Guarded on `status == 'generating'`: if the user stopped the ask
    (status → `cancelled`) while the answer was in its final, un-interruptible
    LLM call, the finished-but-unwanted answer must NOT overwrite the cancel and
    resurface. The conditional update no-ops in that race, so a cancelled job
    stays cancelled."""
    c = require_client()
    c.table("ask_jobs").update({
        "response": payload or {},
        "status": "ready",
        "error": None,
        "updated_at": _now(),
    }).eq("id", ask_id).eq("status", "generating").execute()


def fail_ask_job(ask_id: int, error: str) -> None:
    """Mark the job `error` (best-effort — the worker never crashes on this).

    Guarded on `status == 'generating'` for the same reason as
    complete_ask_job: a cancel that landed first must not be clobbered by a
    trailing failure from the (now-abandoned) worker."""
    c = require_client()
    c.table("ask_jobs").update({
        "status": "error",
        "error": (error or "")[:500],
        "updated_at": _now(),
    }).eq("id", ask_id).eq("status", "generating").execute()


def cancel_ask_job(ask_id: int) -> str | None:
    """Stop an in-flight Ask: flip `generating` → `cancelled`, then return the
    job's ACTUAL resulting status (or None if the row is gone).

    The update is conditional on `status == 'generating'` so it's a race-safe
    no-op when the worker already finished (the row is `ready`/`error`) — the
    subsequent read then reports that real terminal state, letting the caller be
    idempotent. Returns 'cancelled' when this call won the race."""
    c = require_client()
    c.table("ask_jobs").update({
        "status": "cancelled",
        "updated_at": _now(),
    }).eq("id", ask_id).eq("status", "generating").execute()
    row = get_ask_job(ask_id)
    return row.get("status") if row else None


def is_ask_cancelled(ask_id: int) -> bool:
    """True if the Ask job has been cancelled — the worker's cooperative
    cancellation checkpoint reads this between LLM steps to abort before the
    next (expensive) call. Any read error degrades to False so a transient DB
    blip never spuriously aborts a healthy answer."""
    try:
        row = get_ask_job(ask_id)
    except Exception:  # noqa: BLE001 — cancellation is best-effort; never abort on a read blip
        return False
    return bool(row) and row.get("status") == "cancelled"


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
