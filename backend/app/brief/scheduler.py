"""Monday Brief scheduler — fan-out entry point.

Spec source: Master PRD §4.2 — "The scheduler fires every Monday 9am
workspace TZ; every active workspace runs the Comprehensive tier."

What's in this PR:
  * `run_monday_brief_for_all_workspaces` — iterates active workspaces
    and calls `run_brief_comprehensive` for each, swallowing per-
    workspace errors so one tenant's failure doesn't drag down the
    whole batch.
  * A simple `_list_active_workspaces` helper that pulls (workspace_id,
    dataset_slug) pairs from the `datasets` table — every dataset is
    treated as a workspace for now (the slug-as-workspace-id transitional
    shape, same as the existing /v1/brief/regenerate route).

What's deferred (P3 deploy ticket):
  * Real APScheduler / cron wiring at boot.
  * Workspace timezone resolution (currently all UTC). The signature
    deliberately leaves room for a per-workspace `now()` injection so
    the cron harness can drive it.
  * Slack / email delivery — Synthesis Step 11 is the caller's job per
    the spec.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from app.brief.comprehensive import run_brief_comprehensive
from app.graph import GraphFacade
from app.synthesis.brief_assembly import Brief

logger = logging.getLogger(__name__)


def _list_active_workspaces() -> list[tuple[str, str]]:
    """Return (workspace_id, dataset_slug) for every active workspace.

    Transitional: the slug doubles as the workspace_id until multi-
    tenant routing lands. Deduped on slug because the same dataset
    shouldn't fire twice.
    """
    try:
        from app.db import list_dataset_slugs
    except ImportError:
        logger.warning("list_dataset_slugs unavailable; no workspaces to brief.")
        return []
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for slug in list_dataset_slugs():
        if slug in seen:
            continue
        seen.add(slug)
        out.append((slug, slug))
    return out


def run_monday_brief_for_all_workspaces(
    graph: GraphFacade,
    llm_call: Callable[..., dict[str, Any]],
    ds_runner: Optional[Callable[..., dict[str, Any]]] = None,
    *,
    workspaces: Optional[list[tuple[str, str]]] = None,
) -> list[Brief]:
    """Fire the Comprehensive Brief for every active workspace.

    Args:
        graph: shared KG facade.
        llm_call: shared LLM callable (single Anthropic client at the
            caller).
        ds_runner: optional override (e.g. tests).
        workspaces: optional explicit list, e.g. a single-tenant
            backfill. Defaults to the full active set.

    Returns the list of Briefs produced. Per-workspace failures are
    logged and skipped (so a bad row doesn't poison the whole Monday
    run). The scheduled cron should treat a non-empty return as success;
    persistent failure across a workspace is left for a separate
    monitoring path.
    """
    if workspaces is None:
        workspaces = _list_active_workspaces()
    results: list[Brief] = []
    for workspace_id, dataset_slug in workspaces:
        try:
            brief = run_brief_comprehensive(
                workspace_id=workspace_id,
                dataset_slug=dataset_slug,
                graph=graph,
                llm_call=llm_call,
                ds_runner=ds_runner,
            )
            results.append(brief)
        except Exception:
            logger.exception(
                "Comprehensive Brief failed for workspace=%s dataset=%s; "
                "continuing with the remaining workspaces.",
                workspace_id,
                dataset_slug,
            )
            continue
    logger.info(
        "Monday Brief batch complete: %d/%d workspaces succeeded.",
        len(results),
        len(workspaces),
    )
    return results


__all__ = ["run_monday_brief_for_all_workspaces"]
