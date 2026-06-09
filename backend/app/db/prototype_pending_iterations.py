"""DB helpers for `prototype_pending_iterations` (P3-06, AD11).

Message queue: up to 5 iterate prompts stack per prototype; they run SERIALLY
(one at a time). DB-backed so a process restart recovers the queue (the
orphan-clear pattern) — Sprntly runs a single uvicorn worker that restarts on
deploy, so an in-memory deque would silently drop up to 5 queued iterate
prompts. The row stores INPUTS only (prompt, applied_comment_id, mode, status,
timestamps); `queue_position` is DERIVED at read time, never stored — re-deriving
is a cheap count, so storing it would violate the inference rule.

These helpers are *synchronous* and use `require_client()` + `utc_now()`,
mirroring `db/prototypes.py` / `db/prototype_comments.py` exactly — supabase-py is
a synchronous client, and the async-task routes call these sync helpers directly
from their async handlers.

Ordering note: dequeue order and `queue_position` derivation key on `id`
(monotonic identity), NOT `created_at`. `utc_now()` is second-precision, so two
enqueues in the same second tie on `created_at`; `id` is the deterministic
tiebreaker — the same rationale `find_existing_prototype` uses for "most recent".

Workspace isolation (Architecture Rules #20-#23):
- INSERTs populate `workspace_id` from the caller (the route threads
  `require_session().aud`, or the resolved prototype's workspace_id); never
  hardcoded here.
- All user-driven SELECT / UPDATE filter by `workspace_id`.
- `invalidate_orphan_running_iterations` is a lifespan sweep ACROSS ALL
  workspaces (Rule #23) — system cleanup, NOT user-driven, so it deliberately
  does NOT filter by workspace_id.

Observability (Rule #24): every log line carries identifiers + counts only —
never the iterate prompt body (it can contain PII).
"""
from __future__ import annotations

import logging
from typing import Any

from app.db.client import require_client, utc_now

logger = logging.getLogger(__name__)

_TABLE = "prototype_pending_iterations"
_QUEUE_CAP = 5
# An iteration occupies a queue slot while pending OR running; both count toward
# the cap and toward "ahead of you" position.
_ACTIVE = ("pending", "running")


class QueueFullError(Exception):
    """Raised by `enqueue_iteration` when the queue already holds `_QUEUE_CAP`
    active (pending + running) iterations for the prototype. The `POST /iterate`
    route maps it to HTTP 429."""


def _active_rows(c: Any, *, prototype_id: int, workspace_id: str) -> list[dict[str, Any]]:
    """All active (pending + running) rows for a prototype, workspace-filtered,
    ordered by `id` ascending (queue order). Shared by the cap check, the count,
    and the position derivation so they agree on one definition of 'active'."""
    resp = (
        c.table(_TABLE).select("*")
        .eq("prototype_id", prototype_id)
        .eq("workspace_id", workspace_id)
        .in_("status", list(_ACTIVE))
        .order("id", desc=False)
        .execute()
    )
    return resp.data or []


def _derive_position(rows: list[dict[str, Any]], *, iteration_id: int) -> int:
    """Position of `iteration_id` within its prototype's active queue.

    `rows` is the active set (pending + running), id-ascending. The rule (AC7):
      - a PENDING row = (number of active rows ahead of it) + 1, so the head of an
        all-pending queue is 1, the next is 2, etc.;
      - a RUNNING row = 0 (it is executing — nobody is ahead of it);
      - once the head finishes (leaves the active set), every following row's
        position decreases by one.
    Returns 0 for an id not in the active set (already done/failed, or unknown).
    """
    target = next((r for r in rows if r["id"] == iteration_id), None)
    if target is None:
        return 0
    ahead = sum(1 for r in rows if r["id"] < iteration_id)
    return ahead + (1 if target["status"] == "pending" else 0)


def count_pending(*, prototype_id: int, workspace_id: str) -> int:
    """Count active (pending OR running) iterations for a prototype,
    workspace-filtered. This is the value the queue cap (5) is checked against:
    'pending' is the queue sense (uncompleted), and a currently-running row still
    occupies a slot, so it counts."""
    c = require_client()
    return len(_active_rows(c, prototype_id=prototype_id, workspace_id=workspace_id))


