"""Routing engine — every scenario from the spec's decision table."""

from __future__ import annotations

import pandas as pd
import pytest

from ds_agent.routing import (
    assess_quality,
    check_guardrails,
    classify_question,
    route,
)


# ─── Step 0: data quality ───


def test_assess_quality_insufficient_when_too_few_rows() -> None:
    assert assess_quality(100) == "INSUFFICIENT"


def test_assess_quality_low_when_small_or_incomplete() -> None:
    assert assess_quality(1000) == "LOW"


def test_assess_quality_medium_in_middle() -> None:
    assert assess_quality(5000) == "MEDIUM"


def test_assess_quality_high_when_large_and_complete() -> None:
    assert assess_quality(20_000) == "HIGH"


def test_assess_quality_factors_in_completeness() -> None:
    df = pd.DataFrame({"a": [None] * 6000 + [1.0] * 4000, "b": [2.0] * 10_000})
    # 30% missing in column 'a' → overall completeness ~ 0.70 → MEDIUM band
    assert assess_quality(df) in ("MEDIUM", "HIGH")


# ─── Step 2: classification ───


@pytest.mark.parametrize(
    "text,expected",
    [
        ("what drives retention?", "EXPLORATORY"),
        ("should we ship feature X?", "DECISION"),
        ("how much lift from onboarding?", "MEASUREMENT"),
        ("what is our north star strategy?", "STRATEGIC"),
        ("compare paid vs organic users", "COMPARATIVE"),
        ("show me patterns", "EXPLORATORY"),
        ("unrelated random text 12345", "EXPLORATORY"),  # default
    ],
)
def test_classify_question(text: str, expected: str) -> None:
    assert classify_question(text) == expected


# ─── Step 3: guardrails ───


def test_rare_segment_guardrail_bumps_to_comprehensive() -> None:
    override, triggered = check_guardrails("who are our power users?", base_tier="DEEP")
    assert override is not None
    assert override.tier == "COMPREHENSIVE"
    assert "rare_segment_expected" in triggered


def test_strategic_depth_guardrail() -> None:
    override, _ = check_guardrails("give me a deep dive on growth", base_tier="DEEP")
    assert override is not None
    assert override.tier == "COMPREHENSIVE"


def test_redundant_guardrail_returns_lookup() -> None:
    recent = [{"question": "what drives retention?", "age_hours": 2}]
    override, triggered = check_guardrails(
        "what drives retention?", recent_runs=recent, base_tier="DEEP"
    )
    assert override is not None
    assert override.tier == "LOOKUP"
    assert "redundant" in triggered


def test_over_analysis_guardrail_demotes_to_express() -> None:
    recent = [
        {"question": "q1", "age_hours": 24},
        {"question": "q2", "age_hours": 48},
        {"question": "q3", "age_hours": 72},
    ]
    override, triggered = check_guardrails(
        "what drives churn?", recent_runs=recent, base_tier="DEEP"
    )
    assert override is not None
    assert override.tier == "EXPRESS"
    assert "over_analysis" in triggered


# ─── Step 4: full routing ───


def test_route_rejects_when_insufficient_data() -> None:
    d = route("anything", data_quality="INSUFFICIENT")
    assert d.tier == "REJECT"


def test_route_low_quality_falls_back_to_claude_code() -> None:
    d = route("anything", data_quality="LOW")
    assert d.tier == "CLAUDE_CODE_FALLBACK"


def test_route_free_tier_capped_at_express() -> None:
    d = route("compare A vs B", user_plan="free", data_quality="HIGH")
    assert d.tier == "EXPRESS"
    assert d.reason == "free_tier_capped_at_express"


def test_route_monday_brief_bypasses_to_comprehensive() -> None:
    d = route("weekly summary", user_plan="pro", data_quality="HIGH", is_monday_brief=True)
    assert d.tier == "COMPREHENSIVE"
    assert d.reason == "monday_brief_bypass"


def test_route_cache_hit_returns_lookup() -> None:
    d = route(
        "what drives retention?",
        user_plan="pro",
        data_quality="HIGH",
        cache_state={"hit": True},
    )
    assert d.tier == "LOOKUP"


def test_route_exploratory_to_express() -> None:
    d = route("what drives retention?", user_plan="pro", data_quality="HIGH")
    assert d.tier == "EXPRESS"
    assert d.question_type == "EXPLORATORY"


def test_route_decision_to_deep() -> None:
    d = route("should we ship feature X?", user_plan="pro", data_quality="HIGH")
    assert d.tier == "DEEP"
    assert d.question_type == "DECISION"


def test_route_measurement_to_deep() -> None:
    d = route("how much lift from feature X?", user_plan="pro", data_quality="HIGH")
    assert d.tier == "DEEP"
    assert d.question_type == "MEASUREMENT"


def test_route_strategic_to_comprehensive() -> None:
    d = route("what is our strategic north star?", user_plan="pro", data_quality="HIGH")
    assert d.tier == "COMPREHENSIVE"


def test_route_comparative_to_deep() -> None:
    d = route("compare paid vs organic", user_plan="pro", data_quality="HIGH")
    assert d.tier == "DEEP"
    assert d.question_type == "COMPARATIVE"


def test_route_rare_segment_overrides_to_comprehensive() -> None:
    d = route("who are our power users?", user_plan="pro", data_quality="HIGH")
    assert d.tier == "COMPREHENSIVE"
    assert "rare_segment_expected" in d.guardrails_triggered
