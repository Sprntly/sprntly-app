"""Brief generation — Comprehensive composition.

Spec source: Master PRD §4.2 — the Monday Brief always runs the
Comprehensive tier, regardless of team size, plan, or trust level. This
module wires together:

    1. KG session context           (PR #12 — GraphFacade)
    2. Dataset corpus → CanonicalUserRow  (PR #15 — data_format
                                            normalizers)
    3. Data summary + quality gate  (PR #15 — data_format)
    4. DS Agent Comprehensive tier  (PR #21 — ds_agent.tiers.comprehensive)
    5. Synthesis 11-step assembly   (PR #18 — synthesis.brief_assembly)
    6. Persist + cache              (this PR)

Defensive design notes:

  * DS Agent is invoked via an injectable `ds_runner` callable so we
    don't hard-import its package on the branch (PR #21 hasn't merged
    yet and we want CI to stay green here). The default lazily imports
    `ds_agent.tiers.comprehensive.run_comprehensive` at call time so
    production paths "just work" once both PRs are merged.
  * `data_format` (PR #15) is similarly lazy-imported. If it's not yet
    available on the branch the build still imports clean; calls fall
    back to a permissive quality verdict so manual smoke-tests work.
  * DS failures degrade gracefully — Synthesis can produce signal-only
    recommendations from KG state, so a DS exception is logged-and-swallowed
    rather than failing the whole Brief.
  * The Comprehensive Brief cache (week-keyed) is checked BEFORE any
    expensive call. A cache hit short-circuits everything below it.
"""
from __future__ import annotations

import importlib
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from app.brief.cache import (
    get_cached_brief,
    save_cached_brief,
    week_start_iso,
)
from app.brief.persist import persist_brief
from app.corpus import load_corpus
from app.graph import GraphFacade, Workspace
from app.synthesis.brief_assembly import (
    Brief,
    CompetitivePulse,
    SignalHealth,
    assemble_brief,
)

logger = logging.getLogger(__name__)


# ─────────────────────── lazy helpers for soft deps ───────────────────────


def _default_ds_runner(user_table: Any, goal_metric: str) -> dict[str, Any]:
    """Default DS Comprehensive invocation.

    Lazy-imports `ds_agent.tiers.comprehensive.run_comprehensive` so this
    module imports clean on branches where PR #21 isn't merged. Tests
    inject a deterministic `ds_runner` to bypass this entirely.
    """
    try:
        mod = importlib.import_module("ds_agent.tiers.comprehensive")
    except ImportError as exc:
        logger.warning(
            "ds_agent.tiers.comprehensive not importable (%s); "
            "running Brief with empty DS output.",
            exc,
        )
        return {}
    runner = getattr(mod, "run_comprehensive", None)
    if runner is None:
        logger.warning(
            "ds_agent.tiers.comprehensive has no run_comprehensive; "
            "running Brief with empty DS output."
        )
        return {}
    result = runner(user_table, goal_metric)
    # Some implementations return a pydantic model; normalise to dict so
    # Synthesis Step 2 sees a uniform shape.
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")  # type: ignore[no-any-return]
    if isinstance(result, dict):
        return result
    logger.warning(
        "DS runner returned non-dict %s; coercing to {} for Synthesis.",
        type(result).__name__,
    )
    return {}