def queue_position(*, prototype_id: int, iteration_id: int, workspace_id: str) -> int:
    """Derived position of an iteration in its prototype's queue, workspace-
    filtered. NOT stored — recomputed from the active set on each read (cheap).
    See `_derive_position` for the exact rule (AC7)."""
    c = require_client()
    rows = _active_rows(c, prototype_id=prototype_id, workspace_id=workspace_id)
    return _derive_position(rows, iteration_id=iteration_id)


def enqueue_iteration(
    *,
    prototype_id: int,
    workspace_id: str,
    prompt: str,
    applied_comment_id: int | None = None,
    mode: str = "execute",
    plan: str | None = None,
) -> dict[str, Any]:
    """Insert a 'pending' iteration row and return it WITH a derived
    `queue_position`. Raises `QueueFullError` when `_QUEUE_CAP` active
    (pending + running) iterations already exist for this prototype (AD11 cap).

    The cap is checked BEFORE insert, so the 6th enqueue never lands a row.

    `plan` (P3-07): the APPROVED plan text for a confirm-plan execute row — it is
    prepended to the EXECUTE run's system blocks as an addendum. Left None for a
    plain re-prompt iterate and for plan-mode rows (whose plan is written AFTER the
    run by `set_iteration_plan`). The `plan` key is only included in the insert
    when non-None, so callers running against a schema without the `plan` column
    (e.g. pre-migration test DDLs) are unaffected.
    """
    c = require_client()
    active = _active_rows(c, prototype_id=prototype_id, workspace_id=workspace_id)
    if len(active) >= _QUEUE_CAP:
        raise QueueFullError(
            f"queue full: {len(active)} active iterations for prototype {prototype_id}"
        )
    payload: dict[str, Any] = {
        "prototype_id": prototype_id,
        "workspace_id": workspace_id,
        "prompt": prompt,
        "applied_comment_id": applied_comment_id,
        "mode": mode,
        "status": "pending",
    }
    if plan is not None:
        payload["plan"] = plan
    resp = c.table(_TABLE).insert(payload).execute()
    row = resp.data[0]
    # Re-read the active set (now includes the new row) and derive position over it.
    rows = _active_rows(c, prototype_id=prototype_id, workspace_id=workspace_id)
    row["queue_position"] = _derive_position(rows, iteration_id=row["id"])
    # Identifiers + position only -- never the prompt body (PII, Rule #24).
    logger.info(
        "iteration_enqueued prototype_id=%s iteration_id=%s queue_position=%s",
        prototype_id, row["id"], row["queue_position"],
    )
    return row


def dequeue_next(*, prototype_id: int, workspace_id: str) -> dict[str, Any] | None:
    """Mark the OLDEST pending row 'running' (stamp `started_at`) and return it;
    None when no pending rows remain OR a row is ALREADY running for this
    prototype. Workspace-filtered.

    Concurrency guard (the load-bearing invariant — AD11 "at most one iteration
    running per prototype"): the route fires a fresh `drain_iteration_queue` on
    EVERY enqueue. Without this guard, enqueuing iteration #2 while #1 is still
    running would let the new drain pick up #2's pending row and run it CONCURRENTLY
    with #1 — two runs mutating the same prototype bundle (lost update). So before
    promoting a pending row we check for an existing 'running' row for this
    prototype and no-op (return None) when one exists. The serial drain chains the
    next row only AFTER the current one is marked done/failed, so the guard clears
    naturally and the queue still drains to completion.

    DB-level (not just an in-process lock) so it is correct across workers. A
    single-worker deploy (BUILD.md §6) means the select-running-then-select-pending
    -then-update window is not a real race today; horizontal scaling would still
    want `SELECT ... FOR UPDATE SKIP LOCKED`, but the running-row guard already
    makes a second concurrent drain a no-op instead of a duplicate run.
    """
    c = require_client()
    running = (
        c.table(_TABLE).select("id")
        .eq("prototype_id", prototype_id)
        .eq("workspace_id", workspace_id)
        .eq("status", "running")
        .limit(1)
        .execute()
    )
    if running.data:
        # An iteration is already running for this prototype; a second concurrent
        # drain must not start another. The in-flight run chains the next row when
        # it finishes, so the queue still drains.
        logger.info(
            "iteration_dequeue_skipped_running prototype_id=%s running_id=%s",
            prototype_id, running.data[0]["id"],
        )
        return None
    resp = (
        c.table(_TABLE).select("*")
        .eq("prototype_id", prototype_id)
        .eq("workspace_id", workspace_id)
        .eq("status", "pending")
        .order("id", desc=False)
        .limit(1)
        .execute()
    )
    if not resp.data:
        return None
    row = resp.data[0]
    (
        c.table(_TABLE)
        .update({"status": "running", "started_at": utc_now()})
        .eq("id", row["id"])
        .eq("workspace_id", workspace_id)
        .execute()
    )
    row["status"] = "running"  # reflect the transition in the returned dict
    logger.info(
        "iteration_dequeued prototype_id=%s iteration_id=%s", prototype_id, row["id"],
    )
    return row


