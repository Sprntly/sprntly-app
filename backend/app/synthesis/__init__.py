"""Synthesis Agent package — cross-signal reasoning + Brief assembly.

This package will house:
  - hypothesis.py        — the framing format for Brief recommendations (P0-4)
  - brief_assembly.py    — the 11-step scheduled-mode pipeline (P0-3)
  - on_demand.py         — PM-chat → artifact flow (P1-13)
  - clarification.py     — KG-first clarifying-question loop (P2)

Per the Synthesis_Agent_Spec, every recommendation emitted in a Brief
follows the HypothesisFraming structure (see hypothesis.py). This is the
contract every Synthesis output — Brief recs, on-demand recs — must obey.
"""

from app.synthesis.hypothesis import (
    HypothesisConfidence,
    HypothesisFraming,
    HypothesisImpact,
    HypothesisOutput,
    SignalCitation,
)

__all__ = [
    "HypothesisConfidence",
    "HypothesisFraming",
    "HypothesisImpact",
    "HypothesisOutput",
    "SignalCitation",
]
