"""Background PRD generation. Triggered when a user clicks 'Generate PRD';
the HTTP request returns immediately with a prd_id and status='generating',
the actual Claude call runs in a worker thread, and the prds row gets
updated to status='ready' (or 'failed') when done.

Mirrors evidence_runner; both consume a brief insight + corpus + template
and produce a markdown document via call_md.
"""
import asyncio
import json
import logging

from app.corpus import load_corpus, load_prd_template
from app.db import complete_prd, fail_prd, get_brief_by_id
from app.llm import call_md
from app.prompts import PRD_SYSTEM, PRD_USER_TEMPLATE

logger = logging.getLogger(__name__)


def _run_sync(prd_id: int, brief_id: int, insight_index: int) -> None:
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
    template = load_prd_template()
    user = PRD_USER_TEMPLATE.format(
        insight_json=json.dumps(insight, indent=2),
        corpus=corpus.joined(),
        template=template,
    )
    md = call_md(system=PRD_SYSTEM, user=user)
    title = insight.get("title") or f"Insight #{insight_index + 1}"
    complete_prd(prd_id=prd_id, title=title, md=md)


async def generate_prd(prd_id: int, brief_id: int, insight_index: int) -> None:
    """Run PRD generation in a worker thread; update DB with result."""
    logger.info(
        "PRD generation starting prd_id=%s brief_id=%s insight_index=%s",
        prd_id,
        brief_id,
        insight_index,
    )
    try:
        await asyncio.to_thread(_run_sync, prd_id, brief_id, insight_index)
        logger.info("PRD generation succeeded prd_id=%s", prd_id)
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        logger.exception("PRD generation failed prd_id=%s", prd_id)
        fail_prd(prd_id, msg)
