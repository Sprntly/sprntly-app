"""Pipeline orchestrator: coordinates the full multi-stage intelligence pipeline.

Stages:
    1. Sync all active connectors (parallel)
    2. Run Marketing + Competitor agents (parallel)
    3. DS Agent analysis (if running)
    4. Knowledge Graph refresh (entity extraction)
    5. Brief generation (with signal fusion)

Stages 4 & 5 drive the KG-synthesis path that the UI actually reads: stage 4
runs ``seed_incremental`` (incrementally ingesting newly uploaded source docs
into the synthesis KG) and stage 5 runs ``generate_brief_for`` (re-seeds
idempotently, then runs synthesis and saves the brief the UI serves).

Each stage records timing and status in the pipeline_runs table.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app import db
from app.config import settings
from app.db.companies import company_id_for_slug
from app.db.pipeline_runs import (
    complete_run,
    create_run,
    fail_run,
    update_run_stage,
)

logger = logging.getLogger(__name__)


async def run_full_pipeline(
    dataset: str,
    trigger: str = "scheduled",
) -> dict[str, Any]:
    """Execute the 5-stage intelligence pipeline for a dataset.

    Returns a summary dict with per-stage results.
    """
    # Create audit log entry
    try:
        run_id = create_run(dataset, trigger)
    except Exception:
        run_id = 0
        logger.warning("Failed to create pipeline_runs row", exc_info=True)

    result: dict[str, Any] = {"dataset": dataset, "trigger": trigger, "stages": {}}

    try:
        # ── Stage 1: Sync connectors ──
        stage1 = await _stage_sync_connectors(dataset)
        result["stages"]["sync_connectors"] = stage1
        if run_id:
            update_run_stage(run_id, "sync_connectors", stage1)

        # ── Stage 2: Marketing + Competitor agents ──
        stage2 = await _stage_agents(dataset)
        result["stages"]["agents"] = stage2
        if run_id:
            update_run_stage(run_id, "agents", stage2)

        # ── Stage 3: DS Agent (optional) ──
        stage3 = await _stage_ds_agent(dataset)
        result["stages"]["ds_agent"] = stage3
        if run_id:
            update_run_stage(run_id, "ds_agent", stage3)

        # ── Stage 4: Knowledge Graph refresh ──
        stage4 = await _stage_knowledge_graph(dataset)
        result["stages"]["knowledge_graph"] = stage4
        if run_id:
            update_run_stage(run_id, "knowledge_graph", stage4)

        # ── Stage 5: Brief generation ──
        stage5 = await _stage_brief_generation(dataset)
        result["stages"]["brief"] = stage5
        if run_id:
            update_run_stage(run_id, "brief", stage5)

        result["status"] = "completed"
        if run_id:
            complete_run(run_id)

    except Exception as exc:
        result["status"] = "failed"
        result["error"] = str(exc)
        if run_id:
            fail_run(run_id, str(exc))
        logger.error("Pipeline failed for %s: %s", dataset, exc, exc_info=True)

    result["run_id"] = run_id
    return result


async def _stage_sync_connectors(dataset: str) -> dict[str, Any]:
    """Stage 1: Sync all active connectors in parallel.

    Issue #218 fix: pre-fix this called `db.list_connections()` with no
    args (broken signature → TypeError → caught by a bare `except` and
    silently returned "skipped/no connections"). Now we resolve the
    dataset slug → company_id and call the scoped helper; unknown slugs
    return a typed skipped status, real errors are logged and surfaced
    as an error status instead of being swallowed.
    """
    t0 = time.time()
    results: dict[str, Any] = {}

    company_id = company_id_for_slug(dataset)
    if not company_id:
        return {
            "status": "skipped",
            "reason": "no_company_for_slug",
            "duration_s": round(time.time() - t0, 1),
        }

    try:
        connections = db.list_connections(company_id)
    except Exception as exc:  # noqa: BLE001 — log + surface, never swallow
        logger.exception(
            "_stage_sync_connectors: list_connections failed for %s/%s",
            dataset, company_id,
        )
        return {
            "status": "error",
            "reason": "list_connections_failed",
            "error": str(exc),
            "duration_s": round(time.time() - t0, 1),
        }

    active = [c for c in connections if c.get("status") == "active"]
    if not active:
        return {"status": "skipped", "reason": "no active connections", "duration_s": 0}

    tasks = []
    providers = []

    for conn in active:
        provider = conn.get("provider", "")
        providers.append(provider)

        if provider == "slack":
            tasks.append(_sync_slack(dataset))
        elif provider == "hubspot":
            tasks.append(_sync_hubspot(dataset))
        else:
            # Other connectors don't have sync-to-corpus yet
            tasks.append(_no_corpus_sync())

    task_results = await asyncio.gather(*tasks, return_exceptions=True)

    for provider, res in zip(providers, task_results):
        if isinstance(res, Exception):
            results[provider] = {"status": "error", "error": str(res)}
        else:
            results[provider] = res or {"status": "ok"}

    return {
        "status": "completed",
        "providers": results,
        "duration_s": round(time.time() - t0, 1),
    }


async def _no_corpus_sync() -> dict[str, Any]:
    """A connector with no corpus-sync support yet — nothing to do.

    Replaces a removed-in-Python-3.11 `asyncio.coroutine(lambda: ...)()` call
    that raised `module 'asyncio' has no attribute 'coroutine'` and crashed the
    WHOLE pipeline for any company whose active connector wasn't Slack/HubSpot
    (e.g. Figma/GitHub/ClickUp/Drive) — so a brand-new company that connected
    such a source could never run the pipeline.
    """
    return {"status": "no_sync"}


async def _sync_slack(dataset: str) -> dict[str, Any]:
    # Slack is per-user now: sync_slack needs (company_id, user_id) to
    # resolve a specific user's bot token. The company-level pipeline run
    # has no single user to act as, so corpus sync is driven from the
    # per-user route (POST /slack/sync-to-corpus) instead. Skip here rather
    # than guess an owner.
    return {"status": "skipped", "reason": "slack_sync_is_per_user"}


async def _sync_hubspot(dataset: str) -> dict[str, Any]:
    # HubSpot sync is company-scoped: sync_hubspot needs a company_id to
    # resolve a specific company's HubSpot token. The company-level pipeline
    # run carries only a dataset (no company context), so corpus sync is
    # driven from the company-scoped route (POST /hubspot/sync) instead.
    # Skip here rather than guess an owner.
    return {"status": "skipped", "reason": "hubspot_sync_is_company_scoped"}


async def _stage_agents(dataset: str) -> dict[str, Any]:
    """Stage 2: Run Marketing + Competitor agents in parallel."""
    t0 = time.time()

    from app.agents.marketing import run_marketing_agent
    from app.agents.competitor import run_competitor_agent

    marketing_task = run_marketing_agent(dataset)
    competitor_task = run_competitor_agent(dataset)

    results = await asyncio.gather(
        marketing_task, competitor_task, return_exceptions=True,
    )

    marketing_result = (
        results[0] if not isinstance(results[0], Exception)
        else {"status": "error", "error": str(results[0])}
    )
    competitor_result = (
        results[1] if not isinstance(results[1], Exception)
        else {"status": "error", "error": str(results[1])}
    )

    return {
        "status": "completed",
        "marketing": marketing_result,
        "competitor": competitor_result,
        "duration_s": round(time.time() - t0, 1),
    }


async def _stage_ds_agent(dataset: str) -> dict[str, Any]:
    """Stage 3: Trigger DS Agent analysis (optional)."""
    t0 = time.time()

    # DS Agent runs as a separate service — try to trigger it via HTTP
    ds_agent_url = getattr(settings, "ds_agent_url", "")
    if not ds_agent_url:
        return {"status": "skipped", "reason": "DS Agent URL not configured"}

    try:
        import httpx
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{ds_agent_url}/api/pipeline/{dataset}",
                headers={"X-Internal-Key": settings.internal_api_key},
            )
            if resp.status_code < 400:
                return {
                    "status": "completed",
                    "response": resp.json() if resp.text else {},
                    "duration_s": round(time.time() - t0, 1),
                }
            return {"status": "error", "http_code": resp.status_code}
    except Exception as exc:
        return {
            "status": "skipped",
            "reason": f"DS Agent unreachable: {exc}",
            "duration_s": round(time.time() - t0, 1),
        }


async def _stage_knowledge_graph(dataset: str) -> dict[str, Any]:
    """Stage 4: Ingest source docs into the knowledge graph.

    Incrementally seeds the synthesis KG that the UI reads — picking up any
    newly-uploaded corpus docs (idempotent; unchanged docs are skipped by
    content hash) and, on a first-ever empty KG, best-effort connector pulls.
    """
    t0 = time.time()

    try:
        from app.graph.facade import GraphFacade
        from app.synthesis_brief import resolve_company, seed_incremental

        company_id, slug = resolve_company(dataset)
        seed = await asyncio.to_thread(
            seed_incremental, GraphFacade(), company_id, slug
        )
        return {
            "status": "completed",
            "engine": "synthesis",
            "seed": seed,
            "duration_s": round(time.time() - t0, 1),
        }
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
            "duration_s": round(time.time() - t0, 1),
        }


async def _stage_brief_generation(dataset: str) -> dict[str, Any]:
    """Stage 5: Generate the ranked brief (uses signal fusion internally).

    Runs ``generate_brief_for`` — which re-seeds the KG idempotently (cheap)
    and runs synthesis, save_brief()ing the brief the UI's /current endpoint
    reads. A company with no data to brief yet is NOT a pipeline failure:
    ``EmptyKnowledgeGraphError`` maps to a benign ``skipped`` status (mirroring
    routes/brief.py).
    """
    t0 = time.time()

    try:
        from app.synthesis.agent import EmptyKnowledgeGraphError
        from app.synthesis_brief import generate_brief_for

        try:
            await asyncio.to_thread(generate_brief_for, dataset)
        except EmptyKnowledgeGraphError:
            # Benign: nothing ingested yet. Not a pipeline failure — the
            # user just needs to connect a source or upload files first.
            return {
                "status": "skipped",
                "reason": "KG has no data to brief yet — connect a "
                          "source or upload files",
                "engine": "synthesis",
                "duration_s": round(time.time() - t0, 1),
            }
        # Warm the drill-downs for the fresh brief — same as the regenerate
        # route + scheduler — so a pipeline-generated brief also auto-generates
        # its PRDs (prd_warm_count, default 3 = all insights), evidence, and Ask
        # answers. Fire-and-forget in the background lane; never fails the stage.
        try:
            from app.brief_runner import warm_synthesis_drilldowns
            warm_synthesis_drilldowns(dataset)
        except Exception:  # noqa: BLE001 — warming is best-effort, never blocks the run
            logger.exception("pipeline: drill-down warming failed for %s", dataset)
        return {
            "status": "completed",
            "engine": "synthesis",
            "duration_s": round(time.time() - t0, 1),
        }
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
            "duration_s": round(time.time() - t0, 1),
        }
