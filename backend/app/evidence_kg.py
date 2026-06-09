"""KG-grounded Evidence Page generation.

The legacy path (app.evidence_runner) builds the evidence doc by handing the
brief insight + the whole corpus to one Claude call — corpus-only, KG-blind,
no provenance, no decision-log. This module repoints evidence at the
knowledge graph so the doc becomes the PROVENANCE TRAIL for a weekly-brief
insight: the actual data-source signals + KG reasoning that produced it.

THE LINKAGE (written by synthesis.agent.run_synthesis):
  - Each chosen brief insight carries a `theme_id` in its payload.
  - run_synthesis persists, per insight, a `hypothesis` Entity whose
    `properties.theme_id` matches the insight, with:
      * an ADDRESSES edge   hypothesis -> theme
      * SUPPORTS edges      signal     -> hypothesis  (one per backing signal)
  - The theme itself collects its convergence signals via inbound edges
    (edges_to the theme, source_kind="signal").

So given (brief_id, insight_index) we:
  1. read the insight -> theme_id,
  2. find the hypothesis Entity for this enterprise+theme,
  3. gather the EVIDENCE TRAIL = SUPPORTS signals (hypothesis-backing) UNION
     the theme's convergence signals, deduped, each with content +
     source_type + provenance + confidence + weight,
  4. run ONE gateway llm_call that assembles the evidence doc strictly from
     those signals (never inventing),
  5. decision-log it (kg_refs = signal/hypothesis/theme ids used),
  6. write the same `payload_md` the UI renders.

Falls back to the legacy corpus path when the KG has no backing for the
insight (no theme_id, no hypothesis, no signals), so it never hard-fails.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from app.corpus import load_evidence_template
from app.db import complete_evidence, fail_evidence, get_brief_by_id
from app.graph.decision_log import log_agent_decision
from app.graph.facade import GraphFacade
from app.graph.gateway import llm_call
from app.graph.types import Entity, Signal
from app.prompts import (
    EVIDENCE_KG_PROMPT_VERSION,
    EVIDENCE_KG_SYSTEM,
    EVIDENCE_KG_USER_TEMPLATE,
)
from app.synthesis_brief import resolve_company

logger = logging.getLogger(__name__)

AGENT = "evidence"


class NoKGBackingError(RuntimeError):
    """Raised when the insight has no KG evidence trail (no hypothesis/theme
    signals). The caller falls back to the legacy corpus path."""


def _find_hypothesis(
    facade: GraphFacade, enterprise_id: str, theme_id: Optional[str],
    insight_title: Optional[str],
) -> Optional[Entity]:
    """Resolve the insight to its hypothesis Entity.

    Delegates to the ONE shared resolver (`graph.retrieval.resolve_insight_
    hypothesis`) so the Evidence page and the PRD trail always ground on the
    SAME hypothesis for a given insight — including the no-`theme_id` path (both
    title-fall-back, else empty). Kept as a thin wrapper so the module-internal
    call sites (and tests) keep their name; the resolution logic lives in one
    place. Imported function-locally to avoid a load-time import cycle (retrieval
    pulls in graph types that re-enter through the facade)."""
    from app.graph.retrieval import resolve_insight_hypothesis

    return resolve_insight_hypothesis(facade, enterprise_id, theme_id, insight_title)


def _signal_to_trail_item(sig: Signal, edge_type: str) -> dict:
    """Flatten a Signal into the trail dict the prompt + tests consume.

    `provenance` is the source attribution: the connector/tool the signal came
    from (e.g. {"connector": "hubspot"} / {"agent": "..."}). We surface it raw
    so the doc can cite the actual data source, never inventing one."""
    return {
        "signal_id": sig.id,
        "source_type": sig.source_type,
        "kind": sig.kind,
        "content": sig.content,
        "provenance": sig.provenance or {},
        "confidence": round(float(sig.confidence), 3),
        "weight": round(float(sig.weight), 3),
        "edge": edge_type,
    }


def gather_evidence_trail(
    facade: GraphFacade,
    enterprise_id: str,
    *,
    theme_id: Optional[str],
    hypothesis: Optional[Entity],
) -> list[dict]:
    """The EVIDENCE TRAIL = SUPPORTS signals backing the hypothesis UNION the
    theme's convergence signals. Deduped by signal id (a signal can both
    support the hypothesis and converge on the theme). Skips superseded
    signals (bitemporal close), matching convergence's read posture."""
    seen: set[str] = set()
    trail: list[dict] = []

    def _collect(target_id: str) -> None:
        for edge in facade.edges_to(enterprise_id, target_id, type="SUPPORTS") + [
            e for e in facade.edges_to(enterprise_id, target_id)
            if e.type != "SUPPORTS"
        ]:
            if edge.source_kind != "signal" or edge.source_id in seen:
                continue
            sig = facade.get_signal(enterprise_id, edge.source_id)
            if sig is None or sig.properties.get("superseded_by"):
                continue
            seen.add(sig.id)
            trail.append(_signal_to_trail_item(sig, edge.type))

    if hypothesis is not None:
        _collect(hypothesis.id)
    if theme_id:
        _collect(theme_id)

    # Strongest evidence first — weight then confidence.
    trail.sort(key=lambda t: (-t["weight"], -t["confidence"]))
    return trail


