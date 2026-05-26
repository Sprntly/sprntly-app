"""FalkorDB-backed graph implementation — production target per spec.

This backend uses Graphiti's bitemporal graph layer + FalkorDB as the
storage engine, with Cognee's ECL pipeline handling entity resolution
and signal extraction from raw connector blobs.

CURRENT STATUS: Connection + ping implemented. Read/write paths raise
NotImplementedError pending P1-10 (write events) and P1-11 (full query
API) PRs. The SqliteBackend covers all functionality the facade exposes
today; flipping `GRAPH_BACKEND=falkor` once those land is the cutover.

Engineering decision: defer the actual Graphiti integration to dedicated
PRs because (a) it requires the FalkorDB container running on EC2 for
real integration tests, and (b) Graphiti's API surface is large enough
that mixing it into the entities-and-infra PR would make this PR
unreviewable. This file establishes the connection plumbing and the
shape of the class; the methods get filled in next.

Required env vars (see config.py):
  FALKORDB_HOST          (default: 127.0.0.1)
  FALKORDB_PORT          (default: 6379)
  COGNEE_DATA_PATH       (default: /var/lib/sprntly/cognee/data)
  COGNEE_SYSTEM_PATH     (default: /var/lib/sprntly/cognee/system)
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.graph.backends.base import GraphBackend
from app.graph.edges import Edge
from app.graph.entities import (
    Artifact,
    Decision,
    Hypothesis,
    Outcome,
    Signal,
    Workspace,
)
from app.graph.exceptions import GraphError, NotConnectedError

logger = logging.getLogger(__name__)


def _not_yet(method: str):  # pragma: no cover - helper only used until P1-10/P1-11 land
    raise NotImplementedError(
        f"FalkorBackend.{method} is not yet implemented. "
        "Lands in the P1-10 (write events) and P1-11 (query API) PRs. "
        "Use GRAPH_BACKEND=sqlite during the transition."
    )


class FalkorBackend(GraphBackend):
    """Production KG backend. See module docstring for current status."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6379,
        password: Optional[str] = None,
    ):
        self.host = host
        self.port = port
        self.password = password
        self._client: Any = None  # FalkorDB() instance, lazily created

    def _get_client(self):
        """Lazy-import falkordb so the package isn't required for SqliteBackend users."""
        if self._client is not None:
            return self._client
        try:
            from falkordb import FalkorDB  # type: ignore[import-not-found]
        except ImportError as e:
            raise NotConnectedError(
                "FalkorDB Python driver not installed. "
                "Add `falkordb` to requirements.txt and `pip install`."
            ) from e
        try:
            self._client = FalkorDB(
                host=self.host, port=self.port, password=self.password
            )
        except Exception as e:
            raise NotConnectedError(f"FalkorDB connection failed: {e}") from e
        return self._client

    # ──────────── lifecycle ────────────

    def ping(self) -> bool:
        try:
            client = self._get_client()
            # The FalkorDB client exposes a Redis-compatible PING via the
            # underlying connection; if not available, a no-op graph read
            # works equivalently.
            client.connection.ping()  # type: ignore[attr-defined]
            return True
        except NotConnectedError:
            return False
        except Exception:  # pylint: disable=broad-except
            logger.warning("FalkorDB ping failed", exc_info=True)
            return False

    def initialize_schema(self) -> None:
        """Create per-workspace graphs lazily. FalkorDB doesn't require
        upfront schema, but we'll create the indexes the spec expects in
        P1-11. For now this is a no-op."""
        _ = self._get_client()
        logger.info(
            "FalkorBackend.initialize_schema: no-op until P1-11 (indexes land there)"
        )

    # ──────────── entity writes / reads — deferred to P1-10/P1-11 ────────────

    def write_workspace(self, workspace: Workspace) -> None: _not_yet("write_workspace")
    def write_signal(self, signal: Signal) -> None: _not_yet("write_signal")
    def write_hypothesis(self, hypothesis: Hypothesis) -> None: _not_yet("write_hypothesis")
    def write_decision(self, decision: Decision) -> None: _not_yet("write_decision")
    def write_outcome(self, outcome: Outcome) -> None: _not_yet("write_outcome")
    def write_artifact(self, artifact: Artifact) -> None: _not_yet("write_artifact")

    def get_workspace(self, workspace_id: str) -> Optional[Workspace]: _not_yet("get_workspace")
    def get_signal(self, workspace_id: str, signal_id: str) -> Optional[Signal]: _not_yet("get_signal")
    def get_hypothesis(self, workspace_id: str, hypothesis_id: str) -> Optional[Hypothesis]:
        _not_yet("get_hypothesis")
    def get_decision(self, workspace_id: str, decision_id: str) -> Optional[Decision]:
        _not_yet("get_decision")
    def get_outcome(self, workspace_id: str, outcome_id: str) -> Optional[Outcome]:
        _not_yet("get_outcome")
    def get_artifact(self, workspace_id: str, artifact_id: str) -> Optional[Artifact]:
        _not_yet("get_artifact")

    def write_edge(self, edge: Edge) -> None: _not_yet("write_edge")
    def edges_from(
        self, workspace_id: str, source_entity_id: str, edge_type: Optional[str] = None
    ) -> list[Edge]: _not_yet("edges_from")
    def edges_to(
        self, workspace_id: str, target_entity_id: str, edge_type: Optional[str] = None
    ) -> list[Edge]: _not_yet("edges_to")

    def load_session_context(self, workspace_id: str) -> dict[str, Any]:
        _not_yet("load_session_context")
    def list_active_hypotheses(self, workspace_id: str, limit: int = 10) -> list[Hypothesis]:
        _not_yet("list_active_hypotheses")
    def list_recent_decisions(self, workspace_id: str, limit: int = 5) -> list[Decision]:
        _not_yet("list_recent_decisions")
    def list_recent_outcomes(
        self, workspace_id: str, limit: int = 3, measured_only: bool = True
    ) -> list[Outcome]:
        _not_yet("list_recent_outcomes")
    def list_active_signals(
        self,
        workspace_id: str,
        source_types: Optional[list[str]] = None,
        limit: int = 50,
    ) -> list[Signal]:
        _not_yet("list_active_signals")

    def all_entity_ids(self, workspace_id: str) -> dict[str, list[str]]:
        _not_yet("all_entity_ids")
    def wipe_workspace(self, workspace_id: str) -> None:
        _not_yet("wipe_workspace")
