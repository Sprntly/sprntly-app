import asyncio
import logging

from fastapi import Depends, APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.auth import CompanyContext, require_company
from app.brief_runner import get_status, set_status, warm_synthesis_drilldowns
from app.db import (
    find_existing_evidence,
    find_existing_prd,
    get_current_brief,
    start_evidence,
    start_prd,
)
from app.db import nudge as nudge_db
from app.db.companies import display_name_for_slug
from app.db.finding_state import set_finding_action
from app.deps.ownership import require_owned_brief, require_owned_dataset
from app.evidence_kg import generate_evidence_kg
from app.kg_ingest.auto_sync import kickoff_corpus_seed
from app.prd_runner import PRD_VARIANT, generate_prd
from app.prompts import (
    EVIDENCE_TEMPLATE_VERSION,
    EVIDENCE_VARIANT,
    PRD_TEMPLATE_VERSION,
)
from app.synthesis.agent import EmptyKnowledgeGraphError
from app.synthesis_brief import generate_brief_for, resolve_company

# EVIDENCE_VARIANT comes from app.prompts — the SAME constant routes/evidence.py
# dedups against. This used to be a stale local "v2" copy, so brief-time
# pre-generation wrote rows the Evidence tab could never reuse and every first
# open regenerated the evidence from scratch at the current variant.

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/brief", tags=["brief"])

# Strong refs to in-flight background brief-generation tasks (see the note in
# routes/design_agent.py): without this, the bare create_task result can be
# garbage-collected mid-run and the regenerate would silently die.
_inflight_tasks: set[asyncio.Task] = set()


def _track(task: asyncio.Task) -> asyncio.Task:
    _inflight_tasks.add(task)
    task.add_done_callback(_inflight_tasks.discard)
    return task


def _with_company_name(brief: dict) -> dict:
    """Attach the company's display name to a brief payload.

    The `dataset` slug is an internal key (db / infra-api only) — the UI must
    never have to render it. `company_name` is None for legacy demo datasets
    that have no companies row; the frontend falls back to the slug then.
    """
    return {**brief, "company_name": display_name_for_slug(brief.get("dataset") or "")}


def _notify_brief_ready(dataset: str, brief: dict | None) -> None:
    """Send the short "Hey, your brief is generated." ping (Slack + email) after
    a USER-TRIGGERED regenerate — not the full weekly brief message, which stays
    reserved for the scheduled delivery time. Only fires for a FRESH brief; a
    cache-returned run (`_from_cache`, KG unchanged) produced nothing new to
    announce. Best-effort: blocking HTTP, never raises."""
    if not brief or brief.get("_from_cache"):
        return
    from app.synthesis.delivery import deliver_brief_ready_ping

    try:
        company_id, _slug = resolve_company(dataset)
        deliver_brief_ready_ping(company_id)
    except Exception:  # noqa: BLE001 — a ping failure never breaks the brief
        logger.exception("brief ready ping failed for %s", dataset)


async def _synthesis_generate_bg(dataset: str) -> None:
    """Background body for /regenerate under the synthesis engine.

    Seed-if-empty + run_synthesis runs off the event loop (it makes blocking
    LLM/Supabase calls); failures are
    logged, never raised — the service keeps serving the prior cached brief.
    run_synthesis save_brief()s the new brief, so /current picks it up.
    Delivery: the fresh brief is announced with the short ready ping (see
    _notify_brief_ready), not the full scheduled brief message.
    """
    set_status(dataset, "generating")
    try:
        brief = await asyncio.to_thread(generate_brief_for, dataset, deliver=False)
        set_status(dataset, "ready")
        logger.info("Synthesis brief generated for %s", dataset)
    except EmptyKnowledgeGraphError:
        # Benign: new company with no data yet. Mark failed with a helpful
        # message so the frontend can tell the user what to do.
        set_status(dataset, "failed",
                   error="No data to generate a brief from yet — upload files "
                         "or connect a data source, then regenerate.")
        logger.info("Synthesis brief skipped for %s — KG empty after seeding", dataset)
        return
    except Exception:  # noqa: BLE001 — fire-and-forget; prior brief stays
        set_status(dataset, "failed",
                   error="Brief generation failed — check server logs.")
        logger.exception("Synthesis brief generation failed for %s", dataset)
        return
    # Tell the user their brief is ready (short ping, fresh briefs only).
    await asyncio.to_thread(_notify_brief_ready, dataset, brief)
    # Warm the per-insight drill-downs so the first click is instant.
    # Error-isolated inside the
    # helper, so it can never undo the brief we just generated.
    warm_synthesis_drilldowns(dataset)