def _normalize_corpus_to_rows(
    workspace: Workspace, dataset_slug: str
) -> list[Any]:
    """Read the dataset corpus + dispatch to the right normalizer.

    The connector type lives in `workspace.preferences['connector_type']`
    (set when the user wires up Amplitude / Mixpanel / CSV). If absent
    we fall back to "csv" — the safest no-op normalizer. Errors here
    don't kill the Brief: we return an empty list and let the quality
    gate flag INSUFFICIENT data.
    """
    try:
        corpus = load_corpus(dataset_slug)
    except FileNotFoundError:
        logger.info(
            "Dataset %s not found on disk; treating as empty user table.",
            dataset_slug,
        )
        return []
    connector_type = str(
        workspace.preferences.get("connector_type") or "csv"
    ).lower()
    try:
        normalizers = importlib.import_module("app.data_format.normalizers")
    except ImportError:
        logger.warning(
            "app.data_format.normalizers unavailable; "
            "skipping row normalisation (empty user_table)."
        )
        return []
    normalizer = getattr(
        normalizers, f"normalize_{connector_type}", None
    ) or getattr(normalizers, "normalize_csv", None)
    if normalizer is None:
        logger.warning(
            "No normalizer found for connector_type=%s; "
            "passing empty user_table.",
            connector_type,
        )
        return []
    try:
        return list(normalizer(corpus))
    except Exception:  # data_format is soft-dep; degrade rather than fail
        logger.exception(
            "Normalizer %s failed for dataset=%s; "
            "continuing with empty user_table.",
            connector_type,
            dataset_slug,
        )
        return []


def _build_data_summary(
    rows: list[Any], goal_metric: str
) -> dict[str, Any]:
    """Soft-dep wrapper around `app.data_format.summarize.build_data_summary`."""
    try:
        summarize = importlib.import_module("app.data_format.summarize")
    except ImportError:
        return {"rows": len(rows), "goal_metric": goal_metric, "stub": True}
    fn = getattr(summarize, "build_data_summary", None)
    if fn is None:
        return {"rows": len(rows), "goal_metric": goal_metric, "stub": True}
    try:
        result = fn(rows, goal_metric)
        if hasattr(result, "model_dump"):
            return result.model_dump(mode="json")  # type: ignore[no-any-return]
        return dict(result) if isinstance(result, dict) else {"summary": result}
    except Exception:
        logger.exception("build_data_summary failed; returning empty summary.")
        return {"rows": len(rows), "goal_metric": goal_metric, "error": True}


# ─────────────────────── quality verdict (with fallback) ───────────────────────


# Stable verdict strings — kept here so consumers don't need to import
# app.data_format.quality to know the shape.
QUALITY_INSUFFICIENT = "INSUFFICIENT"
QUALITY_LOW = "LOW"
QUALITY_MEDIUM = "MEDIUM"
QUALITY_HIGH = "HIGH"


def _assess_quality(rows: list[Any]) -> str:
    """Wrapper around `app.data_format.quality.assess_quality`.

    If the package is missing we return MEDIUM (permissive fallback —
    we'd rather generate a Brief than block on a soft-dep miss). When
    the package returns an enum we coerce to its `.value` so the
    string-based switch in `run_brief_comprehensive` stays portable.
    """
    if not rows:
        return QUALITY_INSUFFICIENT
    try:
        quality = importlib.import_module("app.data_format.quality")
    except ImportError:
        return QUALITY_MEDIUM
    fn = getattr(quality, "assess_quality", None)
    if fn is None:
        return QUALITY_MEDIUM
    try:
        verdict = fn(rows)
    except Exception:
        logger.exception("assess_quality failed; defaulting to MEDIUM.")
        return QUALITY_MEDIUM
    # Accept enum, string, or anything with `.value` / `.name`.
    if hasattr(verdict, "value"):
        return str(verdict.value).upper()
    if hasattr(verdict, "name"):
        return str(verdict.name).upper()
    return str(verdict).upper()


# ─────────────────────── degraded-brief builders ───────────────────────


