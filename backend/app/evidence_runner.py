"""Background Evidence Page generation. Triggered when the user clicks
'View full evidence' on a brief insight; the HTTP request returns immediately
with an evidence_id and status='generating', the actual Claude call runs in a
worker thread, and the evidences row gets updated to status='ready' (or
'failed') when done.

The corpus fallback (KG-empty) path. Like evidence_kg, it is bound to the
`evidence-brief` skill via the gateway and produces the skill's native visual
HTML brief; the frontend wraps the body HTML with the house stylesheet in a
sandboxed iframe. (The KG-grounded primary path lives in app.evidence_kg.)
"""
import asyncio
import json
import logging

from app.corpus import load_corpus
from app.db import complete_evidence, fail_evidence, get_brief_by_id
from app.db.companies import company_id_for_slug
from app.graph.gateway import llm_call
from app.prompts import EVIDENCE_HTML_PROMPT_VERSION, EVIDENCE_HTML_SYSTEM, EVIDENCE_HTML_USER_TEMPLATE

logger = logging.getLogger(__name__)

_AGENT = "evidence"
_SKILL = "evidence-brief"


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
    company_id = company_id_for_slug(dataset)
    corpus = load_corpus(dataset)
    user = EVIDENCE_HTML_USER_TEMPLATE.format(
        insight_json=json.dumps(insight, indent=2),
        evidence_trail=corpus.joined(),
    )
    # Bind the evidence-brief skill (METHOD injected + version pinned) and stream
    # on the long read timeout — evidence-brief is a long-output skill, so the
    # gateway streams it and never trips the default non-streamed read timeout.
    result = llm_call(
        enterprise_id=company_id or dataset,
        agent=_AGENT,
        purpose="generate_evidence",
        prompt_version=EVIDENCE_HTML_PROMPT_VERSION,
        system=EVIDENCE_HTML_SYSTEM,
        input=user,
        skill=_SKILL,
    )
    md = str(result.output).strip()
    title = insight.get("title") or f"Insight #{insight_index + 1}"
    complete_evidence(evidence_id=evidence_id, title=title, md=md)


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
