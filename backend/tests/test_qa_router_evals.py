"""Router regression gate.

A labeled (question → expected skill) set. The offline gate is deterministic
and runs everywhere: (1) every expected id is installed + routable, and (2) the
regex fast-path NEVER mis-routes a labeled question — for each case it either
returns the expected skill or defers (None / low-confidence / non-routable),
letting the LLM router decide. A wrong regex match is a regression.

The live-router accuracy check (real haiku) is opt-in (`-m integration` with an
API key) so the gate needs no network by default.
"""
from __future__ import annotations

import os

import pytest

import app.qa_agent as qa
from app.skill_router import detect_intent
from app.skills.catalog import routable_manifest

# (question, expected skill id). Mix of regex-covered and LLM-only skills.
EVALS: list[tuple[str, str]] = [
    ("Write a PRD for in-app onboarding checklists", "prd-author"),
    # Broadened PRD phrasings — "Give me a prd for …" was a real user miss
    # under the old generate/create/write/draft-only verb list.
    ("Give me a prd for the Machine Purchase Order project", "prd-author"),
    ("We need a PRD for offline exports", "prd-author"),
    ("Put together a quick prd for usage-based pricing", "prd-author"),
    ("Generate user stories for the checkout flow", "user-stories"),
    ("Prioritize these features with RICE: SSO, export, dark mode", "prioritize"),
    ("Re-rank our backlog by WSJF", "prioritize"),
    ("Triage this messy backlog and dedupe it", "ideation-prioritize"),
    ("Draft a decision memo: build vs buy for billing", "decision-memo"),
    ("Synthesize this pile of customer feedback into themes", "feedback-synthesis"),
    ("Synthesize these user interviews into themes", "interview-synthesis"),
    ("What did we learn from these customer calls?", "interview-synthesis"),
    ("What are people saying about us on the App Store and Reddit?", "public-feedback-report"),
    ("Mine our G2 and Trustpilot reviews for sentiment trends", "public-feedback-report"),
    ("Analyze our support tickets and customer complaints", "voice-of-customer-report"),
    ("Summarize my customer calls", "voice-of-customer-report"),
    ("What feedback came up in our Fireflies call recordings?", "voice-of-customer-report"),
    ("Build a voice of customer report from last quarter's calls", "voice-of-customer-report"),
    ("Run a competitive analysis vs Linear and Jira", "competitive-intelligence-review"),
    ("Write an incident runbook for a sev-1 outage", "incident-runbook"),
    # LLM-only skills (no regex rule) — regex must defer, not mis-route.
    ("Why are users churning after the second week?", "retention-churn"),
    ("Design an A/B test for the new pricing page", "experiment-design"),
    ("Where is our activation funnel leaking?", "funnel-activation"),
    ("Build a Now/Next/Later roadmap for Q3", "roadmap"),
    ("Write OKRs for the growth team", "okr-nct"),
    ("Help me position this product against incumbents", "positioning"),
    ("Frame the real problem behind this feature request", "problem-framing"),
    ("Map our stakeholders and their interests", "stakeholder-map"),
    ("Write a status update for leadership", "status-report"),
    ("Diagnose our SaaS metrics health", "saas-metrics-diagnosis"),
    ("Pre-mortem this launch — how could it fail?", "pre-mortem"),
]


def test_expected_skills_are_routable():
    routable = {s["id"] for s in routable_manifest()}
    bad = sorted({exp for _, exp in EVALS} - routable)
    assert bad == [], f"eval labels not routable/installed: {bad}"


@pytest.mark.parametrize("question", [
    "Give me a prd for the Machine Purchase Order project",
    "We need a PRD for offline exports",
    "Put together a quick prd for usage-based pricing",
    "can you build a prd for the referral program",
])
def test_regex_catches_broadened_prd_phrasings(question):
    """These must HIT the regex tier (not merely defer to the LLM router) —
    the fast-path is what guarantees the phrasing routes even when the LLM
    router is down or times out."""
    m = detect_intent(question)
    assert m is not None and m.skill_id == "prd-author"
    assert m.confidence >= 0.75


@pytest.mark.parametrize("question,expected", EVALS)
def test_regex_never_misroutes(question, expected):
    m = detect_intent(question)
    if m and m.confidence >= 0.75 and qa._routable(m.skill_id):
        assert m.skill_id == expected, (
            f"regex mis-routed {question!r}: got {m.skill_id}, expected {expected}"
        )
    # else: regex defers to the LLM router — fine.


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="needs live router")
def test_live_router_accuracy():
    """Opt-in: the real haiku router should get most labels right."""
    correct = sum(
        1
        for q, exp in EVALS
        if qa.route(q, enterprise_id="eval").skill_id == exp
    )
    assert correct / len(EVALS) >= 0.7, f"router accuracy {correct}/{len(EVALS)}"
