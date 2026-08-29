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
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StringConstraints

from app.auth import WorkspaceContext
from app.crucible.claims import project_signals
from app.crucible.cluster import assign_clusters, parse_embedding
from app.crucible.kg_themes import assign_themes, load_theme_map
from app.crucible.goal import KpiTreeSource, confirm as confirm_goal, resolve
from app.crucible.pipeline import build_findings
from app.crucible.plan import build_plan
from app.crucible.types import GoalDefinition
from app.db import crucible_runs as runs_db
from app.entitlements import require_crucible_module

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/crucible", tags=["crucible"])

#: Small and dedicated. Runs can only ever starve each other; the queue beyond
#: it is the durable `resolving_goal` row, which is what that row is for.
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="crucible")

#: Rows per page of the signal read. Small BECAUSE of the embeddings: each row
#: Rows per page of the metadata read. No embeddings in it, but `content` is
#: free text and 1,000 of them timed out the statement on a 15,569-signal
#: tenant — measured, after 1,000 worked fine on a 2,777-signal one. Sized for
#: the big tenant, because the small one does not care.
_PAGE = 400

#: Rows per page of the EMBEDDING read. Far smaller, because each row carries
#: ~19KB of JSON. Measured against a real 2,777-signal tenant: 250 alongside
#: the other columns timed out outright, and 100 on its own still lost a page.
_EMBED_PAGE = 50

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
        # Stage 0's question and its prefilled proposal. Without this the panel
        # can render that a run is WAITING but not what it is waiting for, so
        # the confirmation step — the one thing the user has to do — is a blank
        # box. `prioritisation` holds no raw error text; it is the run's own
        # framing, which is exactly what the reader needs.
        # Carries the Stage 0 ask AND the run plan. The plan is the thing the
        # user approves, so it has to reach the client.
        "prioritisation": row.get("prioritisation") or {},
        # The run's report AS AN EDITABLE DOCUMENT, when one has been made.
        # An id only: the body lives on `custom_artifacts` and is fetched by
        # `GET /{run_id}/document`, so a run listing does not carry N report
        # bodies over the wire (the `_LIST_COLUMNS` posture).
        "artifact_id": row.get("artifact_id"),
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
        created_by=company.user_id,
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
async def confirm(
    run_id: int,
    body: ConfirmGoal,
    company: WorkspaceContext = Depends(require_crucible_module),
):
    """I9's human gate. The ONLY path from `awaiting_confirmation` onward.

    Nothing here infers a definition and no LLM output can reach this state —
    a user typed or approved these words, and `confirm_goal` records who and
    when because `GoalDefinition` refuses to be locked without both.

    ASYNC, not a sync `def`. A sync handler runs on FastAPI's anyio worker
    thread, where `get_event_loop()` RAISES — so every confirm would 500 in
    production while passing under pytest, and line 1 of the failure would
    already have flipped the row to `running`, bricking it behind the 409.
    `routes/custom_artifacts.py` documents this exact mistake as one already
    made here: the fix for "a sync handler has no loop" is not to keep the sync
    handler.
    """
    claimed = await asyncio.to_thread(
        runs_db.claim_for_confirmation, run_id, company.company_id
    )
    if claimed is None:
        # Indistinguishable on purpose: a foreign id and an id that was never
        # issued must look the same, since a 403 is itself a disclosure.
        row = await asyncio.to_thread(runs_db.get, run_id, company.company_id)
        if not row:
            raise HTTPException(404, "Run not found")
        raise HTTPException(
            409,
            f"This run is {row.get('status')}, so there is no definition "
            f"waiting to be confirmed.",
        )

    kwargs = dict(run_id=run_id, company_id=company.company_id,
                  goal_text=claimed.get("goal_text") or "",
                  definition_text=body.definition_text,
                  confirmed_by=company.user_id)
    if "pytest" in sys.modules:
        await asyncio.to_thread(execute_run, **kwargs)
    else:
        loop = asyncio.get_running_loop()
        task = asyncio.ensure_future(
            loop.run_in_executor(_POOL, partial(execute_run, **kwargs))
        )
        _inflight.add(task)
        task.add_done_callback(_inflight.discard)
    row = await asyncio.to_thread(runs_db.get, run_id, company.company_id)
    return _public(row or claimed)


# ── The report as a document ────────────────────────────────────────────────
#
# THE RUN IS IMMUTABLE. THE REPORT IS A DOCUMENT ABOUT THE RUN.
#
# That sentence is the whole design, and everything below is it being enforced.
# A run's claim against asking a general model the same question is that it is
# reproducible: every finding traces to claim ids and source documents, and the
# same corpus gives the same ranking. An EDITED run would not be that. So the
# findings and the ledger are never written again after a run finishes, and the
# prose a user edits lives somewhere else — a `custom_artifacts` row, which
# already has a body, a version, compare-and-set concurrency and an editor.
#
# DETACHMENT IS DERIVED, NOT DECLARED. The run stores `report_body_hash`, the
# fingerprint of the body it rendered. The report is detached — edited, and no
# longer regenerated from the run — exactly when the stored body hashes to
# something else. Nothing has to remember to set a flag, which matters because
# the ordinary hand edit arrives through `PATCH /v1/custom-artifacts/{id}`, a
# route that knows nothing about Goal Analysis and should not have to learn.


def _document_payload(run: dict, artifact: dict) -> dict:
    """One report document, as the panel reads it.

    Carries the body, because the caller opened it to render it — unlike the
    run listing, which carries an id and nothing else.
    """
    from app.crucible.report import body_fingerprint

    # THE FINGERPRINT COVERS THE BODY AND NOTHING ELSE, and this line is where
    # that is decided — `link_document` stores `body_fingerprint(body_html)`,
    # so anything added here compares against a hash that never included it and
    # every report reads as edited from the moment it is created.
    #
    # The title is deliberately outside it. Renaming a document is filing, not
    # authorship: it does not change a word of the analysis, and detaching on it
    # would fire the "no longer regenerated from the run" banner at someone who
    # only tidied a name.
    body = artifact.get("body_html") or ""
    stored_hash = run.get("report_body_hash") or ""
    return {
        "run_id": run.get("id"),
        "id": artifact.get("id"),
        "kind": artifact.get("kind") or "",
        "title": artifact.get("title") or "",
        "status": artifact.get("status") or "ready",
        "version": int(artifact.get("version") or 1),
        "body_html": body,
        "updated_at": artifact.get("updated_at"),
        "updated_by": artifact.get("updated_by"),
        # FAIL TOWARDS "DETACHED" WHEN THE HASH IS MISSING. A run linked before
        # this column existed, or one whose link write half-landed, has no
        # fingerprint to compare — and the two possible mistakes are not
        # symmetric. Calling an untouched report edited costs a banner nobody
        # needed; calling an EDITED report untouched tells the reader their
        # prose is still the run's own output, which is the false claim this
        # whole mechanism exists to prevent.
        "detached": (not stored_hash) or body_fingerprint(body) != stored_hash,
    }


def _load_document(run_id: int, company_id: str) -> Optional[tuple[dict, dict]]:
    """`(run, artifact)` for a run that has a report, else None.

    Both reads are company-filtered, so a foreign id is absent on the first one
    and never reaches the second.
    """
    from app.db.custom_artifacts import get_artifact

    run = runs_db.get(run_id, company_id)
    if not run:
        return None
    artifact_id = run.get("artifact_id")
    if not artifact_id:
        return None
    artifact = get_artifact(company_id, artifact_id)
    if artifact is None:
        return None
    return run, artifact


def _render_document_html(run: dict, company_id: str) -> str:
    from app.crucible.report import render_report_html

    findings, ledger = runs_db.load_findings(run["id"], company_id)
    return render_report_html(run, findings, ledger)


