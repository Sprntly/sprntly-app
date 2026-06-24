"""QA Test Cases generator agent.

Produces Given/When/Then test SCENARIOS from a PRD (Part A + Part B) and its
evidence trail, via the vendored `test-scenario-builder` skill (its SKILL.md
is the METHOD layer: read the spec → happy paths → edge cases → failure modes
→ trace each to its requirement → risk-rank). The Sprntly OUTPUT contract is a
single `:::qa-scenarios` block the frontend renders as grouped scenario cards
(see web/app/lib/qa-adapter.ts).

Bound to the LLM gateway for attribution + decision logging.
"""
from __future__ import annotations

import asyncio
import logging

from app.db.multi_agent_docs import complete_doc, fail_doc
from app.graph.gateway import llm_call

logger = logging.getLogger(__name__)

PROMPT_VERSION = "qa-test-cases-v2"
AGENT = "qa_test_cases"

_SYSTEM = """\
You are Sprntly's QA Test Scenarios agent, running the **test-scenario-builder** \
skill's METHOD (prepended above). Apply that method to the PRD: read each \
requirement / acceptance criterion as the spec to verify; cover the HAPPY \
path(s), then EDGE cases (boundaries, empty/zero/max, concurrency, \
permissions, localization), then FAILURE modes (invalid input, timeouts, \
partial success, idempotency/retry); trace every scenario to the requirement \
it verifies; risk-rank so the highest-damage scenarios are flagged to test \
first.

GROUNDING DISCIPLINE (non-negotiable):
- Every scenario verifies a SPECIFIC requirement or acceptance criterion from \
the PRD. Never invent requirements, data, or expected results.
- When an expected result is assumed rather than stated, mark it [ASSUMPTION].
- Coverage gaps become open questions — never fabricated scenarios.

OUTPUT CONTRACT (Sprntly — emit EXACTLY this; ignore the skill's own output \
formatting): a short title line, one sentence of test strategy, then a single \
`:::qa-scenarios` fenced block whose body is JSON the app renders as grouped \
scenario cards. No other prose, no markdown tables, no TC-ID tables.

# QA Test Scenarios — <PRD title>

<one-sentence strategy: what's covered and the riskiest area to test first>

:::qa-scenarios
{
  "scenarios": [
    {
      "id": "QA-001",
      "group": "happy" | "edge" | "failure",
      "title": "<short scenario title>",
      "given": "<preconditions / starting state>",
      "when": "<the action under test>",
      "then": "<the expected, verifiable outcome>",
      "traces": "<the requirement or acceptance criterion this verifies>",
      "risk": "high" | "medium" | "low"
    }
  ],
  "open_questions": ["<coverage gap or ambiguity, if any>"]
}
:::

Order scenarios happy → edge → failure. `risk` reflects the damage if the \
scenario fails in production. Emit valid JSON only inside the block."""

_USER_TEMPLATE = """\
Generate Given/When/Then test scenarios for the spec below. The PRD's \
requirements and acceptance criteria ARE the spec to verify — derive scenarios \
from them, covering happy paths, edge cases, and failure modes, each traced \
back to the requirement it verifies.

## PRD (the spec — requirements + acceptance criteria)
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
        # Bind the test-scenario-builder skill: its SKILL.md is the METHOD
        # (happy → edge → failure → trace → risk-rank). The Sprntly
        # `:::qa-scenarios` output contract lives in _SYSTEM.
        skill="test-scenario-builder",
    )
    md = result.output if isinstance(result.output, str) else str(result.output)

    try:
        from app.graph.decision_log import log_agent_decision
        log_agent_decision(
            enterprise_id=enterprise_id,
            agent=AGENT,
            decision_type="generate_qa_test_cases",
            factors={
                "prd_id": prd.get("id"),
                "has_evidence": bool(evidence_md),
                "skill": "test-scenario-builder",
            },
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
        title = f"QA Test Scenarios — {prd.get('title', 'Untitled PRD')}"
        complete_doc(doc_id, title, md)
        logger.info("QA test case generation succeeded doc_id=%s", doc_id)
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        logger.exception("QA test case generation failed doc_id=%s", doc_id)
        fail_doc(doc_id, msg)