def _degraded_brief_for_workspace(
    workspace_id: str,
    workspace: Optional[Workspace],
    quality_verdict: str,
) -> Brief:
    """Build a structurally-valid Brief that surfaces a single data-quality
    warning recommendation. Used when the quality gate flags INSUFFICIENT
    data — we still ship a Brief so the PM knows something needs attention,
    but it carries no recommendations they can act on.
    """
    from app.synthesis.hypothesis import (
        HypothesisFraming,
        HypothesisImpact,
        HypothesisOutput,
        SignalCitation,
    )
    company = workspace.company_name if workspace is not None else "your workspace"
    warning = HypothesisOutput(
        rank=1,
        title="Data quality is insufficient for a Comprehensive Brief",
        framing=HypothesisFraming(
            signal_summary=(
                f"The connected user-table for {company} has too few rows or "
                "missing core columns to drive a Comprehensive analysis."
            ),
            hypothesis=(
                "Reconnecting the analytics source (or backfilling at least "
                "the goal metric, user id, timestamp columns) will unblock "
                "next Monday's Brief."
            ),
            predicted_impact=HypothesisImpact(
                metric="brief_actionability",
                direction="up",
                low=0.0,
                high=1.0,
                basis="Data-quality gate (spec §4.2).",
            ),
            assumptions=[
                "The current connector is healthy.",
                "The corpus is missing rows rather than the entire connector being down.",
            ],
            disconfirming_signals=[],
        ),
        supporting_signals=[
            SignalCitation(
                signal_id="data-quality-gate",
                source_tool="agent",
                summary="Data quality verdict: INSUFFICIENT.",
                confidence=1.0,
            )
        ],
        confidence="high",
        ds_agent_tier="comprehensive",
        reversal_condition=(
            "Verdict upgrades to LOW or above on the next scheduled run."
        ),
    )
    return Brief(
        brief_id=f"brief-degraded-{workspace_id}-{week_start_iso()}",
        workspace_id=workspace_id,
        generated_at=datetime.now(timezone.utc),
        kpi_status=[],
        recommendations=[warning],
        signal_health=SignalHealth(total_active=0),
        competitive_pulse=CompetitivePulse(active=False),
        open_outcomes=[],
        caveats=[f"Data quality verdict: {quality_verdict}. Brief degraded."],
        metadata={
            "degraded": True,
            "quality_verdict": quality_verdict,
            "ds_agent_tier": "comprehensive",
        },
    )


# ─────────────────────── orchestrator ───────────────────────


def _resolve_goal_metric(workspace: Workspace) -> str:
    """Pick the goal metric the DS Agent should optimise.

    Preference order: the north_star KPI tree node → the first OKR
    string → "activation" as a last-resort default so DS never gets
    an empty string.
    """
    for k in workspace.kpi_tree:
        if k.role == "north_star":
            return k.name
    if workspace.strategy.okrs:
        return workspace.strategy.okrs[0]
    return "activation"