@router.get("/{run_id}/document")
async def get_document(
    run_id: int,
    company: WorkspaceContext = Depends(require_crucible_module),
):
    """The run's report document, or 404 when it has none yet."""
    found = await asyncio.to_thread(_load_document, run_id, company.company_id)
    if found is None:
        raise HTTPException(404, "This analysis has no report document")
    return _document_payload(*found)


@router.post("/{run_id}/document")
async def create_document(
    run_id: int,
    company: WorkspaceContext = Depends(require_crucible_module),
):
    """Render the run to a document and link it. IDEMPOTENT.

    A second call returns the FIRST document, untouched — including when it has
    since been edited. That is not a convenience: the panel calls this to open
    the report, so a re-render on the second call would mean opening your own
    edited report is what destroys it.
    """
    found = await asyncio.to_thread(_load_document, run_id, company.company_id)
    if found is not None:
        return _document_payload(*found)

    row = await asyncio.to_thread(runs_db.get, run_id, company.company_id)
    if not row:
        # Tenant filter is in the query, so a foreign run and one that was
        # never issued are the same 404.
        raise HTTPException(404, "Run not found")
    if row.get("status") != "ready":
        raise HTTPException(
            409,
            f"This analysis is {row.get('status')}, so there is nothing to "
            f"write a report from yet.",
        )

    if row.get("artifact_id"):
        # A link pointing at a document that is not there. The FK is ON DELETE
        # SET NULL, so Postgres cannot produce this — but a half-landed link
        # write can, and leaving the pointer would make the report permanently
        # unopenable behind a 404 with no way back. Cleared so the create below
        # can claim the slot.
        logger.warning(
            "crucible: run %s points at missing artifact %s; relinking",
            run_id, row.get("artifact_id"),
        )
        await asyncio.to_thread(
            runs_db.update, run_id, company.company_id, artifact_id=None,
        )

    return await asyncio.to_thread(_create_document, row, company)


#: §6's convention statement, per metric family. "If no computation is found,
#: state the common convention you are assuming for that metric, in one
#: sentence, and let them change it."
#:
#: THIS IS AN ASSUMPTION OFFERED FOR CORRECTION, NOT AN INFERENCE. §10 forbids
#: inferring a definition and I9 forbids locking one without a human; §6
#: explicitly asks for the opposite move — say out loud what you would otherwise
#: assume silently, so the user can overwrite it in the box directly beneath.
#: The difference is whether the sentence is stated and editable, and it is
#: both: nothing here reaches `crucible_goal_definitions` unless the user leaves
#: it in their own confirmed text.
#:
#: Keyed on words that appear in the GOAL, not on anything about the company —
#: a per-company convention would be exactly the cross-customer contamination
#: README F11 bars. These are the ordinary industry readings, and each names the
#: fork it is choosing, because the fork is the part that resizes every
#: recommendation (F4: "two teams both say revenue and mean recognised versus
#: booked").
_METRIC_CONVENTIONS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("churn", "attrition"),
     "I will read churn as LOGO churn — accounts that cancel or fail to renew — "
     "rather than revenue churn, and as voluntary and involuntary together."),
    (("retention", "renewal", "nrr", "grr"),
     "I will read retention on ACCOUNTS rather than on revenue, and count an "
     "account retained if it renews at all, regardless of contraction."),
    (("revenue", "arr", "mrr", "bookings"),
     "I will read revenue as RECOGNISED rather than booked, net of refunds and "
     "credits, excluding internal and comped accounts."),
    (("activation", "onboarding", "adoption"),
     "I will read activation as an account reaching first meaningful use, not "
     "merely signing up, and count it once per account."),
    (("engagement", "active", "usage", "dau", "mau"),
     "I will read active as having taken an action in the window, not merely "
     "having logged in."),
    (("conversion", "signup", "trial"),
     "I will read conversion as the share of starts that reach the paid step "
     "within the window, counted on accounts rather than on sessions."),
)


#: WHAT THE RUN DOES WITH THE SENTENCE, once there is a sentence.
#:
#: Split out of `_method_note` when the clarification gate folded into the
#: plan. On the folded path the CONVENTION is no longer a note about the
#: definition — it IS the definition, sitting in an editable field — so
#: repeating it underneath is exactly the "multiple repetitions and LLM
#: re-explaining" the feedback asked us to cut. What still needs saying is the
#: part the sentence itself cannot: that it is taken literally, and what gets
#: read against it.
#:
#: A CONSTANT for every company and goal, deliberately: the mechanism it
#: describes does not vary, so branching on anything would imply the run
#: behaves differently when it does not.
_PROCESS_NOTE = (
    "I will work to that sentence exactly as you write it: I do not "
    "recompute it, and I do not fill in anything you leave out. The "
    "analysis then reads your documents, tickets and conversations against "
    "it, not a metric series, so it reports how much of your book each "
    "theme touches rather than a movement in this number."
)


def _convention_definition(goal_text: str) -> str:
    """The definition this run will assume when nothing else has one.

    Same table `_method_note` reads, returning the CONVENTION SENTENCE alone —
    the note wraps it in process prose, which reads as an explanation rather
    than as a definition you can edit. Empty when the goal names no metric
    family we have a convention for, and empty is load-bearing: there is then
    nothing to propose, so the run asks rather than inventing one, which is the
    inference I9 exists to forbid.
    """
    lowered = f" {(goal_text or '').lower()} "
    return next(
        (text for words, text in _METRIC_CONVENTIONS
         if any(w in lowered for w in words)),
        "",
    )


def _method_note(goal_text: str) -> str:
    """§6's one sentence: the calculation, stated and editable.

    A metric NAME is not a definition — "revenue" can be recognised or booked,
    "active" can mean logged in or took an action, and none of that is visible
    in the name while all of it resizes every recommendation (F4).

    §6's rule is to surface the company's own computation where one exists and
    otherwise to STATE THE CONVENTION being assumed. Nothing in this codebase
    reads a dbt model or a metric layer yet, so the second branch is the honest
    one — and an earlier version of this function skipped it entirely, saying
    only what the ANALYSIS reads (documents, not a metric series) while citing
    §6 and F4 as its justification. That is a true sentence answering a
    different, easier question than the one §6 poses, with §6's citation on it.

    Where the goal names no recognisable metric family there is no convention to
    state, and inventing one would be the inference §10 forbids. That case gets
    the process sentence alone, which is what it always was.
    """
    lowered = f" {(goal_text or '').lower()} "
    convention = next(
        (text for words, text in _METRIC_CONVENTIONS
         if any(w in lowered for w in words)),
        "",
    )
    process = _PROCESS_NOTE
    if not convention:
        return process
    return f"{convention} Say otherwise below and I will use your reading. {process}"


