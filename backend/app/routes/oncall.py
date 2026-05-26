"""On-Call Agent HTTP routes.

V1 surface is a single endpoint: POST /v1/oncall/emergency-brief. Body
is a TicketInput JSON; response is an EmergencyBrief. Auth-gated via
`require_session` (same gate the brief routes use).

The full investigation pipeline, auto-trigger UI, and PM-notification
routing live behind P2 and are NOT exposed here.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.auth import require_session
from app.oncall import EmergencyBrief, TicketInput, generate_emergency_brief

router = APIRouter(prefix="/v1/oncall", tags=["oncall"])


@router.post("/emergency-brief", response_model=EmergencyBrief)
def emergency_brief(
    ticket: TicketInput,
    workspace_id: str = Query(..., min_length=1),
    _session: dict = Depends(require_session),
) -> EmergencyBrief:
    """Generate an emergency brief from a single ticket (or cluster).

    Synchronous: blocks until the LLM returns (~10–30s). Caller is
    expected to be a human operator / PM clicking "escalate" in the
    UI, not a hot path.

    Invariant: the returned brief's `proposed_solution.requires_pm_
    approval` is ALWAYS true. V1 never auto-acts.
    """
    return generate_emergency_brief(workspace_id, ticket)
