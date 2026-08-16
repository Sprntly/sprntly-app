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


ORPHAN_ASK_JOB_ERROR = (
    "Generation was interrupted by a server restart. Please ask again."
)

# How long an `ask_jobs` row may sit in `generating` WITHOUT A HEARTBEAT before
# we treat it as abandoned. See fail_orphan_generating_ask_jobs for why this
# must not be tightened to "anything generating at startup".
#
# The age is measured against `updated_at`, and a live worker now bumps that
# every ORPHAN_ASK_JOB_HEARTBEAT_SECONDS (ask_job_runner). Before the heartbeat
# existed this window was a hard ceiling on answer DURATION, not on abandonment:
# a report path that legitimately runs longer than 15 minutes had its row failed
# out from under a worker that was still going, and because complete_ask_job is
# guarded on `status == 'generating'`, the answer it eventually produced was
# then silently discarded. Observed on staging with the competitive-intelligence
# review (~20 min), where it cost a full run every time.
ORPHAN_ASK_JOB_AFTER_MINUTES = 15
# Comfortably inside the window above, so a couple of missed beats (a slow DB, a
# blocked thread) still don't trip the sweep.
ORPHAN_ASK_JOB_HEARTBEAT_SECONDS = 60


def fail_orphan_generating_ask_jobs(
    older_than_minutes: int = ORPHAN_ASK_JOB_AFTER_MINUTES,
) -> int:
    """Fail `ask_jobs` rows abandoned in `generating` by a dead worker.

    When the process dies mid-answer the owning worker goes with it, so nothing
    will ever move the row to a terminal state. The Ask UI polls
    `GET /v1/ask/{id}` until the status leaves `generating`, so an interrupted
    job spins forever and reads to the user as "failed to generate answer" with
    no error to explain it. Observed on staging when a deploy restart landed 34s
    into job 189. `cached_asks` already had this treatment
    (invalidate_orphan_generating_cached_asks); `ask_jobs` did not.

    IMPORTANT — why the age cutoff rather than "fail everything generating":
    staging and prod share one Supabase project, so both environments' rows live
    in this table. A blanket sweep at staging startup would kill answers being
    generated right then by the prod process (and vice versa). Age is the only
    signal here that separates "owner is dead" from "owner is another live
    process", since rows carry no owner/heartbeat column."""
    from datetime import datetime, timedelta, timezone

    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=older_than_minutes)
    ).isoformat()
    c = require_client()
    rows = (
        c.table("ask_jobs")
        .select("id")
        .eq("status", "generating")
        .lt("updated_at", cutoff)
        .execute()
        .data
    )
    ids = [r["id"] for r in rows]
    if ids:
        c.table("ask_jobs").update({
            "status": "error",
            "error": ORPHAN_ASK_JOB_ERROR,
            "updated_at": _now(),
        }).in_("id", ids).eq("status", "generating").execute()
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
    kind: str | None = None,
    project_id: int | None = None,
    source_turn_id: int | None = None,
    run_id: str | None = None,
    client_message_id: str | None = None,
    attempt: int | None = None,
) -> int:
    """Persist a `generating` Ask job row and return its id. The POST returns
    this id immediately; the background worker fills `response` and flips the
    status to `ready` (or `error`).

    `kind`/`project_id`/`source_turn_id`/`run_id`/`client_message_id`/
    `attempt` are the chat-parity execution-identity columns — optional
    passthrough, all default `None` (the existing main/private ask shape).
    Callers that don't pass them get byte-identical rows to before this
    ticket."""
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
        "kind": kind,
        "project_id": project_id,
        "source_turn_id": source_turn_id,
        "run_id": run_id,
        "client_message_id": client_message_id,
        "attempt": attempt,
    }).execute()
    return resp.data[0]["id"]


class RetryAttemptLive(Exception):
    """A retry was requested while an attempt for the same source turn is
    still `generating` — the route maps this to 409. DB-enforced by the
    `ask_jobs_active_attempt_uidx` partial-unique (a concurrent second claim
    violates it)."""


class RetryHasSideEffects(Exception):
    """A retry was requested for a run that recorded tool side effects
    (a delegation / execute_task artifact for its source turn) — re-running
    the body would double-delegate/double-edit, so auto-retry is refused and
    the route maps this to 422 (resend as a new turn)."""