def _create_document(row: dict, company: WorkspaceContext) -> dict:
    """Render, store, link. Blocking; called from a thread."""
    from app.db.custom_artifacts import create_artifact, delete_artifact, get_artifact
    from app.crucible.report import ARTIFACT_KIND, body_fingerprint, report_title

    run_id, company_id = row["id"], company.company_id
    html = _render_document_html(row, company_id)

    # A BODY THE STORE REFUSES MUST NOT LOOK LIKE A DEAD SERVER.
    #
    # `custom_artifacts` caps a body at `MAX_BODY_CHARS` and raises
    # `BodyTooLarge`. This route did not catch it, so a large report 500'd on an
    # unhandled exception and the browser reported "Failed to fetch" — a
    # dropped connection, which reads as an outage rather than as a refused
    # write. Found on staging against a real 831-finding run that rendered to
    # 421,696 characters.
    #
    # `_findings_section` now bounds the document so this should not fire. It
    # stays because "should not" is not "cannot": a future run with longer
    # statements can still cross the line, and when it does the user is owed a
    # sentence rather than a broken tab.
    html = _body_or_413(html, run_id)

    artifact = create_artifact(
        company_id,
        kind=ARTIFACT_KIND,
        title=report_title(row),
        body_html=html,
        # NO `conversation_id`, deliberately, even though the run has one. The
        # thread-resume probe (`useThreadDocumentSync`) attaches the newest
        # document of a conversation to the panel's DOCUMENT tab on reload — so
        # stamping it here would grow a phantom Document tab beside every Goal
        # Analysis run, holding the same report the analysis tab already shows.
        # The run carries the link, and the panel finds the report through the
        # run, which is the relationship that actually exists.
        created_by=company.user_id,
    )
    # The fingerprint is of the STORED body, not the rendered string: the
    # storage layer sanitizes on write, so hashing what we sent would never
    # match what is there and every report would read as edited on creation.
    linked = runs_db.link_document(
        run_id, company_id,
        artifact_id=artifact["id"],
        body_hash=body_fingerprint(artifact.get("body_html") or ""),
    )
    if linked is None:
        # Lost the claim — a concurrent POST linked first. Delete the orphan we
        # just made (nobody has an id for it, so nothing is lost) and return
        # the winner's. Without this a double-click leaves a stray report in
        # the shared library that no run points at.
        delete_artifact(company_id, artifact["id"])
        winner = runs_db.get(run_id, company_id) or row
        existing = get_artifact(company_id, winner.get("artifact_id") or 0)
        if existing is None:
            raise HTTPException(409, "The report was being created; try again.")
        return _document_payload(winner, existing)
    return _document_payload(linked, artifact)


@router.post("/{run_id}/document/fork")
async def fork_document(
    run_id: int,
    company: WorkspaceContext = Depends(require_crucible_module),
):
    """Save a SEPARATE copy of this report as an ordinary team document.

    The other half of "edit in place". The run's own report keeps its link and
    is untouched; this is a free-standing document that will never be compared
    to the run again — no detach marker, because there is nothing to detach
    from.

    It copies whatever the report SAYS RIGHT NOW, edits included. A fork of the
    original rendering would be a different feature ("revert"), and offering it
    under this button would quietly discard the user's edits at the moment they
    asked to keep them.
    """
    found = await asyncio.to_thread(_load_document, run_id, company.company_id)
    if found is not None:
        run, artifact = found
        body, title = artifact.get("body_html") or "", artifact.get("title") or ""
    else:
        run = await asyncio.to_thread(runs_db.get, run_id, company.company_id)
        if not run:
            raise HTTPException(404, "Run not found")
        if run.get("status") != "ready":
            raise HTTPException(
                409,
                f"This analysis is {run.get('status')}, so there is nothing to "
                f"write a report from yet.",
            )
        body = await asyncio.to_thread(_render_document_html, run, company.company_id)
        from app.crucible.report import report_title

        title = report_title(run)

    return await asyncio.to_thread(_fork_document, run, title, body, company)


def _body_or_413(html: str, run_id: int) -> str:
    """The sanitized body, or a 413 that says what happened.

    SHARED BY BOTH WRITERS, which is the point. `custom_artifacts` caps a body
    at `MAX_BODY_CHARS` and raises `BodyTooLarge`; neither route caught it, so a
    large report took the worker down mid-request and the browser reported a
    dropped connection — a refused write presenting as an outage.

    The first version of this fix guarded only `_create_document` and left
    `_fork_document` eighty lines below calling `create_artifact` with the same
    unbounded rendered body. Same file, same exception, same failure. Hence one
    function rather than two try blocks: a third writer gets the behaviour by
    calling this, not by remembering to.
    """
    from app.db.custom_artifacts import BodyTooLarge, _checked_body

    try:
        return _checked_body(html)
    except BodyTooLarge as exc:
        logger.error("crucible: report for run %s exceeds the body limit: %s",
                     run_id, exc)
        raise HTTPException(
            413,
            "This run's report is too large to save as a document. The run "
            "itself is unaffected and still readable in the panel.",
        ) from exc


def _fork_document(
    run: dict, title: str, body: str, company: WorkspaceContext
) -> dict:
    from app.db.custom_artifacts import create_artifact

    body = _body_or_413(body, int(run.get("id") or 0))
    copy = create_artifact(
        company.company_id,
        # An ordinary document kind, NOT `goal_analysis`. The kind is what the
        # chat edit tool resolves its target by, and a fork is not on a run —
        # calling it a report would let `edit_goal_report` find a document with
        # no run behind it.
        kind="Goal analysis copy",
        title=f"{title} (copy)" if title else "Goal analysis (copy)",
        body_html=body,
        # STAMPED HERE, unlike the linked report. A fork is a document the user
        # deliberately created, so it belongs in the thread's library and in
        # the Document tab — which is exactly what the linked report must not
        # do (see `_create_document`).
        conversation_id=run.get("conversation_id"),
        created_by=company.user_id,
    )
    return {
        "id": copy["id"],
        "title": copy.get("title") or "",
        "kind": copy.get("kind") or "",
        "version": int(copy.get("version") or 1),
        "conversation_id": copy.get("conversation_id"),
        # A fork has no run and never had one.
        "run_id": None,
        "detached": False,
    }


class ReportEdit(BaseModel):
    instruction: str = Field(min_length=1, max_length=4000)


@router.post("/{run_id}/document/chat-edit")
async def chat_edit_document(
    run_id: int,
    body: ReportEdit,
    company: WorkspaceContext = Depends(require_crucible_module),
):
    """Apply a free-form chat instruction to this run's report.

    THE TARGET IS THE URL'S RUN, NOT AN ARGUMENT. The client names the report
    the user has open; nothing in the request body can redirect the write, and
    the writer re-derives the artifact from the run rather than trusting an id
    it was handed. Same rule as `edit_prd`, same reason: a model — or a
    prompt-injected instruction inside a customer's own documents — must not be
    able to edit a document the user is not looking at.

    Live on call, no confirm gate (retired for PRDs in e05577dc; keeping the
    two surfaces aligned is worth more here than a second click).
    """
    from app.goal_report_chat_edit import apply_report_edit_scoped

    found = await asyncio.to_thread(_load_document, run_id, company.company_id)
    if found is None:
        raise HTTPException(404, "This analysis has no report document")
    run, artifact = found
    result = await asyncio.to_thread(
        apply_report_edit_scoped, artifact["id"], body.instruction, company
    )
    fresh = await asyncio.to_thread(runs_db.get, run_id, company.company_id) or run
    return {
        "document": _document_payload(fresh, result["artifact"]),
        "sections_changed": result["sections_changed"],
        "summary": result["summary"],
    }


class ApprovePlan(BaseModel):
    """What the user changed about the plan before saying go."""
    #: THE DEFINITION, ADOPTED BY THIS CLICK. The plan renders it as an
    #: editable proposal, so approving is the moment a person agrees to those
    #: words — I9's human act, now on the same screen as the method rather
    #: than one gate earlier. Optional because a client that sends nothing is
    #: agreeing to the proposal exactly as it was shown, which the server can
    #: read straight off the stored plan.
    definition_text: Optional[Annotated[str, StringConstraints(max_length=4_000)]] = None
    #: ── WHAT THE RUN CANNOT KNOW, ANSWERED AT THE GATE. ────────────────
    #: All optional: a reader who skips them gets exactly the document they got
    #: before, with the corresponding sections stating what is missing rather
    #: than guessing at it.
    #: Bounded because a value typed into a box reaches arithmetic — a negative
    #: or absurd account value would render a headline nobody could defend.
    account_value: Optional[Annotated[float, Field(ge=0, le=100_000_000)]] = None
    decision_owner: Optional[Annotated[str, StringConstraints(max_length=120)]] = None
    needed_by: Optional[Annotated[str, StringConstraints(max_length=120)]] = None
    excluded_sources: list[str] = Field(default_factory=list, max_length=12)
    # `max_length` on a `list[str]` bounds the LIST, not the strings in it —
    # ten 40,000-char hypotheses render past the document limit with only a
    # dozen findings. The item constraint is what actually bounds the payload.
    hypotheses: list[Annotated[str, StringConstraints(max_length=2_000)]] = Field(
        default_factory=list, max_length=10
    )


