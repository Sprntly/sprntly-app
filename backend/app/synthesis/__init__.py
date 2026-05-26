"""Synthesis Agent — generates artifacts from KG context.

Public API:

    from app.synthesis.on_demand import respond_to_pm, PmChatTurn

On-demand mode (spec §4) accepts a PM chat turn, decides whether the KG
holds enough context to produce the requested artifact, and either:
  - returns one targeted clarifying question, or
  - generates the artifact (optionally with assumptions flagged inline).

Background-mode synthesis (§5) is a separate module and ships in a
follow-up PR.
"""
from app.synthesis.on_demand import (
    ArtifactResponse,
    ClarifyingQuestion,
    PmChatTurn,
    SynthesisOnDemandResponse,
    respond_to_pm,
)

__all__ = [
    "ArtifactResponse",
    "ClarifyingQuestion",
    "PmChatTurn",
    "SynthesisOnDemandResponse",
    "respond_to_pm",
]
