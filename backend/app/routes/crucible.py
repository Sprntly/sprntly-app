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
from app.crucible.figure_class import (
    apply_classes,
    classify_figures,
    persist_classes,
)
from app.crucible.pipeline import build_findings
from app.crucible.plan import build_plan
from app.crucible.types import GoalDefinition
from app.billing import enforce
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
    #: THE READER'S OWN SENTENCE, when the caller has one distinct from
    #: `goal_text` — chat dispatches the planner's EXTRACTED goal as
    #: `goal_text` (right for `goal.resolve` and the KPI-tree match, which
    #: want the normalised words) and this alongside it, so a count or target
    #: phrased in the reader's own words ("what are three things…") is not
    #: silently dropped by that extraction. Optional and backward-compatible:
    #: a caller with nothing to add (the direct API, an older client) omits
    #: it and every downstream reader falls back to `goal_text`, exactly as
    #: before this field existed.
    asked_text: Optional[str] = Field(default=None, max_length=2000)


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
    # Billable action. Goal Analysis is a multi-stage sweep, hence the price.
    enforce.bill(company.company_id, "crucible", actor_user_id=company.user_id)
    row = await asyncio.to_thread(
        runs_db.create,
        company.company_id,
        goal_text=body.goal_text,
        conversation_id=body.conversation_id,
        created_by=company.user_id,
        asked_text=body.asked_text,
    )

    kwargs = dict(run_id=row["id"], company_id=company.company_id,
                  goal_text=body.goal_text, asked_text=body.asked_text)
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
                  confirmed_by=company.user_id,
                  # READ BACK OFF THE ROW, not resupplied by this body —
                  # `create()` is the only place `asked_text` is ever
                  # written, and this endpoint's own request shape
                  # (`ConfirmGoal`) never carried it.
                  asked_text=_row_meta(claimed).get("asked_text"))
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

    # `status="ready"` fires the moment the analysis exists, up to
    # `DEADLINE_SECONDS` (relevance) + `DEADLINE_SECONDS` (recommend) +
    # `DEADLINE_SECONDS` (deep recommend) later than the recommendations and
    # the appendix actually land — and `POST /{run_id}/document` above is
    # IDEMPOTENT FOREVER: a document created in that window is permanently
    # missing them, its `detached` flag reads false, and it certifies itself
    # as the run's complete output with no way back. Refused here instead —
    # the panel already renders a "still generating" banner off this same
    # flag, so a reader who tries anyway is not surprised by the 409.
    if bool((await asyncio.to_thread(_meta_of, run_id, company.company_id))
            .get("enrichment_pending")):
        raise HTTPException(
            409,
            "This analysis is still finishing — the recommendations and the "
            "appendix have not landed yet. Try again in a moment.",
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
        # READ BACK OFF THE ROW — see the `/confirm` handler's own comment.
        asked_text=_row_meta(claimed).get("asked_text"),
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
    #: THE READER'S OWN SENTENCE — see `StartRun.asked_text`. Threaded through
    #: to the plan (so the gate can show it) and to the deep-recommendation
    #: count (so a count phrased in it is not lost to `goal_text`'s
    #: extraction). Never reaches `resolve()`/`_convention_definition` below —
    #: the metric definition stays sourced from `goal_text` alone (I9).
    asked_text: Optional[str] = None,
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
                        # A WHOLESALE REPLACE, not a merge — this write used
                        # to drop `asked_text` that `create()` had already
                        # stored, because nothing here read it back before
                        # overwriting the blob. Threaded through explicitly
                        # so `/confirm`'s own re-read of the row still finds
                        # it.
                        "asked_text": asked_text or "",
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
                asked_text=asked_text or "",
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
                kept_inventory = [
                    SourceInventory(
                        source_type=src.get("source_type") or "",
                        signal_count=int(src.get("signal_count") or 0),
                        label=src.get("label") or "",
                        witnesses=src.get("witnesses") or "",
                    )
                    for src in kept
                ]

                # AND THE FRAMEWORK, RE-CHOSEN FROM THE KEPT SET TOO.
                # `build_plan` picked a framework when analytics was still on
                # the table; a reader who then unticks the one numeric source
                # that made RICE derivable must not keep a RICE table with
                # every row scoring None — the exact failure this ticket
                # exists to remove, one step later than the gaps bug it was
                # extracted alongside.
                from app.crucible.framework import (
                    questions_for, select_framework,
                )
                from app.db.companies import declared_prioritization_framework

                declared = None
                try:
                    declared = declared_prioritization_framework(company_id)
                except Exception:  # noqa: BLE001 — never block approval on this
                    logger.warning(
                        "crucible approve: could not read declared framework "
                        "for %s", company_id,
                    )
                choice = select_framework(kept_inventory, declared)
                plan_json["framework"] = choice.framework
                plan_json["framework_reason"] = choice.reason
                plan_json["questions"] = [
                    {"id": q.id, "prompt": q.prompt, "why": q.why}
                    for q in questions_for(choice.framework)
                ]

                gaps, produce = derive_gaps_and_promises(
                    kept_inventory, tuple(hypotheses), framework_choice=choice,
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

        # WHAT KIND OF MONEY EACH FIGURE IS — its own stage, deliberately
        # BEFORE the pipeline, because `pipeline` contains no LLM call
        # anywhere and that property is what makes a run reproducible. The
        # model returns a category per figure; the pipeline reads it as an
        # ordinary deterministic input and decides the consequence itself.
        #
        # Degrades rather than fails: any claim the classifier does not
        # answer for keeps `figure_class=None` and falls back to the
        # deterministic phrase families, which admit money to a sum only on
        # a positive signal.
        # Rows already carrying a stored class are not re-sent, so this
        # call shrinks to nothing once a corpus has been classified — and,
        # more importantly, the answer stops moving between runs.
        newly_classified = classify_figures(claims, enterprise_id=company_id)
        if newly_classified:
            # PERSISTED BEFORE USE, so a run that crashes after classifying
            # does not throw away the draw and take a different one next
            # time. A write failure is logged and the run continues on the
            # in-memory classes.
            try:
                persist_classes(newly_classified, company_id=company_id)
            except Exception:  # noqa: BLE001 — analysis outlives a write
                logger.exception(
                    "crucible: could not persist figure classes for %s",
                    company_id,
                )
        claims = apply_classes(claims, newly_classified)

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
                    # Real, transcript-stated commercial figures (if any of
                    # this finding's claims carry one) — carried alongside
                    # `value`, never folded into it, so the report can say
                    # "customers named $X across N accounts" as evidence
                    # distinct from any projection. See
                    # `pipeline._grounded_commercial_native_units`.
                    "native_units": dict(impact.native_units),
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
        #
        # `_run_enrichment` is SHARED with `_reenrich_stalled_run` (the
        # stalled-enrichment sweep below) — the same relevance-gate-then-
        # recommend decision must never be reimplemented twice and drift, and
        # it also carries the `_progress` calls that double as this stage's
        # heartbeat: every stage a reader sees advance is a point a dead
        # process would never reach, so a process that died anywhere in these
        # three model calls no longer looks, from the row alone, identical to
        # one still working.
        enrichment_meta = _run_enrichment(
            run_id=run_id, company_id=company_id, goal_text=goal_text,
            definition_text=definition_text, findings=result.findings,
            impacts=result.impacts, confidences=result.confidences,
            claims=claims, asked_text=asked_text,
        )
        # DOWN IN THE SAME WRITE THAT PUBLISHES THE RESULTS. Clearing it
        # separately leaves a window where the panel has stopped polling and
        # the verdicts are not there yet — the exact bug this flag exists to
        # close, one write narrower.
        meta = dict(_meta_of(run_id, company_id))
        meta.update(enrichment_meta)
        meta["enrichment_pending"] = False
        # A NORMAL FINISH SAYS SO. The sweep writes its own value here when it
        # is the one that closes out a stranded run (`sweep_stalled_enrichment`
        # below); a run that reaches this line unassisted gets the ordinary
        # marker, so the two are distinguishable on the row rather than only
        # in the logs.
        meta["enrichment_outcome"] = "completed"
        runs_db.update(run_id, company_id, prioritisation=meta)

    except Exception as exc:  # noqa: BLE001 — total by contract
        logger.exception("crucible: run %s failed", run_id)
        runs_db.fail(run_id, company_id, code="internal", detail=str(exc))


def _run_enrichment(
    *, run_id: int, company_id: str, goal_text: str, definition_text: str,
    findings: list, impacts: list, confidences: list, claims: list,
    asked_text: Optional[str] = None,
) -> dict:
    """Stages 9-10: which findings bear on the goal, and what to do about the
    ones that do. Returns the `prioritisation` fields this stage contributes;
    the CALLER writes them (merged with whatever else its own meta carries) —
    this function never touches the row itself, so a caller reading a fresh
    `_meta_of` right before merging never loses a concurrent write.

    SHARED between `execute_run`'s own tail and `_reenrich_stalled_run` (the
    stalled-enrichment sweep), so the relevance-gate-then-recommend decision
    is made in exactly one place: two implementations of "which findings bear
    on the goal" would be two places that could silently stop agreeing.

    `findings`/`impacts`/`confidences` must be the same length and in the same
    positional order — `execute_run` passes `pipeline.build_findings`'s own
    three return sequences directly; the sweep passes ones it reconstructed
    from stored rows (`_reconstruct_enrichment_inputs`). Either way this
    function only reads them, never re-derives an order (I10).
    """
    # ── WHICH OF THEM BEAR ON THE GOAL THAT WAS ASKED. ──────────────────────
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
    _progress(run_id, company_id, enrichment_step="judging_relevance")
    set_aside_by_rank: list = [None] * len(findings)
    relevance_gate_ran = False
    relevance_judged_info: dict = {}
    try:
        from app.crucible.relevance import judge_relevance, partition

        verdicts = judge_relevance(
            enterprise_id=company_id,
            goal_text=goal_text,
            definition_text=definition_text,
            findings=findings,
        )
        _, aside = partition(findings, verdicts)
        reason_of = {f.id: reason for f, reason in aside}
        set_aside_by_rank = [reason_of.get(f.id) for f in findings]
        # THE GATE RAN, whether or not it set anything aside — the report's
        # own disclosure turns on this, not on `aside` being non-empty. A
        # gate that judged everything `true` still ran, and the "nothing here
        # was filtered" sentence is just as false for it as for a run with a
        # full appendix.
        relevance_gate_ran = True
        # THE COVERAGE DISCLOSURE. `len(verdicts)` is exactly the findings the
        # gate returned a usable answer for — the same count `partition` reads
        # to decide kept/aside — so it is the honest number of "evaluated",
        # never a guess at how many chunks ran.
        relevance_judged_info = {
            "judged": len(verdicts), "considered": len(findings),
        }
    except Exception:  # noqa: BLE001 — a gate that failed keeps everything
        logger.exception("crucible: relevance gate skipped for run %s", run_id)

    # AND THE SUGGESTIONS GO TO THE ONES THAT SURVIVED IT. Recommending an
    # action for a theme the gate just judged irrelevant would spend the
    # reader's attention on the thing they were told to ignore.
    relevant = [
        f for f, reason in zip(findings, set_aside_by_rank) if reason is None
    ]
    # SAME FILTER, ON THE OTHER TWO SEQUENCES — see this function's own
    # docstring on why the three arrive positionally paired.
    relevant_impacts = [
        imp for imp, reason in zip(impacts, set_aside_by_rank)
        if reason is None
    ]
    relevant_confidences = [
        conf for conf, reason in zip(confidences, set_aside_by_rank)
        if reason is None
    ]
    claims_by_id = {c.id: c for c in claims}

    # ── WHAT TO DO ABOUT EACH OF THEM. ──────────────────────────────────────
    #
    # AFTER the ranking, and that ordering is the invariant rather than a
    # detail. `findings` is already sorted and every score is already frozen;
    # nothing below is fed back into either. I2 says no LLM returns a score, a
    # rank or a decision, and it still holds — this returns prose to hang
    # beside a decision the engine already made on its own.
    #
    # TOTAL, like everything else on this path: a suggestion layer that failed
    # must not cost a reader the findings that succeeded.
    _progress(run_id, company_id, enrichment_step="recommending")
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

    # A DEEP RECOMMENDATION FOR THE TOP OF THE RANKING, SIZED BY THE GOAL.
    #
    # Apurva: "once we pick the top two, then we could just compare them."
    # `build_deep_recommendations` decides how many with pure arithmetic over
    # the frozen `relevant_impacts` (I2/I10) — never an LLM, and never a count
    # this route invents. TOTAL, same reasoning as the flat pass above.
    _progress(run_id, company_id, enrichment_step="deep_recommending")
    deep = {}
    deep_attempted_ids: frozenset[str] = frozenset()
    recommendation_basis = ""
    try:
        from app.crucible.recommend import build_deep_recommendations

        deep_result = build_deep_recommendations(
            enterprise_id=company_id,
            goal_text=goal_text,
            definition_text=definition_text,
            findings=relevant,
            impacts=relevant_impacts,
            confidences=relevant_confidences,
            claims=claims,
            asked_text=asked_text,
        )
        deep = deep_result.by_id
        deep_attempted_ids = deep_result.attempted_ids
        recommendation_basis = deep_result.count.basis
    except Exception:  # noqa: BLE001
        logger.exception("crucible: deep recommendations skipped for run %s", run_id)

    # LIST PRICING, FOR THE LIVE PANEL — computed UNCONDITIONALLY, unlike
    # `recommendation_basis` above. That one only exists to answer a money
    # target somebody named, so it lives inside the money-target branch of
    # `resolve_recommendation_count` and is silent on every other run. List
    # pricing is not a property of one goal — `report.py`'s own words: "it
    # is what the product costs, and it turns up wherever pricing was
    # discussed" — so `report.py`'s `_findings_section` renders it on EVERY
    # report, and this line has to match that scope: computed every run,
    # over the same goal-relevant `relevant_impacts` set the deep pass above
    # reads, gated on nothing but whether any finding actually carries
    # pricing units.
    #
    # NEVER BUILT ON A REPORT.PY IMPORT. `quoted_list_pricing_basis` lives
    # beside `_quoted_money_toward_target` in `recommend.py` and reads
    # `Impact.native_units` directly — the frozen scored shape this route
    # already has in hand, not the stored-row dict shape `report.py`'s own
    # `_list_pricing` reads back later. Both call the same
    # `aggregate_price_range` for the arithmetic, so the two surfaces can
    # never disagree even though they start from different data.
    list_pricing_basis = ""
    try:
        from app.crucible.recommend import quoted_list_pricing_basis

        list_pricing_basis = quoted_list_pricing_basis(relevant_impacts) or ""
    except Exception:  # noqa: BLE001
        logger.exception("crucible: list pricing basis skipped for run %s", run_id)

    # THE FUNNEL'S OWN "written up in full" NUMBER, CORRECTED. `execute_run`
    # published `deep=result.deep_count` before this function ever ran —
    # Stage 10a's screening-tier cap (a constant, e.g. 5), which is the best
    # guess available while a run is still live. The actual deep-RECOMMENDATION
    # count is not knowable until the citation gate above has run, and it is
    # routinely smaller (a named count under the cap, or a citation-bar
    # shortfall). The narration widget and its "How this was narrowed" recap
    # both read this same stored field and call it "written up in full", so
    # left uncorrected it states a stale tier cap forever — exactly the
    # "615 findings — top 5 written up in full" bug reported over a run that
    # actually wrote 3. Overwritten here, once, with the true count.
    _progress(run_id, company_id, deep=len(deep))

    # CARRIED IN THE RUN'S OWN JSON, not in new columns on `crucible_findings`.
    # Adding columns means a migration against the shared Supabase, which is a
    # production change and not one to make without being asked; the meta blob
    # is already where this run's plan lives and costs nothing to extend.
    findings_extra = {
        f.id: {
            "label": f.label,
            "example": f.example,
            # RICE's Impact term is read from the kinds of claim behind a
            # finding, so the renderer needs them. Carried here for the same
            # reason as the label: adding a column to `crucible_findings`
            # means a migration against the shared Supabase. Read from the
            # CLAIMS this call was actually given, not from
            # `f.confidence_inputs.claim_types` — the sweep's reconstructed
            # findings carry no confidence_inputs worth reading (see
            # `_reconstruct_enrichment_inputs`), and computing it here instead
            # means this function does not care which caller built `findings`.
            "claim_types": sorted({
                claims_by_id[cid].type for cid in f.claim_ids
                if cid in claims_by_id
            }),
            **({"recommendation": {
                "action": recs[f.id].action, "because": recs[f.id].because,
            }} if f.id in recs else {}),
            # WAS THIS A CANDIDATE FOR A FULL WRITE-UP, EVEN IF IT DID NOT GET
            # ONE. `f.id in deep_attempted_ids and f.id not in deep` is exactly
            # the citation-gate-dropped case (or a deep pass that failed
            # outright) — the finding was in the top N `resolve_recommendation_
            # count` named, but its evidence did not clear the bar. The
            # renderer uses this to connect that finding's plain
            # `recommendation` to the shortfall already disclosed in
            # `recommendation_basis`, instead of leaving it looking like an
            # unexplained absence next to findings that were never candidates
            # at all. Silent (key omitted, since the merge below drops falsy
            # values) for a finding that kept its deep recommendation — it
            # already renders one — and for one that was never attempted.
            **({"deep_attempted": True}
               if f.id in deep_attempted_ids and f.id not in deep else {}),
            # THE DEEP PASS TAKES PRECEDENCE where both exist — a finding in
            # the deep set is one the flat pass ALSO ran on (`relevant` feeds
            # both), and the renderer shows the deeper one rather than both,
            # so a reader is never shown two disagreeing suggestions for the
            # same finding.
            **({"deep_recommendation": {
                "action": deep[f.id].action,
                "because": deep[f.id].because,
                "changes": [
                    {"text": c.text, "claim_id": c.claim_id,
                     "cited_claim": c.cited_claim}
                    for c in deep[f.id].changes
                ],
                "open_questions": list(deep[f.id].open_questions),
                "what_would_falsify": deep[f.id].what_would_falsify,
                "comparison": deep[f.id].comparison,
            }} if f.id in deep else {}),
        }
        for f in findings
    }
    return {
        "recommendation_basis": recommendation_basis,
        # Sibling of `recommendation_basis` above, same panel-parity purpose:
        # `GoalAnalysisReport.tsx` reads this straight off the run's
        # `prioritisation` dict and renders it verbatim, the same way it
        # already does for `recommendation_basis` — see that field's comment.
        "list_pricing_basis": list_pricing_basis,
        # Recorded so the renderer can tell "the gate ran and kept
        # everything" from "no gate ever touched this run" — see
        # `report.py`'s `_definition_section`/`_limits_section`.
        "relevance_gate_ran": relevance_gate_ran,
        # How many of the found themes the gate actually judged, so a
        # truncated pass says so rather than looking like a complete one.
        # Empty when the gate did not run at all.
        "relevance_judged": relevance_judged_info,
        # Keyed by RANK, because the stored finding rows carry no id — the
        # renderer reads them back positionally.
        "set_aside_by_rank": list(set_aside_by_rank),
        "findings_extra_by_rank": [findings_extra[f.id] for f in findings],
    }


def _reconstruct_enrichment_inputs(
    rows: list[dict],
) -> tuple[list, list, list]:
    """`Finding`/`Impact`/`Confidence` objects, rebuilt from STORED
    `crucible_findings` rows for the stalled-enrichment sweep below — never
    re-scored (I10). Every
    number that is actually READ downstream (by `judge_relevance`,
    `build_recommendations`/`build_deep_recommendations`, or `_run_enrichment`
    itself) is read back exactly as `execute_run` wrote it: `id`, `statement`,
    `claim_ids`, `adjudication`, and the frozen `impact_value`/
    `affected_population`/`confidence_band`/`weakest_leg*`/`cap_reason`.

    The placeholder fields below (`impact_inputs`, `confidence_inputs`,
    `Confidence.score`, `movable_gap`, `value_per_unit`, `native_units`) are
    NEVER read by any of those three call sites — only `Finding`'s own
    `id`/`label`/`statement`/`claim_ids`/`adjudication` and the separate
    `Impact`/`Confidence` objects this function ALSO builds are — so filling
    them with honest, inert placeholders costs nothing real. `label`/`example`
    are not stored columns (they live only in `findings_extra_by_rank`, which
    a stalled run never got to write); both fall back to `statement` wherever
    they would have been read, same as any run stored before `label` shipped.
    """
    from app.crucible.types import (
        Confidence, ConfidenceInputs, Finding, Impact, ImpactInputs,
    )

    findings: list = []
    impacts: list = []
    confidences: list = []
    for row in rows:
        currency = row.get("currency") or "accounts"
        impact_dict = row.get("impact") or {}
        confidence_dict = row.get("confidence") or {}
        findings.append(Finding(
            id=str(row.get("id")),
            statement=row.get("statement") or "",
            claim_ids=tuple(row.get("claim_ids") or ()),
            impact_inputs=ImpactInputs(
                currency=currency, affected_population=None,
                movable_gap=None, value_per_unit=None,
            ),
            confidence_inputs=ConfidenceInputs(
                strengths=(), claim_types=(), observed_ats=(),
                authoritative_count=0, claim_count=0,
                independent_authoritative_source_types=0,
            ),
            adjudication=row.get("adjudication") or "no_authoritative_source",
        ))
        impacts.append(Impact(
            value=row.get("impact_value"),
            currency=currency,
            affected_population=impact_dict.get("affected_population"),
            movable_gap=None, value_per_unit=None,
        ))
        confidences.append(Confidence(
            band=row.get("confidence_band") or "low",
            score=0.0,   # "internal only, NEVER rendered" (types.py)
            weakest_leg=confidence_dict.get("weakest_leg") or "problem",
            weakest_leg_reason=confidence_dict.get("weakest_leg_reason") or "",
            cap_reason=confidence_dict.get("cap_reason"),
        ))
    return findings, impacts, confidences


def _load_signals_by_id(company_id: str, ids: set[str]) -> list[dict]:
    """A targeted signal read, scoped to exactly `ids` — the "fresh signal
    read" the stalled-enrichment sweep needs to reconstruct `Claim` objects
    for citation grounding, without re-reading a whole (possibly 10k+-signal)
    corpus for a
    handful of already-selected findings. Claim ids ARE signal ids
    (`app.crucible.claims.project_signal`), so this is the same table and the
    same columns `_load_signals` reads, filtered by id instead of paged.
    """
    if not ids:
        return []
    from app.db.client import require_client

    client = require_client()
    cols = (
        "id,kind,source_type,content,properties,provenance,"
        "valid_at,created_at,source_id"
    )
    id_list = list(ids)
    rows: list[dict] = []
    # Chunked well under PostgREST's practical `in.()` URL-length limits.
    for i in range(0, len(id_list), 200):
        batch = id_list[i:i + 200]
        page = (
            client.table("kg_signal").select(cols)
            .eq("enterprise_id", company_id)
            .in_("id", batch)
            .execute()
        ).data or []
        rows.extend(page)
    return rows


def _reenrich_stalled_run(run_id: int, company_id: str, row: dict) -> None:
    """One re-run attempt for a run the stalled-enrichment sweep just
    claimed. TOTAL, like
    `execute_run`'s own tail: whatever happens here, this ends with
    `enrichment_pending` cleared and an honest `enrichment_outcome` — never a
    bare exception, and NEVER `runs_db.fail()`. `fail()` sets
    `status="failed"`, which would hide a perfectly good, already-published
    analysis behind an error screen — exactly the run this sweep exists to
    rescue, not to discard.
    """
    meta = dict(_meta_of(run_id, company_id))
    outcome = "gave_up_after_sweep"
    enrichment_meta: dict = {}
    try:
        finding_rows, _ledger_rows = runs_db.load_findings(run_id, company_id)
        if finding_rows:
            plan = meta.get("plan") or {}
            goal_text = row.get("goal_text") or ""
            definition_text = str(plan.get("definition_text") or "")

            findings, impacts, confidences = _reconstruct_enrichment_inputs(
                finding_rows
            )
            all_claim_ids = {
                cid for f in findings for cid in f.claim_ids if cid
            }
            signals = _load_signals_by_id(company_id, all_claim_ids)
            from app.crucible.claims import project_signals

            claims, _stats = project_signals(signals)

            enrichment_meta = _run_enrichment(
                run_id=run_id, company_id=company_id, goal_text=goal_text,
                definition_text=definition_text, findings=findings,
                impacts=impacts, confidences=confidences, claims=claims,
                # `meta` is this same run's own blob, already read above —
                # `create()` is the only writer of `asked_text`, so it is
                # still there to read back.
                asked_text=meta.get("asked_text"),
            )
            outcome = "completed_by_sweep"
        else:
            # No stored findings at all (should not normally happen — findings
            # are saved before `enrichment_pending` is ever set) — nothing to
            # re-run, but still a terminal state rather than a spin.
            outcome = "no_findings_to_enrich"
    except Exception:  # noqa: BLE001 — this function must ALWAYS reach the
        # write below. A re-run attempt that failed leaves the run no worse
        # off than the stall it was trying to fix — the findings and any
        # flat/deep recommendations already in `meta` before this attempt are
        # untouched — and it must not become a second stall.
        logger.exception(
            "crucible: sweep re-enrichment failed for run %s; clearing "
            "enrichment_pending without recommendations", run_id,
        )

    # RE-READ, NOT THE SNAPSHOT TAKEN BEFORE `_run_enrichment` RAN. That call
    # narrates its OWN stages via `_progress` (`judging_relevance`,
    # `recommending`, `deep_recommending`, and the corrected `deep` count at
    # its tail) — each one a fresh read-modify-write of this same row's
    # `prioritisation` blob. Writing back the `meta` snapshot taken at the top
    # of THIS function, before any of those ran, would silently overwrite
    # every one of them with what the row looked like before re-enrichment
    # started: a sweep-completed run would show no `enrichment_step` at all
    # (or a stale one), while a normally-completed run keeps its last one —
    # exactly the drift reported live. `execute_run`'s own tail already reads
    # fresh here for the same reason, after its own call to `_run_enrichment`.
    meta = dict(_meta_of(run_id, company_id))
    meta.update(enrichment_meta)
    meta["enrichment_pending"] = False
    meta["enrichment_outcome"] = outcome
    runs_db.update(run_id, company_id, prioritisation=meta)


#: A single stalled-enrichment recovery measured 22:50:57 -> 22:52:42 on
#: staging — 105s of synchronous model calls, inline inside the SAME 5-minute
#: job that also fails abandoned Ask jobs (`scheduler._run_orphan_ask_job_
#: sweep`, "Fail abandoned Ask jobs (every 5m)"). APScheduler's default
#: `max_instances=1` means several stranded runs recovered in one tick would
#: overrun the 5-minute interval and cause SKIPPED executions of that sweep —
#: a different subsystem's reliability degraded by this one's recovery. The
#: nesting itself is fine (an existing, deliberate pattern); the unbounded
#: work per tick is the risk. 2 * 105s ~= 3.5 minutes, leaving real margin
#: against the interval even after every sweep ahead of this one in
#: `_run_orphan_ask_job_sweep` has also run. Whatever does not fit in one
#: tick is simply still a candidate on the next one — `find_stalled_
#: enrichment` re-lists it (its `heartbeat_at` was never touched), and
#: `claim_stalled_enrichment`'s compare-and-set means no run is ever silently
#: skipped forever, only deferred by a few minutes.
MAX_STALLED_ENRICHMENT_RECOVERIES_PER_TICK = 2


def sweep_stalled_enrichment(
    *, max_recoveries: int = MAX_STALLED_ENRICHMENT_RECOVERIES_PER_TICK,
) -> int:
    """A deploy that restarts the process mid-enrichment strands a run
    forever: it is already `ready` (findings published), it
    still says `enrichment_pending`, and `sweep_orphans`'s own predicate
    (`resolving_goal`/`planning`/`running`) cannot see it — a `ready` run is,
    by definition, past all three. THE ROW IS THE JOB (`routes/crucible.py`'s
    own header): no `design_agent_jobs`-style table is built here. Findings
    are already durable, so a stalled run is re-run ONCE from the stored rows
    plus a fresh, targeted signal read, then its flag is cleared with an
    honest outcome marker EITHER WAY — see `_reenrich_stalled_run`.

    Called on the same recurring interval as `sweep_orphans` (see
    `scheduler.py`), not startup-only, for the reason `sweep_orphans`'s own
    docstring states: a process that dies at 03:00 must not wait for the next
    deploy to be noticed.

    CAPPED PER TICK (`max_recoveries`) — see `MAX_STALLED_ENRICHMENT_
    RECOVERIES_PER_TICK`'s own comment for the measured cost this bounds.
    Candidates beyond the cap are left untouched this tick, not merely
    counted: `find_stalled_enrichment` orders most-recent-stall-first, so
    whichever ones this tick does not reach are exactly the ones with the
    most slack before `STALLED_ENRICHMENT_AGE_MINUTES` next `.range()` page
    would need to widen — recovered on the next tick instead, never dropped.
    """
    candidates = runs_db.find_stalled_enrichment()[:max_recoveries]
    swept = 0
    for row in candidates:
        run_id, company_id = row["id"], row["company_id"]
        # THE CLAIM IS ATOMIC (compare-and-set on `heartbeat_at`), same
        # reasoning as `claim_for_confirmation`: two sweep ticks — or a sweep
        # tick racing a worker that was alive after all and about to
        # heartbeat — must not both re-run the same enrichment. The loser
        # does no work.
        claimed = runs_db.claim_stalled_enrichment(
            run_id, company_id, expected_heartbeat_at=row.get("heartbeat_at"),
        )
        if claimed is None:
            continue
        swept += 1
        _reenrich_stalled_run(run_id, company_id, row)
    if swept:
        logger.info("crucible: swept %d stalled enrichment(s)", swept)
    return swept


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


def _row_meta(row: dict) -> dict:
    """The `prioritisation` blob off an ALREADY-FETCHED row — same tolerant
    string-or-dict parsing `_meta_of` applies when it fetches the row itself,
    reused here for a caller that already has one (`claimed`, from
    `claim_for_confirmation`/`claim_for_approval`) so it does not cost a
    second read to see what `create()` stored at `asked_text`."""
    meta = row.get("prioritisation") or {}
    if isinstance(meta, str):
        import json

        try:
            meta = json.loads(meta)
        except Exception:  # noqa: BLE001
            meta = {}
    return meta if isinstance(meta, dict) else {}


def _meta_of(run_id: int, company_id: str) -> dict:
    """The run's meta blob. `prioritisation` is the run's own framing — the
    Stage 0 ask, and now the plan — so it is read-modify-written rather than
    replaced, or approving a plan would erase the question that produced it."""
    return _row_meta(runs_db.get(run_id, company_id) or {})


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

    ALSO THE ENRICHMENT HEARTBEAT. Every stage this narrates during
    enrichment (`judging_relevance`, `recommending`, `deep_recommending`) is a
    point a dead process would never reach, so bumping `heartbeat_at` in the
    SAME write a reader sees advance gives `sweep_stalled_enrichment` a
    liveness signal for free — one write, two purposes, rather than a second
    query per stage. Before this, nothing touched `heartbeat_at` between the
    write just before `save_findings` and the one enrichment eventually
    finishes with, so a run whose worker died anywhere in between looked, from
    the row alone, identical to one still working.

    TOTAL, like its caller. A run that produced real findings must not fail
    because a progress write did — the narration is display, and display never
    outranks the answer.
    """
    try:
        meta = dict(_meta_of(run_id, company_id))
        progress = dict(meta.get("progress") or {})
        progress.update(fields)
        meta["progress"] = progress
        runs_db.update(
            run_id, company_id, prioritisation=meta,
            heartbeat_at=datetime.now(timezone.utc).isoformat(),
        )
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
