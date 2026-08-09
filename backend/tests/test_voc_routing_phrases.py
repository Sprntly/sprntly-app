"""VoC routing phrase scope — intent first, phrases as the eval set.

Decision (Apurva, 2026-07-27): skill selection is INTENT matching — the haiku
router over skill descriptions — with the deterministic regex rules kept only
as a zero-latency fast-path for unambiguous shapes. This file is the scope of
phrasings that must reach the voice-of-customer surface, kept as TEST DATA
rather than routing rules, so new phrasings extend the eval set instead of
growing regexes.

Two tiers:
  - Unit (always runs): the fast-path matchers hit the high-precision shapes
    and stay quiet on the negative space.
  - Live eval (`-m integration`, real haiku router): paraphrases the regexes
    deliberately DON'T cover must still land on voice-of-customer-report via
    the intent stage — and the negative space must not.

The answer-path half of the decision (LLM-routed VoC gets the same live call
digest as regex-routed VoC) is covered in qa_agent by the routing itself:
both stages converge on decision.skill_id == "voice-of-customer-report".
"""
from __future__ import annotations

import os

import pytest

from app.skill_router import is_call_digest, is_voc_report_request


def _fast_path(question: str) -> bool:
    return is_voc_report_request(question) or is_call_digest(question)


# ── The phrase scope ─────────────────────────────────────────────────────────
# Shapes: summary/digest · superlative/complaint · channel-specific · trend ·
# sentiment · quotes · explicit-VoC — each with and without time windows.

FAST_PATH_POSITIVES = [
    # The two user-reported examples, verbatim (typos included).
    "give me the summary of customer feedback from today",
    "what is the number 1 user complain from todays customers conversation? ",
    # summary / digest
    "summarize customer feedback",
    "customer feedback summary for this week",
    "summarize the customer calls from last week",
    "recap this week's customer meetings",
    "what did we hear on our sales calls",
    "catch me up on the support calls since Monday",
    # superlative / complaint
    "top customer complaint this week",
    "biggest pain point customers raised this month",
    "most common complaint from users this week",
    "what's the #1 customer complaint",
    # explicit VoC
    "voice of customer",
    "voc report for last quarter",
    # feedback ↔ conversations
    "summary of feedback from recent customer conversations",
    "top takeaways from user feedback",
]

# Paraphrases the regexes deliberately don't chase — the INTENT stage owns
# these (live eval below). Kept here so the scope is complete in one place.
INTENT_STAGE_POSITIVES = [
    "what are customers most frustrated about right now",
    "how are customers feeling about the product this month",
    "show me customer quotes about onboarding",
    "did complaints about exports increase this week",
    "what's new in what users are telling us",
    "which problems came up most in support this week",
    "anything our clients keep asking for lately?",
]

NEGATIVES = [
    # Incidental mentions — must not divert.
    "we built this feature from customer feedback",
    "customers raised an issue yesterday",
    # Commands and other surfaces.
    "generate a PRD from the top insight",
    "turn this feedback into tickets",
    # Public/anonymous channels → public-feedback-report, never VoC.
    "what are people saying about us online",
    "summarize our app store reviews",
    "what's trending about us on Reddit",
]


@pytest.mark.parametrize("q", FAST_PATH_POSITIVES)
def test_fast_path_positive(q):
    assert _fast_path(q), f"fast-path missed: {q!r}"


@pytest.mark.parametrize("q", NEGATIVES)
def test_fast_path_stays_quiet_on_negative_space(q):
    assert not _fast_path(q), f"fast-path false positive: {q!r}"


# ── Live intent-router eval (real haiku call) ────────────────────────────────

@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="needs live model")
@pytest.mark.parametrize("q", INTENT_STAGE_POSITIVES)
def test_intent_router_routes_voc_paraphrases(q, isolated_settings):
    from app.qa_agent import route

    decision = route(q, enterprise_id="eval-ent")
    assert decision.skill_id == "voice-of-customer-report", (
        f"intent router sent {q!r} to {decision.skill_id!r} "
        f"(source={decision.source}, conf={decision.confidence})"
    )


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="needs live model")
@pytest.mark.parametrize("q", [
    "what are people saying about us online",
    "summarize our app store reviews",
])
def test_intent_router_keeps_public_feedback_out_of_voc(q, isolated_settings):
    from app.qa_agent import route

    decision = route(q, enterprise_id="eval-ent")
    assert decision.skill_id != "voice-of-customer-report", (
        f"public-channel ask {q!r} misrouted to VoC"
    )