@router.post("/{run_id}/approve")
async def approve(
    run_id: int,
    body: ApprovePlan,
    company: WorkspaceContext = Depends(require_crucible_module),
):
    """The SECOND gate. The plan said what would be read and what could not be
    answered; this is the user saying go, having seen both.

    THIS NOW CARRIES THE DEFINITION TOO, and the comment it replaces argued
    the opposite: that confirming a definition and approving a method are
    different decisions, and collapsing them is how a user agrees to something
    they never saw. The second half of that is still exactly right and is why
    the plan renders the definition as an editable field rather than swallowing
    it — what changed is that a SEPARATE SCREEN turned out to be the thing
    producing agreement-without-seeing. Asked cold, with no plan yet on screen
    to give the question meaning, one reader answered it "that is accurate" and
    that sentence became the definition of the run.

    So both decisions are made here, both are visible here, and neither is
    inferred: `/confirm` still exists for runs already parked at the old gate.
    """
    claimed = await asyncio.to_thread(
        runs_db.claim_for_approval, run_id, company.company_id
    )
    if claimed is None:
        row = await asyncio.to_thread(runs_db.get, run_id, company.company_id)
        if not row:
            raise HTTPException(404, "Run not found")
        raise HTTPException(
            409,
            f"This run is {row.get('status')}, so there is no plan waiting to "
            f"be approved.",
        )

    meta = _meta_of(run_id, company.company_id)
    # THE EDIT WINS, THE PROPOSAL IS THE FALLBACK. A body that carries text is
    # the reader having changed the proposed definition in place; a body that
    # carries none is them agreeing to it as shown. Blank-after-strip is
    # treated as "no change" rather than as an empty definition — `confirm_goal`
    # refuses to lock nothing, and a 500 there would strand a claimed run.
    # THE ANSWERS TO WHAT THE RUN COULD NOT KNOW. Folded into the stored plan
    # below, beside the definition, because the report renders from that plan
    # and an answer that never reaches it is an answer nobody gave.
    answered = {
        "account_value": body.account_value,
        "decision_owner": (body.decision_owner or "").strip(),
        "needed_by": (body.needed_by or "").strip(),
    }
    edited = (body.definition_text or "").strip()
    definition_text = edited or (meta.get("plan") or {}).get("definition_text") or ""

    kwargs = dict(
        run_id=run_id, company_id=company.company_id,
        goal_text=claimed.get("goal_text") or "",
        definition_text=definition_text,
        confirmed_by=company.user_id,
        approved=True,
        answered=answered,
        excluded_sources=tuple(body.excluded_sources),
        hypotheses=tuple(body.hypotheses),
    )
    if "pytest" in sys.modules:
        await asyncio.to_thread(execute_run, **kwargs)
    else:
        loop = asyncio.get_running_loop()
        task = asyncio.ensure_future(
            loop.run_in_executor(_POOL, partial(execute_run, **kwargs))
        )
        _inflight.add(task)
        task.add_done_callback(_inflight.discard)
    row = await asyncio.to_thread(runs_db.get, run_id, company.company_id)
    return _public(row or claimed)


