"""DB helpers for `design_agent_jobs` — the opt-in Design Agent worker queue
(Tier 2).

A Supabase-backed job queue that moves heavy generation (LLM recreate loop +
vite build + Chromium screenshot) off the API request process onto a separate
`python -m app.worker` process. The queue lives in Postgres (not an in-memory
deque) for the same reason the iterate queue does: a deploy/restart recovers the
backlog via the orphan-requeue sweep instead of silently dropping it.

These helpers are *synchronous* and use `require_client()` + `utc_now()`,
mirroring `db/prototype_pending_iterations.py` / `db/prototypes.py` exactly —
supabase-py is a synchronous client; the async route + the async worker loop call
these sync helpers directly.

ALL helpers are FAIL-SOFT / guarded: a missing table (this migration not yet
applied in an environment — e.g. prod before the worker unit is deployed) must
never 500 the /generate route nor crash worker/startup. enqueue_job returns None
on any error so the caller falls back to the in-process path; the read/sweep
helpers return their safe-empty value.

Workspace isolation:
- INSERTs populate `workspace_id` from the caller (the route threads the resolved
  company_id); never hardcoded here.
- The claim/complete/fail helpers key on the job `id` (the worker already holds
  the row it claimed) — they are system-side worker operations, not user-driven
  queries, so they do not re-filter by workspace_id.
- `requeue_orphan_claimed_jobs` is a lifespan sweep ACROSS ALL workspaces
  — system cleanup, NOT user-driven.

Observability: every log line carries identifiers + counts only —
never the payload body (it can echo PRD/instruction content).
"""
from __future__ import annotations

import logging
from typing import Any

from app.db.client import require_client, utc_now

logger = logging.getLogger(__name__)

_JOBS = "design_agent_jobs"
_HEARTBEAT = "design_agent_worker_heartbeat"
_HEARTBEAT_ROW_ID = 1


def enqueue_job(
    *, prototype_id: int, workspace_id: str, payload: dict[str, Any]
) -> dict[str, Any] | None:
    """Insert a 'queued' job for a prototype and return the row, or None on any
    failure (missing table, DB error) so the caller falls back to the in-process
    generation path.

    Dedupe parity with `find_existing_prototype` (one job per prototype): the
    table has a unique index on `prototype_id`, so a re-enqueue for the same
    prototype upserts in place rather than fanning out a duplicate job. We upsert
    on `prototype_id`, resetting status -> 'queued' and clearing the prior claim,
    which is the right behaviour for a re-submitted generation.

    `payload` MUST be JSON-serializable (it is the `_run_generation_bg` kwargs
    with the Pydantic `manual_design` already `model_dump()`-ed by the caller) —
    it is stored as jsonb and handed back verbatim to the worker.
    """
    try:
        c = require_client()
        row = {
            "prototype_id": prototype_id,
            "workspace_id": workspace_id,
            "payload": payload,
            "status": "queued",
            "claimed_by": None,
            "claimed_at": None,
            "error": None,
            "updated_at": utc_now(),
        }
        resp = (
            c.table(_JOBS)
            .upsert(row, on_conflict="prototype_id")
            .execute()
        )
        out = (resp.data or [None])[0]
        logger.info(
            "design_agent_job_enqueued prototype_id=%s job_id=%s",
            prototype_id, (out or {}).get("id"),
        )
        return out
    except Exception:  # noqa: BLE001 — fail-soft: caller degrades to in-process
        logger.warning(
            "design_agent_job_enqueue_failed prototype_id=%s — falling back to "
            "in-process generation",
            prototype_id, exc_info=True,
        )
        return None


def claim_next_job(*, worker_id: str) -> dict[str, Any] | None:
    """Atomically claim the OLDEST queued job for this worker and return it;
    None when nothing is claimable (or the table is missing).

    Atomic-claim approach (mirrors `prototype_pending_iterations.dequeue_next`):
    supabase-py / PostgREST has no `SELECT ... FOR UPDATE SKIP LOCKED`, so we do a
    CONDITIONAL UPDATE claim — select a candidate id, then
    `update(status='claimed', claimed_by=worker_id).eq(id).eq(status,'queued')`.
    The `.eq("status", "queued")` in the UPDATE is the compare-and-swap: only the
    worker whose UPDATE flips the row from 'queued' wins it; a racing worker's
    UPDATE matches zero rows. We confirm the win by re-reading the row and
    checking `claimed_by == worker_id` (robust regardless of how the driver
    reports affected rows). On a lost race we retry the next candidate.

    SINGLE-WORKER-SAFETY NOTE: Sprntly today runs ONE worker unit, so the
    select-candidate-then-conditional-update window is not a real race. The
    conditional UPDATE on status already makes a second concurrent worker's claim
    a no-op rather than a double-run; for a true multi-worker future, swap the
    candidate-select + guarded-update for a single `SELECT ... FOR UPDATE SKIP
    LOCKED` inside a transaction (PostgREST RPC) to also eliminate the wasted
    candidate read under contention.
    """
    try:
        c = require_client()
        # A small candidate batch so a lost race can fall through to the next
        # oldest without another network round-trip per attempt.
        candidates = (
            c.table(_JOBS).select("id")
            .eq("status", "queued")
            .order("created_at", desc=False)
            .order("id", desc=False)
            .limit(5)
            .execute()
        ).data or []
        for cand in candidates:
            job_id = cand["id"]
            (
                c.table(_JOBS)
                .update({
                    "status": "claimed",
                    "claimed_by": worker_id,
                    "claimed_at": utc_now(),
                    "attempts": 1,  # first claim; orphan-requeue bumps on re-claim
                    "updated_at": utc_now(),
                })
                .eq("id", job_id)
                .eq("status", "queued")  # CAS: only flips a still-queued row
                .execute()
            )
            # Confirm we won the claim (a racing worker may have taken it).
            row = (
                c.table(_JOBS).select("*").eq("id", job_id).limit(1).execute()
            ).data
            if row and row[0].get("claimed_by") == worker_id and row[0].get("status") == "claimed":
                logger.info(
                    "design_agent_job_claimed job_id=%s prototype_id=%s worker_id=%s",
                    job_id, row[0].get("prototype_id"), worker_id,
                )
                return row[0]
        return None
    except Exception:  # noqa: BLE001 — fail-soft: missing table => nothing to claim
        logger.warning("design_agent_job_claim_failed worker_id=%s", worker_id, exc_info=True)
        return None


