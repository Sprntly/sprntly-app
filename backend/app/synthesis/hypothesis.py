"""Hypothesis framing — the 5-field structure for every Brief recommendation.

Spec source: Synthesis_Agent_Spec.docx §4.4 + Master PRD §7. Every
recommendation emitted by the Synthesis Agent in a Brief or via on-demand
chat carries:

    signal_summary       — what patterns the agent observed and from
                           which sources, in 1–2 sentences
    hypothesis           — the specific build action recommended and why
                           it addresses the observed pattern
    predicted_impact     — metric(s) expected to move, direction, and a
                           confidence-adjusted range
    assumptions          — what the agent is assuming that, if wrong,
                           would invalidate the hypothesis
    disconfirming_signals — signals that contradict the recommendation
                           (NEVER hidden — spec is explicit)

This module is the canonical Pydantic representation. It is the schema
the Brief Assembly Algorithm (P0-3), the on-demand PM-chat flow (P1-13),
and anyone consuming Synthesis output writes/reads against.

Cross-references:
  * KG_Engineering_Spec §3 Hypothesis entity — these fields map onto
    Hypothesis nodes' `evidence_signal_ids`, `predicted_metric`,
    `predicted_impact_low/high`, `assumptions`, `disconfirming_signals`,
    `reversal_condition`, `confidence_composite`, `confidence_tier`.
  * MASTER_PRD §7.1 Brief Assembly Algorithm Step 8 — "For each: write
    one-line summary, assign confidence level, attach evidence chain
    (Signal IDs), compute predicted metric impact range."
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Confidence tiers per KG_Engineering_Spec — Express → low, Deep → medium,
# Comprehensive → high, Comprehensive+external corroboration → very_high.
HypothesisConfidence = Literal["low", "medium", "high", "very_high"]


# Mapping from DS Agent tier → default confidence tier. The Synthesis Agent
# can override this if cross-source corroboration boosts confidence.
TIER_TO_CONFIDENCE: dict[str, HypothesisConfidence] = {
    "express": "low",
    "deep": "medium",
    "comprehensive": "high",
    "comprehensive_corroborated": "very_high",
}


class SignalCitation(BaseModel):
    """A reference back to a KG Signal node that supports or contradicts
    the hypothesis. Brief detail views render the evidence chain by
    looking up these IDs in the KG.
    """

    model_config = ConfigDict(extra="forbid")

    signal_id: str = Field(..., description="KG Signal node ID.")
    source_tool: str = Field(
        ..., description="The connector tool that produced the signal (amplitude, zendesk, etc.)."
    )
    summary: str = Field(
        ...,
        max_length=280,
        description="One-line plain-English restatement of the signal for the Brief UI.",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="The signal's own confidence at the time the Brief was generated (frozen in evidence snapshot).",
    )


class HypothesisImpact(BaseModel):
    """Predicted metric impact with low/high bounds and a human-readable basis."""

    model_config = ConfigDict(extra="forbid")

    metric: str = Field(
        ...,
        description="Human-readable metric name (e.g. 'Day-30 retention'). MUST exist in Workspace KPI tree.",
    )
    direction: Literal["up", "down"] = Field(
        ..., description="Whether the metric is expected to rise or fall."
    )
    low: float = Field(
        ...,
        description="Lower bound of the predicted impact range (absolute or % depending on metric type).",
    )
    high: float = Field(
        ...,
        description="Upper bound of the predicted impact range. Must be >= low.",
    )
    basis: str = Field(
        ...,
        max_length=500,
        description="Why this range — DS Agent finding, historical analogue, etc. Surfaced in the Brief detail view.",
    )

    @field_validator("high")
    @classmethod
    def _high_gte_low(cls, v: float, info) -> float:  # noqa: ARG003
        low = info.data.get("low")
        if low is not None and v < low:
            raise ValueError(f"predicted_impact.high ({v}) must be >= low ({low})")
        return v


class HypothesisFraming(BaseModel):
    """The canonical 5-field framing for every Brief recommendation.

    Fields map onto Synthesis_Agent_Spec §4.4 and the Hypothesis KG entity.
    Order is the order in which the Brief Intelligence Detail view
    renders them.
    """

    model_config = ConfigDict(extra="forbid")

    signal_summary: str = Field(
        ...,
        min_length=10,
        max_length=600,
        description="What patterns the agent observed and from which sources (1–2 sentences).",
    )

    hypothesis: str = Field(
        ...,
        min_length=10,
        max_length=600,
        description="The specific build action recommended and why it addresses the observed pattern.",
    )

    predicted_impact: HypothesisImpact = Field(
        ...,
        description="Metric, direction, low/high range, basis. Always present — Synthesis cannot emit a recommendation without quantified impact.",
    )

    assumptions: list[str] = Field(
        default_factory=list,
        description="What the agent is assuming that, if wrong, would invalidate the hypothesis. Empty list means 'no caveats' (rare).",
    )

    disconfirming_signals: list[SignalCitation] = Field(
        default_factory=list,
        description=(
            "Signals that contradict the recommendation. NEVER hidden — "
            "Synthesis Agent must surface dissenting evidence per spec. "
            "CONTRADICTS edges in the KG correspond 1:1 with these citations."
        ),
    )


class HypothesisOutput(BaseModel):
    """The full per-recommendation payload Synthesis writes to the Brief
    (and stores in evidence_snapshot on Decision-promotion).

    Combines a HypothesisFraming with the supporting citations + ranking
    + confidence + reversal condition required to compose a Brief entry
    and to write a KG Hypothesis node.
    """

    model_config = ConfigDict(extra="forbid")

    rank: int = Field(
        ..., ge=1, le=5, description="Position in the Brief (1 = top recommendation; spec caps at 5)."
    )
    title: str = Field(
        ...,
        min_length=4,
        max_length=140,
        description="One-line headline rendered on the Brief overview card.",
    )
    framing: HypothesisFraming = Field(
        ..., description="The 5-field framing (signal summary / hypothesis / impact / assumptions / disconfirming)."
    )
    supporting_signals: list[SignalCitation] = Field(
        ...,
        min_length=1,
        description=(
            "Signals that support this recommendation. Spec constraint: at least 1; "
            "promotion from candidate → proposed Hypothesis requires evidence_count >= 3 "
            "from >= 2 distinct source_types (enforced at KG-write time, not here)."
        ),
    )
    confidence: HypothesisConfidence = Field(
        ..., description="Composite confidence tier (low/medium/high/very_high)."
    )
    ds_agent_tier: Optional[Literal["express", "deep", "comprehensive"]] = Field(
        default=None,
        description="Which DS Agent tier produced the underlying finding, if any. None = no DS run (agent-inferred).",
    )
    reversal_condition: str = Field(
        ...,
        min_length=10,
        max_length=400,
        description=(
            "What observation would force a rollback if this ships. Required on every Hypothesis per KG spec — "
            "copied onto the Decision when the PM approves."
        ),
    )

    def primary_source_types(self) -> set[str]:
        """Distinct source_tool families across supporting + disconfirming
        citations. Used by Synthesis to check the spec's promotion rule
        (>= 2 distinct source_types for candidate → proposed).
        """
        return {s.source_tool for s in self.supporting_signals} | {
            s.source_tool for s in self.framing.disconfirming_signals
        }