async def _generate_downstream_docs(dataset: str) -> None:
    """Generate a PRD + an evidence doc for every insight in the current brief.

    Runs after the fresh brief is saved so the whole workspace is warm — the
    user lands on ready PRDs and evidence instead of empty "Generate" panes.
    Skips (brief, insight) pairs that already have a ready/generating doc so a
    repeated full-regen is cheap. Every generation is error-isolated: one
    insight failing must not stop the rest of the fan-out.
    """
    brief = get_current_brief(dataset)
    if not brief:
        return
    brief_id = brief.get("id")
    insights = brief.get("insights") or []
    if not brief_id or not insights:
        return

    for idx, insight in enumerate(insights):
        title = insight.get("title") or f"Insight #{idx + 1}"
        # PRD generation for this insight.
        try:
            if not find_existing_prd(brief_id, idx, variant=PRD_VARIANT):
                prd_id = start_prd(
                    brief_id=brief_id,
                    insight_index=idx,
                    title=title,
                    template_version=PRD_TEMPLATE_VERSION,
                    variant=PRD_VARIANT,
                )
                await generate_prd(prd_id, brief_id, idx)
        except Exception:  # noqa: BLE001 — one insight's failure must not abort the rest
            logger.exception(
                "full-regen: PRD generation failed for brief=%s insight=%s",
                brief_id, idx,
            )
        # Evidence generation for this insight.
        try:
            if not find_existing_evidence(brief_id, idx, variant=EVIDENCE_VARIANT):
                evidence_id = start_evidence(
                    brief_id=brief_id,
                    insight_index=idx,
                    title=title,
                    template_version=EVIDENCE_TEMPLATE_VERSION,
                    variant=EVIDENCE_VARIANT,
                )
                await generate_evidence_kg(evidence_id, brief_id, idx)
        except Exception:  # noqa: BLE001 — isolate per insight
            logger.exception(
                "full-regen: evidence generation failed for brief=%s insight=%s",
                brief_id, idx,
            )


