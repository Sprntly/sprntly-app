"""Background Evidence Page generation. Triggered when the user clicks
'View full evidence' on a brief insight; the HTTP request returns immediately
with an evidence_id and status='generating', the actual Claude call runs in a
worker thread, and the evidences row gets updated to status='ready' (or
'failed') when done.

Mirrors prd_runner; both consume a brief insight + corpus + template and
produce a markdown document via call_md.
"""
import asyncio
import json
import logging

from app.corpus import (
    load_corpus,
    load_evidence_template,
    load_evidence_v2_template,
)
from app.db import complete_evidence, fail_evidence, get_brief_by_id
from app.llm import call_md
from app.prompts import (
    EVIDENCE_SYSTEM,
    EVIDENCE_USER_TEMPLATE,
    EVIDENCE_V2_SYSTEM,
    EVIDENCE_V2_USER_TEMPLATE,
)

logger = logging.getLogger(__name__)


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
    corpus = load_corpus(brief.get("dataset", "asurion"))
    template = load_evidence_template()
    user = EVIDENCE_USER_TEMPLATE.format(
        insight_json=json.dumps(insight, indent=2),
        corpus=corpus.joined(),
        template=template,
    )
    md = call_md(system=EVIDENCE_SYSTEM, user=user)
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


# ---------------------------------------------------------------------------
# v2 — sample-build evidence runner. Parallel path; reuses complete_evidence /
# fail_evidence since the row already lives in the same `evidences` table
# (distinguished by the `variant` column).
# ---------------------------------------------------------------------------

def _run_sync_v2(evidence_id: int, brief_id: int, insight_index: int) -> None:
    brief = get_brief_by_id(brief_id)
    if not brief:
        raise RuntimeError(f"brief_id={brief_id} not found")
    insights = brief.get("insights") or []
    if not (0 <= insight_index < len(insights)):
        raise RuntimeError(
            f"insight_index={insight_index} out of range (0..{len(insights) - 1})"
        )
    insight = insights[insight_index]
    corpus = load_corpus(brief.get("dataset", "asurion"))
    template = load_evidence_v2_template()
    user = EVIDENCE_V2_USER_TEMPLATE.format(
        insight_json=json.dumps(insight, indent=2),
        corpus=corpus.joined(),
        template=template,
    )
    md = call_md(system=EVIDENCE_V2_SYSTEM, user=user)
    title = insight.get("title") or f"Insight #{insight_index + 1}"
    complete_evidence(evidence_id=evidence_id, title=title, md=md)


async def generate_evidence_v2(
    evidence_id: int, brief_id: int, insight_index: int
) -> None:
    """Run v2 evidence generation in a worker thread; update DB with result."""
    logger.info(
        "Evidence-v2 generation starting evidence_id=%s brief_id=%s insight_index=%s",
        evidence_id,
        brief_id,
        insight_index,
    )
    try:
        await asyncio.to_thread(_run_sync_v2, evidence_id, brief_id, insight_index)
        logger.info("Evidence-v2 generation succeeded evidence_id=%s", evidence_id)
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        logger.exception("Evidence-v2 generation failed evidence_id=%s", evidence_id)
        fail_evidence(evidence_id, msg)
