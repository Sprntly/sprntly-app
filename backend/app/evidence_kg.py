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
  4. run ONE gateway llm_call (binding the `evidence-brief` skill) that
     assembles the evidence brief — a single self-contained HTML visual brief —
     strictly from those signals (never inventing),
  5. decision-log it (kg_refs = signal/hypothesis/theme ids used),
  6. write that HTML into `payload_md`, which the UI renders in a sandboxed
     iframe (variant v3).

Falls back to the legacy corpus path when the KG has no backing for the
insight (no theme_id, no hypothesis, no signals), so it never hard-fails.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from app.db import complete_evidence, fail_evidence, get_brief_by_id
from app.graph.decision_log import log_agent_decision
from app.graph.facade import GraphFacade
from app.graph.gateway import llm_call
from app.graph.types import Entity, Signal
from app.html_style import inject_canonical_css
from app.llm import strip_code_fence
from app.prompts import (
    EVIDENCE_KG_PROMPT_VERSION,
    EVIDENCE_KG_SYSTEM,
    EVIDENCE_KG_USER_TEMPLATE,
)
from app.skills.loader import get_skill
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

    # Gather the ordered (source_id, edge_type) pairs for each target FIRST
    # (SUPPORTS edges before the rest, mirroring the original walk order), then
    # batch the signal fetch into ONE query (kills the per-edge N+1). The
    # `edges_to` reads are unchanged; only the per-signal lookups are batched.
    edge_items: list[tuple[str, str]] = []  # (signal source_id, edge.type)

    def _gather_edges(target_id: str) -> None:
        for edge in facade.edges_to(enterprise_id, target_id, type="SUPPORTS") + [
            e for e in facade.edges_to(enterprise_id, target_id)
            if e.type != "SUPPORTS"
        ]:
            if edge.source_kind != "signal":
                continue
            edge_items.append((edge.source_id, edge.type))

    if hypothesis is not None:
        _gather_edges(hypothesis.id)
    if theme_id:
        _gather_edges(theme_id)

    signals_by_id = facade.get_signals(
        enterprise_id, [sid for sid, _ in edge_items]
    )

    for source_id, edge_type in edge_items:
        if source_id in seen:
            continue
        sig = signals_by_id.get(source_id)
        if sig is None or sig.properties.get("superseded_by"):
            continue
        seen.add(sig.id)
        trail.append(_signal_to_trail_item(sig, edge_type))

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
    on_delta=None,
) -> tuple[str, dict]:
    """Build the KG-grounded evidence brief (self-contained HTML) for one
    brief insight.

    `on_delta(text)` — optional; forwards each HTML text delta as it streams so
    the client can render the brief progressively (see app.graph.token_stream).

    Returns (html, meta) where meta carries the kg_refs + the trail used.
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

    user = EVIDENCE_KG_USER_TEMPLATE.format(
        insight_json=json.dumps(insight, indent=2),
        evidence_trail=_render_trail(trail),
    )
    result = llm_call(
        enterprise_id=enterprise_id,
        agent=AGENT,
        purpose="generate_evidence",
        prompt_version=EVIDENCE_KG_PROMPT_VERSION,
        system=EVIDENCE_KG_SYSTEM,
        input=user,
        # Bind the evidence-brief skill: its SKILL.md is the METHOD *and* the
        # OUTPUT contract — the runner emits the skill's self-contained HTML
        # visual brief (converge ≥2 signals → wedge → best-chart-per-finding →
        # honesty pass → value-driven hypothesis), grounded in the trail. The
        # `evidence-brief` skill is a long-output skill (large HTML payload).
        skill="evidence-brief",
        on_delta=on_delta,
    )
    raw = result.output if isinstance(result.output, str) else str(result.output)
    # The model occasionally wraps the document in a ```html code fence despite
    # the prompt; strip it so the stored payload is raw HTML the UI can iframe.
    html = strip_code_fence(raw)
    # The model emits an EMPTY `<style>`; inject the canonical stylesheet here so
    # the stored brief is self-contained and every brief shares one design system
    # (see app.html_style) — the model no longer re-emits ~90 lines of CSS.
    html = inject_canonical_css(html, get_skill("evidence-brief").assets["evidence.css"])

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
    return html, meta


def _run_sync_kg(
    evidence_id: int, brief_id: int, insight_index: int, on_delta=None
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
        html, _meta = build_evidence_kg(
            facade, enterprise_id, insight, on_delta=on_delta
        )
    except NoKGBackingError as exc:
        logger.info(
            "evidence_kg: %s — falling back to legacy corpus path "
            "(evidence_id=%s)", exc, evidence_id,
        )
        from app.evidence_runner import _run_sync as _legacy_run_sync

        _legacy_run_sync(evidence_id, brief_id, insight_index, on_delta=on_delta)
        return

    title = insight.get("title") or f"Insight #{insight_index + 1}"
    complete_evidence(evidence_id=evidence_id, title=title, md=html)


async def generate_evidence_kg(
    evidence_id: int, brief_id: int, insight_index: int
) -> None:
    """KG-grounded evidence generation in a worker thread; update DB with the
    result. Drop-in replacement for evidence_runner.generate_evidence.

    Token-streams the brief's HTML to any connected client over
    `evidence:<evidence_id>` (mirrors prd_runner.generate_prd_and_warm): the
    sink publishes each delta from the LLM worker thread onto this loop, and a
    terminal frame closes the channel on success (done) or failure (error).
    PROGRESSIVE DISPLAY ONLY — the poll on GET /v1/evidence/{id} stays the
    authoritative source; publishing with no subscribers (warm/background
    callers) is a no-op. The corpus fallback streams over the same channel."""
    logger.info(
        "KG evidence generation starting evidence_id=%s brief_id=%s "
        "insight_index=%s", evidence_id, brief_id, insight_index,
    )
    from app.graph import token_stream

    channel = f"evidence:{evidence_id}"
    sink = token_stream.delta_sink(asyncio.get_running_loop(), channel)
    ok = False
    try:
        await asyncio.to_thread(
            _run_sync_kg, evidence_id, brief_id, insight_index, on_delta=sink
        )
        ok = True
        logger.info("KG evidence generation succeeded evidence_id=%s", evidence_id)
    except Exception as exc:  # noqa: BLE001
        msg = f"{type(exc).__name__}: {exc}"
        logger.exception("KG evidence generation failed evidence_id=%s", evidence_id)
        fail_evidence(evidence_id, msg)
    finally:
        token_stream.close(channel, kind="done" if ok else "error")
