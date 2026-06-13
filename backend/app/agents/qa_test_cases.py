"""QA Test Cases generator agent.

Produces comprehensive QA test cases from a PRD (Part A + Part B) and its
evidence trail. Output includes: functional tests, edge cases, negative tests,
integration tests, regression tests, performance tests, and security tests.

Each test case has: ID, title, preconditions, steps, expected result, priority,
and traceability back to PRD requirements.

Bound to the LLM gateway for attribution + decision logging.
"""
from __future__ import annotations

import asyncio
import json
import logging

from app.db.multi_agent_docs import complete_doc, fail_doc
from app.graph.gateway import llm_call

logger = logging.getLogger(__name__)

PROMPT_VERSION = "qa-test-cases-v1"
AGENT = "qa_test_cases"

_SYSTEM = """\
You are Sprntly's QA Test Cases agent. You produce implementation-ready and \
audit-ready QA test cases from a Product Requirements Document (PRD) and its \
supporting evidence.

Your output MUST include:

1. **Test Strategy Overview** — scope, approach, environments, entry/exit criteria.

2. **Functional Test Cases** — one per requirement/user flow. Each test case has:
   - TC-ID (e.g. TC-F-001)
   - Title
   - Requirement traced to (PRD section/requirement ID)
   - Preconditions
   - Test Steps (numbered)
   - Expected Result
   - Priority (P0-Critical / P1-High / P2-Medium / P3-Low)

3. **Edge Case & Negative Tests** — boundary values, invalid inputs, error \
   states, concurrency, timeout scenarios.

4. **Integration Test Cases** — cross-system, API contract, data flow tests.

5. **Performance Test Cases** — load, stress, response time baselines from PRD \
   metrics.

6. **Security Test Cases** — OWASP-relevant checks per the PRD's domain.

7. **Regression Test Suite** — critical paths that must not break.

8. **Test Data Requirements** — what test data is needed, how to set up fixtures.

Ground every test in the PRD's requirements. Reference specific PRD sections. \
Mark any test whose requirement is ambiguous with [ASSUMPTION]. \
Emit Markdown only — no commentary outside the document."""

_USER_TEMPLATE = """\
Generate comprehensive QA test cases for the following PRD.

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


def generate_qa_test_cases_sync(
    enterprise_id: str,
    prd: dict,
    evidence_md: str = "",
    clickup_context: str = "",
) -> str:
    """Generate QA test cases. Returns markdown."""
    result = llm_call(
        enterprise_id=enterprise_id,
        agent=AGENT,
        purpose="generate_qa_test_cases",
        prompt_version=PROMPT_VERSION,
        system=_SYSTEM,
        input=_build_input(prd, evidence_md, clickup_context),
    )
    md = result.output if isinstance(result.output, str) else str(result.output)

    try:
        from app.graph.decision_log import log_agent_decision
        log_agent_decision(
            enterprise_id=enterprise_id,
            agent=AGENT,
            decision_type="generate_qa_test_cases",
            factors={"prd_id": prd.get("id"), "has_evidence": bool(evidence_md)},
            output={"length": len(md)},
            model=result.model,
            prompt_version=result.prompt_version,
        )
    except Exception:
        logger.exception("QA test cases decision log write failed")

    return md


async def generate_qa_test_cases(
    doc_id: int,
    enterprise_id: str,
    prd: dict,
    evidence_md: str = "",
    clickup_context: str = "",
) -> None:
    """Run QA test case generation in a worker thread; update DB."""
    logger.info("QA test case generation starting doc_id=%s", doc_id)
    try:
        md = await asyncio.to_thread(
            generate_qa_test_cases_sync,
            enterprise_id, prd, evidence_md, clickup_context,
        )
        title = f"QA Test Cases — {prd.get('title', 'Untitled PRD')}"
        complete_doc(doc_id, title, md)
        logger.info("QA test case generation succeeded doc_id=%s", doc_id)
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        logger.exception("QA test case generation failed doc_id=%s", doc_id)
        fail_doc(doc_id, msg)
