"""Technical Design Document generator agent.

Produces an implementation-ready technical design document from a PRD
(Part A + Part B), its evidence trail, and optional ClickUp task context.

Output includes: architecture, data model, API contracts, sequence diagrams
(Mermaid), dependency analysis, implementation plan, and deployment strategy.

Bound to the LLM gateway for attribution + decision logging.
"""
from __future__ import annotations

import asyncio
import logging

from app.db.multi_agent_docs import complete_doc, fail_doc
from app.graph.gateway import llm_call

logger = logging.getLogger(__name__)

PROMPT_VERSION = "technical-design-v1"
AGENT = "technical_design"

_SYSTEM = """\
You are Sprntly's Technical Design agent. You produce implementation-ready \
technical design documents from a Product Requirements Document (PRD) and its \
supporting evidence.

Your output MUST include:

1. **Executive Summary** — one-paragraph problem + solution overview.

2. **Architecture Overview** — high-level system architecture, component \
   diagram (describe in text or Mermaid), service boundaries, data flow.

3. **Data Model** — entities, relationships, schema changes, migrations \
   needed. Use table format.

4. **API Contracts** — new/modified endpoints, request/response schemas, \
   error codes, rate limits. Use OpenAPI-style descriptions.

5. **Sequence Diagrams** — key user flows in Mermaid syntax.

6. **Dependency Analysis** — new libraries, services, infrastructure, \
   external APIs needed. Version constraints.

7. **Implementation Plan** — ordered task breakdown with effort estimates \
   (S/M/L), dependencies between tasks, critical path.

8. **Performance Considerations** — expected load, caching strategy, \
   database indexing, query optimization needs.

9. **Security Considerations** — auth changes, data encryption, input \
   validation, OWASP compliance.

10. **Deployment Strategy** — feature flags, rollback plan, migration \
    steps, monitoring/alerting.

11. **Open Questions & Decisions Needed** — unresolved technical decisions \
    with trade-off analysis for each.

Ground every design decision in PRD requirements. Mark assumptions with \
[ASSUMPTION]. Mark decisions that need human input with [DECISION NEEDED]. \
Emit Markdown only — no commentary outside the document."""

_USER_TEMPLATE = """\
Generate a comprehensive technical design document for the following PRD.

## PRD
{prd_content}

## Evidence / Context
{evidence}

## ClickUp Task Context
{clickup_context}
"""


def _build_input(
    prd: dict,
    evidence_md: str = "",
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
        clickup_context=clickup_context or "(no ClickUp context)",
    )


def generate_technical_design_sync(
    enterprise_id: str,
    prd: dict,
    evidence_md: str = "",
    clickup_context: str = "",
) -> str:
    """Generate technical design document. Returns markdown."""
    result = llm_call(
        enterprise_id=enterprise_id,
        agent=AGENT,
        purpose="generate_technical_design",
        prompt_version=PROMPT_VERSION,
        system=_SYSTEM,
        input=_build_input(prd, evidence_md, clickup_context),
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
            decision_type="generate_technical_design",
            factors={"prd_id": prd.get("id"), "has_evidence": bool(evidence_md)},
            output={"length": len(md)},
            model=result.model,
            prompt_version=result.prompt_version,
        )
    except Exception:
        logger.exception("Technical design decision log write failed")

    return md


async def generate_technical_design(
    doc_id: int,
    enterprise_id: str,
    prd: dict,
    evidence_md: str = "",
    clickup_context: str = "",
) -> None:
    """Run technical design generation in a worker thread; update DB."""
    logger.info("Technical design generation starting doc_id=%s", doc_id)
    try:
        md = await asyncio.to_thread(
            generate_technical_design_sync,
            enterprise_id, prd, evidence_md, clickup_context,
        )
        title = f"Technical Design — {prd.get('title', 'Untitled PRD')}"
        complete_doc(doc_id, title, md)
        logger.info("Technical design generation succeeded doc_id=%s", doc_id)
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        logger.exception("Technical design generation failed doc_id=%s", doc_id)
        fail_doc(doc_id, msg)