def execute_run(
    *,
    run_id: int,
    company_id: str,
    goal_text: str,
    definition_text: Optional[str] = None,
    confirmed_by: Optional[str] = None,
    approved: bool = False,
    excluded_sources: tuple[str, ...] = (),
    hypotheses: tuple[str, ...] = (),
    answered: Optional[dict] = None,
) -> None:
    """The whole deterministic pipeline. TOTAL — never raises to its caller.

    A run that dies with an unhandled exception leaves a row spinning until the
    sweep catches it, which is a worse user experience than a failure that says
    what happened.
    """
    now = datetime.now(timezone.utc)
    # WHOSE WORDS THESE ARE. A definition arriving already set came from
    # `/confirm` — a person typed it at the old gate — so it is adopted before
    # this function starts. One resolved below on the folded path is a
    # proposal until `/approve` carries it back, and these two flags are what
    # keep those cases distinguishable everywhere downstream.
    definition_source = "your own words"
    definition_adopted = definition_text is not None
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
            # ── FOLD THE CLARIFICATION INTO THE PLAN. ───────────────────────
            #
            # Decided BEFORE anything is written, and the order is the fix for
            # a race rather than a tidiness preference. The first version of
            # this wrote `awaiting_confirmation` and then fell through to build
            # the plan — so for the second or so that the inventory query took,
            # the row advertised a gate that this run was never going to stop
            # at. The chat polls for either gate; a poll landing in that window
            # rendered the definition card for a run that had already moved on,
            # which is precisely the screen this change exists to remove.
            #
            # Why it stopped being its own step: asking "what does revenue mean
            # to you?" cold, before the reader has seen a single thing the run
            # intends to do, is a question with no context attached — and the
            # answers showed it. One run went out with its definition recorded
            # as the literal words "that is accurate", because that is what the
            # reader typed at a prompt that was not, to them, asking for a
            # definition. A second screen later they were asked to approve a
            # plan built on it.
            #
            # I9 IS NOT WEAKENED, AND THIS IS THE PART TO CHECK. The rule is
            # that a definition is adopted or elicited, never inferred — it has
            # never been a rule about how many screens that takes. What it
            # requires is that a person sees the words and says yes to them.
            # They still do: the proposal is rendered inside the plan, editable
            # in place, and `/approve` locks whatever text comes back from that
            # field. `definition_adopted=False` says in the row itself that
            # nobody has agreed yet.
            #
            # TWO CASES STILL STOP AT THEIR OWN GATE, because in neither is
            # there an honest proposal to put in front of anyone:
            #   - nothing to propose (no KPI-tree definition, and the goal
            #     names no metric family we hold a convention for) — inventing
            #     one is the inference the invariant forbids;
            #   - two authoritative systems that DISAGREE, where the conflict
            #     is worth more than either answer and picking one silently is
            #     the failure. That is a decision, not a confirmation, and it
            #     gets its own screen.
            prefill = proposed or _convention_definition(goal_text)
            folds = bool(prefill) and not resolution.conflicts

            if folds:
                _remember(run_id, resolution)
                definition_text = prefill
                definition_source = (
                    "your own metric tree" if proposed
                    else "the usual reading of this metric"
                )
            else:
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
                        # §6, IN THE SAME STEP. "If no computation is found, state
                        # the common convention you are assuming for that metric,
                        # in one sentence, and let them change it." Identity without
                        # method is README F4's "half of this that gets missed" —
                        # two teams can point at the same metric name and mean
                        # recognised versus booked, and none of that is visible in
                        # the name while all of it resizes every recommendation.
                        "method_note": _method_note(goal_text),
                    },
                )
                _remember(run_id, resolution)
                return


        # NOTHING IS LOCKED ON THE WAY TO THE PLAN. This block used to run
        # here unconditionally, which was right while the definition arrived
        # already confirmed from gate one. It is not right now: on the folded
        # path `definition_text` is a PROPOSAL, and locking it here would
        # record a person as having confirmed words they had not yet been
        # shown — with their user id on it. The lock belongs to the act that
        # actually is the agreement, which is approving the plan.
        if approved:
            pending = _pending(run_id) or _bare_definition(company_id, goal_text)
            locked = confirm_goal(
                pending, user_id=confirmed_by or "", at=now,
                definition_text=definition_text,
            )
            definition_row_id = runs_db.save_definition(company_id, locked)
            runs_db.update(run_id, company_id, goal_definition_id=definition_row_id)

        # ── Stage 1. SAY WHAT WILL BE DONE, then stop for approval. ─────────
        #
        # A run reads the whole corpus and takes minutes, and until now the
        # first thing a user learned about its limits was the coverage notes at
        # the bottom of the finished output — after the wait, phrased as an
        # apology. The same facts BEFORE the run are a decision: connect the
        # missing source, drop one, or accept a qualitative answer knowingly.
        #
        # Inventory only, no content read, so this returns in about a second.
        if not approved:
            plan = build_plan(
                company_id=company_id,
                goal_text=goal_text,
                definition_text=definition_text,
                currency="accounts",
                definition_source=definition_source,
                definition_note=_PROCESS_NOTE,
                definition_adopted=definition_adopted,
            )
            meta = dict(_meta_of(run_id, company_id))
            meta["plan"] = plan.to_json()
            runs_db.update(run_id, company_id, status="awaiting_approval",
                           prioritisation=meta)
            return

        # THE USER'S ANSWER TO THE PLAN IS PART OF THE RECORD. `build_plan`
        # ran before they saw it, so the stored plan still describes the run
        # they were OFFERED, not the one they approved. Left alone, the
        # finished report lists a source the user dropped among the ones it
        # read, and loses the hypotheses they typed entirely — a report that
        # misstates its own inputs is worse than one that shows fewer.
        # AND THE DEFINITION, WHICH WAS THE ONE ANSWER THAT NEVER LANDED.
        #
        # The report renders `plan.definition_text` under the words "You
        # confirmed this goal means, IN YOUR OWN WORDS". Approve folded the
        # dropped sources and the hypotheses into the stored plan and left the
        # definition at whatever was PROPOSED — so a reader who corrected the
        # proposal got their correction locked (the definition ROW was always
        # right) and then read the document attributing the sentence they had
        # just rejected to themselves. A false attribution is worse than the
        # clumsy definition this gate exists to prevent, and a test that checked
        # the write and not the read is how it survived.
        #
        # UNCONDITIONAL NOW. This block only runs on approve, and every approve
        # settles the same three things — what was dropped, what was believed,
        # and what the metric means. The old gate asked whether sources or
        # hypotheses had changed, so a reader who edited ONLY the definition
        # skipped it entirely; a predicate for "did the definition change"
        # would have fixed that case and left a branch whose two sides write the
        # same bytes, since an unedited approve carries the proposal forward
        # verbatim anyway.
        if True:
            meta = dict(_meta_of(run_id, company_id))
            plan_json = dict(meta.get("plan") or {})
            if plan_json:
                # The words the run actually worked from — the reader's edit
                # when they made one, the proposal verbatim when they did not.
                # The gate's answers, recorded beside the definition they sit
                # next to on screen. Empty ones are not written: a blank field
                # means "I did not answer", never "the value is nothing".
                for key, val in (answered or {}).items():
                    if val not in (None, ""):
                        plan_json[key] = val
                plan_json["definition_text"] = definition_text
                # AND IT IS ADOPTED, which is the whole meaning of this click.
                # `definition_adopted` was written False at plan time to say
                # nobody had agreed yet; leaving it False past the agreement
                # would make the record contradict the act it exists to record.
                plan_json["definition_adopted"] = True
                kept = [
                    src for src in (plan_json.get("sources") or [])
                    if src.get("source_type") not in excluded_sources
                ]
                plan_json["sources"] = kept
                plan_json["total_signals"] = sum(
                    src.get("signal_count") or 0 for src in kept
                )
                plan_json["excluded_sources"] = list(excluded_sources)
                plan_json["hypotheses"] = list(hypotheses)
                # AND THE GAPS AND PROMISES, which are DERIVED from the kept
                # set and were being left at their pre-exclusion values. A
                # reader who unticked analytics and revenue still got "your
                # analytics/revenue data is connected and will be read" in the
                # same document that said those sources were excluded — and
                # lost the gap that had just become true ("nothing connected
                # here carries numbers") along with the remedy that would close
                # it, handed "no action needed from you" instead.
                #
                # Re-derived through the same pure function the plan gate uses,
                # so the two cannot drift.
                from app.crucible.plan import (
                    SourceInventory, derive_gaps_and_promises,
                )
                gaps, produce = derive_gaps_and_promises(
                    [
                        SourceInventory(
                            source_type=src.get("source_type") or "",
                            signal_count=int(src.get("signal_count") or 0),
                            label=src.get("label") or "",
                            witnesses=src.get("witnesses") or "",
                        )
                        for src in kept
                    ],
                    tuple(hypotheses),
                )
                plan_json["cannot_answer"] = [
                    {"question": g.question, "because": g.because,
                     "remedy": g.remedy}
                    for g in gaps
                ]
                plan_json["will_produce"] = list(produce)
                meta["plan"] = plan_json
                runs_db.update(run_id, company_id, prioritisation=meta)

        runs_db.update(run_id, company_id, status="running",
                       started_at=now.isoformat())
        runs_db.heartbeat(run_id, company_id)

        # ── Stage 4. Project the corpus into claims, then group them. ───────
        signals = _load_signals(company_id)
        if excluded_sources:
            # The user dropped a source at the plan step. Honoured here rather
            # than at the query, so the run can still report how much it left
            # out — a silently narrower corpus is the thing coverage notes
            # exist to prevent.
            dropped = [r for r in signals if r.get("source_type") in excluded_sources]
            signals = [r for r in signals
                       if r.get("source_type") not in excluded_sources]
            logger.info("crucible: user excluded %d signals from %s",
                        len(dropped), ", ".join(sorted(excluded_sources)))
        claims, stats = project_signals(signals)
        # SOURCES OF THE CLAIMS, not of the rows. Counting `signals` says "read
        # from 4 sources" on a tenant whose entire `docs` corpus was retired —
        # so a PM defending the ranking believes their documents are in it when
        # no claim came from one.
        # BOTH DROP REASONS, separately. `seen - projected` is retired PLUS
        # undated, and attributing all of it to a missing date is the same
        # "confidently stated false reason" the coverage note already avoids —
        # the two would print different numbers for one fact on one run.
        _progress(
            run_id, company_id, step="grouping",
            signals_read=stats.get("seen") or 0,
            claims=stats.get("projected") or 0,
            retired=stats.get("retired") or 0,
            undated=stats.get("no_timestamp") or 0,
            sources=len({c.source_id for c in claims if c.source_id}),
        )

        # GROUPING: the graph's own themes first, embeddings only for whatever
        # it left unthemed.
        #
        # The KG already joins signals to theme entities labelled by the
        # extractor — "Parts request dashboard", "Sales Pipeline". Deriving our
        # own from embeddings re-computed, worse, semantics the graph had
        # already stored, and produced a private taxonomy labelled with
        # truncated sentences that lined up with nothing else in the product.
        # Measured on the test tenant: 165 findings and 3 of them sizeable
        # became 238 and 20 by reading the graph instead.
        theme_map = load_theme_map(company_id)
        claims, unthemed_idx, cluster_stats = assign_themes(claims, theme_map)
        logger.info(
            "crucible: graph themed %s of %s claims for %s",
            cluster_stats.get("themed"), len(claims), company_id,
        )

        # Only the leftovers pay for the embedding read, which is the slowest
        # part of a run — so this is a latency win as well as a quality one.
        unthemed_ids = {str(claims[i].id) for i in unthemed_idx}
        if unthemed_ids:
            embeddings = _load_embeddings(company_id, unthemed_ids)
            if not embeddings:
                logger.warning(
                    "crucible: %d unthemed signals and no embeddings for %s",
                    len(unthemed_ids), company_id,
                )
            # ONLY the unthemed ones. Passing the whole list would re-stamp
            # claims the graph already placed, throwing away the better label.
            leftovers = [claims[i] for i in unthemed_idx]
            regrouped, embed_stats = assign_clusters(leftovers, embeddings)
            for slot, claim in zip(unthemed_idx, regrouped):
                claims[slot] = claim
            cluster_stats.update(embed_stats)

        runs_db.update(run_id, company_id, claim_count=len(claims))
        # EXACT COUNTS ONLY. `themed`/`unthemed` are measured; the number of
        # GROUPS is not known until `build_findings` clusters, so it is not
        # reported here rather than estimated and silently corrected later — a
        # narration that revises its own numbers teaches a reader to distrust
        # all of them.
        # CLAIM counts, named as claim counts. `assign_themes` returns
        # `themed + unthemed == len(claims)`, so rendering them as the parts of
        # a THEME count invites an arithmetic that can never hold — the same
        # unit error this feature already guards at the ungroupable row.
        _progress(
            run_id, company_id, step="analysing",
            claims_themed=cluster_stats.get("themed") or 0,
            claims_unthemed=cluster_stats.get("unthemed") or 0,
        )

        if not claims:
            runs_db.fail(run_id, company_id, code="no_evidence",
                         detail="no signals in this company's knowledge graph")
            return

        # ── Stages 5–8. Findings, verified and scored. ──────────────────────
        ingest_clock = _dates_are_ingest_clock(signals)
        result = build_findings(claims, currency="accounts", now=now,
                                dates_are_ingest_clock=ingest_clock)
        # CAPTURED BEFORE THE MERGE, and this is not a style preference.
        # `assign_clusters` returns its OWN `"clusters"` key counting only the
        # groups formed among the graph-unthemed leftovers, and the merge below
        # lands it on top of `build_findings`' total. Read afterwards, the
        # funnel's headline becomes the leftover count — smaller than numbers
        # printed beneath it, and 0 outright on a tenant whose embeddings are
        # unusable. Worse, `ungroupable` can ONLY be produced by
        # `assign_clusters`, so the wrong headline and the ungroupable row are
        # exactly co-incident: whenever the panel shows one, the other is wrong.
        total_groups = result.stats.get("clusters") or 0
        # READ BEFORE THE MERGE TOO, for the same reason and not by analogy:
        # `assign_clusters` is the function that CREATES ungroupable claims, so
        # it is the most likely future source of an `ungroupable_groups` key of
        # its own. Read after the merge, that key would clobber the pipeline's
        # total and put the headline back on the leftover count — on precisely
        # the no-embedding tenants this feature exists to narrate.
        ungroupable_groups = result.stats.get("ungroupable_groups") or 0
        # Namespaced on the way in so the collision cannot come back.
        result.stats.update({
            ("embed_clusters" if k == "clusters" else k): v
            for k, v in cluster_stats.items()
        })
        runs_db.heartbeat(run_id, company_id)
        # THE FUNNEL. It is also rendered after the run finishes — `progress` is
        # durable in `prioritisation` and the ready view reads it — because the
        # drop rows ARE the feature and the window between this write and
        # `status="ready"` is about a second against a 3s poll, so a reader who
        # only saw it live would usually see nothing at all.
        # TWO NUMBERS, BECAUSE THEY ARE TWO THINGS. `_cluster` gives every
        # ungroupable claim its OWN cluster key, so `total_groups` counts one
        # pseudo-group per claim we could not embed. It is the right number for
        # the balance identity and the WRONG one to call a theme: on a tenant
        # with no usable embeddings it would render "Grouped into 2,410 themes"
        # directly above "2,410 claims never grouped at all" — one screen
        # asserting both. `themes` is what a reader means by a theme, and
        # `groups == themes + ungroupable` keeps the funnel checkable.
        _progress(
            run_id, company_id, step="done",
            groups=total_groups,
            themes=total_groups - ungroupable_groups,
            # PUBLISHED, because `themes` is derived from THIS and not from
            # `dropped.ungroupable`. Without it an auditor checking the
            # identity the `groups` field exists for computes
            # `themes + dropped.ungroupable` and gets the wrong total in
            # exactly the case the group count was introduced to handle.
            ungroupable_groups=ungroupable_groups,
            findings=len(result.findings),
            conflicts=result.stats.get("conflicts") or 0,
            deep=result.deep_count,
            dropped=result.stats.get("dropped") or {},
            # NOT a zero. When the corpus is dated by ingest the echo rule
            # never ran, and rendering "0 dropped" would claim a check passed
            # that could not see. The panel reads this and says so.
            echo_check_skipped=bool(result.stats.get("echo_check_skipped")),
        )

        rows = []
        for rank, (finding, impact, confidence) in enumerate(zip(
            result.findings, result.impacts, result.confidences
        )):
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
                # Everything is stored; only the leading few are presented as
                # analysed in depth. `deep_cap` used to be accepted and then
                # ignored, so every finding claimed the same standing.
                "tier": "deep" if rank < result.deep_count else "shallow",
            })

        ledger = [{
            "label": r.label, "reason": r.reason,
            "stopped_at_stage": r.stopped_at, "claim_ids": list(r.claim_ids),
        } for r in result.rejected]

        runs_db.save_findings(run_id, company_id, rows, ledger)
        # ENRICHMENT IS COMING, AND THE CLIENT HAS TO BE TOLD SO.
        #
        # `GoalAnalysisTab`'s poller treats "ready" as TERMINAL, so publishing
        # the report first — which is what stops the reader waiting on four
        # model calls — also stops the client listening before the gate and the
        # recommendations land. They were written to a row nobody read again:
        # the analysis appeared, and the suggestions never did.
        #
        # This flag is the whole handshake. It goes up BEFORE `ready` so there
        # is no window where the panel can see a ready run without knowing more
        # is coming, and it comes down in the same write that publishes the
        # results — so "pending" is never left true by a path that finished.
        meta = dict(_meta_of(run_id, company_id))
        meta["enrichment_pending"] = True
        runs_db.update(run_id, company_id, prioritisation=meta)

        # READY THE MOMENT THE ANALYSIS EXISTS. The panel polls on this, so
        # leaving it until after the enrichment kept the reader on the spinner
        # for the whole of it — which is what actually happened on staging.
        runs_db.update(
            run_id, company_id, status="ready",
            finished_at=datetime.now(timezone.utc).isoformat(),
            coverage_notes=_coverage_notes(stats, result.stats),
        )

        # ── THE REPORT IS PUBLISHED BEFORE ANYTHING IS ASKED OF A MODEL. ────
        #
        # The gate and the suggestions used to run HERE, above the save, and
        # that was the mistake: everything the deterministic pipeline computed
        # in seconds sat unsaved and invisible behind four sequential model
        # calls. On staging a 149-finding run hung for thirteen minutes past its
        # last narration line, showing nothing, with no error to show — because
        # there was no error. Both layers "failed open" on exceptions and
        # neither had any notion of TIME, which is the failure mode that
        # actually bit.
        #
        # Saved first, enriched second. A reader gets the analysis as soon as it
        # exists; the selection and the suggestions land when they land, and if
        # they never land the document is exactly what it was before either
        # feature existed.
        # ── WHICH OF THEM BEAR ON THE GOAL THAT WAS ASKED. ─────────────
        #
        # The ranking orders by how many accounts mention a theme, so a run for
        # "grow revenue by 5%" led with three descriptions of the company's own
        # product: what gets mentioned most on a sales call is the vendor's own
        # demo. The report conceded the gap in as many words — "nothing here was
        # filtered or ranked by it" — and this is that filter.
        #
        # NOTHING IS DROPPED FROM THE ROW SET. Every finding is still stored and
        # still rendered; a set-aside one moves to an appendix carrying the
        # reason. Storing only the survivors would destroy the record a wrong
        # verdict has to be recoverable from.
        #
        # BY RANK, because the stored rows carry no id and the renderer reads
        # them back positionally.
        set_aside_by_rank: list = [None] * len(result.findings)
        try:
            from app.crucible.relevance import judge_relevance, partition

            verdicts = judge_relevance(
                enterprise_id=company_id,
                goal_text=goal_text,
                definition_text=definition_text,
                findings=result.findings,
            )
            _, aside = partition(result.findings, verdicts)
            reason_of = {f.id: reason for f, reason in aside}
            set_aside_by_rank = [
                reason_of.get(f.id) for f in result.findings
            ]
        except Exception:  # noqa: BLE001 — a gate that failed keeps everything
            logger.exception("crucible: relevance gate skipped for run %s", run_id)

        # AND THE SUGGESTIONS GO TO THE ONES THAT SURVIVED IT. Recommending an
        # action for a theme the gate just judged irrelevant would spend the
        # reader's attention on the thing they were told to ignore.
        relevant = [
            f for f, reason in zip(result.findings, set_aside_by_rank)
            if reason is None
        ]

        # ── WHAT TO DO ABOUT EACH OF THEM. ─────────────────────────────
        #
        # AFTER the ranking, and that ordering is the invariant rather than a
        # detail. `result.findings` is already sorted and every score is already
        # frozen; nothing below is fed back into either. I2 says no LLM returns
        # a score, a rank or a decision, and it still holds — this returns prose
        # to hang beside a decision the engine already made on its own.
        #
        # TOTAL, like everything else on this path: a suggestion layer that
        # failed must not cost a reader the findings that succeeded.
        recs = {}
        try:
            from app.crucible.recommend import build_recommendations

            recs = build_recommendations(
                enterprise_id=company_id,
                goal_text=goal_text,
                definition_text=definition_text,
                findings=relevant,
                claims=claims,
            )
        except Exception:  # noqa: BLE001
            logger.exception("crucible: recommendations skipped for run %s", run_id)

        # CARRIED IN THE RUN'S OWN JSON, not in new columns on
        # `crucible_findings`. Adding columns means a migration against the
        # shared Supabase, which is a production change and not one to make
        # without being asked; the meta blob is already where this run's plan
        # lives and costs nothing to extend.
        meta = dict(_meta_of(run_id, company_id))
        meta["findings_extra"] = {
            f.id: {
                "label": f.label,
                "example": f.example,
                # RICE's Impact term is read from the kinds of claim behind a
                # finding, so the renderer needs them. Carried here for the same
                # reason as the label: adding a column to `crucible_findings`
                # means a migration against the shared Supabase.
                "claim_types": sorted(set(f.confidence_inputs.claim_types)),
                **({"recommendation": {
                    "action": recs[f.id].action, "because": recs[f.id].because,
                }} if f.id in recs else {}),
            }
            for f in result.findings
        }
        # Keyed by RANK as well, because the stored finding rows carry no id —
        # the renderer reads them back positionally.
        # THE FUNNEL, SAID IN NUMBERS THE RENDERER CAN QUOTE. How many were
        # considered and how many bear on the goal is the first thing Apurva's
        # reference memo states, and it is the thing a filtered list has to
        # disclose or it reads as the whole picture.
        # DOWN IN THE SAME WRITE THAT PUBLISHES THE RESULTS. Clearing it
        # separately leaves a window where the panel has stopped polling and
        # the verdicts are not there yet — the exact bug this flag exists to
        # close, one write narrower.
        meta["enrichment_pending"] = False
        meta["set_aside_by_rank"] = list(set_aside_by_rank)
        meta["findings_extra_by_rank"] = [
            meta["findings_extra"][f.id] for f in result.findings
        ]
        runs_db.update(run_id, company_id, prioritisation=meta)

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


