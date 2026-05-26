"""Tests for app.synthesis.hypothesis — the 5-field Brief recommendation framing.

Spec source of truth: Synthesis_Agent_Spec.docx §4.4. Every test here is
either a direct mapping to a spec requirement or a guardrail against
contracts that downstream consumers (Brief Assembly, KG write events,
Decision promotion) rely on.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.synthesis.hypothesis import (
    TIER_TO_CONFIDENCE,
    HypothesisFraming,
    HypothesisImpact,
    HypothesisOutput,
    SignalCitation,
)


# ─────────────────────── HypothesisImpact ───────────────────────


def test_impact_high_must_be_gte_low():
    """high < low is a logical bug — schema must catch it."""
    with pytest.raises(ValidationError, match="high.*>= low"):
        HypothesisImpact(metric="D30 retention", direction="up", low=10.0, high=5.0, basis="ok")


def test_impact_direction_must_be_up_or_down():
    with pytest.raises(ValidationError):
        HypothesisImpact(
            metric="D30 retention",
            direction="sideways",  # type: ignore[arg-type]
            low=1.0,
            high=2.0,
            basis="x",
        )


def test_impact_basis_capped_at_500():
    """Basis is shown in the Brief detail UI; longer means we forgot to
    summarize and are dumping raw text."""
    with pytest.raises(ValidationError):
        HypothesisImpact(metric="m", direction="up", low=1, high=2, basis="x" * 501)


def test_impact_equal_low_high_is_allowed():
    """Point estimates are OK — happens when DS agent is highly confident."""
    impact = HypothesisImpact(
        metric="D30 retention", direction="up", low=3.0, high=3.0, basis="point estimate from PSM"
    )
    assert impact.low == impact.high


# ─────────────────────── HypothesisFraming ───────────────────────


def _impact_ok() -> HypothesisImpact:
    return HypothesisImpact(
        metric="D30 retention",
        direction="up",
        low=2.0,
        high=5.0,
        basis="SHAP top feature from Comprehensive run",
    )


def test_framing_requires_signal_summary_and_hypothesis():
    """Both narrative fields are non-empty; if Synthesis can't say what
    it saw or what to do, it has no business emitting a recommendation."""
    with pytest.raises(ValidationError):
        HypothesisFraming(  # type: ignore[call-arg]
            signal_summary="",
            hypothesis="something",
            predicted_impact=_impact_ok(),
        )


def test_framing_caps_narrative_at_600_chars():
    too_long = "x" * 601
    with pytest.raises(ValidationError):
        HypothesisFraming(
            signal_summary=too_long, hypothesis="ok do this", predicted_impact=_impact_ok()
        )
    with pytest.raises(ValidationError):
        HypothesisFraming(
            signal_summary="we saw X across Y users", hypothesis=too_long, predicted_impact=_impact_ok()
        )


def test_framing_assumptions_and_disconfirming_default_to_empty_lists():
    f = HypothesisFraming(
        signal_summary="we saw X across Y users",
        hypothesis="ship a Day-3 nudge",
        predicted_impact=_impact_ok(),
    )
    assert f.assumptions == []
    assert f.disconfirming_signals == []


def test_framing_rejects_extra_fields():
    """extra='forbid' so downstream code doesn't silently lose fields
    when consumers add new keys without updating the schema."""
    with pytest.raises(ValidationError):
        HypothesisFraming(
            signal_summary="we saw X across Y users",
            hypothesis="ship a Day-3 nudge",
            predicted_impact=_impact_ok(),
            confidence="high",  # type: ignore[call-arg] -- belongs on HypothesisOutput, not framing
        )


# ─────────────────────── SignalCitation ───────────────────────


def test_signal_confidence_must_be_in_unit_range():
    with pytest.raises(ValidationError):
        SignalCitation(signal_id="s1", source_tool="amplitude", summary="ok", confidence=1.5)
    with pytest.raises(ValidationError):
        SignalCitation(signal_id="s1", source_tool="amplitude", summary="ok", confidence=-0.1)


def test_signal_summary_truncates_at_280():
    """Twitter-ish length so the Brief UI doesn't break on long quotes."""
    with pytest.raises(ValidationError):
        SignalCitation(
            signal_id="s1",
            source_tool="zendesk",
            summary="x" * 281,
            confidence=0.5,
        )


# ─────────────────────── HypothesisOutput (full recommendation) ───────────────────────


