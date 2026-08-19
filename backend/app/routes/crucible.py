"""Goal Analysis — the run endpoints. Users never see the word Crucible.

Gated by `require_crucible_module`, which 403s for any company without the
`crucible` flag. THE UI GATE IS NOT THE GATE: the web hides the composer chip
for an unenrolled company, but the client decides what to render and the server
decides what runs, so a direct POST is refused here regardless.

THE ROW IS THE JOB. A run is created durable and `resolving_goal` BEFORE any
work starts, so the panel has an id to poll, a double-click cannot start two
runs (the client never posts content back), and a process death mid-run is
recoverable by `sweep_orphans` rather than invisible. The long work goes to a
DEDICATED bounded executor rather than `to_thread`'s shared default pool — a
run holds its thread for minutes, and the shared pool also serves ~120 short
blocking call sites that would queue behind it. That is the
`routes/custom_artifacts.py` shape, adopted because it was arrived at by
fixing exactly these failures.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import partial
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import WorkspaceContext
from app.crucible.claims import project_signals
from app.crucible.goal import KpiTreeSource, confirm as confirm_goal, resolve
from app.crucible.pipeline import build_findings
from app.crucible.types import GoalDefinition
from app.db import crucible_runs as runs_db
from app.entitlements import require_crucible_module

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/crucible", tags=["crucible"])

#: Small and dedicated. Runs can only ever starve each other; the queue beyond
#: it is the durable `resolving_goal` row, which is what that row is for.
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="crucible")

#: asyncio holds only a WEAK reference to a task, so a bare create_task can be
#: garbage-collected mid-run.
_inflight: set = set()


class StartRun(BaseModel):
    goal_text: str = Field(min_length=3, max_length=2000)
    conversation_id: Optional[int] = None


class ConfirmGoal(BaseModel):
    definition_text: str = Field(min_length=1, max_length=4000)


def _public(row: dict) -> dict:
    """What the client may see. `error` is deliberately absent — it holds raw
    exception text, which is an operator detail, not something to render into a
    shared workspace."""
    return {
        "id": row.get("id"),
        "status": row.get("status"),
        "goal_text": row.get("goal_text"),
        "error_code": row.get("error_code"),
        "coverage_notes": row.get("coverage_notes") or [],
        "claim_count": row.get("claim_count") or 0,
        "conversation_id": row.get("conversation_id"),
        "created_at": row.get("created_at"),
        "finished_at": row.get("finished_at"),
    }


@router.post("")
async def start(
    body: StartRun,
    company: WorkspaceContext = Depends(require_crucible_module),
):
    """Start a run. Returns the row immediately, `resolving_goal`."""
    row = await asyncio.to_thread(
        runs_db.create,
        company.company_id,
        goal_text=body.goal_text,
        conversation_id=body.conversation_id,
        created_by=getattr(company, "user_id", None),
    )

    kwargs = dict(run_id=row["id"], company_id=company.company_id,
                  goal_text=body.goal_text)
    if "pytest" in sys.modules:
        # The TestClient does not keep the loop alive between requests, so a
        # fire-and-forget task would never run and a polling test would spin
        # forever. Inline under pytest, exactly as routes/ask.py does.
        await asyncio.to_thread(execute_run, **kwargs)
        row = await asyncio.to_thread(runs_db.get, row["id"], company.company_id) or row
    else:
        loop = asyncio.get_running_loop()
        task = asyncio.ensure_future(
            loop.run_in_executor(_POOL, partial(execute_run, **kwargs))
        )
        _inflight.add(task)
        task.add_done_callback(_inflight.discard)

    return _public(row)


@router.get("")
def list_runs(company: WorkspaceContext = Depends(require_crucible_module)):
    return {"runs": [_public(r) for r in runs_db.list_for_company(company.company_id)]}


@router.get("/{run_id}")
def get_run(run_id: int, company: WorkspaceContext = Depends(require_crucible_module)):
    row = runs_db.get(run_id, company.company_id)
    if not row:
        # Tenant filter is in the query, so "exists but not yours" is
        # indistinguishable from "does not exist".
        raise HTTPException(404, "Run not found")
    findings, ledger = runs_db.load_findings(run_id, company.company_id)
    return {**_public(row), "findings": findings, "considered": ledger}


@router.post("/{run_id}/confirm")
def confirm(
    run_id: int,
    body: ConfirmGoal,
    company: WorkspaceContext = Depends(require_crucible_module),
):
    """I9's human gate. The ONLY path from `awaiting_confirmation` onward.

    Nothing here infers a definition and no LLM output can reach this state —
    a user typed or approved these words, and `confirm_goal` records who and
    when because `GoalDefinition` refuses to be locked without both.
    """
    row = runs_db.get(run_id, company.company_id)
    if not row:
        raise HTTPException(404, "Run not found")
    if row.get("status") != "awaiting_confirmation":
        raise HTTPException(
            409,
            f"This run is {row.get('status')}, so there is no definition "
            f"waiting to be confirmed.",
        )

    runs_db.update(
        run_id, company.company_id,
        status="running",
        goal_text=row.get("goal_text") or "",
    )
    kwargs = dict(run_id=run_id, company_id=company.company_id,
                  goal_text=row.get("goal_text") or "",
                  definition_text=body.definition_text,
                  confirmed_by=getattr(company, "user_id", None))
    if "pytest" in sys.modules:
        execute_run(**kwargs)
    else:
        loop = asyncio.get_event_loop()
        task = asyncio.ensure_future(
            loop.run_in_executor(_POOL, partial(execute_run, **kwargs))
        )
        _inflight.add(task)
        task.add_done_callback(_inflight.discard)
    return _public(runs_db.get(run_id, company.company_id) or row)


def execute_run(
    *,
    run_id: int,
    company_id: str,
    goal_text: str,
    definition_text: Optional[str] = None,
    confirmed_by: Optional[str] = None,
) -> None:
    """The whole deterministic pipeline. TOTAL — never raises to its caller.

    A run that dies with an unhandled exception leaves a row spinning until the
    sweep catches it, which is a worse user experience than a failure that says
    what happened.
    """
    now = datetime.now(timezone.utc)
    try:
        runs_db.heartbeat(run_id, company_id)

        # ── Stage 0. Resolve, then STOP. I9 is a human gate, not a check. ───
        #
        # A candidate does NOT run. Finding the metric in the company's own KPI
        # tree is a strong proposal, but adopting it is still the user's act —
        # the difference between "adopted" and "inferred" is entirely whether a
        # person said yes, and I9 exists because that difference is invisible
        # once a run has produced confident-looking output. So a candidate goes
        # to the same confirmation state as a miss, with the proposal prefilled:
        # one click for the common case, and a decision either way.
        if definition_text is None:
            tree = None
            try:
                from app.kpi_tree import load_kpi_tree

                tree = load_kpi_tree(company_id)
            except Exception:  # noqa: BLE001 — an unreadable tree means "ask",
                # which is the safe direction: I9 would rather elicit than adopt
                # something it could not actually read.
                logger.exception("crucible: could not read kpi_tree for %s", company_id)

            resolution = resolve(
                company_id=company_id, raw_goal_text=goal_text,
                currency="accounts", sources=[KpiTreeSource(tree)],
            )
            proposed = (
                resolution.definition.definition_text
                if resolution.definition is not None else ""
            )
            runs_db.update(
                run_id, company_id,
                status="awaiting_confirmation",
                prioritisation={
                    "ask": resolution.ask,
                    "resolution": resolution.status,
                    "proposed_definition": proposed,
                    "proposed_source": (
                        resolution.definition.definition_source_ref
                        if resolution.definition is not None else None
                    ),
                    # Carried, never resolved: two authoritative systems
                    # disagreeing about what a metric means is worth more than
                    # either answer, and picking one silently is the failure.
                    "conflicts": [
                        {"metric": c.metric_name,
             "a": {"source": c.source_a, "definition": c.definition_a},
             "b": {"source": c.source_b, "definition": c.definition_b}}
                        for c in resolution.conflicts
                    ],
                },
            )
            _remember(run_id, resolution)
            return

        # Past this line a human confirmed these words. Lock and persist the
        # definition BEFORE spending anything, so the run is auditable even if
        # the analysis below fails.
        pending = _pending(run_id) or _bare_definition(company_id, goal_text)
        locked = confirm_goal(
            pending, user_id=confirmed_by or "", at=now,
            definition_text=definition_text,
        )
        definition_row_id = runs_db.save_definition(company_id, locked)
        runs_db.update(run_id, company_id, goal_definition_id=definition_row_id)

        runs_db.update(run_id, company_id, status="running",
                       started_at=now.isoformat())
        runs_db.heartbeat(run_id, company_id)

        # ── Stage 4. Project the corpus into claims. ────────────────────────
        signals = _load_signals(company_id)
        claims, stats = project_signals(signals)
        runs_db.update(run_id, company_id, claim_count=len(claims))

        if not claims:
            runs_db.fail(run_id, company_id, code="no_evidence",
                         detail="no signals in this company's knowledge graph")
            return

        # ── Stages 5–8. Findings, verified and scored. ──────────────────────
        result = build_findings(claims, currency="accounts", now=now)
        runs_db.heartbeat(run_id, company_id)

        rows = []
        for finding, impact, confidence in zip(
            result.findings, result.impacts, result.confidences
        ):
            rows.append({
                "statement": finding.statement,
                "claim_ids": list(finding.claim_ids),
                "adjudication": finding.adjudication,
                "impact_value": impact.value,
                "currency": impact.currency,
                "confidence_band": confidence.band,
                "surfaced_by": list(finding.confidence_inputs.surfaced_by),
                "assumed_params": [
                    {"name": p.name, "basis": p.basis} for p in impact.assumed_params
                ],
                "impact": {
                    "value": impact.value,
                    "affected_population": impact.affected_population,
                },
                "confidence": {
                    "band": confidence.band,
                    "weakest_leg": confidence.weakest_leg,
                    "weakest_leg_reason": confidence.weakest_leg_reason,
                    "cap_reason": confidence.cap_reason,
                },
                "tier": "deep",
            })

        ledger = [{
            "label": r.label, "reason": r.reason,
            "stopped_at_stage": r.stopped_at, "claim_ids": list(r.claim_ids),
        } for r in result.rejected]

        runs_db.save_findings(run_id, company_id, rows, ledger)
        runs_db.update(
            run_id, company_id, status="ready",
            finished_at=datetime.now(timezone.utc).isoformat(),
            coverage_notes=_coverage_notes(stats, result.stats),
        )
    except Exception as exc:  # noqa: BLE001 — total by contract
        logger.exception("crucible: run %s failed", run_id)
        runs_db.fail(run_id, company_id, code="internal", detail=str(exc))


#: Stage 0's proposal, held between the resolve call and the confirm that
#: follows it. In-process only and deliberately lossy: after a restart the
#: confirm path falls back to `_bare_definition`, which produces an ELICITED
#: definition from the user's own confirmed words. Losing the memo costs a
#: provenance label, never a wrong lock.
_pending_definitions: dict[int, GoalDefinition] = {}


def _remember(run_id: int, resolution) -> None:
    if resolution.definition is not None:
        _pending_definitions[run_id] = resolution.definition


def _pending(run_id: int) -> Optional[GoalDefinition]:
    return _pending_definitions.pop(run_id, None)


def _bare_definition(company_id: str, goal_text: str) -> GoalDefinition:
    """An unresolved shell for words the user typed themselves.

    `confirm` compares the confirmed text against this empty one, sees a
    difference, and marks the result `elicited` — which is exactly right: these
    are the user's words, not a system's.
    """
    return GoalDefinition(
        id="", raw_goal_text=goal_text, metric_name="", definition_text="",
        currency="accounts", direction="increase",
    )


def _coverage_notes(claim_stats: dict, pipeline_stats: dict) -> list[dict]:
    """Every degradation renders. A quietly thinner run is indistinguishable
    from a complete one, which is worse than the failure it replaced."""
    notes = []
    if claim_stats.get("no_timestamp"):
        notes.append({
            "reason": "undated evidence",
            "actual": f"{claim_stats['no_timestamp']} of {claim_stats['seen']} "
                      f"signals carried no usable date and were not read",
        })
    if pipeline_stats.get("findings") and not pipeline_stats.get("sizeable"):
        notes.append({
            "reason": "nothing could be sized",
            "actual": "no finding named an account, so every result is "
                      "unsized rather than small",
        })
    return notes


def _load_signals(company_id: str) -> list[dict]:
    """Read the company's signals, paged.

    `GraphFacade.all_signals` uses `select("*")`, which pulls 1536-float
    embeddings and hits PostgREST's default row cap — measured to time out on a
    5,700-signal tenant. Explicit columns and explicit paging instead.
    """
    from app.db.client import require_client

    client = require_client()
    rows: list[dict] = []
    for page in range(40):
        chunk = (
            client.table("kg_signal")
            .select("id,kind,source_type,content,properties,valid_at,source_id")
            .eq("enterprise_id", company_id)
            .range(page * 1000, page * 1000 + 999)
            .execute()
        ).data or []
        rows.extend(chunk)
        if len(chunk) < 1000:
            break
    return rows
