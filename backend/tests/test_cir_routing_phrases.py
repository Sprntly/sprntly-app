"""CIR routing phrase scope — intent first, phrases as the eval set.

Mirrors tests/test_voc_routing_phrases.py, for the same reason and under the
same decision (Apurva, 2026-07-27): skill selection is INTENT matching via the
haiku router over skill descriptions, with the deterministic regex rules kept
only as a zero-latency fast-path for unambiguous shapes. Phrasings live here as
TEST DATA so new ones extend the eval set instead of growing regexes.

Why this file exists now: the CIR fast-path used to be
`\\b(competit|competitor|competitive analysis|market position)\\b`, so ANY
sentence containing "competitor" bought a multi-minute staged web-research
review — "the PRD should mention our competitors", "who are our competitors?",
"customers keep comparing us to a competitor". The rule is narrowed to
report-intent shapes (`is_competitive_report_request`), which makes the negative
space the interesting half of the test.

Three tiers:
  - Unit (always runs): the fast-path matcher hits the high-precision report
    shapes and stays quiet on the negative space.
  - Rule order (always runs): the sibling skills whose asks read
    competitor-ish keep their questions — sales-battlecard above CIR, and the
    public-feedback rules ahead of both.
  - Live eval (`-m integration`, real haiku router): paraphrases the regex
    deliberately does NOT cover must still land on CIR via the intent stage.
"""
from __future__ import annotations

import os

import pytest

from app.skill_router import detect_intent, is_competitive_report_request

CIR = "competitive-intelligence-review"


# ── The phrase scope ─────────────────────────────────────────────────────────
# Shapes: explicit-report · cadence · standing/comparison · what-shipped ·
# benchmark · landscape — each with and without a named competitor set.

FAST_PATH_POSITIVES = [
    # explicit report asks
    "competitive intelligence",
    "run a competitive intelligence report",
    "competitor report",
    "give me a competitive analysis",
    "run a competitive analysis vs Linear and Jira",
    "competitive landscape report for our category",
    "market landscape",
    "competitor deep dive on Acme",
    # cadence
    "monthly competitor scan",
    "quarterly competitive review",
    "run the monthly competitor scan for us",
    # standing / comparison, with a competitor subject nearby
    "where do we stand vs competitors",
    "vs the competition, where do we stand?",
    "how do we compare to the market right now",
    "how do we stack up against our rivals",
    # what shipped
    "what are competitors shipping",
    "what have our competitors launched this quarter?",
    "what has the competition been up to lately",
    # benchmark
    "benchmark us against the market",
    "benchmark our product against competitors",
]

# Paraphrases the regex deliberately doesn't chase — the INTENT stage owns
# these (live eval below). Kept here so the scope is complete in one place.
INTENT_STAGE_POSITIVES = [
    "who are our rivals",
    "how do we compare to Acme",
    "give me a read on the landscape",
    "are we losing ground to anyone right now",
    "which company is winning in our category",
    "is anyone doing something we should worry about",
]

NEGATIVES = [
    # A single competitor FACT is not a landscape review.
    "who is Acme's CEO?",
    "does our competitor support SSO?",
    "when did Acme launch their API?",
    # Incidental mentions — the old broad rule's worst failure mode.
    "the PRD should mention our competitors",
    "customers keep comparing us to a competitor",
    "we lost that deal to a competitor last week",
    "write a PRD for SSO and mention our competitors",
    # Sibling strategy skills.
    "build a sales battlecard against Acme",
    "how do we sell against Acme?",
    "help me handle objections about pricing",
    "write our positioning statement vs competitors",
    "analyze the market structure for this category",
    "what's the TAM for this market?",
    "run a five forces analysis",
    "which of these two options should we pick — traffic lights please",
    # Public/anonymous channels → public-feedback-report, never CIR.
    "what are people saying about us online",
    "summarize our app store reviews",
    "what's trending about us on Reddit",
    # Other surfaces.
    "turn this feedback into tickets",
    "prioritize these features with RICE",
]


@pytest.mark.parametrize("q", FAST_PATH_POSITIVES)
def test_fast_path_positive(q):
    assert is_competitive_report_request(q), f"fast-path missed: {q!r}"


