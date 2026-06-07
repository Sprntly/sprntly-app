"""Pipeline orchestrator: coordinates the full multi-stage intelligence pipeline.

Stages:
    1. Sync all active connectors (parallel)
    2. Run Marketing + Competitor agents (parallel)
    3. DS Agent analysis (if running)
    4. Knowledge Graph refresh (entity extraction)
    5. Brief generation (with signal fusion)

Each stage records timing and status in the pipeline_runs table.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app import db
from app.config import settings
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
    """Stage 1: Sync all active connectors in parallel."""
    t0 = time.time()
    results: dict[str, Any] = {}

    try:
        connections = db.list_connections()
    except Exception:
        return {"status": "skipped", "reason": "no connections", "duration_s": 0}

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
            tasks.append(asyncio.coroutine(lambda: {"status": "no_sync"})())

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


async def _sync_slack(dataset: str) -> dict[str, Any]:
    from app.connectors.slack_sync import sync_slack
    result = await asyncio.to_thread(sync_slack, dataset)
    return result.to_dict()


async def _sync_hubspot(dataset: str) -> dict[str, Any]:
    from app.connectors.hubspot_sync import sync_hubspot
    result = await asyncio.to_thread(sync_hubspot, dataset)
    return result.to_dict()


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
    """Stage 4: Extract entities and refresh the knowledge graph."""
    t0 = time.time()

    try:
        from app.knowledge_graph import refresh_graph
        stats = await asyncio.to_thread(refresh_graph, dataset)
        return {
            "status": "completed",
            **stats,
            "duration_s": round(time.time() - t0, 1),
        }
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
            "duration_s": round(time.time() - t0, 1),
        }


async def _stage_brief_generation(dataset: str) -> dict[str, Any]:
    """Stage 5: Generate the ranked brief (uses signal fusion internally)."""
    t0 = time.time()

    try:
        from app.brief_runner import auto_generate_brief
        await auto_generate_brief(dataset)
        return {
            "status": "completed",
            "duration_s": round(time.time() - t0, 1),
        }
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
            "duration_s": round(time.time() - t0, 1),
        }