def mark_iteration_done(*, iteration_id: int, workspace_id: str) -> None:
    """Flip a running iteration to 'done' + stamp `finished_at`. Workspace-filtered."""
    c = require_client()
    (
        c.table(_TABLE)
        .update({"status": "done", "finished_at": utc_now()})
        .eq("id", iteration_id)
        .eq("workspace_id", workspace_id)
        .execute()
    )
    logger.info("iteration_done iteration_id=%s", iteration_id)


def mark_iteration_failed(*, iteration_id: int, workspace_id: str, error: str) -> None:
    """Flip an iteration to 'failed' with a truncated error + `finished_at`.
    Workspace-filtered. The drain continues to the next pending row after this —
    one bad prompt does not stall the queue."""
    c = require_client()
    (
        c.table(_TABLE)
        .update({
            "status": "failed",
            "error": (error or "")[:500],
            "finished_at": utc_now(),
        })
        .eq("id", iteration_id)
        .eq("workspace_id", workspace_id)
        .execute()
    )
    logger.info(
        "iteration_failed iteration_id=%s error_class=%s",
        iteration_id, (error or "").split(":", 1)[0][:80],
    )


def set_iteration_plan(*, iteration_id: int, workspace_id: str, plan: str) -> None:
    """Persist the textual plan a PLAN-mode run emitted onto its queue row (P3-07).

    Written AFTER a plan run completes (the plan is the run's output). Workspace-
    filtered. Stores the plan only — never a checkpoint, never a bundle (a plan run
    builds nothing). The plan body can echo PRD/source content, so it is NOT logged
    (Rule #24); only the identifier + length go to the log line."""
    c = require_client()
    (
        c.table(_TABLE)
        .update({"plan": plan})
        .eq("id", iteration_id)
        .eq("workspace_id", workspace_id)
        .execute()
    )
    logger.info(
        "iteration_plan_saved iteration_id=%s plan_chars=%s",
        iteration_id, len(plan or ""),
    )


def invalidate_orphan_running_iterations() -> int:
    """Lifespan hook: flip stuck 'running' rows (the worker task died with the
    previous process) to 'failed', so a restart recovers the queue.

    Operates ACROSS ALL WORKSPACES — this is a system-wide cleanup, not a
    user-driven query, so it deliberately does NOT filter by workspace_id
    (Rule #23). Mirrors `invalidate_orphan_generating_prototypes`
    (db/prototypes.py). Returns the count of rows updated.
    """
    c = require_client()
    rows = c.table(_TABLE).select("id").eq("status", "running").execute().data
    ids = [r["id"] for r in rows]
    if ids:
        (
            c.table(_TABLE)
            .update({
                "status": "failed",
                "error": "orphaned: process restarted mid-iteration",
                "finished_at": utc_now(),
            })
            .in_("id", ids)
            .execute()
        )
        logger.info("iteration_orphan_cleared count=%s", len(ids))
    return len(ids)