@pytest.mark.parametrize("q", NEGATIVES)
def test_fast_path_stays_quiet_on_negative_space(q):
    assert not is_competitive_report_request(q), f"fast-path false positive: {q!r}"


@pytest.mark.parametrize("q", INTENT_STAGE_POSITIVES)
def test_intent_stage_shapes_are_not_claimed_by_the_regex(q):
    """These are report asks by intent, but ambiguous on their words — the
    regex must defer so the haiku router decides. Pinning that here is what
    stops the rule quietly re-widening back to bare "competit*"."""
    assert not is_competitive_report_request(q), (
        f"{q!r} was claimed by the fast-path; it belongs to the intent stage"
    )


# ── Rule order: the siblings keep their own questions ────────────────────────

@pytest.mark.parametrize("q", FAST_PATH_POSITIVES)
def test_detect_intent_routes_report_shapes_to_cir(q):
    d = detect_intent(q)
    assert d and d.skill_id == CIR, (
        f"{q!r} routed to {getattr(d, 'skill_id', None)!r}"
    )


@pytest.mark.parametrize("q", [
    "build a sales battlecard against Acme",
    "how do we sell against Acme?",
    "I need a battlecard for the Acme deal",
    "help me handle objections about pricing",
])
def test_battlecard_wins_over_cir(q):
    """The battlecard rule sits ABOVE CIR: a sales-enablement ask must not buy
    a multi-minute landscape review."""
    d = detect_intent(q)
    assert d and d.skill_id == "sales-battlecard", (
        f"{q!r} routed to {getattr(d, 'skill_id', None)!r}"
    )


@pytest.mark.parametrize("q", [
    "what are people saying about us online",
    "summarize our app store reviews",
    "run review mining on our G2 and Trustpilot reviews",
    "what's our public standing?",
])
def test_public_feedback_rules_still_precede_cir(q):
    """Public-review phrasings STAY on public-feedback-report. The CIR rule
    lives after them in _RULES and the narrowing must not have changed that."""
    d = detect_intent(q)
    assert d and d.skill_id == "public-feedback-report", (
        f"{q!r} routed to {getattr(d, 'skill_id', None)!r}"
    )


@pytest.mark.parametrize("q", [
    "the PRD should mention our competitors",
    "customers keep comparing us to a competitor",
    "we lost that deal to a competitor last week",
    "does our competitor support SSO?",
])
def test_incidental_competitor_mentions_never_fast_path_to_cir(q):
    """The regression this narrowing exists for: an incidental mention must not
    reach the heaviest skill we ship."""
    d = detect_intent(q)
    assert not (d and d.skill_id == CIR), f"{q!r} still fast-paths to CIR"


@pytest.mark.parametrize("q", [
    "analyze the market structure for this category",
    "what's the TAM for this market?",
    "run a five forces analysis",
    "write our positioning statement vs competitors",
])
def test_sibling_strategy_asks_are_vetoed_out_of_cir(q):
    """`_CIR_VETO` defers these so the haiku router can pick market-structure /
    positioning, rather than CIR claiming them on a shared noun."""
    d = detect_intent(q)
    assert not (d and d.skill_id == CIR), f"{q!r} was claimed by CIR"


# ── Live intent-router eval (real haiku call) ────────────────────────────────

@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="needs live model")
@pytest.mark.parametrize("q", INTENT_STAGE_POSITIVES)
def test_intent_router_routes_cir_paraphrases(q, isolated_settings):
    from app.qa_agent import route

    decision = route(q, enterprise_id="eval-ent")
    assert decision.skill_id == CIR, (
        f"intent router sent {q!r} to {decision.skill_id!r} "
        f"(source={decision.source}, conf={decision.confidence})"
    )


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="needs live model")
@pytest.mark.parametrize("q", [
    "build a sales battlecard against Acme",
    "write our positioning statement vs competitors",
    "what are people saying about us online",
])
def test_intent_router_keeps_siblings_out_of_cir(q, isolated_settings):
    from app.qa_agent import route

    decision = route(q, enterprise_id="eval-ent")
    assert decision.skill_id != CIR, f"sibling ask {q!r} misrouted to CIR"
