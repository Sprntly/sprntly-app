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
from datetime import datetime, timedelta, timezone
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
    #: THE READER'S OWN SENTENCE, when the caller has one distinct from
    #: `goal_text` — chat sends the planner's EXTRACTED goal as `goal_text`
    #: and this alongside it, so a count or target the reader phrased in
    #: their own words is not silently dropped. Stored here, ONCE, because
    #: this is the only place a run's whole life this text is ever supplied —
    #: every later stage (`confirm`, `approve`) reads it back off the row
    #: rather than being resupplied it.
    asked_text: Optional[str] = None,
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
    stripped = (asked_text or "").strip()
    if stripped:
        # RIDES IN `prioritisation`, same as the plan and the progress
        # narration this blob already carries — no migration needed. Written
        # only when non-blank, so a caller with nothing to add (the direct
        # API, an older client) leaves the row byte-for-byte what it was
        # before this field existed.
        row["prioritisation"] = {"asked_text": stripped}
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


def claim_for_approval(run_id: int, company_id: str) -> Optional[dict]:
    """Atomically move a run out of `awaiting_approval`. None if it wasn't.

    Same shape and same reason as `claim_for_confirmation`: the expected status
    is IN the WHERE clause, so two approvals cannot both proceed and produce two
    analyses on one row. A double-click is the ordinary way to try.
    """
    res = (
        require_client().table(TABLE)
        .update({"status": "running",
                 "started_at": datetime.now(timezone.utc).isoformat(),
                 "updated_at": datetime.now(timezone.utc).isoformat()})
        .eq("id", run_id).eq("company_id", company_id)
        .eq("status", "awaiting_approval")
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


#: Supabase/PostgREST's own default cap on rows returned by one request. An
#: unpaged `select` past this silently returns exactly this many rows with no
#: error — not a partial-result flag, not a warning, nothing to catch. A real
#: run hit 831 findings, 83% of the way there, before anyone noticed this
#: reader had no `.range` at all.
_FINDINGS_PAGE = 1000


def _paged(client, table: str, run_id: int, company_id: str) -> list[dict]:
    """Every row of `table` for this run, paged past PostgREST's row cap.

    ORDER IS NOT OPTIONAL WITH `.range` — an unordered query's rows may come
    back in a different order per page, which can repeat a row on page 2 and
    never return another (the same reasoning `routes/crucible.py`'s
    `_signal_page` states for the same pattern).
    """
    rows: list[dict] = []
    offset = 0
    while True:
        page = (
            client.table(table).select("*")
            .eq("run_id", run_id).eq("company_id", company_id)
            .order("id")
            .range(offset, offset + _FINDINGS_PAGE - 1)
            .execute()
        ).data or []
        rows.extend(page)
        if len(page) < _FINDINGS_PAGE:
            break
        offset += _FINDINGS_PAGE
    return rows


def load_findings(run_id: int, company_id: str) -> tuple[list[dict], list[dict]]:
    client = require_client()
    # INSERTION ORDER IS THE RANK. `save_findings` writes one batch in the
    # order `_rank` produced, and that order is not recoverable from any
    # column: it puts an authoritative CONFLICT first regardless of size,
    # because two sources that may both speak disagreeing is worth more
    # than either claim. Re-sorting by `impact_value` here threw that away
    # and sent conflicts to the bottom — while the `tier` written at rank
    # time still said `deep`, so the row claimed a standing its position
    # contradicted. `_paged`'s own `.order("id")` preserves it across pages.
    findings = _paged(client, "crucible_findings", run_id, company_id)
    ledger = _paged(client, "crucible_ledger", run_id, company_id)
    return findings, ledger


def link_document(
    run_id: int, company_id: str, *, artifact_id: int, body_hash: str
) -> Optional[dict]:
    """Attach a freshly rendered report document to its run. None if it lost.

    THE CLAIM IS IN THE WHERE CLAUSE (`artifact_id IS NULL`), for the reason
    `claim_for_confirmation` states: read-then-write would let two simultaneous
    POSTs both see an unlinked run and both link, and the second link silently
    replaces the first — leaving a document the user may already be editing
    orphaned, reachable from nothing, and invisible until someone notices their
    edits went to a row nobody opens. A double-click is the ordinary way to
    produce that.

    The loser gets None and is expected to delete the document it created and
    return the winner's, which is what makes the endpoint idempotent rather
    than merely usually-idempotent.
    """
    res = (
        require_client().table(TABLE)
        .update({
            "artifact_id": artifact_id,
            "report_body_hash": body_hash,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        .eq("id", run_id).eq("company_id", company_id)
        .is_("artifact_id", "null")          # the claim
        .execute()
    )
    return (res.data or [None])[0]


def get_by_artifact(artifact_id: int, company_id: str) -> Optional[dict]:
    """The run a report document belongs to, or None.

    The reverse of `link_document`, and the chat edit tool's whole target
    resolution: the model names no id, the client says which document is open,
    and this says whether that document is a Goal Analysis report on THIS
    company's run. Tenant filter in the query, as everywhere else here.
    """
    res = (
        require_client().table(TABLE).select("*")
        .eq("artifact_id", artifact_id).eq("company_id", company_id)
        .limit(1).execute()
    )
    return (res.data or [None])[0]


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


#: How stale `heartbeat_at` must be before an enrichment is presumed dead.
#: `_progress` (routes/crucible.py) refreshes it before each of the three
#: model-call stages, so a genuinely healthy enrichment never sits idle this
#: long — a single stage can retry up to `app.llm.MAX_ATTEMPTS` times against
#: `app.llm._REQUEST_TIMEOUT_S = 120.0`, which is a worst case around 8
#: minutes; this leaves real margin above that rather than tripping on a slow
#: provider.
STALLED_ENRICHMENT_AGE_MINUTES = 10


def find_stalled_enrichment(
    *, older_than_minutes: int = STALLED_ENRICHMENT_AGE_MINUTES,
) -> list[dict]:
    """Runs that are `ready`, still say `enrichment_pending`, and have not
    heartbeat in a while — `sweep_orphans`'s own predicate (`resolving_goal`,
    `planning`, `running`) never sees these, because a run this stuck already
    published its findings and moved to `ready` before its worker died. THE
    ROW IS THE JOB, same as `sweep_orphans` — no separate job table.

    Client-side filtered on `enrichment_pending`, deliberately: it lives
    inside the `prioritisation` jsonb blob, not a column, and this table's
    `status`/`heartbeat_at` columns already narrow the candidate set to
    something small before that filter runs.
    """
    cutoff = datetime.now(timezone.utc).timestamp() - older_than_minutes * 60
    cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
    client = require_client()
    candidates = (
        client.table(TABLE).select("*")
        .eq("status", "ready")
        .lt("heartbeat_at", cutoff_iso)
        .limit(100).execute()
    ).data or []
    out = []
    for row in candidates:
        meta = row.get("prioritisation") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:  # noqa: BLE001
                meta = {}
        if isinstance(meta, dict) and meta.get("enrichment_pending"):
            out.append(row)
    return out


def claim_stalled_enrichment(
    run_id: int, company_id: str, *, expected_heartbeat_at
) -> Optional[dict]:
    """Atomically claim a stalled enrichment for a re-run. None if lost.

    THE CLAIM IS IN THE WHERE CLAUSE (`heartbeat_at` still equal to the value
    just read), same reasoning as `claim_for_confirmation`: read-then-write
    would let two sweep ticks — or a sweep tick racing the run's own worker,
    which was alive after all and about to heartbeat — both see the same stale
    row and both re-run enrichment. The loser's update touches zero rows and
    gets None back; it does no work.
    """
    res = (
        require_client().table(TABLE)
        .update({"heartbeat_at": datetime.now(timezone.utc).isoformat()})
        .eq("id", run_id).eq("company_id", company_id)
        .eq("heartbeat_at", expected_heartbeat_at)   # the claim
        .execute()
    )
    return (res.data or [None])[0]


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


#: How long a report document may sit unlinked before the sweep treats it as
#: stranded. Generous, because the window it covers is milliseconds wide: the
#: only way to strand one is for the process to die between `create_artifact`
#: and `link_document`. A long gate costs nothing and removes any chance of
#: deleting a document whose link is still in flight on a slow box.
STRANDED_DOCUMENT_AGE_MINUTES = 60


def sweep_stranded_documents(*, older_than_minutes: int | None = None) -> dict:
    """Delete report documents no run points at. Returns what it did.

    THE FAILURE THIS COVERS. `POST /{id}/document` creates the artifact, then
    links it. `link_document`'s compare-and-set handles the double-click race —
    the loser deletes its own document. It cannot handle a process death
    BETWEEN the two calls, because the process that would do the deleting is
    gone. What is left is a `goal_analysis` artifact reachable from no run.

    DELETING BLINDLY WOULD BE WORSE THAN THE BUG. A stranded document is not
    invisible: `custom_artifacts` rows appear in the Artifacts library, so
    somebody can open one and edit it. Destroying that is a far bigger failure
    than leaving a stray row in a list.

    So the rule is: delete only what is PROVABLY UNTOUCHED — `version == 1`,
    meaning no content write has ever landed on it (the store starts version at
    1 and increments on every write). Anything edited is left alone and
    reported, because at that point it is somebody's document regardless of how
    it got there, and a human should decide.
    """
    from app.crucible.report import ARTIFACT_KIND
    from app.db.custom_artifacts import delete_artifact

    minutes = (
        STRANDED_DOCUMENT_AGE_MINUTES if older_than_minutes is None
        else older_than_minutes
    )
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    client = require_client()
    out = {"deleted": 0, "kept_edited": 0, "scanned": 0}

    try:
        candidates = (
            client.table("custom_artifacts")
            .select("id,company_id,version,created_at,title")
            .eq("kind", ARTIFACT_KIND)
            .lt("created_at", cutoff.isoformat())
            .order("id")
            .limit(200)
            .execute()
        ).data or []
    except Exception:  # noqa: BLE001 — a sweep failure must never crash the
        # scheduler, and a missed pass costs nothing: the next one catches it.
        logger.exception("crucible: stranded-document scan failed")
        return out

    out["scanned"] = len(candidates)
    for row in candidates:
        artifact_id, company_id = row.get("id"), row.get("company_id")
        if artifact_id is None or not company_id:
            continue
        # Linked? Then it is doing its job and is not stranded.
        if get_by_artifact(int(artifact_id), str(company_id)):
            continue
        if int(row.get("version") or 1) > 1:
            # Somebody wrote to it. Not ours to delete — see the docstring.
            out["kept_edited"] += 1
            logger.warning(
                "crucible: stranded report document %s (company %s) has edits "
                "(version %s) — leaving it for a human",
                artifact_id, str(company_id)[:8], row.get("version"),
            )
            continue
        try:
            if delete_artifact(str(company_id), int(artifact_id)):
                out["deleted"] += 1
        except Exception:  # noqa: BLE001 — one bad row must not end the sweep
            logger.exception(
                "crucible: could not delete stranded document %s", artifact_id)

    if out["deleted"] or out["kept_edited"]:
        logger.info(
            "crucible: swept %d stranded report document(s); kept %d that had "
            "edits", out["deleted"], out["kept_edited"],
        )
    return out