async def _full_pipeline_bg(dataset: str) -> None:
    """Background body for /regenerate-all — the full digest→brief→PRD→evidence chain.

    Order matters:
      1. Ingest newly-arrived source/connector/upload docs into the KG
         (kickoff_corpus_seed — the PR #536 event-driven path).
      2. Seed-if-needed + run synthesis to produce the fresh brief
         (generate_brief_for re-runs the corpus seed synchronously, so the brief
         reflects step 1's docs; the seed is content-hash deduped, so the double
         pass is cheap and idempotent).
      3. Generate a PRD for every insight in the fresh brief.
      4. Generate an evidence doc for every insight.

    Steps 3–4 only run once the brief is ready. Everything is error-isolated —
    a failure leaves the prior cached brief/PRDs/evidence in place.
    """
    set_status(dataset, "generating")
    # Step 1: digest the latest sources/connectors/uploads into the KG. This is
    # fire-and-forget (a daemon thread) and never raises; generate_brief_for below
    # re-seeds synchronously so the brief can't miss what this ingests.
    try:
        company_id, slug = resolve_company(dataset)
        kickoff_corpus_seed(company_id, slug)
    except Exception:  # noqa: BLE001 — ingestion kickoff is best-effort
        logger.exception("full-regen: corpus-seed kickoff failed for %s", dataset)

    # Step 2: seed-if-needed + synthesize the brief off the event loop.
    try:
        brief = await asyncio.to_thread(generate_brief_for, dataset, deliver=False)
        set_status(dataset, "ready")
        logger.info("Full-pipeline brief generated for %s", dataset)
    except EmptyKnowledgeGraphError:
        set_status(dataset, "failed",
                   error="No data to generate a brief from yet — upload files "
                         "or connect a data source, then regenerate.")
        logger.info("Full-pipeline brief skipped for %s — KG empty after seeding", dataset)
        return
    except Exception:  # noqa: BLE001 — fire-and-forget; prior brief stays
        set_status(dataset, "failed",
                   error="Brief generation failed — check server logs.")
        logger.exception("Full-pipeline brief generation failed for %s", dataset)
        return

    # Tell the user their brief is ready (short ping, fresh briefs only) before
    # the slower PRD/evidence fan-out below.
    await asyncio.to_thread(_notify_brief_ready, dataset, brief)

    # Steps 3 + 4: fan out PRD + evidence generation for the fresh brief, then
    # warm the drill-downs. All error-isolated so they can't undo the brief.
    await _generate_downstream_docs(dataset)
    warm_synthesis_drilldowns(dataset)


@router.get("/current")
def current(
    dataset: str,
    company: CompanyContext = Depends(require_company),
):
    """Return the latest cached brief for a dataset.

    If none exists yet, returns 404 with a body that includes the current
    auto-generation status (`empty | generating | failed`). Frontend can
    poll `/v1/brief/status` while this is anything other than `ready`.
    """
    # Tenant gate: the dataset slug must resolve to the caller's company.
    require_owned_dataset(dataset, company.company_id)
    brief = get_current_brief(dataset)
    if brief:
        return _with_company_name(brief)
    raise HTTPException(404, {"message": "No brief generated yet", **get_status(dataset)})


@router.get("/status")
def status(
    dataset: str,
    company: CompanyContext = Depends(require_company),
):
    """Lightweight poll endpoint for the frontend.

    Status values:
      - "ready": brief is in the cache, fetch it via /v1/brief/current
      - "generating": auto-gen is in flight; retry in a few seconds
      - "failed": last attempt failed (see `error`); will retry on service restart
      - "empty": nothing has been attempted yet

    Additive flag:
      - "regenerating": true — a fresh brief is being built *over* a still-cached
        one (status stays "ready" so the current brief keeps rendering). The home
        surface uses this to show a lightweight "refreshing your brief" indicator.
    """
    require_owned_dataset(dataset, company.company_id)
    return {"dataset": dataset, **get_status(dataset)}


@router.post("/regenerate")
async def regenerate(
    dataset: str,
    company: CompanyContext = Depends(require_company),
):
    """Force a fresh brief generation in the background. Returns immediately.

    Use case: API key was just fixed, want to retry without restarting the
    service. Existing cached brief (if any) stays in place until the new
    generation completes successfully.

    Runs the KG seed-if-empty → run_synthesis path in the background.
    """
    require_owned_dataset(dataset, company.company_id)
    _track(asyncio.create_task(_synthesis_generate_bg(dataset)))
    return {"started": True, "dataset": dataset}


@router.post("/regenerate-all")
async def regenerate_all(
    dataset: str,
    company: CompanyContext = Depends(require_company),
):
    """Run the FULL regeneration pipeline in the background. Returns immediately.

    Chains, in order: KG ingestion of the latest sources/connectors/uploads →
    weekly-brief synthesis → PRD generation for each insight → evidence
    generation for each insight. Used by the "Regenerate brief" button on the
    Connectors settings page, where the user has just connected a tool or
    uploaded files and wants the whole workspace rebuilt from the new data.

    Poll `/v1/brief/status` for the brief stage; PRDs/evidence continue warming
    after the brief flips to `ready`.
    """
    require_owned_dataset(dataset, company.company_id)
    _track(asyncio.create_task(_full_pipeline_bg(dataset)))
    return {"started": True, "dataset": dataset}