def _render_trail(trail: list[dict]) -> str:
    """Human/LLM-readable rendering of the trail for the prompt. Each line
    carries the source attribution the doc must cite."""
    lines = []
    for t in trail:
        prov = json.dumps(t["provenance"], sort_keys=True) if t["provenance"] else "{}"
        lines.append(
            f"- [{t['source_type']} / {t['kind']}] "
            f"provenance={prov} "
            f"confidence={t['confidence']} weight={t['weight']} "
            f"(edge={t['edge']})\n  {t['content']}"
        )
    return "\n".join(lines)


def build_evidence_kg(
    facade: GraphFacade,
    enterprise_id: str,
    insight: dict,
) -> tuple[str, dict]:
    """Build the KG-grounded evidence markdown for one brief insight.

    Returns (payload_md, meta) where meta carries the kg_refs + the trail used.
    Raises NoKGBackingError when the KG has no signals for this insight so the
    caller can fall back to the legacy corpus path."""
    theme_id = insight.get("theme_id")
    title = insight.get("title")
    hypothesis = _find_hypothesis(facade, enterprise_id, theme_id, title)
    # If the insight carries no theme_id but we matched a hypothesis, recover
    # the theme_id from the hypothesis so we still pull theme convergence.
    if theme_id is None and hypothesis is not None:
        theme_id = hypothesis.properties.get("theme_id")

    trail = gather_evidence_trail(
        facade, enterprise_id, theme_id=theme_id, hypothesis=hypothesis
    )
    if not trail:
        raise NoKGBackingError(
            f"no KG evidence trail for insight theme_id={theme_id!r} "
            f"title={title!r} (enterprise={enterprise_id})"
        )

    template = load_evidence_template()
    user = EVIDENCE_KG_USER_TEMPLATE.format(
        insight_json=json.dumps(insight, indent=2),
        evidence_trail=_render_trail(trail),
        template=template,
    )
    result = llm_call(
        enterprise_id=enterprise_id,
        agent=AGENT,
        purpose="generate_evidence",
        prompt_version=EVIDENCE_KG_PROMPT_VERSION,
        system=EVIDENCE_KG_SYSTEM,
        input=user,
    )
    md = result.output if isinstance(result.output, str) else str(result.output)

    signal_ids = [t["signal_id"] for t in trail]
    kg_refs = list(signal_ids)
    if hypothesis is not None:
        kg_refs.append(hypothesis.id)
    if theme_id:
        kg_refs.append(theme_id)

    # Semantic decision log (§4d): what evidence produced this doc.
    log_agent_decision(
        enterprise_id=enterprise_id,
        agent=AGENT,
        decision_type="generate_evidence",
        factors={
            "theme_id": theme_id,
            "hypothesis_id": hypothesis.id if hypothesis else None,
            "signal_count": len(signal_ids),
            "source_types": sorted({t["source_type"] for t in trail}),
            "prompt_version": EVIDENCE_KG_PROMPT_VERSION,
        },
        reasoning=(
            f"Evidence grounded in {len(signal_ids)} converging signals across "
            f"{len(set(t['source_type'] for t in trail))} source types for "
            f"insight {title!r}."
        ),
        output={"insight_title": title},
        model=result.model,
        prompt_version=result.prompt_version,
        confidence=insight.get("confidence"),
        kg_refs=kg_refs,
    )

    meta = {
        "kg_refs": kg_refs,
        "theme_id": theme_id,
        "hypothesis_id": hypothesis.id if hypothesis else None,
        "trail": trail,
    }
    return md, meta


def _run_sync_kg(
    evidence_id: int, brief_id: int, insight_index: int
) -> None:
    """Inner worker (mirrors evidence_runner._run_sync). KG-grounded.

    Resolves the brief's dataset slug -> enterprise_id like synthesis_brief
    does, builds the evidence doc from the KG, and completes the row. On
    NoKGBackingError, defers to the legacy corpus runner so the doc still
    generates (resilient fallback)."""
    brief = get_brief_by_id(brief_id)
    if not brief:
        raise RuntimeError(f"brief_id={brief_id} not found")
    insights = brief.get("insights") or []
    if not (0 <= insight_index < len(insights)):
        raise RuntimeError(
            f"insight_index={insight_index} out of range (0..{len(insights) - 1})"
        )
    insight = insights[insight_index]

    enterprise_id, _slug = resolve_company(brief.get("dataset", "asurion"))
    facade = GraphFacade()
    try:
        md, _meta = build_evidence_kg(facade, enterprise_id, insight)
    except NoKGBackingError as exc:
        logger.info(
            "evidence_kg: %s — falling back to legacy corpus path "
            "(evidence_id=%s)", exc, evidence_id,
        )
        from app.evidence_runner import _run_sync as _legacy_run_sync

        _legacy_run_sync(evidence_id, brief_id, insight_index)
        return

    title = insight.get("title") or f"Insight #{insight_index + 1}"
    complete_evidence(evidence_id=evidence_id, title=title, md=md)


async def generate_evidence_kg(
    evidence_id: int, brief_id: int, insight_index: int
) -> None:
    """KG-grounded evidence generation in a worker thread; update DB with the
    result. Drop-in replacement for evidence_runner.generate_evidence."""
    logger.info(
        "KG evidence generation starting evidence_id=%s brief_id=%s "
        "insight_index=%s", evidence_id, brief_id, insight_index,
    )
    try:
        await asyncio.to_thread(_run_sync_kg, evidence_id, brief_id, insight_index)
        logger.info("KG evidence generation succeeded evidence_id=%s", evidence_id)
    except Exception as exc:  # noqa: BLE001
        msg = f"{type(exc).__name__}: {exc}"
        logger.exception("KG evidence generation failed evidence_id=%s", evidence_id)
        fail_evidence(evidence_id, msg)
