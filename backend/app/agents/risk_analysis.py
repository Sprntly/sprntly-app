"""Risk & Gap Analysis agent.

Produces a comprehensive risk assessment, gap analysis, and dependency map
from a PRD, its evidence trail, user stories, and ClickUp task context.

Identifies: missing requirements, risks, assumptions, dependencies,
compliance gaps, and mitigation strategies.

Bound to the LLM gateway for attribution + decision logging.
"""
from __future__ import annotations

import asyncio
import logging

from app.db.multi_agent_docs import complete_doc, fail_doc
from app.graph.gateway import llm_call

logger = logging.getLogger(__name__)

PROMPT_VERSION = "risk-analysis-v1"
AGENT = "risk_analysis"

_SYSTEM = """\
You are Sprntly's Risk & Gap Analysis agent. You produce audit-ready risk \
assessments from a Product Requirements Document (PRD), its evidence, user \
stories, and project context.

Your output MUST include:

1. **Risk Register** — each risk with:
   - RISK-ID (e.g. RISK-001)
   - Category (Technical / Business / Operational / Security / Compliance / \
     Schedule / Resource)
   - Description
   - Likelihood (High / Medium / Low)
   - Impact (High / Medium / Low)
   - Risk Score (Likelihood × Impact matrix)
   - Mitigation Strategy
   - Owner (role, not person)
   - Status (Open / Mitigated / Accepted)

2. **Missing Requirements Analysis** — requirements implied by the problem \
   statement or evidence but NOT explicitly stated in the PRD. Each with:
   - GAP-ID (e.g. GAP-001)
   - Description of missing requirement
   - Evidence/rationale for why it's needed
   - Recommended action
   - Priority (P0-P3)

3. **Assumptions Log** — every assumption the PRD makes (explicit or \
   implicit), with:
   - ASM-ID (e.g. ASM-001)
   - Assumption statement
   - Validation method
   - Risk if assumption is wrong
   - Status (Validated / Unvalidated / Invalid)

4. **Dependency Map** — internal and external dependencies:
   - DEP-ID (e.g. DEP-001)
   - Dependency description
   - Type (Technical / Organizational / External / Data)
   - Owner
   - Status (Confirmed / Pending / Blocked)
   - Impact if unavailable

5. **Compliance & Regulatory Gaps** — any compliance requirements relevant \
   to the PRD's domain that are not addressed.

6. **Impact Assessment** — what happens if this project is delayed, \
   descoped, or cancelled. Business impact quantified where possible.

7. **Mitigation Plan** — top-5 risks with detailed mitigation steps, \
   timeline, and success criteria.

Ground every finding in the PRD or evidence. Never invent risks for the \
sake of padding — only surface genuine concerns. Mark speculative items \
with [ASSUMPTION]. Emit Markdown only."""

_USER_TEMPLATE = """\
Produce a comprehensive risk & gap analysis for the following PRD and context.

## PRD
{prd_content}

## Evidence
{evidence}

## User Stories
{stories}

## ClickUp Task Context
{clickup_context}
"""


def _build_input(
    prd: dict,
    evidence_md: str = "",
    stories_md: str = "",
    clickup_context: str = "",
) -> str:
    sections = []
    human = prd.get("payload_md") or ""
    if human:
        sections.append(human)
    llm_part = prd.get("llm_part") or ""
    if llm_part:
        sections.append(f"\n---\n## Part B (Implementation Spec)\n{llm_part}")
    prd_content = "\n".join(sections) or "(no PRD content)"
    return _USER_TEMPLATE.format(
        prd_content=prd_content,
        evidence=evidence_md or "(no evidence provided)",
        stories=stories_md or "(no user stories provided)",
        clickup_context=clickup_context or "(no ClickUp context)",
    )


def generate_risk_analysis_sync(
    enterprise_id: str,
    prd: dict,
    evidence_md: str = "",
    stories_md: str = "",
    clickup_context: str = "",
) -> str:
    """Generate risk & gap analysis. Returns markdown."""
    result = llm_call(
        enterprise_id=enterprise_id,
        agent=AGENT,
        purpose="generate_risk_analysis",
        prompt_version=PROMPT_VERSION,
        system=_SYSTEM,
        input=_build_input(prd, evidence_md, stories_md, clickup_context),
        # Large markdown doc — stream on the long read timeout (was tripping
        # httpx.ReadTimeout on the default 120s non-streamed path).
        long_output=True,
    )
    md = result.output if isinstance(result.output, str) else str(result.output)

    try:
        from app.graph.decision_log import log_agent_decision
        log_agent_decision(
            enterprise_id=enterprise_id,
            agent=AGENT,
            decision_type="generate_risk_analysis",
            factors={"prd_id": prd.get("id"), "has_evidence": bool(evidence_md),
                     "has_stories": bool(stories_md)},
            output={"length": len(md)},
            model=result.model,
            prompt_version=result.prompt_version,
        )
    except Exception:
        logger.exception("Risk analysis decision log write failed")

    return md


async def generate_risk_analysis(
    doc_id: int,
    enterprise_id: str,
    prd: dict,
    evidence_md: str = "",
    stories_md: str = "",
    clickup_context: str = "",
) -> None:
    """Run risk analysis generation in a worker thread; update DB."""
    logger.info("Risk analysis generation starting doc_id=%s", doc_id)
    try:
        md = await asyncio.to_thread(
            generate_risk_analysis_sync,
            enterprise_id, prd, evidence_md, stories_md, clickup_context,
        )
        title = f"Risk & Gap Analysis — {prd.get('title', 'Untitled PRD')}"
        complete_doc(doc_id, title, md)
        logger.info("Risk analysis generation succeeded doc_id=%s", doc_id)
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        logger.exception("Risk analysis generation failed doc_id=%s", doc_id)
        fail_doc(doc_id, msg)