def complete_job(*, job_id: int) -> None:
    """Flip a claimed job to 'done' (the prototype row is already 'ready').
    Fail-soft."""
    try:
        c = require_client()
        (
            c.table(_JOBS)
            .update({"status": "done", "updated_at": utc_now()})
            .eq("id", job_id)
            .execute()
        )
        logger.info("design_agent_job_done job_id=%s", job_id)
    except Exception:  # noqa: BLE001
        logger.warning("design_agent_job_complete_failed job_id=%s", job_id, exc_info=True)


def fail_job(*, job_id: int, error: str) -> None:
    """Flip a claimed job to 'error' with a truncated message (the prototype row
    is already 'failed' — `_run_generation_bg` owns the prototype-status write).
    Fail-soft."""
    try:
        c = require_client()
        (
            c.table(_JOBS)
            .update({
                "status": "error",
                "error": (error or "")[:500],
                "updated_at": utc_now(),
            })
            .eq("id", job_id)
            .execute()
        )
        logger.info(
            "design_agent_job_error job_id=%s error_class=%s",
            job_id, (error or "").split(":", 1)[0][:80],
        )
    except Exception:  # noqa: BLE001
        logger.warning("design_agent_job_fail_failed job_id=%s", job_id, exc_info=True)


def requeue_orphan_claimed_jobs() -> int:
    """Lifespan sweep: re-queue jobs left 'claimed' by a worker that died (the
    process holding them is gone) so a fresh worker picks them up again.

    Operates ACROSS ALL WORKSPACES — system-wide cleanup, not a user-driven query
    — mirroring `invalidate_orphan_generating_prototypes` /
    `invalidate_orphan_running_iterations`. Returns the count re-queued. Fail-soft
    (missing table => 0) so a missing migration never breaks startup.

    Bumps `attempts` so a poison job that repeatedly orphans is observable. We do
    NOT cap attempts here — that policy is the worker's; this sweep only recovers
    the claim.
    """
    try:
        c = require_client()
        rows = c.table(_JOBS).select("id, attempts").eq("status", "claimed").execute().data or []
        if not rows:
            return 0
        for r in rows:
            (
                c.table(_JOBS)
                .update({
                    "status": "queued",
                    "claimed_by": None,
                    "claimed_at": None,
                    "attempts": (r.get("attempts") or 0) + 1,
                    "updated_at": utc_now(),
                })
                .eq("id", r["id"])
                .execute()
            )
        logger.info("design_agent_jobs_orphan_requeued count=%s", len(rows))
        return len(rows)
    except Exception:  # noqa: BLE001
        logger.warning("design_agent_jobs_orphan_requeue_skipped", exc_info=True)
        return 0


def write_heartbeat(*, worker_id: str) -> None:
    """Upsert the single-row worker heartbeat (id=1) with now(). Fail-soft.

    The worker calls this each loop tick. /generate reads it via
    `worker_heartbeat_fresh` to decide whether a live worker exists before
    enqueuing — the load-bearing half of the 3-way fallback."""
    try:
        c = require_client()
        (
            c.table(_HEARTBEAT)
            .upsert(
                {"id": _HEARTBEAT_ROW_ID, "worker_id": worker_id, "updated_at": utc_now()},
                on_conflict="id",
            )
            .execute()
        )
    except Exception:  # noqa: BLE001 — a heartbeat write failure must not stop the worker
        logger.warning("design_agent_worker_heartbeat_write_failed worker_id=%s", worker_id, exc_info=True)


def worker_heartbeat_fresh(*, within_seconds: int = 30) -> bool:
    """True iff the worker heartbeat row exists AND was updated within
    `within_seconds`. Used by /generate to gate the enqueue branch: a STALE or
    ABSENT heartbeat means no live worker is draining, so the route falls back to
    the in-process path rather than stranding a 'queued' job.

    Fail-soft: any error (missing table, unparseable timestamp) => False, which
    routes /generate to the safe in-process fallback.
    """
    try:
        from datetime import datetime, timezone

        c = require_client()
        rows = (
            c.table(_HEARTBEAT).select("updated_at")
            .eq("id", _HEARTBEAT_ROW_ID)
            .limit(1)
            .execute()
        ).data or []
        if not rows:
            return False
        raw = rows[0].get("updated_at")
        if not raw:
            return False
        ts = _parse_ts(raw)
        if ts is None:
            return False
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        return 0 <= age <= within_seconds
    except Exception:  # noqa: BLE001 — any failure => not fresh => in-process fallback
        logger.warning("design_agent_worker_heartbeat_read_failed", exc_info=True)
        return False


def _parse_ts(raw: str):
    """Parse a stored timestamp into an aware UTC datetime, or None.

    `utc_now()` writes ISO-8601 (the fake-supabase + Supabase both round-trip
    ISO strings); naive values are treated as UTC."""
    from datetime import datetime, timezone

    try:
        s = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