#: Above this share of signals whose `valid_at` is just their `created_at`,
#: the corpus is dated by the ingest clock rather than by when anything
#: happened, and every date-based test is measuring our own backfill.
_INGEST_CLOCK_SHARE = 0.6

#: How close `valid_at` and `created_at` must be to count as the same moment.
#: NOT an exact match: `valid_at` is stamped in Python when the Signal object
#: is built and `created_at` by the database on insert, with an embedding call
#: in between, so identical-in-intent timestamps routinely differ by seconds.
#: An exact second-prefix compare would miss the very pattern it looks for on
#: any tenant whose ingest is slightly slower than this one's.
_INGEST_CLOCK_TOLERANCE_S = 120.0


def _dates_are_ingest_clock(signals: list[dict]) -> bool:
    """Is this corpus dated by when we READ it rather than when it happened?

    `valid_at` defaults to now() at ingest and most pullers never set it, so a
    backfill gives thousands of signals the same few timestamps. Detected
    rather than assumed, because a tenant whose sources DO carry real dates
    should still get the full checks.
    """
    if not signals:
        return False
    same = 0
    for row in signals:
        gap = _seconds_between(row.get("valid_at"), row.get("created_at"))
        if gap is not None and gap <= _INGEST_CLOCK_TOLERANCE_S:
            same += 1
    return (same / len(signals)) >= _INGEST_CLOCK_SHARE