def claim_retry_attempt(
    *,
    source_turn_id: int,
    kind: str,
    project_id: int,
    company_id: str,
    dataset: str,
    question: str,
    conversation_id: int,
    run_id: str,
    had_side_effects: bool,
) -> dict:
    """Atomically claim ONE new retry attempt for a group turn.

    * `had_side_effects` True → raise `RetryHasSideEffects` (422): the prior
      attempt already wrote a delegation/artifact, so re-running is unsafe.
    * A `generating` `ask_jobs` row already exists for this `source_turn_id`
      → raise `RetryAttemptLive` (409): an attempt is in flight.
    * Otherwise insert a NEW `generating` row with `attempt = <prev max> + 1`
      and the fresh `run_id`, returning `{id, run_id, attempt, source_turn_id}`.

    Atomicity is DB-enforced: the `ask_jobs_active_attempt_uidx` partial-unique
    means a second concurrent claim that slips past the read-check still
    violates the index on insert — caught here and surfaced as
    `RetryAttemptLive`. `client_message_id` is intentionally NOT carried onto a
    retry (a retry is a deliberate new attempt, not a client double-submit, so
    it must not collide on the client_message_id unique — that unique stays the
    backstop against a genuine double-SUBMIT)."""
    if had_side_effects:
        raise RetryHasSideEffects(
            "the prior attempt recorded tool side effects — resend as a new turn"
        )
    c = require_client()
    live = (
        c.table("ask_jobs")
        .select("id")
        .eq("source_turn_id", source_turn_id)
        .eq("status", "generating")
        .limit(1)
        .execute()
        .data
    )
    if live:
        raise RetryAttemptLive(f"an attempt is already live for source_turn_id={source_turn_id}")
    prior = (
        c.table("ask_jobs")
        .select("attempt")
        .eq("source_turn_id", source_turn_id)
        .eq("kind", kind)
        .execute()
        .data
        or []
    )
    attempt = max((r.get("attempt") or 0 for r in prior), default=0) + 1
    try:
        new_id = start_ask_job(
            company_id=company_id,
            dataset=dataset,
            question=question,
            conversation_id=conversation_id,
            kind=kind,
            project_id=project_id,
            source_turn_id=source_turn_id,
            run_id=run_id,
            attempt=attempt,
        )
    except APIError as exc:
        # A concurrent claim won the race and its `generating` row now holds
        # the partial-unique slot for this source_turn_id (Postgres 23505).
        if getattr(exc, "code", None) == "23505" or "ask_jobs_active_attempt_uidx" in str(exc):
            raise RetryAttemptLive(
                f"a concurrent attempt claimed source_turn_id={source_turn_id}"
            ) from exc
        raise
    return {
        "id": new_id,
        "run_id": run_id,
        "attempt": attempt,
        "source_turn_id": source_turn_id,
    }


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


def set_ask_job_route(
    ask_id: int, skill_id: str | None, action: str | None = None
) -> None:
    """Record the skill the router picked, WHILE the job is still generating.

    The routed skill used to reach the client only inside `response`, which
    `complete_ask_job` writes at the very end of the run — so the chat's waiting
    surface could not name the skill it was waiting for until the wait was over.
    The Ask worker calls this the moment `qa_agent.route()` resolves, and
    `GET /v1/ask/{id}` surfaces the columns from `generating` onwards.

    Guarded on `status == 'generating'`, like touch_ask_job: a route write must
    never touch a row the user already cancelled or a worker already finished.
    A no-skill decision (direct answer, out-of-scope refusal, or one of the
    pre-routing interceptors) writes NOTHING — the columns stay NULL, which is
    the signal the UI uses to render no chip rather than invent a default.

    Best-effort by contract: this is display metadata, so any DB error is logged
    and swallowed rather than failing an answer that is otherwise fine.
    """
    if not skill_id:
        return
    try:
        c = require_client()
        c.table("ask_jobs").update({
            "routed_skill": skill_id,
            "routed_skill_action": action or None,
            "updated_at": _now(),
        }).eq("id", ask_id).eq("status", "generating").execute()
    except Exception:  # noqa: BLE001 — display metadata must never fail the ask
        logger.warning("routed-skill write failed for ask_id=%s", ask_id, exc_info=True)


def touch_ask_job(ask_id: int) -> bool:
    """Heartbeat: bump `updated_at` so the orphan sweep can tell a LIVE long
    answer from a dead worker's abandoned row.

    Guarded on `status == 'generating'` so a beat can never resurrect a job the
    user cancelled or a worker already finished. Returns True when the row was
    still generating (i.e. the beat landed), False otherwise — the caller uses
    that to stop beating.

    Best-effort by contract: a transient DB error returns True (keep beating)
    rather than aborting a healthy answer over a blip.
    """
    try:
        c = require_client()
        resp = (
            c.table("ask_jobs")
            .update({"updated_at": _now()})
            .eq("id", ask_id)
            .eq("status", "generating")
            .execute()
        )
    except Exception:  # noqa: BLE001 — a blip must not stop the heartbeat
        logger.warning("ask heartbeat failed for ask_id=%s", ask_id, exc_info=True)
        return True
    return bool(resp.data)


def fail_ask_job(
    ask_id: int, error: str, error_class: str | None = None
) -> None:
    """Mark the job `error` (best-effort — the worker never crashes on this).

    Guarded on `status == 'generating'` for the same reason as
    complete_ask_job: a cancel that landed first must not be clobbered by a
    trailing failure from the (now-abandoned) worker.

    `error_class` is the optional typed-error-category passthrough (e.g.
    billing/timeout/local_gate/app) — separate from the generic user-facing
    `status = 'error'` and `error` message, and unused by any existing
    caller (all pass only `ask_id`/`error` today, so this defaults to
    `None` and every existing call is unaffected)."""
    c = require_client()
    c.table("ask_jobs").update({
        "status": "error",
        "error": (error or "")[:500],
        "error_class": error_class,
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