def run_brief_comprehensive(
    workspace_id: str,
    dataset_slug: str,
    graph: GraphFacade,
    llm_call: Callable[..., dict[str, Any]],
    ds_runner: Optional[Callable[..., dict[str, Any]]] = None,
    *,
    use_cache: bool = True,
) -> Brief:
    """End-to-end Comprehensive Brief.

    Steps (Master PRD §4.2 + Synthesis §3.2):

        1. Cache check          (week-keyed; short-circuits everything below)
        2. Session context      (graph.load_session_context)
        3. Corpus → rows        (data_format normalizer dispatch)
        4. Data summary         (data_format.summarize)
        5. Quality assessment   (data_format.quality)
        6. Quality gate         (INSUFFICIENT → degraded Brief)
        7. DS Comprehensive     (ds_agent.tiers.comprehensive)
        8. Synthesis assembly   (synthesis.brief_assembly.assemble_brief)
        9. LOW-quality caveat   (annotate Brief.caveats)
        10. Persist             (briefs table)
        11. Cache               (cached_briefs table, week-keyed)

    Args:
        workspace_id: tenant.
        dataset_slug: which dataset's user-table corpus to load.
        graph: KG facade.
        llm_call: `call_json`-compatible callable injected for testability.
        ds_runner: optional override for the DS Comprehensive call.
            Defaults to a lazy import of `ds_agent.tiers.comprehensive`.
        use_cache: set False on manual smoke-tests that want a fresh run.

    Returns the (cached or freshly-generated) Brief.
    """
    runner = ds_runner or _default_ds_runner

    # Step 1: cache check.
    week_start = week_start_iso()
    if use_cache:
        cached = get_cached_brief(workspace_id, week_start)
        if cached is not None:
            logger.info(
                "Comprehensive Brief cache HIT workspace=%s week=%s",
                workspace_id,
                week_start,
            )
            try:
                return Brief.model_validate(cached)
            except Exception:
                logger.exception(
                    "Cached Brief failed validation; regenerating."
                )

    # Step 2: session context.
    session_ctx = graph.load_session_context(workspace_id)
    workspace: Optional[Workspace] = session_ctx.get("workspace")
    if workspace is None:
        # Unknown workspace — Synthesis already handles this gracefully
        # (returns an empty Brief with a "not found" caveat). Delegate
        # to it for consistency rather than duplicating the message.
        brief = assemble_brief(workspace_id, None, graph, llm_call)
        if use_cache:
            try:
                save_cached_brief(
                    workspace_id,
                    week_start,
                    brief.model_dump(mode="json"),
                    dataset_slug=dataset_slug,
                )
            except Exception:
                logger.exception("Failed to cache empty-workspace Brief.")
        return brief

    # Step 3: corpus → rows.
    rows = _normalize_corpus_to_rows(workspace, dataset_slug)
    goal_metric = _resolve_goal_metric(workspace)

    # Step 4: data summary (currently advisory — passed through Brief.metadata).
    data_summary = _build_data_summary(rows, goal_metric)

    # Step 5: quality assessment.
    quality_verdict = _assess_quality(rows)

    # Step 6: quality gate.
    if quality_verdict == QUALITY_INSUFFICIENT:
        logger.info(
            "Data quality INSUFFICIENT for workspace=%s dataset=%s; "
            "returning degraded Brief.",
            workspace_id,
            dataset_slug,
        )
        brief = _degraded_brief_for_workspace(
            workspace_id, workspace, quality_verdict
        )
        brief.metadata["data_summary"] = data_summary
        try:
            persist_brief(brief, dataset_slug=dataset_slug)
        except Exception:
            logger.exception("Failed to persist degraded Brief.")
        if use_cache:
            try:
                save_cached_brief(
                    workspace_id,
                    week_start,
                    brief.model_dump(mode="json"),
                    dataset_slug=dataset_slug,
                )
            except Exception:
                logger.exception("Failed to cache degraded Brief.")
        return brief

    # Step 7: DS Comprehensive.
    try:
        ds_output: dict[str, Any] = runner(rows, goal_metric) or {}
    except Exception:
        logger.exception(
            "DS runner failed for workspace=%s dataset=%s; "
            "falling back to signal-only Synthesis.",
            workspace_id,
            dataset_slug,
        )
        ds_output = {}

    # Step 8: Synthesis assembly.
    brief = assemble_brief(workspace_id, ds_output, graph, llm_call)

    # Step 9: LOW-quality caveat.
    if quality_verdict == QUALITY_LOW:
        brief.caveats.append(
            "LOW data quality — recommendations may be under-supported by "
            "the analytics signal pool. Treat as directional."
        )
    brief.metadata["quality_verdict"] = quality_verdict
    brief.metadata["data_summary"] = data_summary
    brief.metadata["ds_findings_count"] = len(
        (ds_output or {}).get("findings") or []
    )
    brief.metadata["ds_agent_tier"] = "comprehensive"

    # Step 10: persist.
    try:
        persist_brief(brief, dataset_slug=dataset_slug)
    except Exception:
        # Persisting is best-effort during the migration window — a
        # downstream consumer (cache, return value) still works.
        logger.exception("Failed to persist Comprehensive Brief.")

    # Step 11: cache.
    if use_cache:
        try:
            save_cached_brief(
                workspace_id,
                week_start,
                brief.model_dump(mode="json"),
                dataset_slug=dataset_slug,
            )
        except Exception:
            logger.exception("Failed to cache Comprehensive Brief.")

    return brief


__all__ = [
    "run_brief_comprehensive",
    "QUALITY_INSUFFICIENT",
    "QUALITY_LOW",
    "QUALITY_MEDIUM",
    "QUALITY_HIGH",
]