def _framing_ok() -> HypothesisFraming:
    return HypothesisFraming(
        signal_summary="Activation drops 32% between Day-1 and Day-7 for mobile users.",
        hypothesis="Add a Day-3 in-app nudge with personalized next-action CTA.",
        predicted_impact=_impact_ok(),
        assumptions=["Mobile users see the nudge UI surface", "Push notification token is captured"],
        disconfirming_signals=[
            SignalCitation(
                signal_id="sig-99",
                source_tool="zendesk",
                summary="Users complained about Day-2 onboarding spam in 11 tickets last week.",
                confidence=0.6,
            ),
        ],
    )


def _supporting_signal(tool: str = "amplitude", sid: str = "sig-1") -> SignalCitation:
    return SignalCitation(
        signal_id=sid,
        source_tool=tool,
        summary="Users who fire Day-3 action retain 4.5× more on Day-30.",
        confidence=0.82,
    )


def test_output_requires_at_least_one_supporting_signal():
    """Spec constraint: 'Never generate a recommendation without at least
    one supporting Signal node.'"""
    with pytest.raises(ValidationError):
        HypothesisOutput(
            rank=1,
            title="Nudge users at Day-3",
            framing=_framing_ok(),
            supporting_signals=[],
            confidence="high",
            ds_agent_tier="comprehensive",
            reversal_condition="If Day-7 retention drops >2pp post-launch, roll back.",
        )


def test_output_rank_must_be_in_1_to_5():
    """Brief surface caps at 5 recommendations per spec §4.3."""
    with pytest.raises(ValidationError):
        HypothesisOutput(
            rank=6,
            title="x",
            framing=_framing_ok(),
            supporting_signals=[_supporting_signal()],
            confidence="high",
            reversal_condition="if X then revert",
        )
    with pytest.raises(ValidationError):
        HypothesisOutput(
            rank=0,
            title="x",
            framing=_framing_ok(),
            supporting_signals=[_supporting_signal()],
            confidence="high",
            reversal_condition="if X then revert",
        )


def test_output_reversal_condition_required():
    """Mirrors KG spec invariant: every Hypothesis MUST have a reversal_condition."""
    with pytest.raises(ValidationError):
        HypothesisOutput(  # type: ignore[call-arg]
            rank=1,
            title="x",
            framing=_framing_ok(),
            supporting_signals=[_supporting_signal()],
            confidence="high",
            reversal_condition="",
        )


def test_output_confidence_must_be_known_tier():
    with pytest.raises(ValidationError):
        HypothesisOutput(
            rank=1,
            title="x",
            framing=_framing_ok(),
            supporting_signals=[_supporting_signal()],
            confidence="medium-high",  # type: ignore[arg-type]
            reversal_condition="if X then revert",
        )


def test_primary_source_types_collects_supporting_and_disconfirming():
    """Used by Synthesis Agent to check the spec's promotion rule
    (candidate → proposed requires >= 2 distinct source_types)."""
    output = HypothesisOutput(
        rank=2,
        title="Nudge users at Day-3",
        framing=_framing_ok(),
        supporting_signals=[
            _supporting_signal(tool="amplitude", sid="sig-1"),
            _supporting_signal(tool="mixpanel", sid="sig-2"),
        ],
        confidence="high",
        ds_agent_tier="comprehensive",
        reversal_condition="If Day-7 retention drops >2pp post-launch, revert the experiment.",
    )
    types = output.primary_source_types()
    # Supporting: amplitude + mixpanel. Disconfirming on framing: zendesk.
    assert types == {"amplitude", "mixpanel", "zendesk"}


def test_output_serializes_round_trip():
    """Brief Assembly serializes these to JSON for KG storage + UI delivery."""
    output = HypothesisOutput(
        rank=1,
        title="Nudge users at Day-3",
        framing=_framing_ok(),
        supporting_signals=[_supporting_signal()],
        confidence="high",
        ds_agent_tier="comprehensive",
        reversal_condition="If Day-7 retention drops >2pp post-launch, revert the experiment.",
    )
    blob = output.model_dump_json()
    reloaded = HypothesisOutput.model_validate_json(blob)
    assert reloaded == output


# ─────────────────────── Tier → confidence mapping ───────────────────────


def test_tier_to_confidence_table_covers_all_documented_tiers():
    """Spec defines exactly these DS Agent tier → confidence mappings."""
    assert TIER_TO_CONFIDENCE["express"] == "low"
    assert TIER_TO_CONFIDENCE["deep"] == "medium"
    assert TIER_TO_CONFIDENCE["comprehensive"] == "high"
    assert TIER_TO_CONFIDENCE["comprehensive_corroborated"] == "very_high"
