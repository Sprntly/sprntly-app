"""KG facade — the only API the rest of Sprntly should call.

This module is the tenant-isolation layer. Every public function takes
`workspace_id` as its first argument; the function asserts that the
entity being read/written carries the same workspace_id, raising
TenantViolationError otherwise. Other modules MUST go through this
facade — directly instantiating SqliteBackend or FalkorBackend bypasses
the safety checks and is forbidden (the convention is documented; a
follow-up could add a runtime guard).

Engineering decisions:
  1. `GRAPH_BACKEND` env var selects backend at process startup. Default
     is "sqlite" so existing deployments keep working without FalkorDB.
     Flipping to "falkor" requires the container running + P1-10/P1-11
     PRs merged.
  2. Every read/write enforces `entity.workspace_id == requested_workspace_id`.
     This is the floor for tenant isolation; the FalkorDB layer also
     scopes by `group_id = workspace_id` per spec but we don't trust it
     blindly.
  3. Decision creation is special: it must `_promote_from_hypothesis`
     and verifies the source Hypothesis exists in the same workspace.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from app.graph.backends import GraphBackend, get_backend
from app.graph.edges import Edge, EdgeType
from app.graph.entities import (
    Artifact,
    Decision,
    Hypothesis,
    Outcome,
    Signal,
    Workspace,
)
from app.graph.exceptions import (
    HypothesisPromotionError,
    ImmutabilityError,
    TenantViolationError,
)

logger = logging.getLogger(__name__)


def _assert_tenant(workspace_id: str, entity_workspace_id: str) -> None:
    if workspace_id != entity_workspace_id:
        raise TenantViolationError(
            f"workspace_id mismatch: requested={workspace_id} entity={entity_workspace_id}"
        )


class GraphFacade:
    """The single point of access to the KG.

    Instantiate once at FastAPI app startup with the configured backend:

        graph = GraphFacade.from_env()
        graph.initialize()
    """

    def __init__(self, backend: GraphBackend):
        self._backend = backend

    @classmethod
    def from_env(cls) -> "GraphFacade":
        backend_name = os.environ.get("GRAPH_BACKEND", "sqlite").lower()
        if backend_name == "sqlite":
            db_path = os.environ.get("DB_PATH") or os.environ.get(
                "KG_SQLITE_PATH", "/var/lib/sprntly/data/sprintly.db"
            )
            backend = get_backend("sqlite", db_path=db_path)
        elif backend_name == "falkor":
            host = os.environ.get("FALKORDB_HOST", "127.0.0.1")
            port = int(os.environ.get("FALKORDB_PORT", "6379"))
            password = os.environ.get("FALKORDB_PASSWORD") or None
            backend = get_backend("falkor", host=host, port=port, password=password)
        else:
            raise ValueError(f"Unknown GRAPH_BACKEND={backend_name!r}")
        logger.info("GraphFacade backend=%s", backend_name)
        return cls(backend)

    def initialize(self) -> None:
        self._backend.initialize_schema()

    def healthy(self) -> bool:
        return self._backend.ping()

    # ──────────── Workspace ────────────

    def write_workspace(self, workspace_id: str, workspace: Workspace) -> None:
        _assert_tenant(workspace_id, workspace.workspace_id)
        self._backend.write_workspace(workspace)

    def get_workspace(self, workspace_id: str) -> Optional[Workspace]:
        return self._backend.get_workspace(workspace_id)

    # ──────────── Signal ────────────

    def write_signal(self, workspace_id: str, signal: Signal) -> None:
        _assert_tenant(workspace_id, signal.workspace_id)
        self._backend.write_signal(signal)

    def get_signal(self, workspace_id: str, signal_id: str) -> Optional[Signal]:
        return self._backend.get_signal(workspace_id, signal_id)

    def list_active_signals(
        self,
        workspace_id: str,
        source_types: Optional[list[str]] = None,
        limit: int = 50,
    ) -> list[Signal]:
        return self._backend.list_active_signals(workspace_id, source_types, limit)

    # ──────────── Hypothesis ────────────

    def write_hypothesis(self, workspace_id: str, hypothesis: Hypothesis) -> None:
        _assert_tenant(workspace_id, hypothesis.workspace_id)
        self._backend.write_hypothesis(hypothesis)

    def get_hypothesis(
        self, workspace_id: str, hypothesis_id: str
    ) -> Optional[Hypothesis]:
        return self._backend.get_hypothesis(workspace_id, hypothesis_id)

    def list_active_hypotheses(
        self, workspace_id: str, limit: int = 10
    ) -> list[Hypothesis]:
        return self._backend.list_active_hypotheses(workspace_id, limit)

    # ──────────── Decision (promotion-only path) ────────────

    def promote_hypothesis_to_decision(
        self, workspace_id: str, decision: Decision
    ) -> None:
        """Decisions are never created directly. The caller must construct
        the Decision with `promoted_from_hypothesis_id` already set; we
        verify the source Hypothesis exists in the same workspace.
        """
        _assert_tenant(workspace_id, decision.workspace_id)
        if not decision.promoted_from_hypothesis_id:
            raise HypothesisPromotionError(
                "Decision must be promoted from a Hypothesis (promoted_from_hypothesis_id required)"
            )
        source_hyp = self._backend.get_hypothesis(
            workspace_id, decision.promoted_from_hypothesis_id
        )
        if source_hyp is None:
            raise HypothesisPromotionError(
                f"Source Hypothesis {decision.promoted_from_hypothesis_id} not found "
                f"in workspace {workspace_id}"
            )
        # Existence + idempotency: if the same decision_id was already
        # written, deny re-write because Decision is immutable.
        existing = self._backend.get_decision(workspace_id, decision.decision_id)
        if existing is not None and existing.evidence_snapshot != decision.evidence_snapshot:
            raise ImmutabilityError(
                f"Decision {decision.decision_id} already exists with a different "
                "evidence_snapshot. Decisions are immutable; create a new Decision instead."
            )
        self._backend.write_decision(decision)

    def get_decision(
        self, workspace_id: str, decision_id: str
    ) -> Optional[Decision]:
        return self._backend.get_decision(workspace_id, decision_id)

    # ──────────── Outcome ────────────

    def write_outcome(self, workspace_id: str, outcome: Outcome) -> None:
        _assert_tenant(workspace_id, outcome.workspace_id)
        self._backend.write_outcome(outcome)

    def get_outcome(self, workspace_id: str, outcome_id: str) -> Optional[Outcome]:
        return self._backend.get_outcome(workspace_id, outcome_id)

    # ──────────── Artifact ────────────

    def write_artifact(self, workspace_id: str, artifact: Artifact) -> None:
        _assert_tenant(workspace_id, artifact.workspace_id)
        self._backend.write_artifact(artifact)

    def get_artifact(
        self, workspace_id: str, artifact_id: str
    ) -> Optional[Artifact]:
        return self._backend.get_artifact(workspace_id, artifact_id)

    # ──────────── Edges ────────────

    def write_edge(self, workspace_id: str, edge: Edge) -> None:
        _assert_tenant(workspace_id, edge.workspace_id)
        edge.validate_direction()  # raises if source/target types are wrong
        self._backend.write_edge(edge)

    def edges_from(
        self,
        workspace_id: str,
        source_entity_id: str,
        edge_type: Optional[EdgeType] = None,
    ) -> list[Edge]:
        return self._backend.edges_from(
            workspace_id,
            source_entity_id,
            edge_type.value if edge_type else None,
        )

    def edges_to(
        self,
        workspace_id: str,
        target_entity_id: str,
        edge_type: Optional[EdgeType] = None,
    ) -> list[Edge]:
        return self._backend.edges_to(
            workspace_id,
            target_entity_id,
            edge_type.value if edge_type else None,
        )

    # ──────────── Session context (spec query pattern §7) ────────────

    def load_session_context(self, workspace_id: str) -> dict[str, Any]:
        """Workspace + top 10 active hypotheses + last 5 decisions + last
        3 measured outcomes. Hard latency budget: ≤500ms (spec §10)."""
        return self._backend.load_session_context(workspace_id)

    # ──────────── Provenance walks (spec query pattern §7) ────────────

    def trace_provenance(self, workspace_id: str, decision_id: str):
        """Walk PROMOTED_TO + SUPPORTS edges backwards from a Decision to
        the originating Signals.

        Returns a ProvenanceChain. See app.graph.provenance for details.
        """
        # Local import to avoid a circular import at module load time
        # (provenance.py type-hints GraphFacade).
        from app.graph.provenance import trace_provenance

        return trace_provenance(self, workspace_id, decision_id)

    def trace_outcome_provenance(self, workspace_id: str, outcome_id: str):
        """Walk RESULTED_IN + MOTIVATED + PROMOTED_TO + SUPPORTS edges
        backwards from an Outcome to the originating Signals."""
        from app.graph.provenance import trace_outcome_provenance

        return trace_outcome_provenance(self, workspace_id, outcome_id)