def _seconds_between(a, b) -> Optional[float]:
    """Absolute gap in seconds, or None if either side is unreadable."""
    parsed = []
    for value in (a, b):
        if not value:
            return None
        text = str(value).replace("Z", "+00:00")
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        parsed.append(moment)
    return abs((parsed[0] - parsed[1]).total_seconds())


def _meta_of(run_id: int, company_id: str) -> dict:
    """The run's meta blob. `prioritisation` is the run's own framing — the
    Stage 0 ask, and now the plan — so it is read-modify-written rather than
    replaced, or approving a plan would erase the question that produced it."""
    row = runs_db.get(run_id, company_id) or {}
    meta = row.get("prioritisation") or {}
    if isinstance(meta, str):
        import json

        try:
            meta = json.loads(meta)
        except Exception:  # noqa: BLE001
            meta = {}
    return meta if isinstance(meta, dict) else {}


def _progress(run_id: int, company_id: str, **fields) -> None:
    """Publish what the run has decided SO FAR, for the panel to render.

    WHY A RUN NARRATES ITSELF. Until now `running` was one state and the panel
    showed "Reading N claims…" for minutes, so the first thing a user learned
    about how the answer was reached was the finished report. But this pipeline
    is deterministic and every number in its funnel is already computed — the
    only reason they were invisible is that nothing wrote them down. A reader
    who watched 1,744 groups become 168 findings knows what the ranking IS; a
    reader handed 168 findings has to take them on faith.

    RIDES IN `prioritisation`, so this needs no migration. Read-modify-written
    for the same reason `_meta_of` exists: the blob already holds Stage 0's ask
    and the approved plan, and replacing it would erase the plan the report has
    to reprint.

    TOTAL, like its caller. A run that produced real findings must not fail
    because a progress write did — the narration is display, and display never
    outranks the answer.
    """
    try:
        meta = dict(_meta_of(run_id, company_id))
        progress = dict(meta.get("progress") or {})
        progress.update(fields)
        meta["progress"] = progress
        runs_db.update(run_id, company_id, prioritisation=meta)
    except Exception:  # noqa: BLE001 — see the docstring; display only.
        logger.warning("crucible: could not write progress for run %s", run_id)


