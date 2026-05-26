"""Abstract base class for KG backends.

Two implementations:
  - SqliteBackend  — transitional; uses existing sprintly.db with new
                     bitemporal tables. Lets KG ship without FalkorDB.
  - FalkorBackend  — production; Graphiti + FalkorDB + Cognee per spec.

The facade selects the backend via the GRAPH_BACKEND env var
(default = "sqlite" during the transition; flip to "falkor" once
FalkorDB is running on EC2 and the integration tests pass).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from app.graph.edges import Edge
from app.graph.entities import (
    Artifact,
    Decision,
    Hypothesis,
    Outcome,
    Signal,
    Workspace,
)


class GraphBackend(ABC):
    """Backend-agnostic API. The facade is the only caller."""

    # ──────────── lifecycle ────────────

    @abstractmethod
    def ping(self) -> bool:
        """Return True if the backend is reachable. False = degraded mode."""

    @abstractmethod
    def initialize_schema(self) -> None:
        """Ensure the backing store has whatever schema/indexes the
        backend needs. SQLite creates tables; FalkorDB creates indexes."""

    # ──────────── entity writes ────────────

    @abstractmethod
    def write_workspace(self, workspace: Workspace) -> None: ...

    @abstractmethod
    def write_signal(self, signal: Signal) -> None: ...

    @abstractmethod
    def write_hypothesis(self, hypothesis: Hypothesis) -> None: ...

    @abstractmethod
    def write_decision(self, decision: Decision) -> None: ...

    @abstractmethod
    def write_outcome(self, outcome: Outcome) -> None: ...

    @abstractmethod
    def write_artifact(self, artifact: Artifact) -> None: ...

    # ──────────── entity reads ────────────

    @abstractmethod
    def get_workspace(self, workspace_id: str) -> Optional[Workspace]: ...

    @abstractmethod
    def get_signal(self, workspace_id: str, signal_id: str) -> Optional[Signal]: ...

    @abstractmethod
    def get_hypothesis(
        self, workspace_id: str, hypothesis_id: str
    ) -> Optional[Hypothesis]: ...

    @abstractmethod
    def get_decision(
        self, workspace_id: str, decision_id: str
    ) -> Optional[Decision]: ...

    @abstractmethod
    def get_outcome(
        self, workspace_id: str, outcome_id: str
    ) -> Optional[Outcome]: ...

    @abstractmethod
    def get_artifact(
        self, workspace_id: str, artifact_id: str
    ) -> Optional[Artifact]: ...

    # ──────────── edge writes / reads ────────────

    @abstractmethod
    def write_edge(self, edge: Edge) -> None: ...

    @abstractmethod
    def edges_from(
        self,
        workspace_id: str,
        source_entity_id: str,
        edge_type: Optional[str] = None,
    ) -> list[Edge]: ...

    @abstractmethod
    def edges_to(
        self,
        workspace_id: str,
        target_entity_id: str,
        edge_type: Optional[str] = None,
    ) -> list[Edge]: ...

    # ──────────── query patterns (spec §7) ────────────

    @abstractmethod
    def load_session_context(self, workspace_id: str) -> dict[str, Any]:
        """Spec §7: workspace + top 10 active hypotheses + last 5 decisions
        + last 3 measured outcomes. Hard latency budget: ≤500ms.

        Returns a dict with keys: workspace, active_hypotheses, recent_decisions, recent_outcomes.
        """

    @abstractmethod
    def list_active_hypotheses(
        self, workspace_id: str, limit: int = 10
    ) -> list[Hypothesis]: ...

    @abstractmethod
    def list_recent_decisions(
        self, workspace_id: str, limit: int = 5
    ) -> list[Decision]: ...

    @abstractmethod
    def list_recent_outcomes(
        self, workspace_id: str, limit: int = 3, measured_only: bool = True
    ) -> list[Outcome]: ...

    @abstractmethod
    def list_active_signals(
        self,
        workspace_id: str,
        source_types: Optional[list[str]] = None,
        limit: int = 50,
    ) -> list[Signal]:
        """Non-stale Signals for this workspace, optionally filtered by source_type."""

    # ──────────── debug helpers (not in spec; used by facade tests) ────────────

    @abstractmethod
    def all_entity_ids(self, workspace_id: str) -> dict[str, list[str]]:
        """{'workspaces': [...], 'signals': [...], ...}. Test-only."""

    @abstractmethod
    def wipe_workspace(self, workspace_id: str) -> None:
        """Delete EVERYTHING for a workspace. Test-only — never call in prod."""
