"""crucible_runs and its children — the durable state of a Goal Analysis run.

TENANCY. Every read filters `company_id` IN THE QUERY rather than fetching by id
and comparing after, so a foreign id returns None and the route turns that into
a 404 — "exists but not yours" is never distinguishable from "does not exist".
The backend holds the service-role key, so RLS is bypassed and this filter IS
the tenant boundary (the db/custom_artifacts.py posture).

THE ROW IS THE JOB. There is no in-memory job store, deliberately: the row is
created before the multi-minute work starts, so the panel has an id to poll and
a process death mid-run is recoverable by a sweep rather than invisible. Same
lifecycle as `custom_artifacts`, with two extra states for the human gates.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.db.client import require_client

logger = logging.getLogger(__name__)

TABLE = "crucible_runs"

#: Closed set, mirroring the CHECK constraint in 20260819100000_crucible_core.sql.
#: A code path inventing a state gets a database error rather than a row nobody
#: can render.
STATES = (
    "draft", "resolving_goal", "awaiting_confirmation", "planning",
    "awaiting_approval", "running", "ready", "failed", "cancelled",
)

#: Safe to return to the user. `error` holds raw exception text and never is —
#: a transport error carries URLs, a provider error carries whatever the
#: provider put in its message.
ERROR_CODES = (
    "no_evidence", "goal_unresolved", "llm_error", "interrupted", "cancelled",
    "internal",
)


def create(
    company_id: str,
    *,
    goal_text: str,
    conversation_id: Optional[int] = None,
    created_by: Optional[str] = None,
) -> dict:
    """Create the row FIRST, before any work. Returns it immediately."""
    row = {
        "company_id": company_id,
        "goal_text": goal_text,
        "conversation_id": conversation_id,
        "created_by": created_by,
        "status": "resolving_goal",
        "heartbeat_at": datetime.now(timezone.utc).isoformat(),
    }
    res = require_client().table(TABLE).insert(row).execute()
    return (res.data or [{}])[0]


def get(run_id: int, company_id: str) -> Optional[dict]:
    res = (
        require_client().table(TABLE).select("*")
        .eq("id", run_id).eq("company_id", company_id)   # tenant filter IN the query
        .limit(1).execute()
    )
    return (res.data or [None])[0]


def list_for_company(company_id: str, limit: int = 50) -> list[dict]:
    res = (
        require_client().table(TABLE).select("*")
        .eq("company_id", company_id)
        .order("created_at", desc=True).limit(limit).execute()
    )
    return res.data or []


def update(run_id: int, company_id: str, **fields: Any) -> Optional[dict]:
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    res = (
        require_client().table(TABLE).update(fields)
        .eq("id", run_id).eq("company_id", company_id).execute()
    )
    return (res.data or [None])[0]


def claim_for_confirmation(run_id: int, company_id: str) -> Optional[dict]:
    """Atomically move a run out of `awaiting_confirmation`. None if it wasn't.

    ONE statement, with the expected status IN THE WHERE CLAUSE. Read-then-write
    would let two confirms both see `awaiting_confirmation` and both proceed —
    two locked goal definitions and two sets of findings on one row, which is
    not a race you can see afterwards because both halves look correct. A
    double-click is the ordinary way to produce it.
    """
    res = (
        require_client().table(TABLE)
        .update({"status": "running",
                 "started_at": datetime.now(timezone.utc).isoformat(),
                 "updated_at": datetime.now(timezone.utc).isoformat()})
        .eq("id", run_id).eq("company_id", company_id)
        .eq("status", "awaiting_confirmation")     # the claim
        .execute()
    )
    return (res.data or [None])[0]


def heartbeat(run_id: int, company_id: str) -> None:
    """Say the worker is still alive.

    `custom_artifacts` had to derive its orphan age gate from
    MAX_ATTEMPTS x LONG_REQUEST_TIMEOUT_S because those rows carry no heartbeat,
    so its sweep can only guess at 90 minutes. A run is longer and costlier, so
    it gets the precise signal.
    """
    try:
        update(run_id, company_id, heartbeat_at=datetime.now(timezone.utc).isoformat())
    except Exception:  # noqa: BLE001 — a missed heartbeat must not kill the run
        logger.warning("crucible: heartbeat failed for run %s", run_id)


def fail(run_id: int, company_id: str, *, code: str, detail: str) -> None:
    """Record a failure so the row LISTS rather than disappearing.

    A failed run that is filtered out of the listing is half the reason a
    feature looks broken: the user asked for something, nothing came back, and
    there is no row to explain it.
    """
    if code not in ERROR_CODES:
        code = "internal"
    update(
        run_id, company_id, status="failed", error_code=code, error=detail[:4000],
        finished_at=datetime.now(timezone.utc).isoformat(),
    )


def save_findings(
    run_id: int, company_id: str, findings: list[dict], rejected: list[dict]
) -> None:
    """Write the result. Batched, because a run produces tens of rows."""
    client = require_client()
    if findings:
        client.table("crucible_findings").insert([
            {**f, "run_id": run_id, "company_id": company_id} for f in findings
        ]).execute()
    if rejected:
        client.table("crucible_ledger").insert([
            {**r, "run_id": run_id, "company_id": company_id} for r in rejected
        ]).execute()


def load_findings(run_id: int, company_id: str) -> tuple[list[dict], list[dict]]:
    client = require_client()
    findings = (
        client.table("crucible_findings").select("*")
        .eq("run_id", run_id).eq("company_id", company_id)
        # INSERTION ORDER IS THE RANK. `save_findings` writes one batch in the
        # order `_rank` produced, and that order is not recoverable from any
        # column: it puts an authoritative CONFLICT first regardless of size,
        # because two sources that may both speak disagreeing is worth more
        # than either claim. Re-sorting by `impact_value` here threw that away
        # and sent conflicts to the bottom — while the `tier` written at rank
        # time still said `deep`, so the row claimed a standing its position
        # contradicted.
        .order("id").execute()
    ).data or []
    ledger = (
        client.table("crucible_ledger").select("*")
        .eq("run_id", run_id).eq("company_id", company_id).execute()
    ).data or []
    return findings, ledger


def sweep_orphans(*, older_than_minutes: int = 45) -> int:
    """Fail runs whose worker died. Returns how many.

    Recurring, not startup-only: a process that dies at 03:00 must not leave a
    row spinning until the next deploy. `custom_artifacts` shipped this
    startup-only and it had to be fixed later — same mistake, already made once.
    """
    cutoff = datetime.now(timezone.utc).timestamp() - older_than_minutes * 60
    cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
    client = require_client()
    stale = (
        client.table(TABLE).select("id,company_id")
        .in_("status", ["resolving_goal", "planning", "running"])
        .lt("heartbeat_at", cutoff_iso).limit(100).execute()
    ).data or []
    for row in stale:
        fail(row["id"], row["company_id"], code="interrupted",
             detail="worker stopped reporting; swept")
    if stale:
        logger.info("crucible: swept %d abandoned run(s)", len(stale))
    return len(stale)


def save_definition(company_id: str, definition) -> Optional[int]:
    """Persist a LOCKED goal definition and return its row id.

    Refuses anything unlocked. The table's CHECK constraint refuses it too —
    this is the same rule stated where the caller can see it, so a mistake
    surfaces as a readable error rather than a Postgres constraint violation.
    """
    if getattr(definition, "status", None) != "locked":
        raise ValueError(
            "I9: only a locked definition may be persisted; "
            f"got {getattr(definition, 'status', None)!r}"
        )

    pop = getattr(definition, "population", None)
    row = {
        "company_id": company_id,
        "raw_goal_text": definition.raw_goal_text,
        "metric_name": definition.metric_name,
        "definition_text": definition.definition_text,
        "definition_source_ref": definition.definition_source_ref,
        "source_ref": definition.source_ref,
        "currency": definition.currency,
        "direction": definition.direction,
        "status": "locked",
        "origin": definition.origin,
        "target_value": definition.target_value,
        "horizon_weeks": definition.horizon_weeks,
        "population": {
            k: list(v) for k, v in (getattr(pop, "segments", {}) or {}).items()
        },
        "conflicts_found": [
            {"metric": c.metric_name,
             "a": {"source": c.source_a, "definition": c.definition_a},
             "b": {"source": c.source_b, "definition": c.definition_b}}
            for c in (definition.conflicts_found or ())
        ],
        "confirmed_by_user_at": definition.confirmed_by_user_at.isoformat(),
        "confirmed_by_user_id": definition.confirmed_by_user_id,
        "definition_hash": definition.definition_hash,
    }
    res = require_client().table("crucible_goal_definitions").insert(row).execute()
    return ((res.data or [{}])[0] or {}).get("id")
