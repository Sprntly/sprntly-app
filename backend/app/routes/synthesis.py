"""Synthesis Agent HTTP surface.

Currently exposes:

    POST /v1/synthesis/on-demand  body=PmChatTurn → SynthesisOnDemandResponse

Background-mode synthesis (spec §5) lands in a follow-up PR — that path
is driven by the Brief generator, not by the chat surface, so it doesn't
need an HTTP endpoint here.

Engineering decision: the GraphFacade is instantiated per-request via
`GraphFacade.from_env()` rather than wired up at app startup. The facade
itself is cheap (it just selects the backend) and the underlying SQLite
backend uses short-lived connections, so the overhead is negligible.
When we switch to Falkor at scale we'll move to a singleton — for now,
per-request avoids the startup-ordering snare with `lifespan`.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from app.auth import require_session
from app.graph import GraphFacade
from app.synthesis.on_demand import (
    PmChatTurn,
    SynthesisOnDemandResponse,
    respond_to_pm,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/synthesis", tags=["synthesis"])


def _get_graph() -> GraphFacade:
    """FastAPI dependency that yields a GraphFacade bound to the configured
    backend. Override in tests to inject a Mock or an in-memory SqliteBackend.
    """
    return GraphFacade.from_env()


@router.post("/on-demand", response_model=SynthesisOnDemandResponse)
def on_demand(
    body: PmChatTurn,
    _session: dict = Depends(require_session),
    graph: GraphFacade = Depends(_get_graph),
) -> SynthesisOnDemandResponse:
    """Handle one PM chat turn — clarify or generate an artifact.

    Auth: any signed-in session (app or demo audience). Tenant isolation
    is enforced inside `respond_to_pm` via the GraphFacade — the
    workspace_id in the body MUST match what the KG has, otherwise the
    facade raises TenantViolationError (mapped to 500 by FastAPI for now;
    we'll surface a 403 once we wire workspace claims into JWT, PR TBD).
    """
    return respond_to_pm(body, graph)
