"""Sprntly Knowledge Graph package.

Public API:
    from app.graph import GraphFacade
    graph = GraphFacade.from_env()
    graph.initialize()

Entity + edge models:
    from app.graph.entities import Workspace, Signal, Hypothesis, Decision, Outcome, Artifact
    from app.graph.edges import Edge, EdgeType
"""
from app.graph.edges import Edge, EdgeType, EDGE_DIRECTION_TABLE
from app.graph.entities import (
    Artifact,
    ArtifactType,
    BitemporalMixin,
    ConfidenceTier,
    Decision,
    DismissedReason,
    DsAgentTier,
    Hypothesis,
    HypothesisStatus,
    KpiTreeNode,
    Outcome,
    ProvenanceTag,
    Signal,
    SignalSourceType,
    SIGNAL_STALENESS_BY_SOURCE_TYPE,
    SIGNAL_STALENESS_DAYS,
    TenantMixin,
    TrustLevel,
    Workspace,
    WorkspacePlan,
    WorkspaceStage,
    WorkspaceStrategy,
)
from app.graph.exceptions import (
    EdgeDirectionError,
    GraphError,
    HypothesisPromotionError,
    ImmutabilityError,
    NotConnectedError,
    TenantViolationError,
)
from app.graph.facade import GraphFacade
from app.graph.query_types import (
    ArtifactDelta,
    BriefContext,
    PrdContext,
    ProvenanceChain,
    SessionContext,
    SweepReport,
    WorkspaceSnapshot,
)

__all__ = [
    # facade
    "GraphFacade",
    # query types
    "SessionContext",
    "BriefContext",
    "PrdContext",
    "ProvenanceChain",
    "WorkspaceSnapshot",
    "SweepReport",
    "ArtifactDelta",
    # entities
    "Workspace",
    "Signal",
    "Hypothesis",
    "Decision",
    "Outcome",
    "Artifact",
    # sub-models
    "KpiTreeNode",
    "WorkspaceStrategy",
    # enums
    "TrustLevel",
    "WorkspaceStage",
    "WorkspacePlan",
    "SignalSourceType",
    "ProvenanceTag",
    "HypothesisStatus",
    "ConfidenceTier",
    "DsAgentTier",
    "DismissedReason",
    "ArtifactType",
    # edges
    "Edge",
    "EdgeType",
    "EDGE_DIRECTION_TABLE",
    # exceptions
    "GraphError",
    "NotConnectedError",
    "TenantViolationError",
    "EdgeDirectionError",
    "ImmutabilityError",
    "HypothesisPromotionError",
    # tables + mixins
    "SIGNAL_STALENESS_DAYS",
    "SIGNAL_STALENESS_BY_SOURCE_TYPE",
    "BitemporalMixin",
    "TenantMixin",
]