class DismissIn(BaseModel):
    """Identify the brief finding to dismiss, either directly by theme_id or by
    (brief_id, insight_index) which we resolve to a theme_id via the brief
    payload. Exactly one form is required."""

    theme_id: str | None = None
    brief_id: int | None = Field(default=None, ge=1)
    insight_index: int | None = Field(default=None, ge=0)


@router.post("/dismiss")
def dismiss(
    body: DismissIn,
    company: CompanyContext = Depends(require_company),
):
    """Record that the user dismissed a brief finding (action='dismissed').

    Phase 2 lifecycle: a dismissed finding is NOT 'completed' (completed =
    prd_created | done) — it simply won't reappear as a fresh brief finding via
    the de-dup memory it shares. The frontend keeps its localStorage dismiss UX;
    this server record makes the action durable + theme-keyed.

    Accepts either a raw `theme_id` or a (`brief_id`, `insight_index`) pair which
    is resolved to the theme_id from the owned brief's payload."""
    theme_id = body.theme_id
    if not theme_id:
        if body.brief_id is None or body.insight_index is None:
            raise HTTPException(
                400, "Provide either theme_id or (brief_id and insight_index)"
            )
        # Tenant gate + resolve insight → theme_id from the brief payload.
        brief = require_owned_brief(body.brief_id, company.company_id)
        insights = brief.get("insights") or []
        if not (0 <= body.insight_index < len(insights)):
            raise HTTPException(
                400,
                f"insight_index={body.insight_index} out of range "
                f"(0..{len(insights) - 1})",
            )
        theme_id = insights[body.insight_index].get("theme_id")
        if not theme_id:
            raise HTTPException(400, "Insight carries no theme_id; cannot dismiss")

    set_finding_action(company.company_id, theme_id, "dismissed")
    return {"dismissed": True, "theme_id": theme_id}


@router.post("/{brief_id}/opened")
def mark_opened(
    brief_id: int,
    company: CompanyContext = Depends(require_company),
):
    """Record that the signed-in user opened this brief. This is the open-state
    signal the brief-nudge cadence reads: once a user opens the brief, the
    Day 1/2/3 reminders stop for them (app/brief_nudge.py, app/db/nudge.py).
    Tenant-gated via require_owned_brief; idempotent (upsert)."""
    require_owned_brief(brief_id, company.company_id)
    nudge_db.mark_brief_opened(company.company_id, company.user_id, brief_id)
    return {"opened": True, "brief_id": brief_id}


@router.get("/{brief_id}")
def by_id(
    brief_id: int,
    company: CompanyContext = Depends(require_company),
):
    # require_owned_brief resolves brief → dataset → company and 404s on
    # mismatch (or a missing brief), returning the brief row on success.
    return _with_company_name(require_owned_brief(brief_id, company.company_id))


@router.post("/generate")
def generate(
    dataset: str,
    company: CompanyContext = Depends(require_company),
):
    """Synchronously generate a fresh brief and return it.

    Note: invokes Claude. Costs tokens. Blocks until done (~30s). Use
    /v1/brief/regenerate for fire-and-forget behavior instead.

    Runs the KG seed-if-empty → run_synthesis path (which save_brief()s the
    result); we then read it back to preserve the {brief_id, **payload}
    response shape.
    """
    require_owned_dataset(dataset, company.company_id)
    try:
        payload = generate_brief_for(dataset, deliver=False)
    except ValueError as e:
        # Unknown dataset/company or an empty KG even after seeding.
        raise HTTPException(409, str(e)) from e
    # User-triggered: announce with the short ready ping, not the full message.
    _notify_brief_ready(dataset, payload)
    saved = get_current_brief(dataset)
    brief_id = saved.get("id") if saved else None
    return _with_company_name({"brief_id": brief_id, "dataset": dataset, **payload})
