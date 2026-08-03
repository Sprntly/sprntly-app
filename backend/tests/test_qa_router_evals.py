"""Routing evals for the keyword tier, after the built-in skill layer was cut.

This file used to label questions with the vendored SKILL.md method the router
was expected to pick ("Write a PRD …" → prd-author, "Diagnose our SaaS metrics
health" → saas-metrics-diagnosis, …). Those labels have no referent now: chat
selects no method, and every skill they named is either deleted or bound by
name from its own pipeline, never from a chat turn. Chat→PRD and chat→tickets
run through POST /v1/chat/intent, which does not import `skill_router` at all.

What replaced them is the property the trim was FOR. The keyword tier may now
emit only ids that name real machinery, and it must keep its hands off ordinary
questions — the regression that made this change urgent was a bare
`\\bprototype\\b` rule and a `\\bprd\\b …\\b(for|about|from)\\b` rule sending
"did the prototype ship last week?" and "what's in the PRD for onboarding?" to
`prd-author`, a long-output document generator, so a one-line question came
back as a full PRD.
"""
from __future__ import annotations

import pytest

import app.qa_agent as qa
from app.skill_router import _RULES, PIPELINE_SKILLS, detect_intent

# Questions that must reach the ORDINARY answer — no pipeline, no document.
# The first four are the reported bug; the rest are the method-only rules that
# went with it (prioritize, user-stories, decision-memo, interview-synthesis,
# feedback-synthesis, incident-runbook, fact-check).
ORDINARY: list[str] = [
    "did the prototype ship last week?",
    "what's in the PRD for onboarding?",
    "is the prd for billing signed off yet?",
    "who built the prototype for the new nav?",
    "how do we prioritize which bugs to fix first?",
    "what acceptance criteria did we agree on?",
    "was that a good decision in hindsight?",
    "what did we learn from the interviews with the design team?",
    "is there an incident runbook for the billing outage?",
    "can you fact-check that number for me?",
    "write a PRD for in-app onboarding checklists",
    "generate user stories for the checkout flow",
    "rank these ideas with RICE",
]

# ...and questions that must still reach a pipeline, because the work they ask
# for cannot be done by answering.
PIPELINES: list[tuple[str, str]] = [
    ("run a competitive intelligence report", "competitive-intelligence-review"),
    ("where do we stand vs our competitors?", "competitive-intelligence-review"),
    ("what are people saying about us on the app store?", "public-feedback-report"),
    ("do some deep research on our company pricing", "company-research"),
    ("give me a voice of customer report", "voice-of-customer-report"),
    ("summarize the feedback from our sales calls", "voice-of-customer-report"),
]


def test_every_rule_names_a_pipeline():
    """The admission test for `_RULES`, asserted rather than documented.

    A rule that names anything else is a rule that picks a prompt style, which
    is the whole class this change removed — and it would also route to an id
    `qa_agent.answer` has no dispatch branch for.
    """
    emitted = {skill_id for _, skill_id, _, _ in _RULES}
    assert emitted <= PIPELINE_SKILLS, (
        f"keyword rules emit non-pipeline ids: {sorted(emitted - PIPELINE_SKILLS)}"
    )


def test_pipeline_ids_all_dispatch():
    """Every id the router can produce must be invocable, or it dead-ends."""
    for skill_id in PIPELINE_SKILLS:
        assert qa._invocable(skill_id) is True


@pytest.mark.parametrize("question", ORDINARY)
def test_ordinary_questions_are_not_claimed(question):
    """No keyword rule may claim these — they are answered, not generated."""
    m = detect_intent(question)
    assert m is None or m.confidence < 0.75, (
        f"{question!r} was claimed by the {m.skill_id!r} rule; it should get an "
        "ordinary answer"
    )


@pytest.mark.parametrize("question,expected", PIPELINES)
def test_pipeline_questions_still_fast_path(question, expected):
    m = detect_intent(question)
    assert m is not None, f"{question!r} lost its pipeline fast-path"
    assert m.skill_id == expected
    assert m.confidence >= 0.75


def test_builtin_ids_are_no_longer_routable():
    """A vendored id must not be invocable from a chat turn by any route.

    `_routable` is the custom-skill test now, and `resolve_skill` is
    built-in-first — so returning True for a vendored id here would promise a
    company's upload and deliver the built-in's method.
    """
    for builtin in ("prd-author", "user-stories", "top-insights", "evidence-brief"):
        assert qa._routable(builtin, "co-1") is False
        assert qa._invocable(builtin, "co-1") is False
