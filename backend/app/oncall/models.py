"""Pydantic models for the On-Call Agent emergency brief.

Schema mirrors MASTER_PRD §11. The strict cap on `body` (8000 chars) is
the input-side guardrail: anything bigger gets rejected at the route
layer before we burn tokens. `requires_pm_approval` on
`ProposedSolution` is the invariant that keeps V1 from auto-acting —
even if the LLM hands us `false`, the runner forces it to True before
returning.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# Max chars accepted in TicketInput.body. We strip + reject above this
# at the model layer so a 200KB ticket dump never reaches the LLM.
TICKET_BODY_MAX_CHARS = 8000


class TicketInput(BaseModel):
    source: Literal["zendesk", "intercom", "manual", "email"]
    ticket_id: Optional[str] = None
    title: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1, max_length=TICKET_BODY_MAX_CHARS)
    reporter: Optional[str] = None
    received_at: Optional[str] = None
    linked_ticket_ids: list[str] = Field(default_factory=list)


class RootCauseHypothesis(BaseModel):
    summary: str  # 1-line
    confidence: Literal["low", "medium", "high"]
    evidence: list[str] = Field(default_factory=list)  # bullet points the LLM extracted


class ImpactAssessment(BaseModel):
    affected_user_count_estimate: Optional[int] = None
    severity: Literal["low", "medium", "high", "critical"]
    affected_features: list[str] = Field(default_factory=list)


class ProposedSolution(BaseModel):
    summary: str
    code_change_hint: Optional[str] = None  # "fix X in Y, change Z logic in service W"
    rollback_plan: Optional[str] = None
    # ALWAYS True for V1. The route layer re-stamps this before returning
    # to defend the invariant against an LLM that hands us False; this
    # default is the model-layer belt to the runner's suspenders.
    requires_pm_approval: bool = True


class EmergencyBrief(BaseModel):
    workspace_id: str
    generated_at: str
    ticket_summary: str  # 1-line restatement of the ticket
    root_cause: RootCauseHypothesis
    impact: ImpactAssessment
    proposed_solution: ProposedSolution
    agent_notes: str  # caveats, missing context the LLM flagged