def _coverage_notes(claim_stats: dict, pipeline_stats: dict) -> list[dict]:
    """Every degradation renders. A quietly thinner run is indistinguishable
    from a complete one, which is worse than the failure it replaced."""
    notes = []
    # SUPERSEDED EVIDENCE, WHICH NOTHING WAS SAYING. `project_signals` counts
    # two independent drop reasons — `retired` and `no_timestamp` — and only
    # the second one was ever rendered. A corpus that is mostly superseded
    # therefore read as fully read: "What was read: 49 signals across 1 source"
    # over a run whose findings rested on 4, with the section whose entire
    # purpose is disclosing degradation staying silent about the largest one.
    # The narration in the same panel knew and printed it, so the panel
    # contradicted itself.
    if claim_stats.get("retired"):
        notes.append({
            "reason": "superseded evidence",
            "actual": f"{claim_stats['retired']} of {claim_stats['seen']} "
                      f"signals have been superseded by a later version and "
                      f"were not read",
        })
    if claim_stats.get("no_timestamp"):
        notes.append({
            "reason": "undated evidence",
            "actual": f"{claim_stats['no_timestamp']} of {claim_stats['seen']} "
                      f"signals carried no usable date and were not read",
        })
    degenerate = pipeline_stats.get("degenerate") or 0
    embedded = pipeline_stats.get("embedded") or 0
    if degenerate:
        # The all-zero-vector case. Without this the run says `ready`, reports
        # every claim as a lone anecdote, and looks like a business with no
        # patterns in it rather than like a missing API key.
        notes.append({
            "reason": "some evidence could not be grouped",
            "actual": f"{degenerate} of {degenerate + embedded} signals carry "
                      f"no usable embedding, so they were read but never "
                      f"grouped with anything",
        })
    unattributed = pipeline_stats.get("claims_without_artifact") or 0
    total_claims = pipeline_stats.get("claims") or 0
    if total_claims and unattributed == total_claims:
        notes.append({
            "reason": "evidence carries no source document",
            "actual": "no signal records which document it came from, so the "
                      "check for a finding resting on a single conversation "
                      "could not run",
        })
    if pipeline_stats.get("echo_check_skipped"):
        notes.append({
            "reason": "evidence is dated by ingest, not by when it happened",
            "actual": "most signals carry the timestamp we read them at, so "
                      "the check for one conversation echoing through the "
                      "corpus could not run and nothing here is weighted by "
                      "recency",
        })
    total = pipeline_stats.get("findings") or 0
    sizeable = pipeline_stats.get("sizeable") or 0
    if total and sizeable < total:
        # Fires on PARTIAL sizing too, not only on none of it. A run where
        # three of 168 findings carry a number and the rest do not is exactly
        # the run where a reader assumes the other 165 are small.
        notes.append({
            "reason": "most findings could not be sized",
            "actual": f"{total - sizeable} of {total} findings name no account, "
                      f"so they are unsized rather than small — a missing "
                      f"number here is not a zero",
        })
    return notes


#: A page that times out is retried at this size before the run gives up. A
#: statement timeout is about how much work ONE query does, so the answer to
#: one is a smaller query, not a failed run.
_PAGE_RETRY = 100


def _signal_page(client, company_id: str, page: int) -> list[dict]:
    """One page of signal metadata, retried smaller if the statement times out.

    MEASURED, NOT GUESSED: on a 3,364-signal staging tenant three consecutive
    runs went `ready`, `failed`, `failed`, all with Postgres 57014 "canceling
    statement due to statement timeout". `_load_embeddings` already survives
    this — it catches per page and degrades the run to ungrouped, and says so
    in a coverage note. This loader had no such guard, so one slow page killed
    the whole run and the reader got "Something went wrong on our side partway
    through this run" for a corpus that was merely large.

    IT MUST NOT SILENTLY SHRINK THE CORPUS. Swallowing the failure the way the
    embedding loader does would hand back a partial book with nothing saying
    so — the one thing every coverage note exists to prevent. So a timeout is
    retried in smaller slices and, if those fail too, it RAISES: a run that
    cannot read its evidence has to fail loudly, not quietly read less.
    """
    cols = (
        "id,kind,source_type,content,properties,provenance,"
        "valid_at,created_at,source_id"
    )

    def _fetch(size: int, offset: int) -> list[dict]:
        return (
            client.table("kg_signal")
            .select(cols)
            .eq("enterprise_id", company_id)
            # ORDER IS NOT OPTIONAL WITH RANGE. Postgres may return an
            # unordered query's rows in any order, so paging without one can
            # repeat a row on page 2 and never return another — a run would
            # read a slightly different corpus each time and stop being
            # reproducible, which is the whole claim this engine makes.
            .order("id")
            .range(offset, offset + size - 1)
            .execute()
        ).data or []

    try:
        return _fetch(_PAGE, page * _PAGE)
    except Exception:  # noqa: BLE001 — retried below, re-raised if that fails
        logger.warning(
            "crucible: signal page %d timed out for %s; retrying in slices "
            "of %d", page, company_id, _PAGE_RETRY,
        )

    out: list[dict] = []
    for offset in range(page * _PAGE, (page + 1) * _PAGE, _PAGE_RETRY):
        slice_ = _fetch(_PAGE_RETRY, offset)
        out.extend(slice_)
        # A short slice means the table ended inside this page; the outer loop
        # reads that as "stop", which is correct.
        if len(slice_) < _PAGE_RETRY:
            break
    return out


def _load_signals(company_id: str) -> list[dict]:
    """Read the company's signals, paged, metadata only.

    NO EMBEDDINGS HERE. They are 1536 floats each, and asking for them
    alongside everything else times the statement out on a real tenant — a
    250-row page was already enough. `GraphFacade.all_signals` uses
    `select("*")` and hits the same wall, which is why this exists at all.
    Explicit columns, explicit paging, and the heavy column fetched separately
    by `_load_embeddings`.
    """
    from app.db.client import require_client

    client = require_client()
    rows: list[dict] = []
    for page in range(160):
        chunk = _signal_page(client, company_id, page)
        rows.extend(chunk)
        if len(chunk) < _PAGE:
            break
    return rows


def _load_embeddings(company_id: str, ids: set[str]) -> dict:
    """Fetch the vectors on their own, in small pages.

    Separate request and a much smaller page BECAUSE of the size: each row is
    ~19KB of JSON, so this is the query that decides whether a run completes or
    dies on a statement timeout. A failure here degrades the run to ungrouped
    rather than killing it — the coverage note then says so.
    """
    from app.db.client import require_client

    client = require_client()
    out: dict = {}
    for page in range(400):
        try:
            chunk = (
                client.table("kg_signal")
                .select("id,embedding")
                .eq("enterprise_id", company_id)
                .order("id")
                .range(page * _EMBED_PAGE, page * _EMBED_PAGE + _EMBED_PAGE - 1)
                .execute()
            ).data or []
        except Exception:  # noqa: BLE001 — a slow page degrades the run, it
            # does not end it.
            #
            # SKIP AND CONTINUE, never return. Bailing out on the first failure
            # threw away every page after it: measured against a real tenant,
            # one timed-out page cost 577 of 2,777 vectors, and the run then
            # reported those signals as ungroupable when the only thing wrong
            # was one slow request.
            logger.warning(
                "crucible: embedding page %d timed out for %s; continuing",
                page, company_id,
            )
            continue
        for row in chunk:
            key = str(row.get("id"))
            if key in ids:
                vec = parse_embedding(row.get("embedding"))
                if vec is not None:
                    out[key] = vec
        if len(chunk) < _EMBED_PAGE:
            break
        if len(out) >= len(ids):
            break
    return out
