"""Multi-Agent Orchestrator — concurrent generation of all documents.

Runs in Aggressive Analysis Mode:
  Phase 1 (concurrent): PRD + Evidence generation
  Phase 2 (concurrent): User Stories + Technical Design + QA Test Cases + Risk Analysis
  Phase 3 (sequential): Traceability Matrix (needs outputs from Phase 2)

Each agent runs in a worker thread. The orchestrator coordinates phases,
collects outputs, and feeds downstream agents with upstream results.

ClickUp context ingestion: when the company has a ClickUp connection,
the orchestrator pulls the task's details (title, description, status,
comments, attachments, linked tasks) and feeds them into every agent
as additional grounding context.

Error-isolated: each agent can fail independently — the orchestrator
reports partial results. The traceability matrix degrades gracefully
when upstream docs are missing (it flags gaps).
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Optional

from app.db import get_brief_by_id
from app.db.companies import company_id_for_slug
from app.db.evidences import complete_evidence, fail_evidence, get_evidence, start_evidence
from app.db.multi_agent_docs import (
    complete_doc,
    fail_doc,
    get_doc,
    get_docs_by_run,
    get_run_status,
    start_doc,
)
from app.db.prds import get_prd_rendered, start_prd, find_existing_prd
from app.graph.decision_log import log_agent_decision

logger = logging.getLogger(__name__)


# ── ClickUp context ingestion ─────────────────────────────────────────

def _fetch_clickup_context(company_id: str) -> str:
    """Pull ClickUp task context for the company. Returns formatted markdown.

    Best-effort: returns empty string if ClickUp is not connected or any
    read fails. Never breaks the orchestrator.
    """
    try:
        from app.stories.push import _clickup_access_token
        token = _clickup_access_token(company_id)
    except Exception:
        logger.debug("ClickUp not connected for company=%s — skipping context", company_id)
        return ""

    try:
        from app.kg_ingest.pullers.clickup import pull
        tasks = list(pull(token))
        if not tasks:
            return ""

        lines = ["## ClickUp Tasks Context\n"]
        for t in tasks[:30]:  # Cap at 30 tasks to avoid context overflow
            lines.append(f"### {t.title}")
            lines.append(f"- **Status**: {t.properties.get('status', 'unknown')}")
            lines.append(f"- **Priority**: {t.properties.get('priority', 'none')}")
            lines.append(f"- **List**: {t.properties.get('list', 'unknown')}")
            tags = t.properties.get("tags", [])
            if tags:
                lines.append(f"- **Tags**: {', '.join(tags)}")
            assignees = t.properties.get("assignees", [])
            if assignees:
                lines.append(f"- **Assignees**: {', '.join(a for a in assignees if a)}")
            if t.text:
                lines.append(f"\n{t.text[:500]}\n")
            lines.append("")
        return "\n".join(lines)
    except Exception:
        logger.exception("ClickUp context pull failed for company=%s", company_id)
        return ""


# ── Orchestrator ──────────────────────────────────────────────────────


async def run_multi_agent_generation(
    brief_id: int,
    insight_index: int,
    company_id: str,
    dataset: str,
    run_id: str | None = None,
    mode: str = "aggressive",
    prd_id: int | None = None,
) -> dict[str, Any]:
    """Orchestrate multi-agent generation for a brief insight.

    Returns a status dict with run_id and per-doc status. The generation
    runs in background — caller polls /v1/multi-agent/{run_id} for status.

    Phases:
      1. PRD + Evidence (concurrent) — these produce the base context
      2. User Stories + Technical Design + QA Test Cases + Risk Analysis (concurrent)
      3. Traceability Matrix (sequential — needs Phase 2 outputs)
    """
    run_id = run_id or str(uuid.uuid4())
    is_aggressive = mode == "aggressive"

    brief = get_brief_by_id(brief_id)
    if not brief:
        raise RuntimeError(f"brief_id={brief_id} not found")
    insights = brief.get("insights") or []
    if not (0 <= insight_index < len(insights)):
        raise RuntimeError(f"insight_index={insight_index} out of range")

    insight = insights[insight_index]
    title = insight.get("title") or f"Insight #{insight_index + 1}"

    # ── ClickUp context (aggressive mode) ─────────────────────────────
    clickup_context = ""
    if is_aggressive and company_id:
        clickup_context = await asyncio.to_thread(_fetch_clickup_context, company_id)

    # ── Phase 1: PRD + Evidence (concurrent) ──────────────────────────
    logger.info("Multi-agent Phase 1 starting run_id=%s", run_id)

    # PRD generation
    from app.prd_runner import generate_prd, PRD_VARIANT
    from app.prompts import PRD_TEMPLATE_VERSION

    # The endpoint may pre-create the PRD row (stamped with run_id) so repeat
    # clicks dedupe against it; only create one here when called directly.
    if prd_id is None:
        prd_id = start_prd(
            brief_id=brief_id,
            insight_index=insight_index,
            title=title,
            template_version=PRD_TEMPLATE_VERSION,
            variant=PRD_VARIANT,
            run_id=run_id,
        )

    # Evidence generation
    from app.prompts import EVIDENCE_TEMPLATE_VERSION, EVIDENCE_VARIANT
    evidence_id = start_evidence(
        brief_id=brief_id,
        insight_index=insight_index,
        title=title,
        template_version=EVIDENCE_TEMPLATE_VERSION,
        variant=EVIDENCE_VARIANT,
    )

    # Run Phase 1 concurrently
    phase1_results = await asyncio.gather(
        generate_prd(prd_id, brief_id, insight_index),
        _generate_evidence_safe(evidence_id, brief_id, insight_index),
        return_exceptions=True,
    )

    for i, r in enumerate(phase1_results):
        if isinstance(r, BaseException):
            agent_name = ["PRD", "Evidence"][i]
            logger.error("Phase 1 %s failed: %s", agent_name, r)

    # Fetch the generated PRD and evidence for downstream agents
    prd = get_prd_rendered(prd_id) or {}
    evidence_row = get_evidence(evidence_id)
    evidence_md = (evidence_row or {}).get("payload_md", "") if evidence_row else ""

    if not is_aggressive:
        # Standard mode: just PRD + Evidence + User Stories
        stories_md = await _generate_stories_safe(company_id, prd)
        return {
            "run_id": run_id,
            "mode": mode,
            "status": "ready",
            "prd_id": prd_id,
            "evidence_id": evidence_id,
            "stories": stories_md,
        }

    # ── Phase 2: Aggressive analysis (concurrent) ─────────────────────
    logger.info("Multi-agent Phase 2 starting run_id=%s", run_id)

    # Create doc rows for all Phase 2 + 3 agents
    qa_doc_id = start_doc(brief_id, insight_index, prd_id, "qa_test_cases", title, run_id)
    td_doc_id = start_doc(brief_id, insight_index, prd_id, "technical_design", title, run_id)
    risk_doc_id = start_doc(brief_id, insight_index, prd_id, "risk_analysis", title, run_id)
    tm_doc_id = start_doc(brief_id, insight_index, prd_id, "traceability_matrix", title, run_id)

    # Generate user stories (needed for risk analysis + traceability)
    stories_md = await _generate_stories_safe(company_id, prd)

    # Import agents
    from app.agents.qa_test_cases import generate_qa_test_cases
    from app.agents.technical_design import generate_technical_design
    from app.agents.risk_analysis import generate_risk_analysis

    # Run Phase 2 concurrently
    phase2_results = await asyncio.gather(
        generate_qa_test_cases(
            qa_doc_id, company_id, prd, evidence_md, clickup_context,
        ),
        generate_technical_design(
            td_doc_id, company_id, prd, evidence_md, clickup_context,
        ),
        generate_risk_analysis(
            risk_doc_id, company_id, prd, evidence_md, stories_md, clickup_context,
        ),
        return_exceptions=True,
    )

    for i, r in enumerate(phase2_results):
        if isinstance(r, BaseException):
            agent_name = ["QA Test Cases", "Technical Design", "Risk Analysis"][i]
            logger.error("Phase 2 %s failed: %s", agent_name, r)

    # ── Phase 3: Traceability Matrix (sequential, needs Phase 2) ──────
    logger.info("Multi-agent Phase 3 starting run_id=%s", run_id)

    # Fetch Phase 2 outputs for traceability matrix input
    qa_doc = get_doc(qa_doc_id)
    td_doc = get_doc(td_doc_id)
    risk_doc = get_doc(risk_doc_id)

    qa_md = (qa_doc or {}).get("payload_md", "") if qa_doc else ""
    td_md = (td_doc or {}).get("payload_md", "") if td_doc else ""
    risk_md = (risk_doc or {}).get("payload_md", "") if risk_doc else ""

    from app.agents.traceability_matrix import generate_traceability_matrix

    await generate_traceability_matrix(
        tm_doc_id, company_id, prd, stories_md,
        qa_md, risk_md, td_md, evidence_md,
    )

    # ── Decision log ──────────────────────────────────────────────────
    try:
        log_agent_decision(
            enterprise_id=company_id,
            agent="multi_agent_orchestrator",
            decision_type="multi_agent_run",
            factors={
                "run_id": run_id,
                "mode": mode,
                "brief_id": brief_id,
                "insight_index": insight_index,
                "prd_id": prd_id,
                "evidence_id": evidence_id,
                "has_clickup_context": bool(clickup_context),
                "doc_ids": {
                    "qa_test_cases": qa_doc_id,
                    "technical_design": td_doc_id,
                    "risk_analysis": risk_doc_id,
                    "traceability_matrix": tm_doc_id,
                },
            },
            output={"status": "completed"},
            model="orchestrator",
            prompt_version="multi-agent-v1",
        )
    except Exception:
        logger.exception("Multi-agent decision log write failed")

    logger.info("Multi-agent generation completed run_id=%s", run_id)

    return {
        "run_id": run_id,
        "mode": mode,
        "status": "ready",
        "prd_id": prd_id,
        "evidence_id": evidence_id,
        "doc_ids": {
            "qa_test_cases": qa_doc_id,
            "technical_design": td_doc_id,
            "risk_analysis": risk_doc_id,
            "traceability_matrix": tm_doc_id,
        },
    }


async def _generate_evidence_safe(
    evidence_id: int, brief_id: int, insight_index: int
) -> None:
    """Run KG-grounded evidence generation, matching the evidence route."""
    from app.evidence_kg import generate_evidence_kg
    await generate_evidence_kg(evidence_id, brief_id, insight_index)


async def _generate_stories_safe(company_id: str, prd: dict) -> str:
    """Generate user stories from PRD. Returns formatted markdown. Best-effort."""
    if not prd or prd.get("status") != "ready":
        return ""
    try:
        from app.config import settings
        from app.stories.generate import generate_user_stories

        # Honor the same fan-out settings as the /v1/stories route — this path
        # used to fall back to strategy="single" (one ~3 min 32k-token call,
        # observed 171s on prod vs ~110s fanned out) because it never passed a
        # strategy. There is no per-batch consumer here (the result is one
        # markdown blob), so no on_batch.
        stories = await asyncio.to_thread(
            generate_user_stories,
            company_id,
            prd_id=prd.get("id"),
            strategy="fanout" if settings.ticket_gen_fanout else "single",
            batch_size=settings.ticket_gen_batch_size,
            max_parallel=settings.ticket_gen_max_parallel,
        )
        lines = ["## User Stories\n"]
        for s in stories:
            lines.append(f"### {s.title}")
            lines.append(s.body)
            if s.acceptance_criteria:
                lines.append("\n**Acceptance Criteria:**")
                for ac in s.acceptance_criteria:
                    lines.append(f"- {ac}")
            if s.priority:
                lines.append(f"\n_Priority: {s.priority}_")
            if s.route:
                lines.append(f"_Route: {s.route}_")
            lines.append("")
        return "\n".join(lines)
    except Exception:
        logger.exception("User stories generation failed for prd_id=%s", prd.get("id"))
        return ""
