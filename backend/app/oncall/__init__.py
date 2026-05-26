"""On-Call Agent package.

Reactive (vs scheduled Brief) sub-system: an operator feeds a support
ticket (or a cluster) into the agent and gets back a structured
emergency brief — root cause hypothesis + impact assessment + proposed
solution. V1 scope is intentionally narrow:

  - input: a single TicketInput (with optional linked_ticket_ids)
  - output: an EmergencyBrief (Pydantic) shipped back to the PM
  - invariant: NEVER auto-acts. proposed_solution.requires_pm_approval
    is forced to True on every brief; downstream surfaces (the
    PM-notification routing in P2 and the Claude Code handoff) read
    that flag and gate any side effect on a PM click.

The threshold UI, auto-trigger pipeline, full multi-ticket
investigation, and PM notification routing live behind P2 tasks and
are NOT in this package.
"""
from app.oncall.emergency_brief import generate_emergency_brief
from app.oncall.models import (
    EmergencyBrief,
    ImpactAssessment,
    ProposedSolution,
    RootCauseHypothesis,
    TicketInput,
)

__all__ = [
    "EmergencyBrief",
    "ImpactAssessment",
    "ProposedSolution",
    "RootCauseHypothesis",
    "TicketInput",
    "generate_emergency_brief",
]
