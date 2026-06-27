"""Background Evidence Page generation — CORPUS FALLBACK path.

This is the resilient fallback the KG runner (app.evidence_kg) defers to when an
insight has no knowledge-graph backing. Like the KG path, it produces the
`evidence-brief` skill's self-contained HTML visual brief (variant v3) so the
output format is uniform regardless of grounding; the only difference is the
DATA it grounds on — here the per-dataset corpus instead of the KG evidence
trail. Both bind the `evidence-brief` skill through the gateway so the METHOD +
HTML rendering contract are identical, and both stream on the long read timeout
(the HTML brief is a large generation).

Triggered originally when the user clicks 'View full evidence': the HTTP
request returns immediately with an evidence_id and status='generating', the
Claude call runs in a worker thread, and the evidences row is updated to
status='ready' (or 'failed') when done.
"""
import asyncio
import json
import logging

from app.corpus import load_corpus
from app.db import complete_evidence, fail_evidence, get_brief_by_id
from app.graph.gateway import llm_call
from app.prompts import (
    EVIDENCE_KG_PROMPT_VERSION,
    EVIDENCE_KG_SYSTEM,
    EVIDENCE_KG_USER_TEMPLATE,
)
from app.synthesis_brief import resolve_company

logger = logging.getLogger(__name__)

AGENT = "evidence"


def _run_sync(evidence_id: int, brief_id: int, insight_index: int) -> None:
    brief = get_brief_by_id(brief_id)
    if not brief:
        raise RuntimeError(f"brief_id={brief_id} not found")
    insights = brief.get("insights") or []
    if not (0 <= insight_index < len(insights)):
        raise RuntimeError(
            f"insight_index={insight_index} out of range (0..{len(insights) - 1})"
        )
    insight = insights[insight_index]
    dataset = brief.get("dataset", "asurion")
    corpus = load_corpus(dataset)
    # No KG trail here — feed the corpus as the data the brief grounds on,
    # through the SAME HTML prompt the KG path uses (corpus-as-evidence-trail).
    user = EVIDENCE_KG_USER_TEMPLATE.format(
        insight_json=json.dumps(insight, indent=2),
        evidence_trail=corpus.joined(),
    )
    # Resolve the dataset slug to an enterprise id for the gateway; legacy
    # corpus datasets that own no company fall back to the slug itself (used for
    # telemetry/tenant scoping only).
    enterprise_id, _slug = resolve_company(dataset)
    result = llm_call(
        enterprise_id=enterprise_id or dataset,
        agent=AGENT,
        purpose="generate_evidence",
        prompt_version=EVIDENCE_KG_PROMPT_VERSION,
        system=EVIDENCE_KG_SYSTEM,
        input=user,
        # Same binding as the KG path: SKILL.md is METHOD + HTML output contract.
        # evidence-brief is a registered long-output skill, so the gateway
        # streams on the long read timeout (the HTML brief is a big generation).
        skill="evidence-brief",
    )
    html = result.output if isinstance(result.output, str) else str(result.output)
    title = insight.get("title") or f"Insight #{insight_index + 1}"
    complete_evidence(evidence_id=evidence_id, title=title, md=html)


async def generate_evidence(
    evidence_id: int, brief_id: int, insight_index: int
) -> None:
    """Run evidence generation in a worker thread; update DB with result."""
    logger.info(
        "Evidence generation starting evidence_id=%s brief_id=%s insight_index=%s",
        evidence_id,
        brief_id,
        insight_index,
    )
    try:
        await asyncio.to_thread(_run_sync, evidence_id, brief_id, insight_index)
        logger.info("Evidence generation succeeded evidence_id=%s", evidence_id)
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        logger.exception("Evidence generation failed evidence_id=%s", evidence_id)
        fail_evidence(evidence_id, msg)
