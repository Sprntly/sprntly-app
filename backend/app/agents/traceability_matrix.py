"""Traceability Matrix generator agent.

Produces a full requirements traceability matrix linking:
  Requirements → User Stories → Test Cases → Risks → Implementation Tasks

Ensures complete coverage: every requirement has stories, tests, and risk
assessment. Flags gaps in coverage.

Bound to the LLM gateway for attribution + decision logging.
"""
from __future__ import annotations

import asyncio
import logging

from app.db.multi_agent_docs import complete_doc, fail_doc
from app.graph.gateway import llm_call

logger = logging.getLogger(__name__)

PROMPT_VERSION = "traceability-matrix-v1"
AGENT = "traceability_matrix"

_SYSTEM = """\
You are Sprntly's Traceability Matrix agent. You produce audit-ready \
requirements traceability matrices from a PRD, user stories, QA test cases, \
risk analysis, and technical design.

Your output MUST include:

1. **Requirements Traceability Matrix (RTM)** — a comprehensive table with \
   columns:
   | REQ-ID | Requirement | Source (PRD Section) | User Story | Test Case ID | \
   Risk ID | Design Section | Status | Coverage |

   Every row traces ONE requirement through the FULL lifecycle.

2. **Forward Traceability** — Requirements → Design → Implementation → Test:
   Prove every requirement has a design, an implementation plan, and at \
   least one test case.

3. **Backward Traceability** — Test → Implementation → Design → Requirement:
   Prove every test case traces back to a real requirement (no orphan tests).

4. **Coverage Analysis**:
   - **Requirements Coverage**: % of requirements with at least one test case
   - **Test Coverage**: % of test cases mapped to requirements
   - **Risk Coverage**: % of requirements with risk assessment
   - **Design Coverage**: % of requirements with technical design

5. **Gap Report** — requirements without test coverage, tests without \
   requirements, risks without mitigation, design gaps. Each with:
   - GAP-ID
   - Type (Untested Requirement / Orphan Test / Unmitigated Risk / \
     Missing Design)
   - Description
   - Recommended Action
   - Priority

6. **Verification & Validation Summary** — how each requirement will be \
   verified (inspection / analysis / demonstration / test) and validated \
   (user acceptance criteria met).

7. **Sign-off Checklist** — audit-ready checklist for stakeholder sign-off.

Cross-reference IDs from all input documents (PRD requirement IDs, story \
titles, TC-IDs, RISK-IDs, design sections). Flag any ID that cannot be \
resolved with [UNRESOLVED]. Emit Markdown only."""

_USER_TEMPLATE = """\
Generate a complete requirements traceability matrix from these documents.

## PRD
{prd_content}

## User Stories
{stories}

## QA Test Cases
{qa_test_cases}

## Risk Analysis
{risk_analysis}

## Technical Design
{technical_design}

## Evidence
{evidence}
"""


def _build_input(
    prd: dict,
    stories_md: str = "",
    qa_test_cases_md: str = "",
    risk_analysis_md: str = "",
    technical_design_md: str = "",
    evidence_md: str = "",
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
        stories=stories_md or "(no user stories provided)",
        qa_test_cases=qa_test_cases_md or "(no QA test cases provided)",
        risk_analysis=risk_analysis_md or "(no risk analysis provided)",
        technical_design=technical_design_md or "(no technical design provided)",
        evidence=evidence_md or "(no evidence provided)",
    )


def generate_traceability_matrix_sync(
    enterprise_id: str,
    prd: dict,
    stories_md: str = "",
    qa_test_cases_md: str = "",
    risk_analysis_md: str = "",
    technical_design_md: str = "",
    evidence_md: str = "",
) -> str:
    """Generate traceability matrix. Returns markdown."""
    result = llm_call(
        enterprise_id=enterprise_id,
        agent=AGENT,
        purpose="generate_traceability_matrix",
        prompt_version=PROMPT_VERSION,
        system=_SYSTEM,
        input=_build_input(
            prd, stories_md, qa_test_cases_md,
            risk_analysis_md, technical_design_md, evidence_md,
        ),
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
            decision_type="generate_traceability_matrix",
            factors={"prd_id": prd.get("id"),
                     "has_stories": bool(stories_md),
                     "has_qa": bool(qa_test_cases_md),
                     "has_risk": bool(risk_analysis_md),
                     "has_design": bool(technical_design_md)},
            output={"length": len(md)},
            model=result.model,
            prompt_version=result.prompt_version,
        )
    except Exception:
        logger.exception("Traceability matrix decision log write failed")

    return md


async def generate_traceability_matrix(
    doc_id: int,
    enterprise_id: str,
    prd: dict,
    stories_md: str = "",
    qa_test_cases_md: str = "",
    risk_analysis_md: str = "",
    technical_design_md: str = "",
    evidence_md: str = "",
) -> None:
    """Run traceability matrix generation in a worker thread; update DB."""
    logger.info("Traceability matrix generation starting doc_id=%s", doc_id)
    try:
        md = await asyncio.to_thread(
            generate_traceability_matrix_sync,
            enterprise_id, prd, stories_md, qa_test_cases_md,
            risk_analysis_md, technical_design_md, evidence_md,
        )
        title = f"Traceability Matrix — {prd.get('title', 'Untitled PRD')}"
        complete_doc(doc_id, title, md)
        logger.info("Traceability matrix generation succeeded doc_id=%s", doc_id)
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        logger.exception("Traceability matrix generation failed doc_id=%s", doc_id)
        fail_doc(doc_id, msg)
